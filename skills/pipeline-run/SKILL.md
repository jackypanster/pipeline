---
name: pipeline-run
description: "Agent-native entry skill for the 3-Agent 4-Step development workflow (Planner -> Plan Review -> Implementer -> Code Review) running continuously in background or foreground with zero manual copy-paste. Any model/CLI can fill any of the 3 roles via ~/.config/pipeline/runners.json (default preset: Gemini 4 planner, Pi implementer, GPT 6.2 reviewer). Stops ONLY on ACCEPT (ready for human merge confirm), 3rd rejection breaker, NEEDS_HUMAN, or error. Triggers on: 'pipeline-run', 'run 3-agent pipeline', '3 agent 4 step', '后台跑 pipeline', '自动开发并评审'."
---

# pipeline-run

Agent-native entry point to run the full **3-Agent 4-Step** workflow (`plan → plan-review → impl → review`)
continuously without manually relaying context between agents.

**Model-agnostic by design:** concrete CLIs and models live ONLY in operator-local
`~/.config/pipeline/runners.json` (never in Git). Default preset uses Gemini 4 (`agy`) as `planner`,
Pi (`pi`) as `implementer`, and GPT 6.2 (`codex`) as `reviewer`, and **any model or CLI can
substitute any role** as long as `reviewer ≠ planner` and `reviewer ≠ implementer`.

Runner path: `RUNNER="<this skill's base dir>/../pipeline-coordinate/scripts/run-loop.py"`

## Steps

1. **Ensure `~/.config/pipeline/runners.json` exists:**
   - If missing, run `python3 "$RUNNER" init-config` (probes installed CLIs and writes a ready-to-edit
     template; see `runners.example.json` in this skill dir for swappable presets).
   - Never commit `runners.json` to any repo.
2. **Launch the full 4-step workflow (background by default for long tasks):**
   ```bash
   python3 "$RUNNER" run \
     --repo <target-repo-abs-path> \
     --feature <feature-slug> \
     --idea "<clear task requirement>" \
     [--push] [--background]
   ```
   - **Continuous execution:** Step 1 (`planner`) writes `.pipeline/<feature>/plan.md` → Step 2
     (`reviewer`) reviews it (auto-loops back to Step 1 on `REJECT < 3`, auto-advances to Step 3 on
     `ACCEPT` / `ACCEPT WITH NITS` with **0 planner tokens** afterward) → Step 3 (`implementer`)
     writes code + T1–T10 tests + passes `verify[]` on isolated `feat/<feature>` worktree → Step 4
     (`reviewer`) independently verifies & reviews (auto-loops back to Step 3 on `REJECT < 3`).
   - **Stops ONLY when human action is needed:**
     - `exit 0` (`status=accept`): code approved — open interactive `pipeline-review` to verify the
       pinned 40-hex SHA and let the human confirm merge (`go`/`merge`/`confirm`). **Never auto-merge.**
     - `exit 2` (`status=blocked`): 3rd rejection breaker (`>= 3`) or verify/forbid-paths retry cap.
     - `exit 3` (`status=needs_human` / `escalated` / `paused_after_plan`): planner asked
       `NEEDS_HUMAN` questions, Step 4 hit `plan_fact|plan_design|env`, or `pause_after_plan=true`.
     - `exit 4` (`status=error`): CLI crash/timeout, model collision, dirty trunk, or contract violation.
3. **Monitor, resume, or stop background runs:**
   - Check live progress/PID/stage/rejects: `python3 "$RUNNER" status --repo <repo> [--feature <slug>] [--json]`
   - Inspect background logs: `python3 "$RUNNER" logs --repo <repo> [--feature <slug>] [--tail 80]`
   - Resume after answering `NEEDS_HUMAN` questions (or after `pause_after_plan` / env fix):
     `python3 "$RUNNER" resume --repo <repo> --feature <slug> [--answer "<answer>"] [--push] [--background]`
   - Stop a running background job cleanly: `python3 "$RUNNER" stop --repo <repo> --feature <slug>`

## Hard rules

- Keep `reviewer.model_id` strictly distinct from both `planner.model_id` and `implementer.model_id`.
- Target repo trunk checkout must be clean before launching `run` or `resume`.
- Never bypass the 3-rejection breaker (`>= 3`) or auto-merge without direct human confirmation.
- Report exact `status`, `exit_code`, review file path, and pinned `feat/<feature>@<sha40>` to the user.
