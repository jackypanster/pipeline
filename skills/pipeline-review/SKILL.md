---
name: pipeline-review
description: "Pipeline stage 5 — semantic review of a card's diff/PR, enforce the spec freeze, and merge after a human confirm. Wraps the check skill. Only this command merges. Use after pipeline-impl. Args: repo, base, branch, optional pr."
---

# pipeline-review

Stage 5. Follow the **shim loop in CONTRACT.md** with slot = `review`. **This is the only command
that merges, and only after an explicit human confirm.**

**Skill:** `review` slot resolves to `check` — semantic review of the diff. The forge adapter and
the freeze gate are YOUR I/O, not check's.

## Steps
1. `git pull --rebase`. Read `current.json` + the `status: review` card.
2. Resolve `review` slot; verify installed (else STOP).
3. **Freeze gate (deterministic, run FIRST):** `git diff <card.spec-rev> -- <card.spec-paths>`.
   Non-empty ⇒ the coder edited the frozen spec ⇒ **reject**: `attempts++`, append the reason to the
   card, route to pipeline-impl (or pipeline-hunt if `attempts >= 3`). Do not proceed to review.
4. Get the change via the **forge adapter** (github→`gh pr diff`; gitee→`gitee-cli pr diff`; else
   `git diff base..branch`). Run **check** for correctness/design issues CI can't see.
5. Write `.pipeline/<feature>/reviews/review-NN.md` (verdict + findings). Commit.
6. **Approved** ⇒ ask the operator to confirm. On confirm: merge (forge squash + delete the task
   branch, or local squash-merge), set the card `status: done`, commit/push `main`. **Rejected** ⇒
   `attempts++`, append required fixes to the card, handoff to **pipeline-impl** (or hunt at ≥3).

## Hard rules
- The human's "go" is your authorization to merge — never merge without it.
- Never force-push; deleting the task's own branch on merge is the only deletion allowed.
- CI-green / freeze-pass is necessary, not sufficient — the semantic review still gates.
