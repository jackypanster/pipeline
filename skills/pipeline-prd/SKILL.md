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
3. **Survey the codebase before grilling** (skip only if the idea is already fully scoped):
   - If the idea is "pick N endpoint/feature to implement": cross-reference the API doc / spec index
     against existing command enum variants to build the GAP list. Rank by ROI (usage frequency ×
     implementation simplicity). Present a SINGLE recommendation with rationale.
   - If a scoped feature: read the relevant source files, learn the existing patterns, identify
     what's missing. Summarize findings before grilling.
   - **Code-first**: answer as many questions as possible by reading code; ask the human only for
     genuine ambiguity / preference. Target ~5 code-verified per 1 human-asked.
   Then **grill-me** — one question at a time, recommend answers — and **think** to harden it into a
   decision-complete plan.
4. Write `.pipeline/<feature>/PRD.md` — problem, goal, success criteria, scope/non-scope, the
   resolved decisions. Agent-first (dense, no filler). Set `current.json.stage: prd` (most-recently-
   completed = prd). **Append your handoff to `journal.md`** (CONTRACT §Run journal). `git add` `PRD.md`
   **+ `current.json` + `journal.md`** (this stage created/seeded them), commit **once**, push.
5. **Print the handoff** to **pipeline-arch** (already journaled in step 4; per CONTRACT.md §handoff):
   repo, branch, artifact path, `do: read PRD.md, grill the architecture against the codebase`.

## Hard rules

- You may ask the human questions (this is the HITL stage). Wait for answers; do not guess.
- One feature at a time. Write only `PRD.md` (+ `current.json` metadata). No code, no architecture yet.
