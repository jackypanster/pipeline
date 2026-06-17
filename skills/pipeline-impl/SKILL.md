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
   a cold node must read the live status from trunk; never leave a status flip on the branch). Advance
   `current.json.stage` to `impl` on `main`.
3. **goal**: implement inside `impl-paths:` (+ `src/**`) on `feat/<feature>` until the card's `verify:`
   commands all exit 0 (its red test goes green). Loop think→code→check within the turn budget. Only
   code lives on the branch; never touch `spec-paths:`.
4. **Green** ⇒ push `feat/<feature>`, open/update a PR via the forge adapter, and flip the card
   `status: review` **on `main`**. Opening the PR needs the repo's forge token (loaded per CONTRACT
   step 2 from `.env` etc.). If the token is absent, **do NOT fail** — push the branch + set
   `status: review` on `main` anyway, and say in the handoff that the PR must be opened manually (branch
   + base named). Then print the handoff to **pipeline-review**.
5. **Fail / budget exhausted** ⇒ on `main`: `attempts++`; `attempts < 3` ⇒ back to `status: todo`
   (re-queue); `attempts >= 3` ⇒ `status: blocked`. Either way print the handoff to **pipeline-hunt**
   with the reason, and append a `## Attempt N` note to the card (the next run reads only the card).

## Hard rules
- Never touch `spec-paths:` (the frozen spec). Never merge. Only this card's files.
- Code (`impl-paths`/`src`) lives on `feat/<feature>`; card `status` flips commit to `main` (trunk
  authority — never leave card state stranded on the branch). White-box tests in `impl-paths:` are fine;
  the acceptance test stays frozen.
