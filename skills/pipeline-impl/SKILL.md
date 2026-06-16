---
name: pipeline-impl
description: "Pipeline stage 4 — implement one task card: make its frozen red test green, open a PR. Wraps the goal skill (think→code→check loop). Writes ZERO spec tests. Use after pipeline-task. Args: repo, branch, optional card-id."
---

# pipeline-impl

Stage 4. Follow the **shim loop in CONTRACT.md** with slot = `impl`.

**Skill:** `impl` slot resolves to **`goal-driven-implementation`** (the Hermes skill that drives the
`/goal` autonomous loop) — think→design-tests→code→check. Note the slot value in `roles.yaml` is the
real skill name `goal-driven-implementation`, not the bare word "goal". It writes
**white-box tests in `impl-paths:` (allowed)**; it must NOT create or edit anything under
`spec-paths:`. Constrain it accordingly (a `/subgoal` "do not touch spec-paths; do not author
acceptance tests" is the cheap seam if needed).

## Steps
1. `git pull --rebase`. Read `current.json`. Pick the **oldest** `status: todo` card (or the given
   card-id). Idempotency: if a `task/<id>-*` branch already has an open PR, skip — already in flight.
2. Resolve `impl` slot; verify installed (else STOP). Create branch `task/<id>-<slug>`, set the card
   `status: in-progress`, commit on the branch.
3. **goal**: implement inside `impl-paths:` until the card's `verify:` commands all exit 0 (its red
   test goes green). Loop think→code→check within the turn budget.
4. **Green** ⇒ set `status: review`, push the branch, open a PR/branch via the forge adapter. Print
   the handoff to **pipeline-review**.
5. **Fail / budget exhausted** ⇒ `attempts++`; `attempts < 3` ⇒ back to `status: todo` (re-queue);
   `attempts >= 3` ⇒ `status: blocked`. Either way print the handoff to **pipeline-hunt** with the
   reason, and append a `## Attempt N` note to the card (the next run reads only the card).

## Hard rules
- Never touch `spec-paths:` (the frozen spec). Never merge. Only this card's files.
- White-box tests in `impl-paths:` are fine; the acceptance test stays frozen.
