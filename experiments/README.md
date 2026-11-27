# `experiments/plite.py` — Local 2-Agent × 3-Step Pilot

Minimal, reversible companion experiment to `jackypanster/pipeline`. Leaves `CONTRACT.md`, `DESIGN.md`, and all 11 `pipeline-*` skills untouched while testing whether local headless subprocess orchestration + 2-model separation delivers lower human interruption without sacrificing quality on **low-risk tasks**.

## Admission Boundary (87.199)

`--risk` is **required** (never defaults to `low`):

- **Eligible (`--risk low`)**: single-card bugfixes, small scoped features, doc/lint/chore changes with concrete, immutable `--acceptance` criteria.
- **Not eligible (`--risk high`)**: trading/money/auth/production write-paths, shared schema/interface changes, multi-card features (`≥2` independent cards), cross-repo changes, or unclear acceptance criteria. `plite.py` refuses `--risk high` immediately (before run creation; admission rejections are excluded from pilot telemetry by design) and escalates to full `pipeline` (`prd → arch → task → impl → review` with frozen red tests).

## Usage

### 1. AGY implements, Codex GPT-6.2 reviews
```bash
python3 experiments/plite.py run \
  --repo /path/to/repo \
  --risk low \
  --task "Add --json output flag to status command" \
  --acceptance "status --json outputs valid JSON with keys ok,version; existing tests pass" \
  --verify "pytest -q" \
  --worker agy --worker-model gemini-4-neon \
  --reviewer codex --reviewer-model gpt-6.2-sol
```

### 2. Claude Code implements, Codex GPT-6.2 reviews
```bash
python3 experiments/plite.py run \
  --repo /path/to/repo \
  --risk low \
  --task-file /path/to/brief.md \
  --acceptance "all unit tests pass; no breaking CLI flags" \
  --verify "cargo test" \
  --worker claude \
  --reviewer codex --reviewer-model gpt-6.2-sol
```

### 3. Phase-aware resume after quota exhaustion (swapping worker or reviewer)
```bash
python3 experiments/plite.py resume \
  --run-id <run_id> \
  --worker claude \
  --reviewer codex --reviewer-model gpt-6.2-sol
```
- If the run failed during **review** (e.g., reviewer hit quota), `resume` skips re-running the worker and retries review directly on the already-verified `candidate_sha`.
- `contributing_families` is tracked cumulatively across retries/resumes so no model family that contributed code can later review that candidate.
- Runs terminated with `integrity_violation` are quarantined and **cannot** be resumed.

### 4. Post-merge backfill & pilot scorecard
```bash
python3 experiments/plite.py mark \
  --run-id <run_id> \
  --merged-sha <sha> \
  --regression none_observed \
  --human-time 45

python3 experiments/plite.py summary
```

## Built-in Integrity Guards (Hardened per Codex GPT-6.2 Design & Code Reviews)

1. **Model-family independence**: resolves family from explicit flag, model arg, or CLI config (`~/.codex/config.toml`, `~/.claude/settings.json`); rejects explicit family contradicting a recognizable model name; passes resolved `--model` to `claude`, `codex`, and `agy`; fails closed on unqualified `agy`; blocks any reviewer family in `contributing_families`.
2. **Immutable SHA & branch binding**: pins `base_sha` and verifies branch name + ancestry (`git merge-base --is-ancestor`); requires clean worktree before and after external `--verify` (Python bytecode writes disabled via `PYTHONDONTWRITEBYTECODE=1`; target repos should keep test/build caches in `.gitignore`); verifies `base_ref` and worktree are untouched by reviewer.
3. **Intermediate test & config protection**: uses NUL-delimited `git diff -z`; deleting existing test files or editing verification/build configs (Python/JS/TS/Rust/Go/CI globs + `--protected-glob`) escalates fail-closed unless `--allow-protected-edit` is explicitly passed; editing existing test files is flagged to the reviewer for mandatory assertion-weakening review.
4. **Failure-closed verdict parsing**: requires **exact count = 1** `plite-verdict` fenced JSON block matching exact `candidate_sha`, valid tags/severities, non-empty `file`, and consistent blocking-finding semantics.
5. **Durable state, full-transaction locking & no survivorship bias**: exclusive `fcntl.flock` wraps every read-modify-write transaction (`run`, `resume`, `mark`); atomic `manifest.json` updates on every phase transition; every attempted eligible run—including worktree init failures, unexpected exceptions, crashes, quota errors, blocks, escalations, and integrity violations—writes a terminal record to `~/.local/state/plite/runs.jsonl`.
6. **Human-controlled integration**: Phase 1 never auto-merges `main`.

## Pilot Decision Gate

Evaluate after **10–15 eligible low-risk runs** (compared against comparable low-risk full-pipeline runs, not all 97 historical features blindly). Enforced mechanically by `plite.py summary`:

| Decision | Enforced Criteria |
|---|---|
| **KILL — stop pilot** | Any `integrity_violation` (`> 0`) |
| **COLLECTING** | Fewer than 10 eligible runs |
| **GO — expand pilot** | `≥ 10` eligible runs, **autonomous** convergence (approved with 0 rescue interventions) `≥ 80%`, median interventions `≤ 1`, 100% of approved runs have regression observations recorded, confirmed regression rate `≤ 10%`, `spec_gap` rate `≤ 30%`, `0` integrity violations |
| **HOLD — revise workflow** | Any GO criterion not met (missing observations, low autonomous convergence, repeated `spec_gap`, or regressions `> 10%`) |
| **PROMOTE — prune pipeline** | Only after a larger comparable cohort proves lower human effort with equal or better quality |

## Self-Test

```bash
python3 -m unittest discover -s experiments -p '*_test.py' -v
```
