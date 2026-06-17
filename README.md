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

## Install

```bash
# 1. command shims → the agent runtime's skill dir (Claude Code shown; or a Hermes profile dir)
cp -r skills/pipeline-* ~/.claude/skills/
# 2. slot bindings → the target repo
cp roles.yaml <target-repo>/.pipeline/roles.yaml
```

Delegated skills (`grill-me` `grill-with-docs` `think` `goal` `check` `hunt`) install separately;
`roles.yaml` only names them. On init, `pipeline-prd` verifies every slot resolves to an installed
skill (hard gate).

## State

Contract + 6 shims scaffolded. Not yet run end-to-end. Rationale: [DESIGN.md](DESIGN.md).
