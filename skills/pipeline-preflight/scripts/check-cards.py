#!/usr/bin/env python3
"""Deterministic executor for the card invariants CONTRACT already mandates. Read-only
(frontmatter reads + `git rev-parse --verify`); writes nothing; adds no rule — every check cites a
CONTRACT clause (see SKILL.md). Exit 0 = clean or advisory-only, 2 = violation, 64 = usage.
`--stage hunt` is advisory: findings print, the exit stays 0 (hunt REPAIRS cards).
"""
import glob, json, os, re, subprocess, sys

KEYS = ("status", "attempts", "verify", "spec-paths", "impl-paths", "spec-rev")
LISTS = ("verify", "spec-paths", "impl-paths")
STATUS = ("todo", "in-progress", "review", "done", "blocked")
BAD, ODD = "\x00unparsed", "\x00odd"


def parse_value(val, listy):
    """One value, in three ordered steps — no quote/comment grammar is re-implemented:
    1. JSON, which is exact: a `#` inside a JSON string is data, and JSON has no comments;
    2. else, ONLY if the text carries no quote at all, a bare scalar with a whitespace-preceded
       ` #…` tail dropped (that much is unambiguous);
    3. else — a quote we could not parse as JSON — ODD: we do not guess (fail-open)."""
    try:
        v = json.loads(val)
        return [str(x) for x in v] if isinstance(v, list) else str(v)
    except Exception:
        pass
    if '"' in val or "'" in val:
        return ODD
    val = re.sub(r"\s+#.*$", "", val).strip()
    if val == "":
        return []
    #                      a bracket that is not JSON, or a comma-separated list we cannot split
    return BAD if val.startswith("[") or (listy and "," in val) else val


def parse_front(text):
    """Leading `---` fence only; scalars, JSON arrays, YAML block lists (indented or not) and
    trailing ` #` comments outside quotes — every form the live corpus uses. Anything else ⇒ ODD
    or BAD: our limitation, not the card's (CONTRACT mandates the FIELDS, not a serialization),
    so the caller notes it and skips the checks that need it. Returns (None, …) only when there
    is no `---` fence at all; a fence we cannot match (a line above it) is ODD, not absent
    (CRLF needs nothing: python reads these files in text mode, which normalises it)."""
    m = re.match(r"---\n(.*?)\n---\n?(.*)\Z", text, re.S)
    if not m:   # a `---` line we could not match is ODD; NO fence line at all is a missing artifact
        return ({ODD: True} if re.search(r"(?m)^[ \t]*---[ \t\r]*$", text) else None), text
    d, key, blocks = {}, None, set()   # `blocks` = keys DECLARED empty, the only ones a `- item`
    for raw in m.group(1).split("\n"):  #            may extend (an inline array must not be)
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        item = raw.strip()
        if item.startswith("- "):          # block-list item, indented or not …
            v = parse_value(item[2:].strip(), False)
            if key in blocks and isinstance(v, str) and v not in (BAD, ODD):
                d[key] = d[key] + [v]
            else:                          # after a scalar / an inline array, or unreadable:
                d[ODD] = True              # a shape we cannot read — never a silent replacement
            continue
        if raw[:1] in " \t" or ":" not in raw:
            d[ODD] = True              # a shape this parser does not understand
            continue
        key, _, val = raw.partition(":")
        key, val = key.strip(), val.strip()
        v = parse_value(val, key in LISTS)
        if v == ODD:
            d[ODD] = True
        else:
            d[key] = v
        if val == "":
            blocks.add(key)
        else:
            blocks.discard(key)
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
    fv = cur.get("full-verify") if isinstance(cur, dict) and cur.get("feature") == feature else None
    if not isinstance(fv, list) or not fv:
        fv = None                      # CONTRACT L72 marks `full-verify?` OPTIONAL ⇒ note, never a STOP
        out.append("CARDS note full-verify-unknown feature=%s" % feature)
    revs, skipped = set(), set()
    for path in cards:
        tag = "card=%s/%s" % (feature, os.path.basename(path)[:-3])
        d, body = parse_front(open(path, encoding="utf-8", errors="replace").read())
        if d is None:
            stop("card-no-frontmatter " + tag)
            continue
        if ODD in d:   # never STOP on a shape we cannot read — note it, judge nothing
            out.append("CARDS note card-frontmatter-unrecognized " + tag)
            skipped.add(tag)
            continue
        miss = [k for k in KEYS if k not in d or d[k] == "" or (k not in LISTS and d[k] == [])]
        if miss:
            stop("card-missing-field %s %s" % (",".join(miss), tag))
            continue
        if [k for k in KEYS if k not in LISTS and d[k] == BAD]:
            out.append("CARDS note card-frontmatter-unrecognized " + tag)   # e.g. `spec-rev: [x]`
            skipped.add(tag)
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
                skipped.add(tag)
                v = []                 # unreadable ⇒ the checks needing it are SKIPPED, never a STOP
            else:                      # an empty ITEM is not a value: `verify: [""]` is empty
                v = [x for x in ([v] if isinstance(v, str) else (v if isinstance(v, list) else [])) if x]
                # `impl-paths: []` is LEGAL — impl's write-set is impl-paths + src/** (CONTRACT L160)
                if not v and k != "impl-paths":
                    stop("card-%s-empty %s" % (k, tag))
            vals[k] = v
        over = sorted(set(vals["spec-paths"]) & set(vals["impl-paths"]))
        if over:
            stop("card-spec-impl-overlap %s %s" % (",".join(over), tag))
        for p in vals["spec-paths"]:
            if re.search(r"[*?\[]", p):
                out.append("CARDS note card-spec-path-glob %s %s" % (p, tag))
                skipped.add(tag)
            elif not os.path.exists(os.path.join(root, p)):
                stop("card-spec-path-absent %s %s" % (p, tag))
        if fv and vals["verify"] == [str(x) for x in fv]:
            stop("card-verify-full-suite " + tag)
        rev = str(d["spec-rev"])       # resolve to the FULL sha: a 7-char prefix and its own
        full = ""                      # 40-char form are the same baseline (never `not-shared`)
        if re.match(r"^[0-9a-fA-F]{4,40}$", rev):
            r = subprocess.run(["git", "-C", root, "rev-parse", "--verify", "--quiet",
                                rev + "^{commit}"], capture_output=True)
            full = r.stdout.decode("utf-8", "replace").strip() if not r.returncode else ""
        if full:
            revs.add(full)
        else:                          # not hex, ambiguous, or not a commit in THIS repo
            stop("card-spec-rev-unresolvable %s %s" % (rev, tag))
        if d["status"] == "review" and "## Assumptions" not in body:
            out.append("CARDS note assumptions-missing " + tag)
    if len(revs) > 1:
        stop("feature-spec-rev-not-shared %s feature=%s"
             % (",".join(r[:7] for r in sorted(revs)), feature))
    bad = sum(1 for l in out if l.startswith("CARDS stop "))
    if not bad:                        # never claim `ok` over a check we did not run
        if skipped or fv is None:      # fv unknown ⇒ check 4 was skipped for EVERY card
            out.append("CARDS unverified feature=%s n=%d unchecked=%d"
                       % (feature, len(cards), len(cards) if fv is None else len(skipped)))
        else:
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
