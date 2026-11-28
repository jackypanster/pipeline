# DESIGN.md — architectural rationale

A thin, model-agnostic, forge-agnostic dev pipeline. **The only durable asset is [CONTRACT.md](CONTRACT.md).**
Every stage skill is a thin shim over a swappable `roles.yaml` slot; no DB, no daemon, no vendor lock-in.

## Core principles

1. **Skills reason; the shim owns I/O.** Delegated skills (`grill-me`, `think`, `check`, `hunt`,
   `<autonomous-coding-skill>`) focus on reasoning and coding. The shim + `preflight.sh` own git
   sync, `current.json`, `journal.md`, verdict grammar, write-set enforcement, and handoffs.
2. **Two-phase quota economics.** Frontier reasoning tokens are scarce; coding/test-fix loops are
   token-heavy. Concentrating the frontier `planner` strictly in **Phase I (`Step 1 ↔ Step 2`)**
   and exiting before **Phase II (`Step 3 ↔ verify ↔ Step 4`)** cuts frontier planner spend to zero
   during implementation and rework while preserving independent quality gates.
3. **Anti-cheat without pre-impl red-test bloat.** Writing fine-grained unit tests blind before
   implementation guesses fixtures wrong on complex state machines and I/O harnesses, creating
   high test-rework drag without catching upstream spec flaws. Instead, anti-cheat rests on four
   cheaper, stronger seams:
   - Step 1 writes a **≤10-spec, zero-inference-space `plan.md`** with a **T1–T10 observable
     acceptance matrix** (no brittle private function names), explicit `forbid-paths[]`, and exact
     `verify[]` commands.
   - Step 2 (`reviewer ≠ planner`) spot-checks ≥3 codebase citations and pins `approved_plan_blob`.
   - Step 3 (`implementer ≠ reviewer`) ships code + T1–T10 tests together and must pass `verify[]`
     green before Step 4 runs.
   - Step 4 (`reviewer`) enforces `.pipeline/` + `forbid-paths` (NUL-delimited, rename-aware),
     independently re-runs `verify[]`, requires explicit `## Assumptions`, audits T1–T10 coverage,
     and runs a **mutation/revert spot-check** (`CAUSE: weak_test` on hollow tests).
4. **Fast convergence with hard breakers.**
   - Step 2 allows `ACCEPT WITH NITS` so 1–2 minor doc wording fixes pass directly to Step 3
     without burning a planner rework round; Step 4 stays strict binary `ACCEPT`/`REJECT`.
   - Step 4 `REJECT` on `plan_fact | plan_design | env` short-circuits immediately to human/planner
     so Step 3 never wastes retries on a flawed plan or broken environment.
   - Either review gate halts for human intervention on the **3rd rejection (`>= 3`)**.

## Bounded automation (why `run-loop.py` is allowed without a daemon)

**Default: human-relayed, no daemon, no DB.** Three bounded L1 transports share the same contract,
and NONE of them can merge (only `pipeline-review` merges behind the SHA-bound human-direct GO-gate):

- **Headless CLI runner (`skills/pipeline-coordinate/scripts/run-loop.py`):** invokes configured CLIs
  synchronously via `subprocess.Popen(argv, shell=False)` with process-group timeouts, an exclusive
  `fcntl.flock` repo lock, and an isolated `feat/<feature>` git worktree. Unlike fragile TUI
  screen-scraping loops, synchronous subprocesses give exact exit codes, real git SHA checks, and
  deterministic `verify[]` gating before burning reviewer tokens.
- **Coordinated mode (`multica` / `herdr`):** opt-in per feature (`control.json`), routing solely
  on remote `journal.md` tail transitions with fail-closed stale-dispatch guards.

## Non-goals (deliberately rejected)

- **Hardcoding LLM/vendor names in repo contracts or `roles.yaml`:** models rotate continuously;
  concrete bindings belong strictly in operator-local config (`~/.config/pipeline/`).
- **Mandatory online discovery/OSS-search gates:** break in air-gapped environments; web lookups
  stay opportunistic inside `think`.
- **Auto-merge by implementers, coordinators, or runners:** merge authority stays exclusively with
  `pipeline-review` + direct human confirmation.
