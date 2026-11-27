---
name: pipeline-coordinate
description: "Coordinator playbook + headless runner guide — drives the 3-Agent 4-Step pipeline across planner (frontier A), reviewer (independent frontier B), and implementer (coding CLI C) via headless CLI (run-loop.py, zero planner tokens in Phase II), multica issues, or Herdr panes. Never implements, never reviews, never merges. Args: repo, what to coordinate (feature or meta-PR)."
---

# pipeline-coordinate — 3-Agent 4-Step coordination playbook

This playbook lets a cold session (or headless CLI runner) automate handoff TYPING across the 3
roles without violating model independence or human merge authority.

**Founding invariant (3 roles, independent reviewer):** `planner` (Step 1), `reviewer` (Step 2 &
Step 4), and `implementer` (Step 3) are abstract roles. `model(reviewer) ≠ model(planner)` AND
`model(reviewer) ≠ model(implementer)` (3 distinct model families recommended). Never hardcode a
specific vendor/model into repo files — concrete bindings live in operator-local config
(`~/.config/pipeline/runners.json` or `~/.config/pipeline/transport`).

## Hard rules (violating any one voids the run)

1. **Role & model separation.** Planner plans only; implementer codes+tests only; reviewer reviews
   and (on direct human GO token) merges only. Any collapse of reviewer onto planner or implementer
   VOIDS the run.
2. **Implementer death = STOP.** If the implementer dies (quota, crash, timeout, blocked), STOP and
   report exact state (commits, verify status, last review). Never silently take over coding.
3. **Git + deterministic `verify` are the only truth.** Never trust an agent self-report; verify
   commits, `forbid-paths`, and `verify:` exit codes before advancing. Never paste artifact bodies
   across transports — git is the bus, handoffs carry paths.
4. **Fail closed to the human.** Malformed verdicts, unexpected journal states, 3rd rejection
   (`>= 3`), or Step 4 `plan_fact|plan_design|env` short-circuits MUST stop for the human. Never
   merge, never force-push trunk.

## Transport 1 (Recommended for single-machine): Headless CLI runner (`scripts/run-loop.py`)

**Zero planner tokens in Phase II.** Once Step 2 (`plan-review`) approves `plan.md`, the frontier
planner session exits; `run-loop.py` runs `Step 3 (implementer CLI) ↔ deterministic verify ↔ Step 4
(reviewer CLI)` synchronously via `subprocess.run(argv, shell=False)` in an isolated `feat/<feature>`
git worktree.

### Operator-local config (`~/.config/pipeline/runners.json`, never committed)

```json
{
  "implementer": {
    "model_id": "<impl-model-id>",
    "argv": ["<impl-cli>", "-p", "--no-session", "--no-mcp", "{prompt}"],
    "timeout_s": 1800
  },
  "reviewer": {
    "model_id": "<reviewer-model-id>",
    "argv": ["<reviewer-cli>", "exec", "-s", "read-only", "-"],
    "prompt_via": "stdin",
    "timeout_s": 600
  }
}
```

- `argv` MUST be a JSON array of strings (`shell=False`). Use `"{prompt}"` / `"{prompt_file}"` in
  `argv`, or `"prompt_via": "stdin"`.
- `model_id` MUST be non-empty for both roles; `run-loop.py` refuses to start if
  `reviewer.model_id == implementer.model_id` or `reviewer.model_id == planner_model`.

### Commands & frozen exit codes

```bash
# Step 2 (optional headless single-shot plan review from Phase I):
python3 <skill-base-dir>/scripts/run-loop.py review-plan \
  --repo <repo> --config ~/.config/pipeline/runners.json [--push]

# Only when pause_after_plan=true paused Step 2 — explicit human approval to enter Phase II:
python3 <skill-base-dir>/scripts/run-loop.py approve-plan --repo <repo> [--push]

# Phase II (Step 3 Impl ↔ deterministic verify ↔ Step 4 Code Review, 0 planner tokens):
python3 <skill-base-dir>/scripts/run-loop.py phase2 \
  --repo <repo> --config ~/.config/pipeline/runners.json [--push]
```

| exit | meaning | next action |
|---|---|---|
| `0` | `review-plan`: `ACCEPT`/`ACCEPT WITH NITS`; `approve-plan`: resumed; `phase2`: headless `ACCEPT` (NOT merged) | `review-plan`/`approve-plan`: run `phase2`; `phase2`: open interactive `pipeline-review` to verify SHA & arm human-direct GO-gate |
| `2` | Circuit breaker hit (`plan_rejects >= 3`, `review_rejects >= 3`, or verify/forbid-paths retry cap) | Human intervention / `pipeline-hunt` |
| `3` | Short-circuit escalation (`CAUSE: plan_fact \| plan_design \| env`) or `pause_after_plan` | Human / Planner fixes plan/env (or runs `approve-plan`) |
| `4` | Fail-closed error (implementer crash/timeout, model collision, dirty trunk, stale plan, malformed verdict, lock busy) | Inspect stderr + git state |
| `10` | `review-plan` rejected with `plan_rejects < 3` | Planner revises `plan.md` and re-runs Step 2 |
| `64` | Usage or invalid `runners.json` config | Fix CLI args or `runners.json` |

## Transport 2 & 3: `multica` issues or `herdr` panes

**Resolution:** invocation selector (`review=multica:<name>`, `impl=multica:<name>`,
`review=herdr`, `impl=herdr`) → `~/.config/pipeline/transport` (read-only) → Herdr default. If
either role uses `multica`, read `transport-multica.md` (base dir) IN FULL before preflight.

### Herdr preflight & verbs

1. `herdr pane list` — locate distinct panes for each role (exclude your own pane).
2. `herdr agent explain <pane> --json` — require `.fallback_reason == null` (authoritative).
3. Verify distinct underlying models via pane footer (`herdr pane read <pane> --source visible --lines 3`).
4. **send:** `herdr pane run <pane> '<single line>'` ONLY after fresh authoritative+idle sample and
   empty visible input line. (Claude TUI slash-popup recovery: only if visible input line equals the
   exact dispatched slash line byte-for-byte, send `herdr pane run <pane> ''`.)
5. **watch:** `python3 <skill-base-dir>/scripts/watch-pane.py <pane>` (stdlib, frozen exit codes:
   `0` working→idle, `2` blocked, `3` max samples, `4` authority lost, `5` never started, `64` bad
   env). Idle alone never proves completion — verify git commits / journal tail.
6. **Redelivery is NOT safe:** unchanged git after a send ALWAYS stops for human inspection.

## Profile A — meta-PR flow (toolchain repos, no `.pipeline/` state)

Same-repo PRs on `jackypanster/pipeline` or sibling toolchain repos (`pipeline-driver` /
`-dashboard` / `-dispatch`) only:
1. Write disposable handoff (context, allowed/forbidden files, deliverables, done-when, commit
   locally / push delivery ref `impl/<branch>` on multica).
2. Dispatch implementer; watch; verify tests + `git diff --stat` yourself before pushing PR branch.
3. Dispatch reviewer (`pipeline-review` meta-PR mode, head SHA, 2–3 review axes, PR comment only).
   Arm edge-triggered verdict watcher (snapshot latest non-bot comment before dispatch; require
   newer comment naming head SHA + explicit decision).
4. Relay fixes with typed old-head evidence (behavioral ⇒ regression test; doc ⇒ exact citation).
   3rd rejection without convergence ⇒ STOP for human.
5. **Merge gate:** on approval, tell human to send `go`/`merge`/`confirm` directly in the reviewer
   session/issue. **From arming until merge, coordinator MUST send NOTHING to the reviewer.**

## Profile B — 4-Step feature flow (target repo with `.pipeline/`)

Requires `.pipeline/<feature>/control.json` (`{"schema_version":1,"mode":"coordinated","merge_gate":"human-direct"}`).
- **Phase I (`plan ↔ plan-review`):** planner writes `plan.md`; reviewer runs `pipeline-plan-review`
  (either headless via `run-loop.py review-plan` or dispatched with the 5-field envelope). On
  `ACCEPT`/`ACCEPT WITH NITS`, proceeds directly to Phase II (unless `pause_after_plan: true`); on
  3rd `REJECT`, STOP for human.
- **Phase II (`impl ↔ review`):** prefer `run-loop.py phase2` (0 planner tokens) on single machine,
  or dispatch `pipeline-impl` / `pipeline-review` with the 5-field envelope (`repo= branch= feature=
  expected_seq= expected_commit=`) over `multica`/`herdr`. Route strictly on `journal.md` tail
  transitions (CONTRACT §Handoff block).
- **Merge gate:** identical to Profile A — reviewer arms GO-gate; human sends direct token;
  coordinator stays silent toward reviewer until PR merges and `review→done` lands on trunk.

## What this playbook is not

No daemon, no DB, no auto-merge, no unattended parallel features. Sole timed carve-out: duty mode
(target repo `.pipeline/duty-tick.md`, read-only toward target repo, human merge gate unchanged).
