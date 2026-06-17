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
3. **Freeze gate (deterministic, run FIRST):** the **two-commit** diff
   `git diff <card.spec-rev> <review-tip> -- <card.spec-paths>`, where `<review-tip>` is the PR head
   (forge: `gh pr view --json headRefOid` / the `gitee-cli` equivalent; no forge: the `feat/<feature>`
   tip). Diff two commits, never the working tree. Non-empty ⇒ the coder edited the frozen spec ⇒
   **reject**: `attempts++`, append the reason to the card, route to pipeline-impl (or pipeline-hunt if
   `attempts >= 3`). Do not proceed to review.
4. Get the change via the **forge adapter** (github→`gh pr diff`; gitee→`gitee-cli pr diff`; else
   `git diff base..branch`). Run **check** for correctness/design issues CI can't see.
5. Write `.pipeline/<feature>/reviews/review-NN.md` (verdict + findings). Commit.
6. **Approved** ⇒ ask the operator to confirm. On confirm: **squash-merge** the `feat/<feature>` PR via
   the forge adapter (delete the merged branch; no local non-PR merges), set the card `status: done` and
   `current.json.stage: done`, commit/push `main`. **Rejected** ⇒ `attempts++`, append required fixes to
   the card, handoff to **pipeline-impl** (or hunt at ≥3).

## Completion checklist (cold bots skip these — do ALL, in order)

Merge is NOT the end. After the human's go and the merge, you MUST, in order:
- [ ] freeze gate ran (`git diff <spec-rev> -- <spec-paths>` — empty before you proceeded)
- [ ] wrote `.pipeline/<feature>/reviews/review-NN.md` (verdict + findings — even one line)
- [ ] every merged card's `status` → `done`
- [ ] set `.pipeline/current.json` `stage: done` (the top-level pointer = most-recently-completed stage
      per CONTRACT; after the merge the feature is done. Cold bots flip the cards but leave `stage` stale
      at an earlier value, misleading the next node)
- [ ] committed + pushed the above to the trunk branch

Observed: cold review bots have TWICE done only the merge and skipped `review-NN.md` + the card→done
flip; one also left `current.json.stage` stale at `task` after completing the review. These are NOT
optional bookkeeping — they are the audit contract. A merge without them is an incomplete review.

## Hard rules
- The human's "go" is your authorization to merge — never merge without it.
- Never force-push; deleting the task's own branch on merge is the only deletion allowed.
- CI-green / freeze-pass is necessary, not sufficient — the semantic review still gates.
- **Merge with no `review-NN.md` written AND no card→done flip = review NOT complete; not `done`.**
