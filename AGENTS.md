# AGENTS.md

Pointers for agents working in this repo. **This file is not the authority.**

[CONTRACT.md](CONTRACT.md) is the frozen protocol and single source of truth for every `pipeline-*`
skill — the shim loop, the state machine, the `.pipeline/` layout, and the per-stage write-sets.
Read it before changing anything under `skills/`. [DESIGN.md](DESIGN.md) carries the rationale.

## Agent skills

Config consumed by the `mattpocock/skills` engineering commands (`/wayfinder`, `/to-tickets`,
`/to-spec`, `/triage`, `/implement`). These are user-invoked only — none of them fire automatically.

### Issue tracker

GitHub issues on `jackypanster/pipeline`, driven by the `gh` CLI. See
[docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) for the operation vocabulary, including
the wayfinding map/ticket/blocking conventions.

Triage labels are **not** configured — that file records why and what a skill must do instead.

### Domain docs

Not configured here **by design**. `CONTEXT.md` and `docs/adr/*` are governed by CONTRACT.md, which
freezes them under `.pipeline/<feature>/` in the **target** repo and scopes the `arch` write-set to
exactly those paths. Do not add a repo-root domain-doc layout — it would give `grill-with-docs` (bound
as the `arch` slot in [roles.yaml](roles.yaml)) a second, contradicting instruction.
