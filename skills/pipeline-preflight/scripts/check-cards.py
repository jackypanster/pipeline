#!/usr/bin/env python3
"""Deterministic executor for the card invariants CONTRACT already mandates. Read-only
(frontmatter reads + `git cat-file -e`); writes nothing; adds no rule — every check cites a
CONTRACT clause (see SKILL.md). Exit 0 = clean or advisory-only, 2 = violation, 64 = usage.
`--stage hunt` is advisory: findings print, the exit stays 0 (hunt REPAIRS cards).
"""
import glob, json, os, re, subprocess, sys

KEYS = ("status", "attempts", "verify", "spec-paths", "impl-paths", "spec-rev")
LISTS = ("verify", "spec-paths", "impl-paths")
STATUS = ("todo", "in-progress", "review", "done", "blocked")
BAD, ODD = "\x00unparsed", "\x00odd"


def unquote(s):
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'" else s


def parse_front(text):
    """Leading `---` fence only; scalars, JSON arrays and YAML block lists (all three are in
    real use). Unreadable ⇒ BAD: our limitation, not the card's (CONTRACT mandates the FIELDS,
    not a serialization), so the caller notes it and skips the checks that need it."""
    m = re.match(r"---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not m:
        return None, text
    d, key = {}, None
    for raw in m.group(1).split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw[:1] in " \t":
            item = raw.strip()
            if key is not None and item.startswith("- "):
                v = d.get(key)
                d[key] = (v if isinstance(v, list) else []) + [unquote(item[2:].strip())]
            else:
                d[ODD] = True          # a shape this parser does not understand
            continue
        if ":" not in raw:
            d[ODD] = True
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        if val.startswith("["):
            try:
                d[key] = [str(x) for x in json.loads(val)]
            except Exception:
                d[key] = BAD
        else:   # a comma-separated scalar is a list we cannot honestly split ⇒ BAD, never a path
            d[key] = [] if val == "" else (BAD if key in LISTS and "," in val else unquote(val))
    return d, m.group(2)


def check(root, feature):   # -> (lines printed verbatim by the caller, violation count)
    out = []
    def stop(msg):
        out.append("CARDS stop " + msg)
    cards = sorted(glob.glob(os.path.join(root, ".pipeline", feature, "tasks", "*.md")))
    if not cards:
        return [], 0
    try:
        cur = json.load(open(os.path.join(root, ".pipeline", "current.json")))
    except Exception:
        cur = {}
    fv = None
    if isinstance(cur, dict) and cur.get("feature") == feature:
        fv = cur.get("full-verify")
        if not fv:
            stop("full-verify-missing feature=%s" % feature)
    else:
        out.append("CARDS note full-verify-unknown feature=%s" % feature)
    revs = set()
    for path in cards:
        tag = "card=%s/%s" % (feature, os.path.basename(path)[:-3])
        d, body = parse_front(open(path, encoding="utf-8", errors="replace").read())
        if d is None:
            stop("card-no-frontmatter " + tag)
            continue
        if ODD in d:   # never STOP on a shape we cannot read — note it, judge nothing
            out.append("CARDS note card-frontmatter-unrecognized " + tag)
            continue
        miss = [k for k in KEYS if k not in d or d[k] == ""]
        if miss:
            stop("card-missing-field %s %s" % (",".join(miss), tag))
            continue
        if d["status"] not in STATUS:
            stop("card-bad-status %s %s" % (d["status"], tag))
        if not re.match(r"^[0-9]+$", str(d["attempts"])):
            stop("card-bad-attempts %s %s" % (d["attempts"], tag))
        vals = {}
        for k in LISTS:
            v = d[k]
            if v == BAD:
                out.append("CARDS note card-list-unparsed %s %s" % (k, tag))
                v = []                 # unreadable ⇒ the checks needing it are SKIPPED, never a STOP
            elif not v:
                stop("card-%s-empty %s" % (k, tag))
            vals[k] = [v] if isinstance(v, str) else (v if isinstance(v, list) else [])
        over = sorted(set(vals["spec-paths"]) & set(vals["impl-paths"]))
        if over:
            stop("card-spec-impl-overlap %s %s" % (",".join(over), tag))
        for p in vals["spec-paths"]:
            if re.search(r"[*?\[]", p):
                out.append("CARDS note card-spec-path-glob %s %s" % (p, tag))
            elif not os.path.exists(os.path.join(root, p)):
                stop("card-spec-path-absent %s %s" % (p, tag))
        if fv and vals["verify"] == [str(x) for x in fv]:
            stop("card-verify-full-suite " + tag)
        rev = str(d["spec-rev"])
        revs.add(rev)
        if subprocess.run(["git", "-C", root, "cat-file", "-e", rev + "^{commit}"],
                          capture_output=True).returncode:
            stop("card-spec-rev-unresolvable %s %s" % (rev, tag))
        if d["status"] == "review" and "## Assumptions" not in body:
            out.append("CARDS note assumptions-missing " + tag)
    if len(revs) > 1:
        stop("feature-spec-rev-not-shared %s feature=%s" % (",".join(sorted(revs)), feature))
    bad = sum(1 for l in out if l.startswith("CARDS stop "))
    if not bad:
        out.append("CARDS ok feature=%s n=%d spec-rev=%s"
                   % (feature, len(cards), sorted(revs)[0][:7] if revs else "-"))
    return out, bad


USAGE = "usage: check-cards.py --repo <path> --feature <slug> [--stage <s>]\n"


def main(argv):
    opt = {"--repo": "", "--feature": "", "--stage": ""}
    while argv:
        k = argv.pop(0)
        if k not in opt or not argv:
            sys.stderr.write(USAGE)
            return 64
        opt[k] = argv.pop(0)
    if not opt["--repo"] or not opt["--feature"]:
        sys.stderr.write(USAGE)
        return 64
    lines, bad = check(os.path.abspath(opt["--repo"]), opt["--feature"])
    for l in lines:
        print(l)
    if bad and opt["--stage"] == "hunt":
        print("CARDS advisory stage=hunt findings=%d (hunt repairs cards — not a STOP)" % bad)
        return 0
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
