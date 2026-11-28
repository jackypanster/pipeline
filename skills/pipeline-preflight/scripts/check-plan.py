#!/usr/bin/env python3
"""Read-only validator for .pipeline/<feature>/plan.md (v2 3-Agent 4-Step).

Always validates the COMMITTED Git blob (never checkout-filtered working-tree bytes),
so Git `ident`/smudge filters cannot cause the reviewed plan and executed verify[] to diverge.

Checks:
  1. Committed blob `<commit>:.pipeline/<feature>/plan.md` exists and is non-empty (default commit: HEAD).
  2. No uncommitted working-tree edits to `.pipeline/<feature>/plan.md`.
  3. On `--stage impl` or `--stage review`: committed `<commit>:.pipeline/current.json`'s
     `approved_plan_blob` is a 40-hex SHA matching `<commit>:.pipeline/<feature>/plan.md`
     (never reads approval from uncommitted working-tree `current.json`).
  4. Leading `--- ... ---` YAML frontmatter (LF or CRLF line endings normalized on parse while
     preserving raw blob SHA for approval) has no duplicate keys, explicit non-empty `verify:`
     list (scalars rejected), explicit `forbid-paths:` list (`[]` allowed; missing or bare
     empty key rejected), and matching `feature` (when present).

Exit codes:
  0  clean (or advisory on `--stage hunt`)
  2  contract violation (`PLAN stop <reason>`)
  64 usage error
"""
import json
import os
import re
import subprocess
import sys

LIST_KEYS = ("verify", "forbid-paths")
BAD, ODD, DUP = "\x00bad", "\x00odd", "\x00dup"
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


def _parse_value(val, is_list):
    if val[:1] == "#":
        return [] if is_list else ""
    if val[:1] in ("[", '"', "{"):
        try:
            v = json.loads(val)
        except Exception:
            v = None
        if isinstance(v, list):
            if all(isinstance(x, str) for x in v):
                return v
            return BAD
        if isinstance(v, str):
            return BAD if is_list else v
        return BAD if val[:1] == "[" else ODD
    if '"' in val or "'" in val:
        return ODD
    val = re.sub(r"\s+#.*$", "", val).strip()
    if val == "":
        return [] if is_list else ""
    if is_list:
        return BAD
    return val


def parse_plan_frontmatter(text):
    """Parse leading YAML frontmatter into (dict | None, body)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    m = re.match(r"---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not m:
        has_fence = bool(re.search(r"(?m)^[ \t]*---[ \t\r]*$", text))
        return ({ODD: True} if has_fence else None), text
    d, key, blocks, block_items = {}, None, set(), set()
    for raw in m.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = raw.strip()
        if item.startswith("- "):
            rest = item[2:].strip()
            if rest[:1] in ("#", ""):
                d[ODD] = True
                continue
            v = _parse_value(rest, False)
            if key in blocks and isinstance(v, str) and v not in (BAD, ODD):
                d[key] = d[key] + [v]
                block_items.add(key)
            else:
                d[ODD] = True
            continue
        if raw[:1] in " \t" or ":" not in raw:
            d[ODD] = True
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        if key in d:
            d[DUP] = key
            continue
        v = _parse_value(val, key in LIST_KEYS)
        if v == ODD:
            d[ODD] = True
        else:
            d[key] = v
        if (val == "" or val[:1] == "#") and key in LIST_KEYS:
            blocks.add(key)
        else:
            blocks.discard(key)
    for bkey in blocks:
        if bkey not in block_items:
            d[bkey] = BAD
    return d, m.group(2)


def extract_plan_meta_from_text(text, expected_feature=None):
    """Validate plan.md text and return (meta_dict | None, errors_list)."""
    if not text or not text.strip():
        return None, ["plan-empty"]
    d, _body = parse_plan_frontmatter(text)
    if d is None:
        return None, ["plan-no-frontmatter"]
    if DUP in d:
        return None, [f"plan-duplicate-key {d[DUP]}"]
    if ODD in d:
        return None, ["plan-frontmatter-unrecognized"]

    errors = []
    feat = d.get("feature", "")
    if expected_feature and isinstance(feat, str) and feat.strip() and feat.strip() != expected_feature:
        errors.append("plan-feature-mismatch")

    raw_verify = d.get("verify")
    if not isinstance(raw_verify, list):
        errors.append("plan-verify-invalid")
        verify = []
    else:
        if any(not isinstance(x, str) or not x.strip() for x in raw_verify):
            errors.append("plan-verify-empty-item")
        verify = [x.strip() for x in raw_verify if isinstance(x, str) and x.strip()]
        if not verify:
            errors.append("plan-verify-empty")

    if "forbid-paths" not in d:
        errors.append("plan-forbid-paths-missing")
        forbid = []
    else:
        raw_forbid = d.get("forbid-paths")
        if not isinstance(raw_forbid, list):
            errors.append("plan-forbid-paths-invalid")
            forbid = []
        else:
            if any(not isinstance(x, str) or not x.strip() for x in raw_forbid):
                errors.append("plan-forbid-paths-empty-item")
            forbid = [x.strip() for x in raw_forbid if isinstance(x, str) and x.strip()]

    planner_model = d.get("planner_model", "")
    if not isinstance(planner_model, str):
        planner_model = ""
    return {
        "feature": feat.strip() if isinstance(feat, str) else "",
        "verify": verify,
        "forbid_paths": forbid,
        "planner_model": planner_model.strip(),
    }, errors


def extract_plan_meta(plan_path, expected_feature=None):
    """Convenience file-path wrapper (prefer extract_plan_meta_from_text on committed git blobs)."""
    if not os.path.isfile(plan_path):
        return None, ["plan-missing"]
    text = open(plan_path, encoding="utf-8", errors="replace").read()
    return extract_plan_meta_from_text(text, expected_feature=expected_feature)


def _git_blob(repo, rel_path, commit="HEAD"):
    r = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"{commit}:{rel_path}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None, None
    blob_sha = r.stdout.strip()
    c = subprocess.run(["git", "-C", repo, "cat-file", "blob", blob_sha], capture_output=True)
    if c.returncode != 0:
        return blob_sha, None
    try:
        return blob_sha, c.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return blob_sha, "\x00non-utf8"


def _plan_is_dirty(repo, rel_path, commit="HEAD"):
    r = subprocess.run(["git", "-C", repo, "diff", "--quiet", commit, "--", rel_path], capture_output=True)
    return r.returncode != 0


def main(argv):
    opt = {"--repo": "", "--feature": "", "--stage": "", "--commit": "HEAD"}
    while argv:
        k = argv.pop(0)
        if k not in opt or not argv:
            sys.stderr.write("usage: check-plan.py --repo <path> --feature <slug> [--stage <s>] [--commit <rev>]\n")
            return 64
        opt[k] = argv.pop(0)
    if not opt["--repo"] or not opt["--feature"]:
        sys.stderr.write("usage: check-plan.py --repo <path> --feature <slug> [--stage <s>] [--commit <rev>]\n")
        return 64

    repo = os.path.abspath(opt["--repo"])
    feature = opt["--feature"]
    stage = opt["--stage"]
    commit = opt["--commit"] or "HEAD"
    rel_plan = f".pipeline/{feature}/plan.md"

    blob_sha, text = _git_blob(repo, rel_plan, commit=commit)
    errors = []
    meta = None
    if blob_sha is None:
        errors.append("plan-missing")
    elif text == "\x00non-utf8":
        errors.append("plan-non-utf8")
    elif text is None:
        errors.append("plan-blob-unreadable")
    else:
        if stage in ("plan-review", "impl", "review") and _plan_is_dirty(repo, rel_plan, commit=commit):
            errors.append("plan-dirty-local")
        if stage in ("impl", "review"):
            cur_blob, cur_text = _git_blob(repo, ".pipeline/current.json", commit=commit)
            if cur_blob is None or cur_text is None or cur_text == "\x00non-utf8":
                cur = {}
            else:
                try:
                    cur = json.loads(cur_text)
                except Exception:
                    cur = {}
            pin = cur.get("approved_plan_blob") if isinstance(cur, dict) else None
            if not isinstance(pin, str) or not SHA40_RE.fullmatch(pin):
                errors.append("plan-not-approved")
            elif pin != blob_sha:
                errors.append("plan-blob-mismatch")
        meta, meta_errs = extract_plan_meta_from_text(text, expected_feature=feature)
        errors.extend(meta_errs)

    if errors:
        for e in errors:
            print("PLAN stop %s feature=%s" % (e, feature))
        if stage == "hunt":
            print("PLAN advisory stage=hunt findings=%d (hunt repairs blocked features — not a STOP)" % len(errors))
            return 0
        return 2
    print("PLAN ok feature=%s verify=%d forbid=%d blob=%s" % (
        feature, len(meta["verify"]), len(meta["forbid_paths"]), blob_sha[:10]
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
