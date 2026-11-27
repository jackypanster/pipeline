#!/usr/bin/env python3
"""Read-only validator for .pipeline/<feature>/plan.md frontmatter (v2 3-Agent 4-Step).

Checks:
  1. .pipeline/<feature>/plan.md exists and is non-empty.
  2. Leading `--- ... ---` YAML frontmatter is present, parseable, and has no duplicate keys.
  3. `verify` is an explicit non-empty list of non-empty command strings (scalars rejected).
  4. `forbid-paths` is present as an explicit list of non-empty path strings (empty list `[]` allowed;
     missing key or bare scalar rejected).
  5. If `feature` is present, it matches `--feature`.

Exit codes:
  0  clean (or advisory on `--stage hunt`)
  2  contract violation (`PLAN stop <reason>`)
  64 usage error
"""
import json
import os
import re
import sys

LIST_KEYS = ("verify", "forbid-paths")
BAD, ODD, DUP = "\x00bad", "\x00odd", "\x00dup"


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


def extract_plan_meta(plan_path, expected_feature=None):
    """Return (meta_dict, errors_list) where meta_dict has verify[], forbid_paths[], planner_model, feature."""
    if not os.path.isfile(plan_path):
        return None, ["plan-missing"]
    text = open(plan_path, encoding="utf-8", errors="replace").read()
    if not text.strip():
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


def main(argv):
    opt = {"--repo": "", "--feature": "", "--stage": ""}
    while argv:
        k = argv.pop(0)
        if k not in opt or not argv:
            sys.stderr.write("usage: check-plan.py --repo <path> --feature <slug> [--stage <s>]\n")
            return 64
        opt[k] = argv.pop(0)
    if not opt["--repo"] or not opt["--feature"]:
        sys.stderr.write("usage: check-plan.py --repo <path> --feature <slug> [--stage <s>]\n")
        return 64
    plan_path = os.path.join(os.path.abspath(opt["--repo"]), ".pipeline", opt["--feature"], "plan.md")
    meta, errors = extract_plan_meta(plan_path, expected_feature=opt["--feature"])
    if errors:
        for e in errors:
            print("PLAN stop %s feature=%s" % (e, opt["--feature"]))
        if opt["--stage"] == "hunt":
            print("PLAN advisory stage=hunt findings=%d (hunt repairs blocked features — not a STOP)" % len(errors))
            return 0
        return 2
    print("PLAN ok feature=%s verify=%d forbid=%d" % (
        opt["--feature"], len(meta["verify"]), len(meta["forbid_paths"])
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
