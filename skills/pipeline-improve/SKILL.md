---
name: pipeline-improve
description: "Meta-command — turn a SKILL-PROPOSAL (a skill gap learned from a real run) into a REVIEWED PR against the pipeline repo. Never edits live/installed skills; never merges its own proposal. Args: the proposal (which skill, what change, why)."
---

# pipeline-improve

Meta-stage: improves the **pipeline repo's own** skills/CONTRACT, gated exactly like any code change.
Operates on the pipeline repo (`github.com/jackypanster/pipeline`), NOT a target project — **so there is
NO `.pipeline/` state here** (no `current.json`, no cards, no `spec-rev`). **This command does NOT run
the feature shim loop** (whose step 3 would STOP on the missing `current.json`); its own steps are below,
and its gate is `pipeline-review` in **meta-PR mode** (CONTRACT §Self-improvement), not the feature
review steps.

## Steps

1. `git pull --rebase` the pipeline repo; read `CONTRACT.md` (esp. §Self-improvement).
2. Take the `SKILL-PROPOSAL` (which skill, what change, why). **Frozen-invariant guard:** if it touches
   the state machine, only-reviewer-merges, the freeze gate, or never-force-push, **STOP** and flag for
   explicit human decision — these are not routinely improvable.
3. **Resolve `improve` slot from `roles.yaml`; verify installed (else STOP).** Invoke **`think`** — it
   validates the proposal is a real improvement (not a weakening) and produces the minimal, additive
   change that preserves every hard rule. `think` reasons and writes NO files; YOU apply the edit (the
   shim owns I/O — same skill delegation as every other stage; improve skips only the feature-state loop
   steps, not the resolve-and-invoke).
4. Create branch `improve/<slug>`. Apply the edit `think` specified to the relevant `skills/*/SKILL.md`
   or `CONTRACT.md`: **minimal, additive, preserve every existing rule.** Agent-first (dense, no filler).
   One proposal = one focused change.
5. Commit + push the branch. Open a PR to the pipeline repo's `main` via the forge adapter. **If the
   edit trips the size-budget axis** (a `skills/*/SKILL.md` whose net growth > 0 AND post-merge length
   > 120 lines), **lead the PR description with the size-budget justification** — why not net-neutral,
   why not in CONTRACT.md / a reference file — so review doesn't have to ask (CONTRACT §Self-improvement). **Do NOT merge.**
6. Hand off to **pipeline-review in meta-PR mode** (CONTRACT §Self-improvement — NOT the feature review
   steps: no cards, no `spec-rev`, no freeze gate, no full-suite gate on this repo): semantic-review the
   skill diff — is it a real improvement (not a weakening/破坏)? does it preserve every hard rule + frozen
   invariant? — then a human confirms the squash-merge to `main`. Improved skill propagates to runtimes
   on their next pull.

## Hard rules

- **NEVER edit a live/installed skill in place; NEVER merge your own proposal.**
- **Additive over destructive** — don't delete a hard rule to "simplify"; flag it for human judgment.
- Skill changes are markdown + git: fully revertible. The review + human-confirm gate is what makes a
  self-improvement safe rather than a silent contract drift.
