---
name: pipeline-improve
description: "Meta-command — turn a SKILL-PROPOSAL (a skill gap learned from a real run) into a REVIEWED PR against the pipeline repo. Never edits live/installed skills; never merges its own proposal. Args: the proposal (which skill, what change, why)."
---

# pipeline-improve

Meta-stage: improves the **pipeline repo's own** skills/CONTRACT, gated exactly like any code change.
Operates on the pipeline repo (`github.com/jackypanster/pipeline`), NOT a target project. Follow the
shim loop in CONTRACT.md (slot = `improve`), with these specifics.

## Steps
1. `git pull --rebase` the pipeline repo; read `CONTRACT.md` (esp. §Self-improvement).
2. Take the `SKILL-PROPOSAL` (which skill, what change, why). **Frozen-invariant guard:** if it touches
   the state machine, only-reviewer-merges, the freeze gate, or never-force-push, **STOP** and flag for
   explicit human decision — these are not routinely improvable.
3. Create branch `improve/<slug>`. Apply the edit to the relevant `skills/*/SKILL.md` or `CONTRACT.md`:
   **minimal, additive, preserve every existing rule.** Agent-first (dense, no filler). One proposal =
   one focused change.
4. Commit + push the branch. Open a PR to the pipeline repo's `main` via the forge adapter. **Do NOT merge.**
5. Hand off to **pipeline-review** (per CONTRACT §handoff): semantic-review the skill diff — is it a
   real improvement (not a weakening/破坏)? does it preserve all hard rules + frozen invariants? — then a
   human confirms the merge to `main`. Improved skill propagates to runtimes on their next pull.

## Hard rules
- **NEVER edit a live/installed skill in place; NEVER merge your own proposal.**
- **Additive over destructive** — don't delete a hard rule to "simplify"; flag it for human judgment.
- Skill changes are markdown + git: fully revertible. The review + human-confirm gate is what makes a
  self-improvement safe rather than a silent contract drift.
