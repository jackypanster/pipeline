---
name: pipeline-review
description: "Pipeline stage 5 — semantic review of a card's diff/PR, enforce the spec freeze, and merge after a human confirm. Wraps the check skill. Only this command merges. Use after pipeline-impl. Args: repo, base, branch, optional pr."
---

# pipeline-review

Stage 5. Follow the **shim loop in CONTRACT.md** with slot = `review`. **This is the only command
that merges, and only after an explicit human confirm.**

**Skill:** `review` slot resolves to `check` — semantic review of the diff. The forge adapter and
the freeze gate are YOUR I/O, not check's.

## Meta-PR mode (pipeline self-improvement) — decide this BEFORE step 1

If the PR is a **`pipeline-improve` proposal** — a diff to *this pipeline repo's* own `skills/*` /
`CONTRACT.md`, with **no `.pipeline/` feature state** (no `current.json`, cards, or `spec-rev`) — you are
in **meta-PR mode**: **SKIP steps 1, 3, the cards/freeze-gate, and the final full-suite gate** (none of
that exists here). Do ONLY: (a) `check` the skill diff — is it a **real improvement, not a weakening**?
does it **preserve every existing hard rule + the frozen invariants**? (b) write the verdict as a PR
comment; (c) on the human's explicit confirm, **squash-merge**. Everything else still holds:
only-reviewer-merges, human-confirm-before-merge, never-force-push. The feature steps below are for a
**target-repo feature PR**; do not run them against a skill PR.

## Steps
1. `git pull --rebase`. Read `current.json` + **all of the feature's cards** (this stage runs on a
   COMPLETE feature — expect every card `status: review`; see the pre-merge guard in step 6).
2. Resolve `review` slot; verify installed (else STOP).
3. **Freeze gate (deterministic, run FIRST):** the **two-commit** diff
   `git diff <card.spec-rev> <review-tip> -- <card.spec-paths>`, where `<review-tip>` is the PR head
   (forge: `gh pr view --json headRefOid` / the `gitee-cli` equivalent; no forge: the `feat/<feature>`
   tip). Diff two commits, never the working tree. Non-empty ⇒ the coder edited the frozen spec ⇒
   **reject**: `attempts++`, append the reason to the card **+ a `journal.md` entry** (CONTRACT §Run
   journal — `status=failed`, the freeze-violation is run history), and **flip that card
   `status: todo`** (`attempts >= 3` ⇒ `blocked` instead) so `pipeline-impl` — which picks the oldest
   `todo` — has an actionable retry target, and **name that card in the handoff** so impl re-picks
   exactly it; **commit all**; route to pipeline-impl (or pipeline-hunt if `blocked`). Do not proceed to
   review.
4. Get the change via the **forge adapter** (github→`gh pr diff`; gitee→`gitee-cli pr diff`; else
   `git diff base..branch`). Run **check** for correctness/design issues CI can't see.
5. Write `.pipeline/<feature>/reviews/review-NN.md` (verdict + findings) **and append a `journal.md`
   entry** (CONTRACT §Run journal — transition `…→review`, status **`completed`** [the run-status enum
   `completed|failed|blocked`, NOT a stage name]; body: "review verdict written; awaiting human confirm").
   **Commit both together** — so this durable commit is explained by the journal, never orphaned (the
   merge→done or reject disposition appends its own later entry).
6. **Approved** ⇒ ask the operator to confirm. **Pre-merge guard (multi-card features):** every card in
   the feature must be `status: review` — if any is still `todo`/`in-progress`, the feature is INCOMPLETE;
   do NOT merge or set `done`, hand back to **pipeline-impl** for the remaining card(s). **Final full-suite
   gate (CONTRACT §State authority):** card `verify`s are card-scoped, so they never proved cross-card
   integration — run **`current.json.full-verify`** (the exact whole-suite command task recorded; if it is
   absent, STOP and ask the operator — do NOT guess by dropping a filter) once on the `feat/<feature>`
   branch HEAD (it carries all frozen tests inherited from trunk + every card's code). **GREEN required**;
   red ⇒ cross-card integration broke ⇒ do NOT merge. Flip a card back **only if** the failing test(s)/diff
   attribute the break to a specific card (then `attempts++`, that card → `todo`/`blocked`, name it in the
   handoff, route impl/hunt). If no single card owns the failure, write a **feature-level integration
   incident report** `.pipeline/<feature>/reviews/integration-NN.md` (your OWN write-set — body = the
   failing `full-verify` output + which tests broke + "cross-card integration, no single owner"), **append
   a `journal.md` entry** (CONTRACT §Run journal — `status=blocked`, transition `review→hunt`, output =
   the report path), **commit both**, and route **pipeline-hunt** to that report (name its path in the
   handoff). Do NOT create a `tasks/` card for it — a lingering `blocked` card would deadlock every future
   merge guard; the report is evidence, not an impl card. Never blind-flip a real card. On confirm (cards
   all `review` AND suite green):
   **squash-merge** the `feat/<feature>` PR via the forge adapter (delete the merged branch; no local
   non-PR merges), set **every** card in the feature `status: done` and `current.json.stage: done` (only
   now is the whole feature done), commit/push `main`. **Rejected** ⇒ `attempts++`, append required fixes
   to the **offending** card **+ a `journal.md` entry** (CONTRACT §Run journal — `status=failed`, the
   rejection is run history), and **flip that card `status: todo`** (`attempts >= 3` ⇒ `blocked`) — on a
   multi-card feature every card is `review`, so without this flip impl has no `todo` to pick; **name the
   card in the handoff** so impl re-picks exactly it; **commit**; then hand off to **pipeline-impl** (or
   hunt at ≥3).

## Completion checklist (cold bots skip these — do ALL, in order)

Merge is NOT the end. After the human's go and the merge, you MUST, in order:
- [ ] freeze gate ran (the two-commit `git diff <spec-rev> <review-tip> -- <spec-paths>`, review-tip = PR head — empty before you proceeded)
- [ ] **final full-suite gate ran GREEN** on the `feat/<feature>` branch HEAD before the merge (card `verify`s are card-scoped — this is the only cross-card integration check)
- [ ] wrote `.pipeline/<feature>/reviews/review-NN.md` (verdict + findings — even one line)
- [ ] every merged card's `status` → `done`
- [ ] set `.pipeline/current.json` `stage: done` — **only when EVERY card in the feature is `done`**
      (multi-card guard, step 6); the top-level pointer = most-recently-completed stage per CONTRACT.
      Cold bots flip the cards but leave `stage` stale at an earlier value, misleading the next node
- [ ] **appended the journal entry** to `.pipeline/<feature>/journal.md` (CONTRACT §Run journal —
      `review→done`, the feature's final entry; it closes the auditable run)
- [ ] committed + pushed the above to the trunk branch

Observed: cold review bots have TWICE done only the merge and skipped `review-NN.md` + the card→done
flip; one also left `current.json.stage` stale at `task` after completing the review. These are NOT
optional bookkeeping — they are the audit contract. A merge without them is an incomplete review.

## Hard rules
- The human's "go" is your authorization to merge — never merge without it.
- Never force-push; deleting the merged `feat/<feature>` branch on merge is the only deletion allowed.
- CI-green / freeze-pass is necessary, not sufficient — the semantic review still gates.
- **Merge with no `review-NN.md` written AND no card→done flip = review NOT complete; not `done`.**
- **The verdict you post to chat must be plain text the human can copy** — short bullets, **NO markdown
  tables, NO ATX headings (`#`)**. Some chat bridges render tables/rich markdown as an *image*, which the
  human cannot select, copy, or relay to the next node (only screenshot — lossy). Lead with the verdict
  (approve / changes-requested) + each finding as `file:line — one line`; put any detailed table only in
  `review-NN.md` / the forge PR comment (git is the durable record), never in the chat verdict.
