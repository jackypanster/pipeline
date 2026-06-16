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

See **[DESIGN.md](DESIGN.md)** for the full contract.

> Status: design landed, skills not yet scaffolded. This repo currently holds the contract only.
