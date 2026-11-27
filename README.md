# pipeline (v2: 3-Agent 4-Step)

Agent-facing skill collection. Consumers are LLM/agents, not humans — read [CONTRACT.md](CONTRACT.md).

**What:** a forge-agnostic, machine-agnostic, model-agnostic **3-agent 4-step** dev pipeline over a
git+md state bus. Three abstract roles (`planner`, `reviewer`, `implementer`) keep model families
separated (`reviewer ≠ planner` and `reviewer ≠ implementer`). **Phase I** (`plan ↔ plan-review`)
uses scarce frontier planner quota only to converge the spec; once approved, **Phase II**
(`impl+tests ↔ verify ↔ code-review`) runs with **zero planner tokens** — either human-relayed, via
the stateless headless CLI runner (`run-loop.py`), or via coordinated mode (`multica`/`herdr`).
Every review gate stops for human intervention on the **3rd rejection** (`>= 3`), and merge remains
strictly behind the **human-direct GO-gate** in `pipeline-review`.

## Files

- `CONTRACT.md` — frozen v2 protocol: 3 roles · 4 steps · verdict grammar · 3-reject breaker · anti-cheat · forge adapter.
- `DESIGN.md` — rationale, 608-card empirical provenance, and bounded-automation constraints.
- `roles.yaml` — per-target-repo slot→skill bindings (copy into `<target>/.pipeline/`; never put model names here).
- `skills/pipeline-*/SKILL.md` — stage shims + `pipeline-coordinate` + `pipeline-preflight` + `pipeline-install` + `pipeline-update`.

| command | role | slot → skill | in → out |
|---|---|---|---|
| `pipeline-plan` | `planner` | `[grill-me, think]` | idea → `plan.md` (evidence, ≤10 specs, T1–T10, `verify`, `forbid-paths`) |
| `pipeline-plan-review` | `reviewer` | `check` | `plan.md` → `VERDICT \| CAUSE` (`ACCEPT`/`ACCEPT WITH NITS` → Step 3; 3rd `REJECT` → human) |
| `pipeline-impl` | `implementer` | `<autonomous-coding-skill>` | approved `plan.md` → `feat/<feature>` code + T1–T10 tests + green `verify` + `impl-report.md` |
| `pipeline-review` | `reviewer` | `check` | diff/PR → `forbid-paths` + `verify` + T1–T10 + mutation spot-check + human-direct GO-gate merge |
| `pipeline-hunt` | `planner`/human | `hunt` | `blocked` feature (`>=3` rejects or `plan_fact`/`plan_design`/`env`) → root cause → re-route |
| `pipeline-improve` | `planner` | `think` | skill gap → reviewed meta-PR on THIS repo (never self-edits, never auto-merges) |
| `pipeline-coordinate` | (playbook + runner) | — | drives Phase II via headless CLI (`scripts/run-loop.py`), `multica`, or `herdr` |
| `pipeline-preflight` | (helper script) | — | deterministic step-0 executor (`scripts/preflight.sh`): pull, `current.json`, slot, guard, plan check |
| `pipeline-install` / `pipeline-update` | (maintenance) | — | stand up / ff-pull `~/.agents/pipeline` symlinks on this machine |

## Operating modes

1. **Human-relayed (default):** human relays handoffs between the 3 role sessions. Mandatory for
   high-consequence write-paths (trading, irreversible migrations, production credentials).
2. **Headless CLI runner (`skills/pipeline-coordinate/scripts/run-loop.py`):** zero-planner-token
   single-machine execution for Phase II (`Step 3 Impl CLI ↔ deterministic verify ↔ Step 4 Reviewer
   CLI`) plus optional single-shot Step 2 (`review-plan`). Configured via operator-local
   `~/.config/pipeline/runners.json` (never committed to repos):
   ```json
   {
     "implementer": {
       "model_id": "<your-impl-model>",
       "argv": ["<impl-cli>", "-p", "--no-session", "--no-mcp", "{prompt}"]
     },
     "reviewer": {
       "model_id": "<your-reviewer-model>",
       "argv": ["<reviewer-cli>", "exec", "-s", "read-only", "-"],
       "prompt_via": "stdin"
     }
   }
   ```
   Mechanically enforces `model_id` separation, isolates `feat/<feature>` in a clean git worktree,
   runs `plan.md`'s `verify` commands BEFORE Step 4, short-circuits `plan_fact|plan_design|env`
   immediately to human, halts on the 3rd rejection (`exit 2`), and **never merges**.
3. **Coordinated mode (`pipeline-coordinate` over `multica` or `herdr`):** opt-in per feature via
   `.pipeline/<feature>/control.json` (`{"schema_version":1,"mode":"coordinated","merge_gate":"human-direct"}`).
   Transport resolves invocation → `~/.config/pipeline/transport` → Herdr (`transport-multica.md`).
4. **Duty mode:** queued timed re-entry of coordinated mode over `.pipeline/queue.md`, read-only
   toward target repo, human-direct merge gate unchanged.

### Choosing the mode

| situation | recommend |
|---|---|
| dangerous write-path / trading / external side effects | human-relay (or Step 2 `pause_after_plan: true` + manual review) |
| single-machine daily dev, save frontier planner quota | Phase I interactive (`plan ↔ plan-review`), Phase II `run-loop.py phase2` |
| multi-machine / fleet with online `multica` runtimes | coordinated mode (`control.json` + `multica` transport) |
| operator away with queued low-risk features | duty mode (`/loop` + `queue.md`) |

## Onboard a target project (paste into its `AGENTS.md` / `CLAUDE.md`)

> **This project uses the `pipeline` v2 (3-Agent 4-Step) workflow — forge-agnostic, machine-agnostic,
> model-agnostic over a git+markdown state bus under `.pipeline/`.**
>
> **Flow:** `pipeline-plan` (frontier planner A) → `pipeline-plan-review` (independent reviewer B) →
> `pipeline-impl` (coding CLI C: code + T1–T10 tests + green `verify`) → `pipeline-review`
> (independent reviewer B: `forbid-paths` + `verify` + T1–T10 + mutation spot-check + merge), plus
> `pipeline-hunt` on `blocked`. After Step 2 passes (`ACCEPT` / `ACCEPT WITH NITS`), Step 3 proceeds
> by default with **zero planner tokens**.
>
> **Frozen invariants:** `model(reviewer) ≠ model(planner)` and `model(reviewer) ≠ model(implementer)`;
> 3rd rejection at either review gate (`>= 3`) stops for human; Step 4 `plan_fact|plan_design|env`
> short-circuits immediately to human; only `pipeline-review` merges, strictly after explicit direct
> human confirmation (`go`/`merge`/`confirm`); never force-push trunk; `.pipeline/` metadata lives on
> trunk (`journal.md` tail is authoritative) while `feat/<feature>` holds reviewable code+tests only.
>
> **To act:** read `CONTRACT.md` in [`jackypanster/pipeline`](https://github.com/jackypanster/pipeline),
> then `.pipeline/<feature>/plan.md` and the `journal.md` tail. Run `pipeline-preflight/scripts/preflight.sh`
> as step 0 of every stage.

## Install (agent: execute this; it is written for you, not a human)

Every machine that runs a pipeline stage installs the full `pipeline-*` set (stage skills locate
`pipeline-preflight` as a sibling dir).

```bash
( set -e
# 1. Read-only consumer clone (never edit skills here — CONTRACT §Self-improvement).
[ -e ~/.agents/pipeline ] || git clone https://github.com/jackypanster/pipeline.git ~/.agents/pipeline

# 2. Canonical entries: one RELATIVE symlink per skill into the clone.
mkdir -p ~/.agents/skills
for d in ~/.agents/pipeline/skills/pipeline-*/; do n=$(basename "$d")
  [ -e ~/.agents/skills/$n ] || [ -L ~/.agents/skills/$n ] || ln -s ../pipeline/skills/$n ~/.agents/skills/$n
done

# 3. Runtime attachments → canonical entries, for each runtime present on this machine.
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

```bash
# 4. Per target project, bind slots (never clobber an existing roles.yaml).
( set -e
cd <target-repo>
mkdir -p .pipeline
[ -e .pipeline/roles.yaml ] || [ -L .pipeline/roles.yaml ] || cp ~/.agents/pipeline/roles.yaml .pipeline/roles.yaml
echo "bound $(pwd)/.pipeline/roles.yaml" )
```

### Verify + supplement dependencies

| delegated skill | used by | source |
|---|---|---|
| `think` | `plan`, `improve` | Waza — `github.com/tw93/Waza` (`skills/think`) |
| `check` | `plan-review`, `review` | Waza (`skills/check`) |
| `hunt` | `hunt` | Waza (`skills/hunt`) |
| `grill-me` | `plan` | `github.com/mattpocock/skills` (`skills/productivity/grill-me`) |
| `<autonomous-coding-skill>` | `impl` | your runtime's real installed autonomous coding skill (e.g. `goal-driven-implementation`) |

Non-skill runtime tools: `git`, `python3` ≥3.9 (stdlib only — used by `preflight.sh`, `watch-pane.py`,
`run-loop.py`), optional `gh`/`gitee-cli` (forge adapter), optional `herdr` / `multica` (coordinated
transports).

## Update (agent: execute this on the machine being updated)

Run `bash ~/.agents/skills/pipeline-update/scripts/update.sh` between stages (never mid-stage).
On upgrade from v1 to v2:
1. Remove any dangling v1 symlinks (`pipeline-prd`, `pipeline-arch`, `pipeline-task`) under
   `~/.agents/skills/`, `~/.claude/skills/`, `~/.codex/skills/`.
2. Re-run the §Install machine block to attach the new `pipeline-plan` and `pipeline-plan-review`
   symlinks.
3. Reconcile each target repo's `.pipeline/roles.yaml` to the v2 slots (`plan`, `plan-review`,
   `impl`, `review`, `hunt`, `improve`).

## Tests (run locally on this repo)

```bash
bash skills/pipeline-preflight/scripts/preflight-test.sh
/bin/bash skills/pipeline-preflight/scripts/preflight-test.sh
bash skills/pipeline-update/scripts/update-test.sh
python3 skills/pipeline-coordinate/scripts/watch-pane-test.py
python3 skills/pipeline-coordinate/scripts/run-loop-test.py
```
