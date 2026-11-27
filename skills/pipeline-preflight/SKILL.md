---
name: pipeline-preflight
description: "Helper script — NOT a pipeline stage, NOT a roles.yaml slot, never invoke by hand: the stage skills run scripts/preflight.sh as step 0 (CONTRACT §shim loop). Executes pull, dotenv count, current.json, roles.yaml slot + install check, coordinated stale-dispatch guard, and read-only plan.md check. Args: none — you are not the caller."
---

# pipeline-preflight

```bash
bash <the stage skill's base dir>/../pipeline-preflight/scripts/preflight.sh \
     --stage <plan|plan-review|impl|review|hunt> \
     [--repo <path>] [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]
```

The five `k=v` envelope fields are all-or-nothing (human-relay ⇒ omit all five). An argument
present-but-empty (`feature=`) is an incomplete envelope (STOP).

| exit | prints | the calling stage then |
|---|---|---|
| `0` | `PREFLIGHT OK stage=<s>` | steps 1/3/4 (+ guard + plan check) DONE — use its printed lines |
| `2` | `PREFLIGHT STOP <reason>` (incl. `STALE_DISPATCH …`, `python3-missing`) | STOPs and reports that reason; zero writes |
| `3` | `PREFLIGHT UNVERIFIED <check>[,<check>]` | everything ELSE passed — does ONLY the named check(s) (`install-check`, `remote-identity`) and STOPs on failure |
| `64` | usage error on stderr | fixes the invocation |

Output grammar (stdout, one line per check):
- `PULL ok head=<sha>` / `PULL fail`
- `ENV file=<n> keys=<N>` / `ENV none` (count only — never names or values)
- `CURRENT ok repo=… branch=… feature=… stage=…[ pr=…]` / `CURRENT absent (plan creates it)`
- `SLOT <stage>=<n1>[,<n2>]` (charset `^[A-Za-z0-9][A-Za-z0-9._-]*$`, no `<placeholder>`)
- `INSTALLED <n> path=<dir>/<n>` (verified via `PIPELINE_SKILL_DIRS`) / `INSTALLED <n> found=… UNVERIFIED (…)` / `INSTALLED <n> UNVERIFIED searched=…`
- `GUARD ok seq=<n> commit=<sha>[ remote=<url>]` / `GUARD n/a (human-relay)`
- `PLAN ok feature=<f> verify=<N> forbid=<M>` / `PLAN stop <reason> feature=<f>` / `PLAN advisory stage=hunt …` / `PLAN unchecked rc=<n>`
- `UPSTREAM ok head=<sha>[ cached]` / `UPSTREAM newer head=<sha> installed=<sha> run=pipeline-update[ cached]` / `UPSTREAM unverified <not-a-clone|network>`

**`PLAN …`** (`scripts/check-plan.py`, read-only, python3 stdlib): on `plan-review`/`impl`/`review`/`hunt`
entry, verifies `.pipeline/<feature>/plan.md` exists, carries YAML frontmatter with no duplicate
keys, has an explicit non-empty `verify:` command list, and has an explicit `forbid-paths:` list
(`[]` allowed). `hunt` is advisory (never STOPs). On mandatory stages, missing checker, `PLAN stop`,
or checker error all exit `2` (`PREFLIGHT STOP`).

**Guarantees:** zero writes inside target repos or skill dirs (except `git pull --rebase` and the
guard's `git fetch`, plus the post-guard 24h `upstream-head` cache stamp on exit 0/3 only).
Deps: `git`, `python3`, coreutils. Self-test: `bash scripts/preflight-test.sh` (also test with
`/bin/bash` for macOS bash-3.2 compatibility).
