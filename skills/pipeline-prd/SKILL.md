---
name: pipeline-prd
description: "Pipeline stage 1 — turn a rough idea into a concrete PRD. Interactively grills the human to clarify, then writes PRD.md into the target repo's .pipeline/<feature>/. Use to START a pipeline feature. Args: repo, branch, the rough idea."
---

# pipeline-prd

Stage 1 of the pipeline. Follow the **shim loop in CONTRACT.md** with slot = `prd`.

**Skill(s):** `prd` slot resolves to `[grill-me, think]` — run `grill-me` to interrogate the rough
idea until shared understanding, then `think` to produce the decision-complete plan. Neither writes
files; YOU write the PRD.

## Steps
1. `git pull --rebase`. Read/seed `.pipeline/current.json`; this command may CREATE it (set
   `feature` from the idea's slug, `stage: prd`).
2. Resolve `prd` slot from `.pipeline/roles.yaml`; verify those skills are installed (else STOP).
3. **grill-me** on the rough idea — one question at a time, recommend answers, explore the repo to
   answer where possible. Then **think** to harden it into a decision-complete plan.
4. Write `.pipeline/<feature>/PRD.md` — problem, goal, success criteria, scope/non-scope, the
   resolved decisions. Agent-first (dense, no filler). `git add` that one file, commit, push.
5. Print the handoff to **pipeline-arch** (per CONTRACT.md §handoff): repo, branch, artifact path,
   `do: read PRD.md, grill the architecture against the codebase`.

## Hard rules
- You may ask the human questions (this is the HITL stage). Wait for answers; do not guess.
- One feature at a time. Write only `PRD.md`. No code, no architecture yet.
