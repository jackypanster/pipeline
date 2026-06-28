---
name: pipeline-impl
description: "Pipeline stage 4 — implement one task card: make its frozen red test green, open a PR. Wraps the goal skill (think→code→check loop). Writes ZERO spec tests. Use after pipeline-task. Args: repo, branch, optional card-id."
---

# pipeline-impl

Stage 4. Follow the **shim loop in CONTRACT.md** with slot = `impl`.

**Skill:** `impl` slot resolves to **`goal-driven-implementation`** — an autonomous
think→design-tests→code→check loop. The pipeline is runtime-agnostic: bind whichever implementation
your agent provides in `roles.yaml` — Hermes's `goal-driven-implementation` (drives its `/goal` loop),
Claude's `goal-driven-impl-claude` twin, or any equivalent. Note the slot value in `roles.yaml` is the
real skill name `goal-driven-implementation`, not the bare word "goal". It writes
**white-box tests in `impl-paths:` (allowed)**; it must NOT create or edit anything under
`spec-paths:`. Constrain it accordingly (a `/subgoal` "do not touch spec-paths; do not author
acceptance tests" is the cheap seam if needed).

## Steps

1. `git pull --rebase`. Read `current.json`. Pick the **oldest** `status: todo` card (or the given
   card-id). Idempotency: if `feat/<feature>` already has an open PR and the card reads
   `status: review`, skip — already in flight.
2. Resolve `impl` slot; verify installed (else STOP). **Create or reconcile the feature branch**
   **`feat/<feature>`** (per CONTRACT §State authority — one branch per feature, NOT per card):
   - **New branch:** cut it from trunk (`main`) — it inherits the current frozen specs.
   - **Existing branch (feature in flight):** if trunk's spec advanced since it was cut (a re-freeze or
     append-card landed a new `spec-rev` the branch lacks), **rebase it onto trunk and force-push**
     (`git fetch origin && git rebase origin/main && git push --force-with-lease`) so it carries the
     current frozen tests. Without this, review's freeze gate diffs the new `spec-rev` against the stale
     branch and falsely rejects. This force-push is the **sanctioned exception** (your own in-flight
     branch, never trunk — CONTRACT §State machine scope). Resolve any rebase conflict via the goal loop
     (the spec changed; the code must adapt).
   Then flip the card `status: in-progress` and commit it to **`main`** (card status is trunk-authoritative metadata —
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
5. **Fail / budget exhausted** ⇒ on `main`: `attempts++`, then **decide the disposition by the retry
   budget BEFORE committing** (CONTRACT state machine):
   - **`attempts < 3`** ⇒ `status: todo` (re-queue), journal `status=failed`, next = **pipeline-impl** —
     the `## Attempt N` note is the informed-retry context (NOT a blind retry; this is the intended retry
     budget; hunt is for blocked cards only, so do NOT route here).
   - **`attempts >= 3`** ⇒ `status: blocked`, journal `status=blocked`, next = **pipeline-hunt**
     (root-cause before any re-queue — never blind retry).
   **Leave `current.json.stage` unchanged** (impl did NOT complete — keep `task`). Append the
   `## Attempt N` note + the selected handoff to `journal.md`, then **commit the card (`attempts` + the
   decided `status` + note) + `journal.md` together to `main` in ONE commit** — so a cold node never
   reads a half-updated state. Then print the handoff to the routed command (the next run reads only the
   card).

## Hard rules

- Never touch `spec-paths:` (the frozen spec). Never merge. Only this card's files.
- Code (`impl-paths`/`src`) lives on `feat/<feature>`; card `status` flips commit to `main` (trunk
  authority — never leave card state stranded on the branch). White-box tests in `impl-paths:` are fine;
  the acceptance test stays frozen.
