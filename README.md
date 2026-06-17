# pipeline

Agent-facing skill collection. Consumers are LLM/agents, not humans — read [CONTRACT.md](CONTRACT.md).

**What:** a forge-agnostic, machine-agnostic dev pipeline as 6 thin command-skills over a git+md
state bus. Human-relayed (no scheduler); each command prints a handoff the operator copies to the
next bot. The only durable asset is the orchestration contract; the skill behind each command is a
swappable `roles.yaml` slot.

## Files

- `CONTRACT.md` — frozen protocol every command follows: shim loop · state machine · anti-cheat · handoff · forge adapter.
- `roles.yaml` — per-target-repo slot→skill bindings (copy into the target repo's `.pipeline/`).
- `skills/pipeline-*/SKILL.md` — the 6 command shims.

| command | slot → skill | in → out |
|---|---|---|
| pipeline-prd | grill-me → think | rough idea → `PRD.md` |
| pipeline-arch | grill-with-docs | PRD → `arch.md` + `CONTEXT.md` + ADRs |
| pipeline-task | think | arch → atomic cards + frozen red test |
| pipeline-impl | goal | card → green + PR (zero spec tests) |
| pipeline-review | check | diff/PR → review + merge (only stage that merges) |
| pipeline-hunt | hunt | blocked card → root cause → re-route |
| pipeline-improve | think | skill gap → reviewed PR on THIS repo (never self-edits, never auto-merges) |

## Install (agent: execute this; it is written for you, not a human)

You are an agent installing this collection on your runtime. Do it once, then verify dependencies.

```bash
# 1. Get the repo as a READ-ONLY consumer clone (never edit skills here — see CONTRACT §Self-improvement).
git clone https://github.com/jackypanster/pipeline.git ~/workspace/pipeline   # or: git -C ~/workspace/pipeline fetch && git reset --hard origin/main

# 2. Install the command shims into the runtime that will RUN them:
#    Claude Code:
cp -r ~/workspace/pipeline/skills/pipeline-* ~/.claude/skills/
#    Hermes: add "~/workspace/pipeline/skills" to skills.external_dirs (a YAML LIST item — NOT a
#    JSON-encoded string, which fails silently), then reload the gateway.

# 3. Per target project, point the slots at your chosen skills:
mkdir -p <target-repo>/.pipeline && cp ~/workspace/pipeline/roles.yaml <target-repo>/.pipeline/roles.yaml
```

### Verify + supplement dependencies (do this BEFORE running any command)

The commands delegate to the skills named in `roles.yaml`. Each must RESOLVE on the runtime that runs
its command. **Check every one; install any that is missing from its source:**

| delegated skill | used by | source |
|---|---|---|
| `think` | prd, task, improve | Waza — `github.com/tw93/Waza` (`skills/think`) |
| `check` | review | Waza (`skills/check`) |
| `hunt` | hunt | Waza (`skills/hunt`) |
| `grill-me` | prd | `github.com/mattpocock/skills` (`skills/productivity/grill-me`) |
| `grill-with-docs` | arch | `github.com/mattpocock/skills` (`skills/engineering/grill-with-docs`) |
| `goal-driven-implementation` | impl | your Hermes runtime's `hermes-skills` (`devops/goal-driven-implementation`) |

**Check procedure:** for each skill, confirm it loads on the runtime (list installed skills, or try to
`skill_view` it). Missing ⇒ install from its source into that runtime's skill dir ⇒ re-check.
**Cross-runtime trap:** a skill in `~/.hermes/skills` is NOT resolvable from Claude Code's
`~/.claude/skills` (and vice versa) — install it where the command actually runs. `pipeline-prd`
re-verifies every slot resolves on init and STOPs if one is missing — but verify up front to avoid a
mid-run stop. Names matter: `roles.yaml` says `goal-driven-implementation`, not bare `goal`.

## State

Contract + 7 command shims (prd/arch/task/impl/review/hunt/improve). Proven end-to-end once on a real
project (a GET endpoint shipped via prd→arch→task→impl→review→merge). Rationale: [DESIGN.md](DESIGN.md).
