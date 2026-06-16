---
name: pipeline-task
description: "Pipeline stage 3 — decompose an architecture into atomic, landable task cards, each carrying a FAILING red test (the frozen spec). Use after pipeline-arch. Args: repo, branch."
---

# pipeline-task

Stage 3. Follow the **shim loop in CONTRACT.md** with slot = `task`.

**Skill:** `task` slot resolves to `think` — it produces the decomposition (think writes NO code).
**YOU write the red-test code and the card frontmatter** — that is the shim's I/O, not think's.

## Steps
1. `git pull --rebase`. Read `current.json` (STOP if missing), `<feature>/arch.md`, `CONTEXT.md`.
2. Resolve `task` slot; verify installed (else STOP).
3. **think** to split the work into atomic sub-tasks. **Concreteness gate:** if you cannot write a
   failing red test for a sub-task, it is not atomic — split it further (re-think). One card = one
   observable behaviour.
4. For each card, write `.pipeline/<feature>/tasks/NN.md` with frontmatter:
   `status: todo`, `attempts: 0`, `verify: [<build cmd>, <test cmd>]`,
   `spec-paths: <glob>`, `impl-paths: <dir>`, `spec-rev: <filled after commit>`.
   Then **write the failing red test** into `spec-paths:` (happy path + key errors/boundaries,
   ~3–5 assertions — appropriate, not 100% coverage).
5. Commit the cards + red tests; record each commit sha into the card's `spec-rev:`. Push to `main`
   (this is queue authoring, distinct from the only-reviewer-merges rule). 
6. Print the handoff to **pipeline-impl**: `do: pick oldest todo card, make its red test green`.

## Hard rules
- The red test you write IS the spec and gets frozen — the coder cannot edit it.
- No implementation. No prose acceptance the coder could misread — the test is the contract.
