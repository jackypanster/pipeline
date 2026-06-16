---
name: pipeline-hunt
description: "Pipeline escalation — root-cause a blocked card before re-queue. Wraps the hunt skill. Entry point for any card that hit attempts>=3 (blocked) or repeated review rejection. Use instead of blind retry. Args: repo, branch, card-id."
---

# pipeline-hunt

Escalation stage (not on the happy path). Follow the **shim loop in CONTRACT.md** with slot = `hunt`.
A `blocked` card routes here — **never blind-retry a blocked card**.

**Skill:** `hunt` slot resolves to `hunt` — systematic root-cause (confirm cause before any fix,
especially "used to work / can't fix it after N tries").

## Steps
1. `git pull --rebase`. Read `current.json` + the `blocked` card, including every `## Attempt N`
   note and the latest `verify:` failure / review rejection.
2. Resolve `hunt` slot; verify installed (else STOP).
3. **hunt** to confirm the ROOT CAUSE (not symptoms). Classify it:
   - **Card too big / not atomic** ⇒ re-split: hand back to **pipeline-task** to break it down.
   - **Spec/red test wrong** ⇒ the test itself is the bug ⇒ hand back to **pipeline-task** to fix
     the spec (architect owns tests; coder cannot).
   - **Environment / dependency** ⇒ name the fix for the operator; once fixed, reset the card to
     `status: todo`, `attempts: 0`.
   - **Genuinely hard but well-scoped** ⇒ append the root-cause findings to the card, reset to
     `status: todo` so the next impl run starts informed (the card is the only memory).
4. Write the root-cause findings into the card. Commit.
5. Print the handoff to whichever command the classification chose (task / impl), with the cause +
   the decision in the `do:` line.

## Hard rules
- Confirm cause before proposing any fix. No speculative re-queue.
- You diagnose and re-route; you do not implement or merge.
