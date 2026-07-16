---
name: pipeline-review
description: "Pipeline stage 5 — semantic review of a card's diff/PR, enforce the spec freeze, and merge after a human confirm. Wraps the check skill. Only this command merges. Use after pipeline-impl. Args: repo, base, branch, optional pr."
---

# pipeline-review

Stage 5. Follow the **shim loop in CONTRACT.md** with slot = `review`. **This is the only command
that merges, and only after an explicit human confirm.**

**Coordinated dispatch guard:** if your invocation carries a dispatch envelope
(`repo= branch= feature= expected_seq= expected_commit=`), run CONTRACT §Coordinated mode's pre-write
stale-dispatch guard immediately after step 1, BEFORE any write; any mismatch ⇒ print
`STALE_DISPATCH <field>` and STOP (zero writes). Preserve `control.json`; never modify it.

**Skill:** `review` slot resolves to `check` — semantic review of the diff. The forge adapter and
the freeze gate are YOUR I/O, not check's.

## Meta-PR mode (pipeline self-improvement) — decide this BEFORE step 1

If the PR is a **toolchain meta-PR** — a diff to *this pipeline repo's* **own files** (`skills/*`,
`CONTRACT.md`, and equally `DESIGN.md` / `README.md` / any other file of this repo), **or to a sibling
toolchain repo** (`pipeline-driver` / `pipeline-dashboard` / `pipeline-dispatch`), in each case with
**no `.pipeline/` feature state** (no `current.json`, cards, or `spec-rev`) — you are
in **meta-PR mode**: **SKIP steps 1, 3, the cards/freeze-gate, and the final full-suite gate** (none of
that exists here). Do ONLY: (a) `check` the diff — is it a **real improvement, not a weakening**?
does it **preserve every existing hard rule + the frozen invariants** (for a sibling repo: its own
documented hard rules and guarantees)? (b) write the verdict as a PR
comment; (c) on the human's explicit confirm via the same self-terminating exact-token GO-gate (step 6 —
direct operator, same reviewer session), **squash-merge**. Everything else still holds:
only-reviewer-merges, human-confirm-before-merge, never-force-push. The feature steps below are for a
**target-repo feature PR**; do not run them against a toolchain meta-PR.

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
   review. **Coordinated mode:** this freeze rejection funnels through the SAME atomic disposition as
   step 5 — the single commit MUST also include `reviews/review-NN.md` (verdict: freeze violation +
   the offending `spec-paths` diff summary) alongside the card flip and the `review→impl · failed`
   (or `review→hunt · blocked`) journal entry, so the watcher observes one routable outcome — never a
   card/journal commit without its review artifact.
4. Get the change via the **forge adapter** (github→`gh pr diff`; gitee→`gitee-cli pr diff`; else
   `git diff base..branch`). Run **check** for correctness/design issues CI can't see.
5. Write `.pipeline/<feature>/reviews/review-NN.md` (verdict + findings) **and append a `journal.md`
   entry** (CONTRACT §Run journal — transition `…→review`, status **`completed`** [the run-status enum
   `completed|failed|blocked`, NOT a stage name]; body: "review verdict written; awaiting human confirm").
   **Commit both together** — so this durable commit is explained by the journal, never orphaned (the
   merge→done or reject disposition appends its own later entry).

   **Coordinated mode is stricter (CONTRACT §Coordinated mode · atomic review outcome):** when the
   feature's `control.json` says `mode: coordinated`, the verdict and its disposition are ONE commit —
   never the two-step above (an intermediate "verdict written; disposition follows" commit is a state
   the coordinator can observe but not route). **Approved** ⇒ FIRST run every step-6 pre-merge guard —
   the every-card-is-`review` completeness guard AND the final full-suite gate (GREEN on the
   `feat/<feature>` HEAD, via `current.json.full-verify`) — and only when ALL pass, publish one
   commit: `review-NN.md` + a `review→review · completed` journal entry whose handoff FIRST line
   after `>>> NEXT` is exactly
   `Await human-direct merge confirmation in this reviewer session.` — then arm the GO-gate (step 6)
   and STOP; the merge→done entry rides the later merge commit as usual. The marker promises
   merge-ready-but-for-the-human-token: publishing it over an incomplete or red feature is a contract
   violation (the watcher would enter `WAITING_HUMAN_MERGE` on a feature that must not merge). If a
   guard fails, the outcome is NOT approved — emit the matching rejection form instead.
   **Changes requested, single-owner** ⇒ one commit: `review-NN.md` + the offending card's
   `status`/`attempts` flip + the `review→impl · failed` (name exactly that one card in the handoff)
   or `review→hunt · blocked` (at `attempts >= 3`) journal entry. **Cross-card integration failure,
   no single owner** ⇒ one commit: `review-NN.md` + `reviews/integration-NN.md` + the
   `review→hunt · blocked` journal entry (output = the report path) — NO card is mutated (never
   blind-flip a real card; the report is the hunt target).
6. **Approved** ⇒ do NOT merge yet: end your turn at a self-terminating, fail-closed **GO-gate**. After
   the pre-merge guards below pass, print an unmistakable prompt the operator acts on **in YOUR
   terminal** — e.g. `APPROVED — reply IN THIS session with a message whose ENTIRE trimmed text is
   exactly 'go' (or 'merge'/'confirm') to squash-merge; anything else = no merge.` — then STOP.
   **Consume the gate ONLY when** the operator's NEXT message **in THIS SAME reviewer session**, trimmed,
   equals EXACTLY one allowed token — whole message, no other content (`go do not merge` / `go; …` do NOT
   qualify). ANY other input — extra content, a lost/different session, a **coordinator-relayed/forwarded
   token** (indistinguishable from automation — NOT a human confirm), or a non-token reply — disarms the
   gate: no merge, re-review. Only a DIRECT operator message in the emitting session consumes it. This
   removes the coordinator poll-and-relay hop while keeping the confirm authentically human. (Scope: this
   hardens confirm AUTHENTICITY only; guaranteeing merged head/base == reviewed head/base against an
   approve-then-push is a separate, pre-existing concern, OUT OF SCOPE here.) **Pre-merge guard (multi-card features):** every card in
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

- Merge ONLY by consuming the armed GO-gate (step 6) with a **direct** operator message **in the same
  reviewer session that emitted it**, whose ENTIRE trimmed content is exactly one allowed token
  (`go`/`merge`/`confirm`) — NEVER a first-token/substring match, NEVER a relayed/forwarded token
  (indistinguishable from automation — not a human confirm), NEVER inferred from arbitrary text; a lost
  session or any non-token reply disarms → re-review. (This PR hardens confirm authenticity only; head/
  base merge atomicity vs an approve-then-push is a separate, pre-existing open item.)
- Never force-push trunk/shared refs (review never force-pushes at all; only `pipeline-impl` rebase-force-pushes its OWN in-flight `feat/<feature>` branch — CONTRACT §State machine scope). Deleting the merged `feat/<feature>` branch on merge is the only deletion allowed.
- CI-green / freeze-pass is necessary, not sufficient — the semantic review still gates.
- **Merge with no `review-NN.md` written AND no card→done flip = review NOT complete; not `done`.**
- **The verdict you post to chat must be plain text the human can copy** — short bullets, **NO markdown
  tables, NO ATX headings (`#`)**. Some chat bridges render tables/rich markdown as an *image*, which the
  human cannot select, copy, or relay to the next node (only screenshot — lossy). Lead with the verdict
  (approve / changes-requested) + each finding as `file:line — one line`; put any detailed table only in
  `review-NN.md` / the forge PR comment (git is the durable record), never in the chat verdict.
