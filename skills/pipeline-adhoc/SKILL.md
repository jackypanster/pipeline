---
name: pipeline-adhoc
description: "Ad-hoc twin of pipeline-coordinate — a CC session coordinates one-off ops tasks (deploys, server ops, config changes, one-off maintenance) through foreground Herdr panes with a pluggable executor CLI (pi/kimi/qwen/hermes/...). NOT a pipeline stage: no .pipeline state bus, no frozen specs, never merges. Fail-fast: any anomaly (pane unrecognized, wait timeout unresolved by a single authoritative sample, review not converging, executor death) stops the run and surfaces to the human. Args: the task (+ optional target repo/host)."
---

# pipeline-adhoc — three-role ad-hoc coordination playbook

You are CC, the coordinator. Where pipeline-coordinate runs the `.pipeline/` staged flow, this
playbook runs tasks that live OUTSIDE it — deploys, server ops, config changes, one-off maintenance.
The doctrine is the same; the bus and the frozen spec are gone.

## §1 Identity

Inherits the three-role doctrine from pipeline-coordinate: requirements/coordination (CC),
implementation (executor pane), review+hunt (Codex) on DIFFERENT models so no model grades its own
work. Covers tasks OUTSIDE the `.pipeline` flow: deploys, server ops, config changes, one-off
maintenance. For pipeline features use pipeline-coordinate.

## §2 Hard rules

- CC coordinates and verifies only; the executor implements only; Codex reviews/hunts only. No role
  collapse.
- NO fallbacks, NO coordinator-level retries. STOP conditions: executor pane unrecognized by herdr
  `agent-status`; a `wait` timeout — take ONE fresh sample via `herdr agent explain <pane> --json`;
  that SAME sample must show `fallback_reason == null` AND `state == idle` → executor finished
  (proceed to ACCEPT); `working`/`blocked`/`unknown` OR non-null `fallback_reason` → STOP, never
  re-wait; review = initial + ONE re-review, still not approve; executor pane dies. STOP
  means append a final log line, report to the human, and discuss — never invent a workaround
  mid-run.
- The executor gets at most 1 retry per step, granted in the plan file, never by the coordinator ad
  hoc.
- Git/system state is the only truth: CC reruns acceptance commands itself; an executor self-report
  is never evidence.
- Push gate: nothing leaves the machine before Codex `verdict=approve`. This is deliberately the
  ONLY Codex gate: commit-less ops runs get independent verification from CC rerunning every
  acceptance command plus the human watching foreground panes; Codex still enters on any failure
  via `/hunt`.

## §3 Run log

Per run dir `~/.local/state/dispatch/<YYYYMMDD-HHMMSS>-<slug>/` holding `plan.md` (the zero-inference
plan) + `run.log`. After EVERY coordinator action append one line:

    $(date '+%F %T') | <step> | <detail>

— dispatch text, observed status flips, verdicts, per-criterion acceptance results, errors verbatim.
The logs are the improvement loop: grep them in retrospectives to refine this skill.

## §4 The loop

1. **PLAN** — write `plan.md`: prechecked facts, numbered steps with exact commands, acceptance
   criteria each checkable by a command, failure protocol (stop + verbatim report, ≤1 retry).
2. **PREFLIGHT** — `herdr pane list` → identify your own pane (exclude from dispatch), the executor
   pane, and the codex pane by agent+cwd. Exactly one pane per role. Then `herdr agent explain
   <pane> --json` per role pane: require authoritative (`fallback_reason == null` — matched_rule is
   deliberately NOT part of the predicate: herdr has no detection rules for some healthy CLIs,
   e.g. an idle pi pane samples matched_rule=null/evaluated_rules=0 with fallback_reason=null;
   empirical completion is re-verified at ACCEPT) AND read each
   pane's footer to confirm three DISTINCT underlying models; anything less → STOP.
3. **DISPATCH** — immediately before EVERY send (task dispatch, fix relay, re-review request) take
   ONE fresh `herdr agent explain <pane> --json` sample; that SAME sample must show
   `fallback_reason == null` AND `state == idle`, else STOP. Then `herdr pane read <pane>` to
   confirm the composer is empty (non-empty content would pollute the dispatch text); then `herdr
   pane send-text <pane> "<one-liner + plan.md path>"` (path, never the plan body) → `sleep 1` →
   `herdr pane send-keys <pane> enter` → take ONE fresh `herdr agent explain <pane> --json` sample;
   that SAME sample must show `fallback_reason == null` AND `state == working` (executor picked up
   the task) within ~10s, else STOP → `herdr workspace focus <ws>` so the human watches the run.
4. **MONITOR** — `herdr wait agent-status <pane> --status <done|idle> --timeout <ms>`. On timeout,
   take ONE fresh sample via `herdr agent explain <pane> --json`; that SAME sample must show
   `fallback_reason == null` AND `state == idle` → executor finished, proceed to ACCEPT verification;
   `working`/`blocked`/`unknown` OR non-null `fallback_reason` → STOP and report — never re-wait.
   Human-action nodes (OAuth URL, confirm prompts): relay via `hermes send --to telegram`.
5. **REVIEW GATE** (only when a commit to push is produced) — dispatch Codex with diff range +
   purpose + "verdict: approve | needs-attention". needs-attention → relay findings to the executor
   (amend while unpushed) → ONE re-review; still not approve → STOP.
6. **ACCEPT** — CC runs every acceptance command itself; all pass → final log line + close-out
   report; any fail → dispatch Codex `/hunt` with "step + command + verbatim error", then STOP for
   the human (hunt informs the next move; no auto-refix loop).

## §5 Gotchas

| pitfall | fix |
|---|---|
| `send-text` followed instantly by `enter` loses the submit | `sleep 1` between them |
| completion vocab is unstable across CLIs and runs (pi observed both `done` and `idle`) | `herdr agent explain` is the authoritative source, never status vocab |
| `herdr wait output --match` self-matches your own dispatch echo | pattern must not occur in the text you sent |
| cross-workspace foreground is `herdr workspace focus <ws>` | `pane focus` is directional-only |
| an empty-composer pane reads as blank lines | readiness is `agent_status`, never screen content; the empty-composer read in §4 DISPATCH guards the dispatch text from pollution |

## §6 Scope boundary

NOT a scheduler/daemon (DESIGN.md holds firm); writes nothing under any repo's `.pipeline/`; never
merges; never force-pushes. Unrelated to the `pipeline-dispatch` REPO (driver→TG notify bridge)
despite the near-name.
