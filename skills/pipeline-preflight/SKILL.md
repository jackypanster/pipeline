---
name: pipeline-preflight
description: "Helper script — NOT a pipeline stage, NOT a roles.yaml slot, never invoke by hand: the six stage skills run its scripts/preflight.sh as their step 0 (CONTRACT §shim loop → deterministic executor). It executes shim steps 1/3/4 (pull · current.json · slot resolve + install check) and the coordinated-mode pre-write stale-dispatch guard, printing one greppable line per check. Args: none — you are not the caller."
---

# pipeline-preflight

```bash
bash <the stage skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage <prd|arch|task|impl|review|hunt> \
     [--repo <path>] [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]
```

The five `k=v` envelope fields are all-or-nothing — pass them exactly as the coordinator typed them, or none at all (human-relay ⇒ the guard does not apply).

| exit | prints | the calling stage then |
|---|---|---|
| 0 | `PREFLIGHT OK stage=<s>` | steps 1/3/4 (+ the guard) are DONE — take the values from the printed lines |
| 2 | `PREFLIGHT STOP <reason>`, incl. `STALE_DISPATCH <field> observed=… expected=…` | STOPs and reports that reason; zero writes |
| 3 | `PREFLIGHT UNVERIFIED <reason>` | everything else passed — verifies the slot skill itself, STOPs if not installed |
| 64 | usage error on stderr | fixes the invocation |

Output grammar (stdout, one line per check): `PULL ok head=<sha>` · `ENV file=<n> keys=<K1,K2>` / `ENV none` ·
`CURRENT ok repo=… branch=… feature=… stage=…` / `CURRENT absent (prd creates it)` · `SLOT <stage>=<n1>[,<n2>]` ·
`INSTALLED <n> path=<dir>/<n>` / `INSTALLED <n> UNVERIFIED searched=…` · `GUARD ok seq=<n> commit=<sha>` / `GUARD n/a (human-relay)`.

**Fallback (mandatory):** script absent on this install, or exit 3 ⇒ execute that part as the prose steps are
written. The prose IS the spec; this script is only its deterministic executor, and rollback = delete this dir.

**Guarantees:** zero file writes anywhere — the only checkout mutations are `git pull --rebase` (CONTRACT step 1) and the guard's `git fetch`; dotenv reporting is **file + KEY names only, never a value**, and nothing is exported (loading stays the stage's own step 2). Deps: `git`, `python3`, coreutils. Tests: `bash scripts/preflight-test.sh` (14 cases, hermetic `$HOME` + temp remote/clone fixtures).
