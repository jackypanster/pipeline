---
name: pipeline-preflight
description: "Helper script — NOT a pipeline stage, NOT a roles.yaml slot, never invoke by hand: the six stage skills run its scripts/preflight.sh as their step 0 (CONTRACT §shim loop → deterministic executor). It executes shim steps 1/3/4 (pull · current.json · slot resolve + install check), the coordinated-mode pre-write stale-dispatch guard, and a read-only card-invariant check on impl/review/hunt entry, printing one greppable line per check. Args: none — you are not the caller."
---

# pipeline-preflight

```bash
bash <the stage skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage <prd|arch|task|impl|review|hunt> \
     [--repo <path>] [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]
```

The five `k=v` envelope fields are all-or-nothing — pass them exactly as the coordinator typed them, or none at all (human-relay ⇒ the guard does not apply). Presence is decided by the ARGUMENT, not its value: `feature=` counts as passed-and-empty, i.e. an incomplete envelope (STOP).

| exit | prints | the calling stage then |
|---|---|---|
| 0 | `PREFLIGHT OK stage=<s>` | steps 1/3/4 (+ the guard, + any card check) are DONE — take the values from the printed lines |
| 2 | `PREFLIGHT STOP <reason>`, incl. `STALE_DISPATCH <field> observed=… expected=…` | STOPs and reports that reason; zero writes |
| 3 | `PREFLIGHT UNVERIFIED <check>[,<check>]` | everything ELSE passed — **does the named check(s) itself** and STOPs on failure |
| 4 | `PREFLIGHT SKIPPED <reason>` (e.g. `python3-missing`) | nothing ran and nothing was mutated — executes steps 1–4 as written, same as if the script were absent |
| 64 | usage error on stderr | fixes the invocation |

The exit-3 checks: **`install-check`** — verify the slot skill is installed on this runtime, STOP if not; **`remote-identity`** — confirm the repo's remote really is the one `current.json.repo` names.

**Remote identity** = `git ls-remote --get-url <remote>` byte-equal to `current.json.repo` (whose fields are never trimmed — surrounding whitespace is `current.json-invalid-field`); anything else is UNVERIFIED (Git's URL grammar is not re-implemented, and this check never STOPs).

**Journal handoff markers are whole lines** (CONTRACT §Run journal): inside the tail entry, the first line that IS `--- handoff ---`, whose next non-empty line must BE `>>> NEXT`; the command is the next non-empty line after that. Anything else ⇒ `STALE_DISPATCH next observed=<absent-handoff-in-tail>`. Prose that merely mentions `>>> NEXT` is prose. **Slot names are directory names**, `^[A-Za-z0-9][A-Za-z0-9._-]*$` — a path such as `../outside` is `PREFLIGHT STOP slot-invalid-name`, never an install found outside the declared dirs.

Output grammar (stdout, one line per check): `PULL ok head=<sha>` / `PULL fail` · `ENV file=<n> keys=<N>` / `ENV none` ·
`CURRENT ok repo=… branch=… feature=… stage=…[ pr=…]` / `CURRENT absent (prd creates it)` · `SLOT <stage>=<n1>[,<n2>]` ·
`UPSTREAM ok head=<sha>[ cached]` (installed == pipeline main, or ahead of it) / `UPSTREAM newer head=<sha> installed=<sha> run=pipeline-update[ cached]` / `UPSTREAM unverified <no-install-stamp|network>` ·
`INSTALLED <n> path=<dir>/<n>` (verified) / `INSTALLED <n> found=<dir>/<n> UNVERIFIED (…)` / `INSTALLED <n> UNVERIFIED searched=…` ·
`FETCH fail <remote>/<branch>` · `REMOTE unverified observed=… current.json.repo=…` ·
`GUARD ok seq=<n> commit=<sha>[ remote=<url>]` / `GUARD n/a (human-relay)` ·
`CARDS ok feature=<f> n=<N> spec-rev=<sha7>` / `CARDS unverified feature=<f> n=<N> unchecked=<k>` ·
`CARDS note <assumptions-missing|card-frontmatter-unrecognized|card-list-unparsed <key>|card-spec-path-glob <path>> card=<f>/<id>` / `CARDS note full-verify-unknown feature=<f>` ·
`CARDS stop <card-no-frontmatter|card-missing-field <keys>|card-bad-status <v>|card-bad-attempts <v>|card-verify-empty|card-spec-paths-empty|card-spec-impl-overlap <paths>|card-spec-path-absent <path>|card-verify-full-suite|card-spec-rev-unresolvable <rev>> card=<f>/<id>` / `CARDS stop feature-spec-rev-not-shared <sha7,…> feature=<f>` ·
`CARDS advisory stage=hunt findings=<n> (hunt repairs cards — not a STOP)` / `CARDS unchecked rc=<n>`.

**`PIPELINE_SKILL_DIRS`** (colon-separated, per runtime) declares which dirs THIS runtime actually loads skills from. A hit there is a **verified** install (`path=`). Without it the script still searches the default dirs, but a hit is evidence only (`found=… UNVERIFIED`) — a readable `SKILL.md` on disk never proves the running agent loads it — so the exit is 3 and the stage verifies the slot itself.

**`UPSTREAM …`** is advisory, printed after the guard, just before the final verdict (so a STOP never writes its cache): at most one `git ls-remote` of the pipeline repo per 24h, throttled through `${XDG_CACHE_HOME:-$HOME/.cache}/pipeline/upstream-head` (a failed fetch is throttled too), with `PIPELINE_UPSTREAM_URL` overriding the URL (tests/mirrors). It compares that sha against the installed version — the clone's HEAD when the skills live in a pipeline clone, else the install stamp `<skills-dir>/.pipeline-update.head` written by `pipeline-update` (no stamp ⇒ `no-install-stamp`). `UPSTREAM newer` ⇒ the stage adds one line to its final report telling the operator to run `pipeline-update` between stages. It never changes the exit code and never STOPs.

**`CARDS …`** = the card-invariant check (`scripts/check-cards.py`, read-only, python3 stdlib): on
`impl`/`review`/`hunt` entry, when `.pipeline/<current feature>/tasks/*.md` exists, it executes the card
rules CONTRACT already states — the six frontmatter fields present · `status` enum · `attempts` an
integer · `spec-paths ∩ impl-paths = ∅` with every `spec-paths` entry present in the checkout ·
non-empty `verify` ≠ `current.json.full-verify` · ONE `spec-rev`, resolved with `git rev-parse` and
shared (as a full sha) by every card of the feature. It adds no rule and reads no frozen one
(`attempts >= 3 ⇒ blocked` and the freeze diff stay with the state machine and `pipeline-review`);
`impl-paths: []` is legal (impl may write `src/**`) and `full-verify` is optional (absent or not a list
⇒ `CARDS note full-verify-unknown` and that check is skipped for every card of the feature). A
`CARDS stop` line becomes `PREFLIGHT STOP <that same reason>`; a `CARDS note` never changes the exit —
what it cannot read it does not judge. A card carrying any frontmatter shape the parser does not
recognise (a line that is neither blank, nor `#`, nor `key: value`, nor a `- item` entry; a `- item`
under a key already holding a scalar; anything above the `---` fence) is reported
`card-frontmatter-unrecognized` with **all** its STOP checks suppressed — a parser-limitation fail-open,
not an anti-tamper control. `card-no-frontmatter` is reserved for a file with no `---` fence line at all
(CRLF needs no handling: the cards are read in text mode, which normalises it). Whenever any check was
skipped — a suppressed card, or a feature-level one — the verdict is `CARDS unverified …
unchecked=<k>`, never `CARDS ok`. **`hunt` is advisory too**
(`CARDS advisory stage=hunt`): hunt REPAIRS cards, so a card defect must never gate its own entry. Any
other exit prints `CARDS unchecked rc=<n>` and the run continues — `pipeline-task` 6b / `pipeline-review`
prose is the spec; this executes it.

**Fallback (mandatory):** script absent on this install, or exit 4 ⇒ execute steps 1–4 as the prose is
written; exit 3 ⇒ execute the named check(s) that way. The prose IS the spec; this script is only its
deterministic executor, and rollback = delete this dir.

**Guarantees:** no file writes inside a repo or skill dir — the only checkout mutations are `git pull --rebase` (CONTRACT step 1) and the guard's `git fetch`, and a failure of either is a STOP, never a fall-back to a cached ref; the one write anywhere else is the once-a-day upstream throttle stamp above; dotenv reporting is **the file and a key COUNT — never a name, never a value** (a multi-line value's continuation line can look like a key, so names are unsafe to print at all), and nothing is exported (loading stays the stage's own step 2). The card check likewise only reads files and runs `git rev-parse --verify`. Deps: `git`, `python3`, coreutils. Tests: `bash scripts/preflight-test.sh` (71 cases, hermetic `$HOME` + temp remote/clone fixtures; also run it with `/bin/bash` for the bash-3.2 path).
