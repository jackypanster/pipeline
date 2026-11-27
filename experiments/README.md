# `experiments/plite-skill` — Local 2-Agent × 3-Step Pilot Skill

Minimal, reversible companion experiment to `jackypanster/pipeline`, packaged as a self-contained Agent Skill (`experiments/plite-skill/SKILL.md` + `experiments/plite-skill/scripts/plite.py`). Leaves `CONTRACT.md`, `DESIGN.md`, and all 11 `pipeline-*` skills untouched while testing whether local headless subprocess orchestration + 2-model separation delivers lower human interruption without sacrificing quality on **low-risk tasks**.

## Local Install (Collision-Safe Multi-Runtime Symlinks)

**Prerequisite**: this pilot lives on the experimental branch `exp/plite` of your working clone (`~/workspace/pipeline`, distinct from the read-only production consumer clone `~/.agents/pipeline`). Check out `exp/plite` before linking:

```bash
( set -e
  SRC="$HOME/workspace/pipeline/experiments/plite-skill"
  [ -f "$SRC/SKILL.md" ]
  [ -x "$SRC/scripts/plite.py" ]
  CANON="$HOME/.agents/skills/plite"
  mkdir -p "$HOME/.agents/skills"
  if [ -L "$CANON" ] && [ "$(readlink "$CANON")" = "$SRC" ]; then
    echo "OK (canonical): $CANON -> $SRC"
  elif [ -e "$CANON" ] || [ -L "$CANON" ]; then
    echo "STOP: conflicting entry at $CANON" >&2; exit 1
  else
    ln -s "$SRC" "$CANON"
    echo "LINKED: $CANON -> $SRC"
  fi
  link_safe() {
    local target=$1 dst=$2
    mkdir -p "$(dirname "$dst")"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$target" ]; then
      echo "OK: $dst -> $target"
    elif [ -e "$dst" ] || [ -L "$dst" ]; then
      echo "STOP: conflicting entry at $dst" >&2; exit 1
    else
      ln -s "$target" "$dst"
      echo "LINKED: $dst -> $target"
    fi
  }
  [ -d "$HOME/.claude" ] && link_safe "../../.agents/skills/plite" "$HOME/.claude/skills/plite"
  [ -d "$HOME/.codex" ] && link_safe "$HOME/.agents/skills/plite" "$HOME/.codex/skills/plite"
  [ -d "$HOME/.pi/agent" ] && link_safe "../../../.agents/skills/plite" "$HOME/.pi/agent/skills/plite"
  link_safe "$SRC/scripts/plite.py" "$HOME/.local/bin/plite"
)
```

Invoke naturally in each runtime:
- **AGY / Antigravity / Claude Code**: `/plite 在当前仓库给 status 加 --json 输出，用 agy(gemini-4-neon) 写、codex(gpt-6.2-sol) 审`
- **Codex CLI**: `$plite ...`
- **Pi**: `/skill:plite ...`

**Target-verified uninstall / rollback** (only removes symlinks pointing at plite):
```bash
( set -e
  unlink_safe() {
    local dst=$1 expect=$2
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$expect" ]; then
      rm "$dst" && echo "REMOVED: $dst"
    elif [ -e "$dst" ] || [ -L "$dst" ]; then
      echo "SKIP (not plite symlink): $dst"
    fi
  }
  unlink_safe "$HOME/.claude/skills/plite" "../../.agents/skills/plite"
  unlink_safe "$HOME/.codex/skills/plite" "$HOME/.agents/skills/plite"
  unlink_safe "$HOME/.pi/agent/skills/plite" "../../../.agents/skills/plite"
  unlink_safe "$HOME/.agents/skills/plite" "$HOME/workspace/pipeline/experiments/plite-skill"
  unlink_safe "$HOME/.local/bin/plite" "$HOME/workspace/pipeline/experiments/plite-skill/scripts/plite.py"
)
```

## Admission Boundary (87.199)

`--risk` is **required** (never defaults to `low`):

- **Eligible (`--risk low`)**: single-card bugfixes, small scoped features, doc/lint/chore changes with concrete, immutable `--acceptance` criteria and **0 HARD** triggers and **≤1 SOFT** triggers.
- **Not eligible (`--risk high`)**: **any 1 HARD** trigger (H1 non-objective acceptance; H2 ≥2 independently mergeable cards or multiple Engineer sessions; H3 new/changed module boundary, interface, schema, or shared state; H4 money/trading, secrets/auth, prod deploy, external comms, or permissions; H5 ≥3 structurally distinct designs; H6 cross-repo/system/multi-agent) OR **≥2 SOFT** triggers (S1 >8 files or new service; S2 conflicting requirements; S3 needs live account/external dep check before design; S4 recurring `LESSONS.md` area; S5 migration/multi-stage change; S6 ~1h of agent work with fuzzy scope). `plite.py` refuses `--risk high` immediately (before run creation; admission rejections are excluded from pilot telemetry by design) and escalates to full `pipeline` (`prd → arch → task → impl → review` with frozen red tests).

## Direct CLI Usage

### 1. AGY implements, Codex GPT-6.2 reviews
```bash
plite run \
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
plite run \
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
plite resume \
  --run-id <run_id> \
  --worker claude \
  --reviewer codex --reviewer-model gpt-6.2-sol
```
- If the run failed during **review** (e.g., reviewer hit quota), `resume` skips re-running the worker and retries review directly on the already-verified `candidate_sha`.
- `contributing_families` is tracked cumulatively across retries/resumes so no model family that contributed code can later review that candidate.
- Runs terminated with `integrity_violation` are quarantined and **cannot** be resumed.

### 4. Post-merge backfill & pilot scorecard
```bash
plite mark \
  --run-id <run_id> \
  --merged-sha <sha> \
  --regression none_observed \
  --human-time 45

plite summary
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
