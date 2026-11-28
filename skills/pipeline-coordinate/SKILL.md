---
name: pipeline-coordinate
description: "Coordinator playbook + headless runner guide — drives the 3-Agent 4-Step pipeline across planner (frontier A), reviewer (independent frontier B), and implementer (coding CLI C) via headless CLI (run-loop.py, zero planner tokens in Phase II), multica issues, or Herdr panes. Never implements, never reviews, never merges. Args: repo, what to coordinate (feature or meta-PR)."
---

# pipeline-coordinate

Automates handoff delivery across the 3 abstract roles (`planner`, `reviewer`, `implementer`)
without violating model independence or human merge authority.

## Hard rules (violating any one voids the run)

1. **Role & model separation.** `model(reviewer) ≠ model(planner)` AND
   `model(reviewer) ≠ model(implementer)`. Concrete models live ONLY in `~/.config/pipeline/`.
2. **Implementer death = STOP.** Crash, timeout, or quota exhaustion stops immediately and reports
   exact git state. Never silently take over coding.
3. **Git + deterministic `verify` are the only truth.** Never trust self-reports; verify commits,
   `forbid-paths`, and `verify[]` exit codes before advancing. Handoffs carry paths, never bodies.
4. **Fail closed to the human.** Malformed verdicts, unexpected journal states, 3rd rejection
   (`>= 3`), or Step 4 `plan_fact|plan_design|env` short-circuits MUST stop. Never merge, never
   force-push trunk.

## Transport 1 (Recommended, single-machine): Headless CLI (`scripts/run-loop.py` / `pipeline-run` skill)

Runs the full 4-step pipeline (`run [--background]`, `resume`, `status`, `logs`, `stop`, `init-config`)
or single-phase commands (`review-plan`, `approve-plan`, `phase2`) with **0 planner tokens in Phase II**
inside an isolated `feat/<feature>` git worktree. Reads `~/.config/pipeline/runners.json` (`argv`
string arrays, `shell=False`, mandatory distinct `model_id` per role; see `skills/pipeline-run/SKILL.md`).

```bash
# Full 4-step end-to-end (foreground or --background):
python3 <skill-base-dir>/scripts/run-loop.py run --repo <repo> --feature <slug> --idea "<req>" [--push] [--background]
python3 <skill-base-dir>/scripts/run-loop.py status|logs|resume|stop --repo <repo> --feature <slug>
# Single-phase entry points:
python3 <skill-base-dir>/scripts/run-loop.py review-plan|approve-plan|phase2 --repo <repo> [--config ~/.config/pipeline/runners.json] [--push]
```

| exit | meaning | next action |
|---|---|---|
| `0` | `review-plan`: passed; `approve-plan`: resumed; `phase2`: headless `ACCEPT` (NOT merged) | `review-plan`/`approve-plan` → `phase2`; `phase2` → interactive `pipeline-review` GO-gate |
| `2` | Breaker hit (`plan_rejects >= 3`, `review_rejects >= 3`, or verify/forbid-paths retry cap) | Human intervention / `pipeline-hunt` |
| `3` | Escalation (`CAUSE: plan_fact \| plan_design \| env`) or `pause_after_plan` | Human/Planner fixes plan or env (or `approve-plan`) |
| `4` | Fail-closed error (impl crash/timeout, model collision, dirty trunk, stale plan, malformed verdict, lock busy) | Inspect stderr + git state |
| `10` | `review-plan` rejected (`plan_rejects < 3`) | Planner revises `plan.md` and re-runs Step 2 |
| `64` | Usage or invalid `runners.json` | Fix CLI args or config |

## Transport 2 & 3: `multica` issues or `herdr` panes

Resolution: invocation selector (`review=multica:<name>`, `impl=multica:<name>`, `review=herdr`,
`impl=herdr`) → `~/.config/pipeline/transport` (read-only) → Herdr default. When any role uses
`multica`, read `transport-multica.md` (base dir) IN FULL before preflight.

**Herdr preflight & verbs:**
1. `herdr pane list` — locate one distinct pane per role (exclude your own pane).
2. `herdr agent explain <pane> --json` — require `.fallback_reason == null` (authoritative).
3. Confirm distinct underlying models from each pane's visible footer (`herdr pane read <pane> --source visible --lines 3`).
4. **send:** `herdr pane run <pane> '<single line>'` ONLY after fresh authoritative+idle sample and
   empty visible input line.
5. **watch:** `python3 <skill-base-dir>/scripts/watch-pane.py <pane>` (frozen exit codes: `0`
   working→idle, `2` blocked, `3` max samples, `4` authority lost, `5` never started, `64` bad env).
   Idle alone never proves delivery; unchanged git after a send STOPs for human inspection.

## Profile A — meta-PR flow (this pipeline repo, no `.pipeline/` state)

1. Write a disposable handoff (scope, allowed/forbidden files, deliverables, done-when).
2. Dispatch implementer; watch; rerun tests and check `git diff --stat` before pushing PR branch.
3. Dispatch reviewer (`pipeline-review` meta-PR mode, head SHA, 2–3 review axes, PR comment only).
   Use an edge-triggered verdict watcher (snapshot latest non-bot comment before dispatch; require
   newer comment naming head SHA + explicit verdict).
4. Relay fixes with typed evidence; 3rd rejection without convergence ⇒ STOP for human.
5. **Merge gate:** on approval, tell the human to reply `go`/`merge`/`confirm` directly in the
   reviewer session/issue. **From arming until merge, coordinator sends NOTHING to the reviewer.**

## Profile B — 4-Step feature flow (target repo with `.pipeline/`)

Requires `.pipeline/<feature>/control.json` (`{"schema_version":1,"mode":"coordinated","merge_gate":"human-direct"}`).
- **Phase I:** planner runs `pipeline-plan`; reviewer runs `pipeline-plan-review` (headless or
  5-field envelope). `ACCEPT`/`ACCEPT WITH NITS` proceeds to Phase II by default; 3rd `REJECT` STOPs.
- **Phase II:** prefer `run-loop.py phase2` on a single machine, or dispatch `pipeline-impl` /
  `pipeline-review` with the 5-field envelope (`repo= branch= feature= expected_seq= expected_commit=`)
  over `multica`/`herdr`. Route strictly on `journal.md` tail transitions.
- **Merge gate:** reviewer arms GO-gate; human sends direct token; coordinator stays silent toward
  reviewer until merge completes and `review→done` lands on trunk.
