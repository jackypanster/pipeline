# pipeline — 3-Agent 4-Step Dev Workflow

A lightweight, **model-agnostic, tool-agnostic, forge-agnostic** agent development pipeline over a
Git + Markdown state bus (`.pipeline/`). Any Git repository and any AI coding agent can adopt it in
minutes.

Read [CONTRACT.md](CONTRACT.md) for the normative protocol and [DESIGN.md](DESIGN.md) for the
rationale.

## How It Works (3 Roles, 4 Steps, 2 Phases)

```text
Phase I (Frontier Planner quota spent ONLY here)
  Step 1: pipeline-plan        [planner, frontier model A]
     └─ writes .pipeline/<feature>/plan.md (evidence, <=10 specs, T1-T10, verify[], forbid-paths[])
  Step 2: pipeline-plan-review [reviewer, independent frontier model B != A]
     ├─ ACCEPT / ACCEPT WITH NITS ─▶ proceeds to Step 3 (Planner exits; 0 planner tokens after this)
     ├─ REJECT (< 3)              ─▶ back to Step 1
     └─ 3rd REJECT (>= 3)         ─▶ STOP for human

Phase II (0 Planner tokens — human-relayed, headless run-loop.py, or multica/herdr)
  Step 3: pipeline-impl        [implementer, coding CLI C != B]
     └─ implements code + T1-T10 tests on feat/<feature>, passes all plan.md verify[] green
  Step 4: pipeline-review      [reviewer, independent frontier model B]
     ├─ forbid-paths + independent verify re-run + Assumptions + T1-T10 + mutation spot-check
     ├─ ACCEPT                    ─▶ SHA-bound human-direct GO-gate ─▶ reviewer squash-merges
     ├─ REJECT (plan_fact|plan_design|env) ─▶ immediate short-circuit to human/planner
     ├─ REJECT (impl_bug|weak_test|scope, < 3) ─▶ back to Step 3
     └─ 3rd REJECT (>= 3)         ─▶ STOP for human
```

Any model family can fill any role (e.g. Gemini / Claude / Kimi as `planner`; GPT / GLM / Claude as
`reviewer`; Pi / Codex / Claude / OpenCode as `implementer`) as long as **`reviewer` is distinct
from both `planner` and `implementer`**. Concrete model names live ONLY in operator-local config,
never in repository files.

## Quick Start (Any Machine, Any Repo, Any Agent)

### 1. Install skills on this machine (once)

```bash
( set -e
# Read-only consumer clone (never edit skills here — CONTRACT §Self-improvement).
[ -e ~/.agents/pipeline ] || git clone https://github.com/jackypanster/pipeline.git ~/.agents/pipeline

# Canonical symlinks under ~/.agents/skills/
mkdir -p ~/.agents/skills
for d in ~/.agents/pipeline/skills/pipeline-*/; do n=$(basename "$d")
  [ -e ~/.agents/skills/$n ] || [ -L ~/.agents/skills/$n ] || ln -s ../pipeline/skills/$n ~/.agents/skills/$n
done

# Attach to local agent runtimes if present (runtimes reading ~/.agents/skills directly need none)
for d in ~/.agents/pipeline/skills/pipeline-*/; do n=$(basename "$d")
  if [ -d ~/.claude ]; then mkdir -p ~/.claude/skills
    [ -e ~/.claude/skills/$n ] || ln -s ../../.agents/skills/$n ~/.claude/skills/$n; fi
  if [ -d ~/.codex ]; then mkdir -p ~/.codex/skills
    [ -e ~/.codex/skills/$n ] || ln -s ~/.agents/skills/$n ~/.codex/skills/$n; fi
done
if [ -d ~/.pi/agent ]; then mkdir -p ~/.pi/agent/skills
  for n in pipeline-impl pipeline-preflight; do
    [ -e ~/.pi/agent/skills/$n ] || ln -s ../../../.agents/skills/$n ~/.pi/agent/skills/$n
  done
fi
echo "install OK" )
```

### 2. Onboard any target Git repository (2 minutes)

Copy `roles.yaml` into `<target-repo>/.pipeline/roles.yaml` (never overwrite an existing one) and
replace `<autonomous-coding-skill>` with your implementer runtime's real installed skill name:

```bash
( set -e
cd <target-repo>
mkdir -p .pipeline
[ -e .pipeline/roles.yaml ] || [ -L .pipeline/roles.yaml ] || cp ~/.agents/pipeline/roles.yaml .pipeline/roles.yaml
echo "bound $(pwd)/.pipeline/roles.yaml" )
```

Paste this block into `<target-repo>/AGENTS.md` (or `CLAUDE.md`) so any cold agent knows the rules:

> **This repo uses the `pipeline` 3-Agent 4-Step workflow (`.pipeline/` on trunk, code+tests only on
> `feat/<feature>`).**
> - **Flow:** `pipeline-plan` (planner A) → `pipeline-plan-review` (reviewer B ≠ A) →
>   `pipeline-impl` (implementer C ≠ B: code + T1–T10 tests + green `verify[]`) → `pipeline-review`
>   (reviewer B: `forbid-paths` + `verify[]` + `## Assumptions` + T1–T10 + mutation spot-check +
>   human-direct GO-gate merge). Escalation: `pipeline-hunt`.
> - **Invariants:** `reviewer ≠ planner` and `reviewer ≠ implementer`; 3rd rejection (`>= 3`) stops
>   for human; Step 4 `plan_fact|plan_design|env` short-circuits to human; only `pipeline-review`
>   merges, strictly on explicit direct human `go`/`merge`/`confirm` against the pinned 40-hex SHA;
>   never force-push trunk; `.pipeline/<feature>/journal.md` tail is authoritative.
> - **Start:** read `CONTRACT.md` in [`jackypanster/pipeline`](https://github.com/jackypanster/pipeline),
>   then `.pipeline/<feature>/plan.md` and `journal.md`. Run `pipeline-preflight/scripts/preflight.sh`
>   as step 0 of every stage.

### 3. Run a feature

**Option A — Agent-native Headless Runner (`pipeline-run` skill, recommended — full 4-step foreground or `--background`, zero manual copy-paste):**

1. Generate operator-local `~/.config/pipeline/runners.json` once (never commit to Git; any model/CLI
   can fill any role as long as `reviewer` differs from both `planner` and `implementer` — see
   [runners.example.json](skills/pipeline-run/runners.example.json)):
   ```bash
   python3 ~/.agents/skills/pipeline-coordinate/scripts/run-loop.py init-config
   ```
2. Launch all 4 steps continuously (or simply ask any installed agent to use `pipeline-run`):
   ```bash
   python3 ~/.agents/skills/pipeline-coordinate/scripts/run-loop.py run \
     --repo <target-repo> --feature <slug> --idea "<requirement>" [--push] --background
   ```
   Monitor / manage:
   ```bash
   python3 ~/.agents/skills/pipeline-coordinate/scripts/run-loop.py status --repo <target-repo> --feature <slug>
   python3 ~/.agents/skills/pipeline-coordinate/scripts/run-loop.py logs   --repo <target-repo> --feature <slug>
   python3 ~/.agents/skills/pipeline-coordinate/scripts/run-loop.py resume --repo <target-repo> --feature <slug> [--answer "<text>"] --background
   python3 ~/.agents/skills/pipeline-coordinate/scripts/run-loop.py stop   --repo <target-repo> --feature <slug>
   ```
   *(Single-phase commands `review-plan`, `approve-plan`, and `phase2` remain available when you
   author `plan.md` interactively and only want headless Phase II with 0 planner tokens.)*
3. The run stops ONLY when human action is required: `ACCEPT` (open interactive `pipeline-review`
   to confirm the pinned 40-hex SHA and merge via GO-gate), 3rd-rejection breaker (`>= 3`),
   `NEEDS_HUMAN` / `plan_fact` / `plan_design` / `env`, or CLI error.

**Option B — Human-relayed (zero extra setup):** copy the printed `>>> NEXT` handoff block
between your 3 role sessions. Mandatory for high-consequence write paths (trading, irreversible ops).

**Option C — Multi-machine / coordinated (`multica` or `herdr`):** create
`.pipeline/<feature>/control.json` (`{"schema_version":1,"mode":"coordinated","merge_gate":"human-direct"}`)
and use `pipeline-coordinate` (see [transport-multica.md](skills/pipeline-coordinate/transport-multica.md)).

## Command Reference

| Command | Role | `roles.yaml` slot | Purpose |
|---|---|---|---|
| `pipeline-run` | (entry skill) | — | Agent-native full 4-step runner (`run --background`, `status`, `logs`, `resume`, `stop`, `init-config`) |
| `pipeline-plan` | `planner` | `[grill-me, think]` | Survey codebase, write `plan.md` (`verify[]`, `forbid-paths[]`, ≤10 specs, T1–T10) |
| `pipeline-plan-review` | `reviewer` | `check` | Spot-check ≥3 citations, pin `approved_plan_blob`, `VERDICT \| CAUSE` |
| `pipeline-impl` | `implementer` | `<autonomous-coding-skill>` | Code + T1–T10 tests on `feat/<feature>`, pass `verify[]`, write `impl-report.md` |
| `pipeline-review` | `reviewer` | `check` | Deterministic gates + mutation spot-check + SHA-bound GO-gate squash-merge |
| `pipeline-hunt` | `planner`/human | `hunt` | Root-cause a `blocked` feature (`>=3` rejects or plan/env escalation) |
| `pipeline-improve` | `planner` | `think` | Propose a reviewed meta-PR against `jackypanster/pipeline` |
| `pipeline-coordinate` | (playbook + runner) | — | `scripts/run-loop.py`, `multica`, or `herdr` coordination |
| `pipeline-preflight` | (helper) | — | Step-0 `scripts/preflight.sh` + `scripts/check-plan.py` |
| `pipeline-install` / `pipeline-update` | (maintenance) | — | Machine symlink install / ff-only update |

## Dependencies

- **Delegated skills** (must exist on the runtime executing that slot): `think`, `check`, `hunt`
  (`tw93/Waza`), `grill-me` (`mattpocock/skills`), and your runtime's autonomous coding skill for `impl`.
- **Runtime tools:** `git`, `python3` ≥3.9 (stdlib only); optional `gh` / `gitee-cli` (forge PRs),
  `multica` / `herdr` (coordinated transports).

## Update & Local Verification

Update between stages: `bash ~/.agents/skills/pipeline-update/scripts/update.sh` (remove any old
dangling symlinks if upgrading, re-run §Quick Start step 1 for new skills, reconcile target
`.pipeline/roles.yaml`).

Run this repo's offline test suite:

```bash
bash skills/pipeline-preflight/scripts/preflight-test.sh
/bin/bash skills/pipeline-preflight/scripts/preflight-test.sh
bash skills/pipeline-update/scripts/update-test.sh
python3 skills/pipeline-coordinate/scripts/run-loop-test.py
```
