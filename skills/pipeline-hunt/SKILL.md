---
name: pipeline-hunt
description: "Pipeline escalation — root-cause a blocked target before re-queue. Wraps the hunt skill. Entry point for any card that hit attempts>=3 (blocked) / repeated review rejection, or a feature-level integration incident report. Use instead of blind retry. Args: repo, branch, target (a blocked card-id OR a reviews/integration-NN.md report path)."
---

# pipeline-hunt

Escalation stage (not on the happy path). Follow the **shim loop in CONTRACT.md** with slot = `hunt`.
A `blocked` **card** — OR a feature-level **integration incident report** (`reviews/integration-NN.md`)
— routes here; **never blind-retry**. (Both are "targets"; see step 1.)

**Skill:** `hunt` slot resolves to `hunt` — systematic root-cause (confirm cause before any fix,
especially "used to work / can't fix it after N tries").

## Steps

1. `git pull --rebase`. Read `current.json` + your target. Usual target = the `blocked` **card**
   (every `## Attempt N` note + the latest `verify:` failure / review rejection). **Alternative target:
   a feature-level integration incident report** `reviews/integration-NN.md` that `pipeline-review`
   routed for a cross-card full-suite failure with no single owner — it is evidence (failing-suite
   output), NOT a card, and there is no `tasks/` card to flip (see the integration branch in step 3).
2. Resolve `hunt` slot; verify installed (else STOP).
3. **hunt** to confirm the ROOT CAUSE (not symptoms). Classify it:
   - **Card too big / not atomic** ⇒ re-split: hand back to **pipeline-task** to break it down.
   - **Spec/red test wrong** ⇒ the test itself is the bug ⇒ hand back to **pipeline-task** to fix
     the spec (architect owns tests; coder cannot) — **name the offending card/spec target in the
     handoff** so task re-freezes only it and preserves sibling state (CONTRACT §Test ownership re-freeze).
   - **Environment / dependency** ⇒ name the fix for the operator; once fixed, reset the card to
     `status: todo`, `attempts: 0`.
   - **Genuinely hard but well-scoped** ⇒ append the root-cause findings to the card, reset to
     `status: todo` **and `attempts: 0`** (same as the environment branch) so the next impl run starts
     informed with a fresh budget — hunt is the deliberate human-relayed diagnosis, NOT a blind retry,
     and the new findings make this a genuinely new attempt; without the reset a requeued card re-blocks
     on its first fail (`attempts` was already at the `>= 3` threshold). The card is the only memory.
   - **Cross-card integration failure** (target = a `reviews/integration-NN.md` incident report) ⇒ the
     cards each pass alone but break together. Diagnose the interaction, append findings to the report,
     and hand back to **pipeline-task via its append-card mode** to author a proper fix — a new card with
     its own frozen test (preserving the existing cards' state), or re-freeze the implicated cards. The
     integration gap needs a real spec, not a blind re-queue; the task→impl handoff rebases the feature
     branch onto trunk (CONTRACT §State authority). The
     report is evidence under `reviews/`, **not** a `tasks/` card — nothing lingers to block the next
     merge guard; the new fix card(s) carry the work forward through the normal flow.
4. Write the root-cause findings into **your target** — if it is a blocked **card**, update/reset it per
   the step-3 classification (e.g. `status: todo` + `attempts: 0`); if it is a **`reviews/integration-NN.md`
   report**, append the findings there and **do NOT flip any card status** (there is no card). Either way
   **append your handoff to `journal.md`** (CONTRACT §Run journal). Commit both **once**.
5. **Print the handoff** to whichever command the classification chose (task / impl) (already journaled
   in step 4), with the cause + the decision in the `do:` line.

## Hard rules

- Confirm cause before proposing any fix. No speculative re-queue.
- You diagnose and re-route; you do not implement or merge.
