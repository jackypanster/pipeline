# pipeline

A lightweight, **forge-agnostic, machine-agnostic** skill-aggregation pipeline for AI-assisted
development. Six thin commands — **prd · arch · task · impl · review · hunt** — each a ~20-line
shim that delegates to a **swappable** community skill (`think`, `grill-with-docs`, `goal`,
`check`, `hunt`) over a **git + markdown** state bus.

- **Human-relayed, no scheduler.** Each command prints a self-contained handoff string; a human
  copies it to the next agent/bot. The human is the intentional bottleneck.
- **Any git forge.** Operates over the git protocol. GitHub / Gitee / plain git are just carriers;
  review uses the forge's PR when available, else a branch diff.
- **Any machine, any model.** Skills install into any agent runtime's skill dir; each command may
  run on the same or a different model/bot. Reasoning commands want a frontier model; `impl`
  tolerates a capable local LLM.
- **The only durable asset is the orchestration contract** (command sequence + handoff format +
  the git+md state convention). The skills behind each command are interchangeable — point a slot
  at a better skill when one ships.

See **[DESIGN.md](DESIGN.md)** for the rationale and **[CONTRACT.md](CONTRACT.md)** for the frozen
protocol every command follows.

## Layout

```
CONTRACT.md            frozen protocol (shim loop, state machine, anti-cheat, handoff, forge adapter)
roles.yaml             per-target-repo skill bindings (copy into the target repo's .pipeline/)
skills/
  pipeline-prd/        rough idea → PRD.md            (grill-me → think)
  pipeline-arch/       PRD → arch.md + CONTEXT + ADRs (grill-with-docs)
  pipeline-task/       arch → atomic cards + red test (think; agent writes the test)
  pipeline-impl/       card → green + PR              (goal)
  pipeline-review/     diff/PR → review + merge       (check; only this stage merges)
  pipeline-hunt/       blocked card → root cause       (hunt)
```

## Install

Copy `skills/pipeline-*` into your agent runtime's skill dir (e.g. `~/.claude/skills/` for Claude
Code, or a Hermes profile's skill dir). Copy `roles.yaml` into each target repo's `.pipeline/` and
point each slot at whatever skill you prefer. The delegated skills (`grill-me`, `grill-with-docs`,
`think`, `goal`, `check`, `hunt`) are installed separately — `roles.yaml` only names them.

> Status: contract + six skill shims scaffolded. Not yet run end-to-end on a real project.
