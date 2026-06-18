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
   card-id). Idempotency: if `feat/<feature>` already has an open PR and the card reads
   `status: review`, skip — already in flight.
2. Resolve `impl` slot; verify installed (else STOP). Create/checkout the feature branch
   **`feat/<feature>`** (per CONTRACT §State authority — one branch per feature, NOT per card). Flip the
   card `status: in-progress` and commit it to **`main`** (card status is trunk-authoritative metadata —
   a cold node must read the live status from trunk; never leave a status flip on the branch). **Leave
   `current.json.stage` at `task`** — `stage` = most-recently-COMPLETED stage (CONTRACT); it advances to
   `impl` only when this card actually completes (step 4), not when work begins.
3. **goal**: implement inside `impl-paths:` (+ `src/**`) on `feat/<feature>` until the card's `verify:`
   commands all exit 0 (its red test goes green). The card's `verify` is **card-scoped** (CONTRACT
   §State authority) — it goes green on THIS card's frozen test alone, regardless of sibling cards still
   red on trunk; do NOT run the full suite to judge this card (that is review's final gate). Loop
   think→code→check within the turn budget. Only code lives on the branch; never touch `spec-paths:`.
4. **Green** ⇒ push `feat/<feature>`, open/update a PR via the forge adapter, then on `main` flip the
   card `status: review`, advance `current.json.stage` to `impl`, and **append your handoff to
   `journal.md`** — these three metadata writes are **one commit on `main`** (this card completed —
   stage = most-recently-completed). Opening the PR needs the repo's forge token (loaded per CONTRACT
   step 2 from `.env` etc.). If the token is absent, **do NOT fail** — push the branch + make that same
   `main` commit (`status: review` + `stage: impl` + journal entry) anyway, and say in the handoff that
   the PR must be opened manually (branch + base named). **Next-card routing:** if the feature still has
   any `status: todo` card,
   hand off to **pipeline-impl** for the next card (the same `feat/<feature>` branch/PR accumulates all
   cards). Only when NO `todo`/`in-progress` cards remain (every card is `status: review`) hand off to
   **pipeline-review** — review runs ONCE on the complete feature, never on a partial one.
5. **Fail / budget exhausted** ⇒ on `main`: `attempts++`; `attempts < 3` ⇒ back to `status: todo`
   (re-queue); `attempts >= 3` ⇒ `status: blocked`. **Leave `current.json.stage` unchanged** (impl did
   NOT complete — keep the last completed stage, `task`). Either way **append a `## Attempt N` note to the
   card and your handoff to `journal.md`** (CONTRACT §Run journal — status `failed`/`blocked`, the dead-end
   is part of the run history), **commit both to `main`**, then print the handoff to **pipeline-hunt** with
   the reason (the next run reads only the card).

## Hard rules
- Never touch `spec-paths:` (the frozen spec). Never merge. Only this card's files.
- Code (`impl-paths`/`src`) lives on `feat/<feature>`; card `status` flips commit to `main` (trunk
  authority — never leave card state stranded on the branch). White-box tests in `impl-paths:` are fine;
  the acceptance test stays frozen.
