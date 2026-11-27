# pipeline — design contract (v2: 3-Agent 4-Step)

A thin, model-agnostic, forge-agnostic dev pipeline. The **only durable asset is `CONTRACT.md`**
(roles, 4-step state machine, structured verdicts, git+md state bus). Each command is a thin shim
delegating to a swappable `roles.yaml` skill. No DB, no daemon, no hardcoded LLM/vendor.

## Core principle: skills reason, the shim does I/O

Aggregated skills (`grill-me`, `think`, `check`, `hunt`, `<autonomous-coding-skill>`) are
reasoning/execution engines. The shim owns commit, journal, handoff, verdict formatting, and
write-set enforcement. `pipeline-preflight/scripts/preflight.sh` deterministically executes shim
steps 1/3/4, the coordinated stale-dispatch guard, and the plan-artifact check as step 0.

## Why v2 replaced the v1 5-stage frozen red-test mainline (2026-11-27)

v1 ran 5 stages (`prd → arch → task [pre-impl frozen red tests on trunk] → impl → review`). Two
empirical pressures converged to replace it with **3-Agent 4-Step**:

1. **Empirical 608-card autopsy (`notes/87.227`, 2026-11-26, 608 cards / 102h / 1.48B tokens):**
   - Independent pre-impl red tests consumed **27%–34% of total pipeline time**, caught **0/60
     (0%)** upstream plan-fact errors after plan review, and caused **20.0% self-inflicted test
     rework** (worse than impl's own 12.7%) because blind pre-impl fixtures guess wrong on complex
     state machines and I/O harnesses.
   - Meanwhile **dual-gate independent review** caught real defects at both boundaries:
     `plan-review` rejected **33.3%** of plans before coding; `code-review` rejected **16.7%** of
     implementations.
2. **Frontier quota economics (2026-11-27):**
   - Running a 2-agent flow (scarce frontier model plans + codes + fixes; independent reviewer
     reviews) exhausts scarce frontier quota on high-token coding/test-fix loops.
   - Splitting into **Phase I (`Step 1 Plan ↔ Step 2 Plan Review`)** and **Phase II (`Step 3 Impl
     + Tests ↔ Deterministic Verify ↔ Step 4 Code Review`)** concentrates frontier planner tokens
     solely on architecture/spec convergence and spends **zero planner tokens** during coding and
     rework loops.

**How anti-cheat survives without pre-impl `spec-rev`:**
- Step 1 (`plan.md`) writes ≤10 atomic specs, a T1–T10 observable acceptance matrix (no private
  function names), `forbid-paths`, and exact `verify` commands — gated by independent Step 2
  (`plan-review`) spot-checking ≥3 codebase citations.
- Step 3 (`impl`) delivers production code AND T1–T10 tests together, gated by running all `verify`
  commands green BEFORE Step 4 ever runs.
- Step 4 (`review`) enforces `forbid-paths`, re-runs `verify`, audits T1–T10 coverage, and runs a
  **mandatory mutation/revert spot-check** (break 1–2 core logic spots; if tests stay green ⇒
  immediate `REJECT | CAUSE: weak_test`).

Full v1 history (`prd`/`arch`/`task` + `spec-rev` freeze gate + `check-cards.py`) remains preserved
in git history prior to v2.

## Commands

| command | role | delegates to (`roles.yaml`) | shim / agent I/O |
|---|---|---|---|
| `pipeline-plan` | `planner` | `[grill-me, think]` | code-first survey, write `plan.md` (+ optional `CONTEXT.md`/ADRs), commit trunk |
| `pipeline-plan-review` | `reviewer` | `check` | spot-check ≥3 citations, emit `VERDICT \| CAUSE`, update `plan_rejects` (≥3 ⇒ human) |
| `pipeline-impl` | `implementer` | `<autonomous-coding-skill>` | code + T1–T10 tests on `feat/<feature>`, run `verify` green, write `impl-report.md`, open PR |
| `pipeline-review` | `reviewer` | `check` | `forbid-paths` + `verify` + T1–T10 + mutation spot-check + GO-gate squash-merge |
| `pipeline-hunt` | `planner` / human | `hunt` | root-cause a `blocked` feature (`rejects >= 3` or `plan_fact`/`plan_design`/`env`) |
| `pipeline-improve` | `planner` | `think` | meta-PR against THIS repo; reviewed by `pipeline-review` meta-PR mode; never self-merges |

`roles.yaml` names skills only. Any frontier model (e.g. Gemini, Claude, Kimi) can fill `planner`;
any independent frontier model (e.g. GPT/Codex, GLM, Claude) can fill `reviewer`; any capable
coding CLI (e.g. Pi, Codex, Claude) can fill `implementer`, provided `reviewer` is distinct from
both `planner` and `implementer`.

## State convention (git + md, zero forge dependency)

Trunk (`main`/`master`) holds all `.pipeline/` metadata. `feat/<feature>` holds ONLY reviewable
product code + unit/integration tests — no `.pipeline/` noise in PR diffs and no intentionally-red
tests committed to trunk.

**Append-only `journal.md`** is the single authoritative state log; `current.json` is a fast cache
(`stage`, `plan_rejects`, `review_rejects`, `planner_model`). Any cold session rebuilds full state
from `git pull` + journal tail.

## Constraints — bounded automation & why headless CLI runner is allowed

**Default: no daemon, no DB, no scheduler (human relays).** Three explicitly bounded,
human-bracketed exceptions exist, and NONE of them can merge (human-direct GO-gate in
`pipeline-review` is untouched):

1. **Headless CLI runner (`skills/pipeline-coordinate/scripts/run-loop.py`, added v2):**
   - **Why previously rejected (`drive.sh` / `pipeline-driver` PR #14):** `drive.sh` drove
     *interactive TUI panes* (`herdr pane run`, screen-scraping, `agent explain` authority polling,
     multi-card trunk rebasing), where popup-eaten Enter keys and false-idle pane flaps made shell
     dispatch fundamentally unreliable.
   - **Why headless CLI subprocesses are different:** `run-loop.py` invokes configured CLIs
     synchronously via `subprocess.run(argv, shell=False)` with bounded timeouts and process-group
     kill (`start_new_session=True` + `os.killpg`), verifies real git commits in an isolated
     `feat/<feature>` worktree, enforces `model_id` separation mechanically, runs `plan.md`'s
     deterministic `verify` commands BEFORE calling Step 4, parses strict `VERDICT | CAUSE` line 1
     (fail closed on any ambiguity), short-circuits `plan_fact`/`plan_design`/`env` immediately to
     human, enforces the 3-rejection breaker in `current.json` + `journal.md`, and **has no merge
     codepath**. Zero daemon, zero DB, zero planner tokens in Phase II.
2. **Coordinated mode (`pipeline-coordinate` playbook over `multica` or `herdr`):** opt-in per
   feature via `.pipeline/<feature>/control.json` (Profile A meta-PRs and Profile B features).
   Default transport resolves invocation → `~/.config/pipeline/transport` → Herdr; failures STOP
   rather than silently switching transport.
3. **Duty mode:** queued timed re-entry of coordinated mode over `.pipeline/queue.md`, strictly
   read-only toward the target repo, bounded by per-feature budget and human-direct merge gate.

## Provenance (surviving field lessons)

- **Authoritative-state precedence:** remote git trunk > local tree > process status > agent
  summary (process exit 0 or `multica completed` never proves delivery; verify git commits/verdict).
- **Deterministic pre-gate before review:** red builds/tests loop inside Step 3 (capped at 2
  consecutive verify failures per round) and never burn Step 4 reviewer tokens.
- **Step 2 `ACCEPT WITH NITS`:** 1–2 minor doc nits pass straight to Step 3 instead of burning a
  full frontier planner rework round; Step 4 code review stays strict binary `ACCEPT`/`REJECT`.
- **Step 4 short-circuit on `plan_fact|plan_design|env`:** prevents Step 3 from burning 3 rounds
  against a flawed plan or broken environment.
- **Typed evidence on review findings:** behavioral defects cite a failing test/repro; doc/contract
  defects cite exact `file:line`; meta-PR `hypothetical` findings do not block unless they violate
  a hard rule or frozen invariant.
- **Single-sample authority+state (`watch-pane.py`) & multica write ban / `cd into clone`:**
  preserved verbatim for Herdr and multica transports.
- **Self-improvement:** never edit `~/.agents/pipeline` or installed skills in place; every change
  lands through a reviewed meta-PR with the `SKILL.md > 120` lines size-budget check.

## Rejected

- **Mandatory front-of-pipeline online discovery/OSS-vetting stage:** fails closed in air-gapped
  environments; web/OSS checks stay opportunistic inside `think` when warranted.
- **Hardcoding vendor/model names in `CONTRACT.md` or `roles.yaml`:** models rotate continuously;
  machine-local files (`~/.config/pipeline/runners.json`, `~/.config/pipeline/transport`) hold
  concrete bindings.
- **Auto-merge by implementer, coordinator, or headless runner:** merge remains exclusively in
  `pipeline-review` behind the human-direct GO-gate.
