---
name: pipeline-arch
description: "Pipeline stage 2 — turn a PRD into an architecture. Uses grill-with-docs to walk the design tree against the existing codebase and emit arch.md + CONTEXT.md + ADRs. Use after pipeline-prd. Args: repo, branch."
---

# pipeline-arch

Stage 2. Follow the **shim loop in CONTRACT.md** with slot = `arch`.

**Skill:** `arch` slot resolves to `grill-with-docs` — it walks each branch of the design tree,
challenges the plan against the repo's existing domain model, sharpens terminology, and updates
`CONTEXT.md` + ADRs inline. YOU ensure the artifacts land under `.pipeline/<feature>/`.

## Steps
1. `git pull --rebase`. Read `.pipeline/current.json` (STOP if missing) and `<feature>/PRD.md`.
2. Resolve `arch` slot from `roles.yaml`; verify installed (else STOP).
3. **grill-with-docs** against the PRD + codebase: resolve cross-decision dependencies one at a
   time, record irreversible/surprising choices as ADRs, sharpen the domain language in CONTEXT.md.
4. Write `.pipeline/<feature>/arch.md` (the chosen shape + component boundaries) and let
   grill-with-docs land `CONTEXT.md` + `docs/adr/*.md`. Commit, push.
5. Print the handoff to **pipeline-task**: `do: decompose into atomic cards, write a red test per card`.

## Hard rules
- HITL stage — ask the human on blocking ambiguity, wait. Don't invent domain terms; ground them.
- Write architecture/ADRs only. No task cards, no code, no tests here.
- Irreversible decisions (migrations, concurrency model, data shape) → an ADR, not a coder card.
