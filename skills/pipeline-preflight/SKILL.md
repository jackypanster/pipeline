---
name: pipeline-preflight
description: "Helper script — NOT a pipeline stage, NOT a roles.yaml slot, never invoke by hand: the six stage skills run its scripts/preflight.sh as their step 0 (CONTRACT §shim loop → deterministic executor). It executes shim steps 1/3/4 (pull · current.json · slot resolve + install check) and the coordinated-mode pre-write stale-dispatch guard, printing one greppable line per check. Args: none — you are not the caller."
---

# pipeline-preflight

```bash
bash <the stage skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage <prd|arch|task|impl|review|hunt> \
     [--repo <path>] [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]
```

The five `k=v` envelope fields are all-or-nothing — pass them exactly as the coordinator typed them, or none at all (human-relay ⇒ the guard does not apply). Presence is decided by the ARGUMENT, not its value: `feature=` counts as passed-and-empty, i.e. an incomplete envelope (STOP).

| exit | prints | the calling stage then |
|---|---|---|
| 0 | `PREFLIGHT OK stage=<s>` | steps 1/3/4 (+ the guard) are DONE — take the values from the printed lines |
| 2 | `PREFLIGHT STOP <reason>`, incl. `STALE_DISPATCH <field> observed=… expected=…` | STOPs and reports that reason; zero writes |
| 3 | `PREFLIGHT UNVERIFIED <check>[,<check>]` | everything ELSE passed — **does the named check(s) itself** and STOPs on failure |
| 4 | `PREFLIGHT SKIPPED <reason>` (e.g. `python3-missing`) | nothing ran and nothing was mutated — executes steps 1–4 as written, same as if the script were absent |
| 64 | usage error on stderr | fixes the invocation |

The exit-3 checks: **`install-check`** — verify the slot skill is installed on this runtime, STOP if not; **`remote-identity`** — confirm the repo's remote really is the one `current.json.repo` names.

Output grammar (stdout, one line per check): `PULL ok head=<sha>` / `PULL fail` · `ENV file=<n> keys=<N>` / `ENV none` ·
`CURRENT ok repo=… branch=… feature=… stage=…[ pr=…]` / `CURRENT absent (prd creates it)` · `SLOT <stage>=<n1>[,<n2>]` ·
`INSTALLED <n> path=<dir>/<n>` (verified) / `INSTALLED <n> found=<dir>/<n> UNVERIFIED (…)` / `INSTALLED <n> UNVERIFIED searched=…` ·
`FETCH fail <remote>/<branch>` · `REMOTE unverified observed=… current.json.repo=…` ·
`GUARD ok seq=<n> commit=<sha>[ remote=<host/owner/name>]` / `GUARD n/a (human-relay)`.

**`PIPELINE_SKILL_DIRS`** (colon-separated, per runtime) declares which dirs THIS runtime actually loads skills from. A hit there is a **verified** install (`path=`). Without it the script still searches the default dirs, but a hit is evidence only (`found=… UNVERIFIED`) — a readable `SKILL.md` on disk never proves the running agent loads it — so the exit is 3 and the stage verifies the slot itself.

**Fallback (mandatory):** script absent on this install, or exit 4 ⇒ execute steps 1–4 as the prose is
written; exit 3 ⇒ execute the named check(s) that way. The prose IS the spec; this script is only its
deterministic executor, and rollback = delete this dir.

**Guarantees:** zero file writes anywhere — the only checkout mutations are `git pull --rebase` (CONTRACT step 1) and the guard's `git fetch`, and a failure of either is a STOP, never a fall-back to a cached ref; dotenv reporting is **the file and a key COUNT — never a name, never a value** (a multi-line value's continuation line can look like a key, so names are unsafe to print at all), and nothing is exported (loading stays the stage's own step 2). Deps: `git`, `python3`, coreutils. Tests: `bash scripts/preflight-test.sh` (26 cases, hermetic `$HOME` + temp remote/clone fixtures; also run it with `/bin/bash` for the bash-3.2 path).
