# CONTRACT.md — the frozen pipeline protocol (3-Agent 4-Step)

Single source of truth for every `pipeline-*` skill. Stage commands carry no logic of their own —
they follow this contract. (See [DESIGN.md](DESIGN.md) for rationale.)

## Three roles, four steps (frozen)

| Step | Command | Role | Model tier | Deliverable |
|---|---|---|---|---|
| 1 | `pipeline-plan` | `planner` | Frontier model A | `.pipeline/<feature>/plan.md` (+ optional `CONTEXT.md`, ADRs) |
| 2 | `pipeline-plan-review` | `reviewer` | Independent frontier model B | `.pipeline/<feature>/reviews/plan-review-NN.md` |
| 3 | `pipeline-impl` | `implementer` | Coding CLI C | `feat/<feature>` code + T1–T10 tests + `impl-report.md` |
| 4 | `pipeline-review` | `reviewer` | Independent frontier model B | `.pipeline/<feature>/reviews/code-review-NN.md` + squash-merge on GO-gate |

Auxiliary stages: `pipeline-hunt` (root-cause on `blocked`) and `pipeline-improve` (meta-PR against
this repo).

**Model-separation invariant (frozen):** `model(reviewer) ≠ model(planner)` AND
`model(reviewer) ≠ model(implementer)`. Three distinct model families are recommended; the reviewer
NEVER grades its own plan or code. Concrete model/vendor names NEVER appear in this contract,
`roles.yaml`, or project onboarding snippets — they live only in operator-local config
(`~/.config/pipeline/`).

**Two-phase quota split:** Phase I (`Step 1 ↔ Step 2`) uses the frontier planner to converge
`plan.md`. Once Step 2 passes (`ACCEPT` / `ACCEPT WITH NITS`), **the planner exits** — Phase II
(`Step 3 ↔ Step 4`) consumes **zero planner tokens**.

## The shim loop (every stage runs this)

1. **`git pull --rebase`** (human-relay). In coordinated mode with a dispatch envelope, `preflight.sh`
   verifies remote `FETCH_HEAD == expected_commit` and locked metadata BEFORE fast-forwarding `HEAD`.
2. **Load repo-local dotenv** (`.env`, `.env.local`, `.envrc`) if present. **Never print secret values.**
3. **Read `.pipeline/current.json`** → `{repo, branch, pr?, feature, stage, plan_rejects?, review_rejects?, planner_model?, approved_plan_blob?}`.
   Missing + `pipeline-plan` ⇒ create it. Missing + any other command ⇒ STOP.
4. **Resolve slot & verify install:** look up your stage slot in `.pipeline/roles.yaml` and verify
   the skill is installed (no silent fallback).

   **Step 0 deterministic executor:** run
   `bash <skill-base-dir>/../pipeline-preflight/scripts/preflight.sh --stage <stage>` (+ five
   envelope fields verbatim when present).
   - Exit `0` ⇒ steps 1/3/4 (+ guard + `plan.md` check) DONE.
   - Exit `3` ⇒ do ONLY the named unverified check(s) (`install-check`, `remote-identity`) and STOP on failure.
   - Script absent or any other exit ⇒ STOP with printed reason.
   If `UPSTREAM newer` is printed, add one advisory line to run `pipeline-update` between stages.
5. **Invoke the bound skill** (reasoning/coding engine); the shim owns commit, journal, handoff,
   verdict format, and write-set enforcement.
6. **Write ONLY inside your stage's write-set**, append one entry to the physical END of
   `.pipeline/<feature>/journal.md`, commit once, and `git push` (unless local-only headless run
   without `--push`).
7. **Print the handoff block** and stop.

## State machine & circuit breakers (frozen)

Progression: `plan → plan-review → impl → review → done`; `blocked` = terminal.

- **3rd-rejection circuit breaker (both gates):**
  - `plan_rejects` starts at `0`; Step 2 `REJECT` increments it. `< 3` ⇒ `plan-review→plan · failed`;
    **`>= 3` ⇒ `plan-review→human · blocked` (STOP for human)**.
  - `review_rejects` starts at `0`; Step 4 `REJECT` for `impl_bug | weak_test | scope` increments it.
    `< 3` ⇒ `review→impl · failed`; **`>= 3` ⇒ `review→human · blocked` (STOP for human)**.
- **Step 2 → Step 3 default:** `ACCEPT` or `ACCEPT WITH NITS` pins `approved_plan_blob` and proceeds
  **directly to Step 3** (`plan-review→impl · completed`) unless `control.json` explicitly sets
  `"pause_after_plan": true` (in which case Step 2 halts `plan-review→human · blocked` until human
  records `human→impl · completed`).
- **Step 4 plan/env short-circuit:** Step 4 `REJECT` with `CAUSE: plan_fact | plan_design | env`
  **immediately stops for human** (`review→human · blocked`) without burning Step 3 retries.
- **Only `pipeline-review` merges**, strictly after direct human confirmation (`go`/`merge`/`confirm`)
  bound to the exact approved 40-hex SHA. No runner, coordinator, or implementer can merge.
- **Never force-push trunk or any shared ref**; never delete anything beyond `feat/<feature>` on merge.

## Structured verdict grammar (physical line 1)

Every `plan-review-NN.md` and `code-review-NN.md` MUST begin on **physical line 1** with:

- **Step 2 (`plan-review`):**
  `VERDICT: <ACCEPT|ACCEPT WITH NITS|REJECT> | CAUSE: <none|plan_fact|plan_design|scope|env>`
  (`ACCEPT` / `ACCEPT WITH NITS` require `CAUSE: none`; `REJECT` requires non-`none` cause.)
- **Step 4 (`review`):**
  `VERDICT: <ACCEPT|REJECT> | CAUSE: <none|impl_bug|weak_test|scope|plan_fact|plan_design|env>`
  (Strictly binary — no `ACCEPT WITH NITS` on code. `ACCEPT` requires `CAUSE: none`; `REJECT`
  requires non-`none` cause.)
- Malformed or missing line 1 ⇒ **fail closed, STOP for human**.

## Target repo layout (`.pipeline/` on trunk)

```text
.pipeline/
  roles.yaml                      per-repo slot→skill bindings (no model names)
  current.json                    {repo, branch, pr?, feature, stage, plan_rejects?, review_rejects?, planner_model?, approved_plan_blob?}
  <feature>/
    journal.md                    append-only run log (physically-last entry is authoritative)
    plan.md                       frontmatter (feature, verify[], forbid-paths[], planner_model) + evidence + <=10 specs + T1-T10
    CONTEXT.md  docs/adr/*.md     optional domain/ADR docs
    impl-report.md                commits + ## Assumptions + verify output + Not run
    reviews/plan-review-NN.md     Step 2 verdicts
    reviews/code-review-NN.md     Step 4 verdicts (includes Reviewed-Head: feat/<feature>@<sha40>)
    control.json                  optional coordinated-mode authorization
```

One feature in flight at a time.

## State authority & write-sets

**Trunk is the single state authority.** `.pipeline/` metadata lives on trunk; `feat/<feature>`
carries **reviewable product code + unit/integration tests ONLY** (zero `.pipeline/` files in PR diffs).

**Precedence:** remote trunk > local working tree > process status > agent summary.

`current.json.stage` (`plan|plan-review|impl|review|hunt|done|blocked`) is a bootstrap cache; on
disagreement (`stage ∉ {tail.from, tail.to}`), the **`journal.md` tail wins**.

| stage | write-set | must NOT touch |
|---|---|---|
| `plan` | `plan.md`, optional `CONTEXT.md`, `docs/adr/*`, `current.json`, optional `control.json` (on explicit request) | `src/**`, product tests |
| `plan-review` | `reviews/plan-review-NN.md`, `current.json` (`stage`, `plan_rejects`, `approved_plan_blob`) | `plan.md`, `src/**`, tests |
| `impl` | on `feat/<feature>`: `src/**` + T1–T10 tests; on trunk: `impl-report.md`, `current.json` (`stage`, `pr`) | `plan.md`, `.pipeline/` on feature branch, `forbid-paths` |
| `review` | `reviews/code-review-NN.md`, `current.json`; squash-merge `feat/<feature>` on GO-gate | product code/tests (except temporary uncommitted mutation check reverted immediately) |
| `hunt` | `reviews/hunt-NN.md`, `current.json` (reset reject counter on informed re-queue) | `plan.md`, product code/tests |

Every stage appends one entry to `journal.md` in its single trunk metadata commit.

## Anti-cheat & quality gates

1. **Zero-inference-space `plan.md` (Steps 1–2):** YAML frontmatter with explicit non-empty `verify:`
   list, explicit `forbid-paths:` list (`[]` allowed), `planner_model`, no duplicate keys; body has
   ≥3 verified `file:line` citations, **≤10 specs**, and a **T1–T10 observable acceptance matrix**
   (no brittle private function names). Step 2 spot-checks ≥3 citations against real files.
2. **Same-step code + tests + `verify` pre-gate (Step 3):** `pipeline-impl` delivers code AND
   unit/integration tests covering every T1–T10 row on `feat/<feature>`. All `verify:` commands must
   exit 0 BEFORE Step 4 runs.
3. **Deterministic gates + mutation spot-check (Step 4):** check zero edits in `.pipeline/` or
   `forbid-paths` (including rename sources), independently re-run `verify:` green, require explicit
   `## Assumptions` in `impl-report.md` (missing ⇒ `REJECT | CAUSE: scope`), verify T1–T10 coverage,
   and run a **mutation/revert spot-check** (break 1–2 core branches; tests must fail; revert
   immediately — staying green ⇒ `REJECT | CAUSE: weak_test`; in read-only sandboxes, record
   per-T-row logic-inversion assertion evidence).

## Append-only journal & legal transitions

Append strictly to the physical END of `.pipeline/<feature>/journal.md`:

```text
## seq=N · <YYYY-MM-DDTHH:MM:SSZ> · <from>→<to> · <completed|failed|blocked> · by=<role:model|?>
done:   <1-3 lines>
output: <artifact path(s) or feat/<feature>@<sha40>>
--- handoff ---
>>> NEXT
Run pipeline-<next> on a FRESH session (rebuild state from git + CONTRACT.md).
...
<<< END
```

Legal transitions:
- `plan→plan-review · completed` / `plan→human · blocked` (`NEEDS_HUMAN` pause)
- `human→plan · completed` (operator answered planner questions)
- `plan-review→impl · completed` / `plan-review→plan · failed` / `plan-review→human · blocked`
- `human→impl · completed` (explicit human approval after `pause_after_plan: true`)
- `impl→review · completed` / `impl→human · blocked`
- `review→review · completed` (GO-gate armed or headless SHA-bound handoff) / `review→done · completed`
- `review→impl · failed` / `review→human · blocked`
- `hunt→plan · completed` / `hunt→impl · completed` / `hunt→human · blocked`

## Transports & coordinated mode

Three L1 transports share this exact contract:
1. **Headless CLI runner (`pipeline-run` skill / `skills/pipeline-coordinate/scripts/run-loop.py`):**
   end-to-end 4-step runner (`run`, `resume`, `status`, `logs`, `stop`, `init-config`, plus
   single-phase `review-plan` / `approve-plan` / `phase2`) in foreground or `--background`
   (`shell=False`, `fcntl.flock` repo lock, isolated `feat/<feature>` worktree, zero planner
   tokens in Phase II, zero merge path), configured via operator-local `~/.config/pipeline/runners.json`.
2. **Human-relayed (zero extra setup):** operator copies handoffs between sessions.
3. **Coordinated mode (`pipeline-coordinate` over `multica` or `herdr`):** opt-in per feature via
   `.pipeline/<feature>/control.json`:
   ```json
   { "schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct" }
   ```
   Optional `"pause_after_plan": false`. Created ONLY by `pipeline-plan` on explicit operator
   request; later stages preserve it untouched. A stage node NEVER dispatches other agents.

**Dispatch envelope (coordinated mode):**
`repo=<abs clone> branch=<trunk> feature=<slug> expected_seq=<N> expected_commit=<sha40>`.
`preflight.sh` validates every field against `FETCH_HEAD == expected_commit` (plus local worktree
`current.json`) BEFORE any fast-forward; mismatch ⇒ `STALE_DISPATCH` and STOP with zero writes.

## Forge adapter (review/merge only)

Keyed on `git config --get remote.origin.url`: **github.com** → `gh`; **gitee** → `gitee-cli`;
**other / no forge** → `git fetch && git diff base..branch` (fail-open; same gates hold).

## Self-improvement (meta-PR lane)

Never edit installed skills or `~/.agents/pipeline` in place (`pipeline-update` is ff-only pull).
Changes land via `pipeline-improve` → meta-PR on `jackypanster/pipeline` → `pipeline-review` in
**meta-PR mode** (semantic review + **size-budget axis**: any `skills/*/SKILL.md` with net growth
`> 0` AND post-merge `wc -l > 120` requires PR justification; **evidence tier**: only `observed` or
invariant violations block) → direct human confirm → reviewer squash-merge.

**Frozen invariants** (3-role model separation · 4-step state machine & 3-reject breaker ·
only-reviewer-merges + SHA-bound human-direct GO-gate · never-force-push trunk) require explicit
human decision to change.
