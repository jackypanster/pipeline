# CONTRACT.md — the frozen pipeline protocol (v2: 3-Agent 4-Step)

Single source of truth for every `pipeline-*` skill. Commands carry **no logic of their own** —
they follow this. (See [DESIGN.md](DESIGN.md) for rationale and v1→v2 provenance.)

## Three roles, four steps (frozen — do not weaken)

Work flows through **3 abstract roles** across **4 mainline steps**:

| Step | Command | Role | Model tier | Output |
|---|---|---|---|---|
| 1 | `pipeline-plan` | `planner` | Frontier model A | `.pipeline/<feature>/plan.md` (+ optional `CONTEXT.md`, ADRs) |
| 2 | `pipeline-plan-review` | `reviewer` | Independent frontier model B | `.pipeline/<feature>/reviews/plan-review-NN.md` |
| 3 | `pipeline-impl` | `implementer` | Capable/cost-efficient coding CLI C | `feat/<feature>` code + T1–T10 unit/integration tests + `impl-report.md` |
| 4 | `pipeline-review` | `reviewer` | Independent frontier model B | `.pipeline/<feature>/reviews/code-review-NN.md` + squash-merge on human confirm |

Auxiliary commands: `pipeline-hunt` (root-cause escalation on `blocked`) and `pipeline-improve`
(meta-PR self-improvement against this pipeline repo).

**Model-separation invariant (frozen):** `model(reviewer) ≠ model(planner)` AND
`model(reviewer) ≠ model(implementer)`. Three distinct model families are recommended; at minimum,
the reviewer NEVER grades its own plan or its own code. Concrete model names NEVER belong in this
contract, `roles.yaml`, or project onboarding snippets — they live only in operator-local machine
config (`~/.config/pipeline/`).

**Two-phase quota split:** Phase I (`Step 1 ↔ Step 2`) converges the plan using the frontier
planner. Once Step 2 passes (`ACCEPT` / `ACCEPT WITH NITS`), **the planner exits** — Phase II
(`Step 3 ↔ Step 4`) consumes **zero planner tokens**.

## The shim loop (every stage command runs exactly this)

1. **`git pull --rebase`** — always your first act (no shared memory; rebuild from git).
2. **Load repo-local config if present** (`.env`, `.env.local`, `.envrc`). Adapt key casing as
   required by the project. **Never print secret values.**
3. **Read `.pipeline/current.json`** → `{repo, branch, pr?, feature, stage, plan_rejects?, review_rejects?, planner_model?}`.
   Missing + you are `pipeline-plan` ⇒ create it. Missing + any other command ⇒ STOP, ask operator.
4. **Resolve your skill**: read `.pipeline/roles.yaml`, look up your slot, verify the named skill
   is installed on this runtime. Not installed ⇒ STOP and report (no silent fallback).

   **Deterministic executor (steps 1, 3, 4 + coordinated pre-write guard + plan check):** run
   `bash <your skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage <stage>` first,
   adding the five envelope fields verbatim when an envelope is present.
   - Exit `0` ⇒ steps 1, 3, 4 (and guard / plan check) DONE — use its printed lines.
   - Exit `3` ⇒ everything ELSE passed; do ONLY the named unverified check(s) (`install-check`,
     `remote-identity`) yourself and STOP on failure.
   - Script absent, or any exit other than `0`/`3` ⇒ STOP and report its printed reason.
   Step 2 (loading dotenv) stays yours (`preflight.sh` reports only file + key COUNT). If it prints
   `UPSTREAM newer`, add one advisory line in your final report to run `pipeline-update` between
   stages (never mid-stage).
5. **Invoke the bound skill** — it does REASONING/interview; the shim owns staging, commit,
   journal, handoff, and write-set enforcement.
6. **Write ONLY within your stage's declared write-set**, append your handoff to
   `.pipeline/<feature>/journal.md`, `git add` those paths + `journal.md`, commit **once** (never
   orphan, never amend), and **`git push`** (unless running a local-only headless loop without
   `--push`). Writing outside your write-set is a contract violation.
7. **Print the handoff block** (already persisted in `journal.md`) and stop.

## State machine & circuit breakers (frozen — do not change)

Feature progression: `plan → plan-review → impl → review → done`; `blocked` = terminal (requires
human intervention or `pipeline-hunt`).

- **Circuit breaker at 3rd rejection (both gates):**
  - `plan_rejects` starts at `0`. Each Step 2 `REJECT` increments `plan_rejects`.
    - `plan_rejects < 3` ⇒ route back to Step 1 (`plan-review→plan · failed`).
    - **`plan_rejects >= 3` ⇒ STOP for human (`plan-review→human · blocked`)**.
  - `review_rejects` starts at `0`. Each Step 4 `REJECT` for `impl_bug | weak_test | scope`
    increments `review_rejects`.
    - `review_rejects < 3` ⇒ route back to Step 3 (`review→impl · failed`).
    - **`review_rejects >= 3` ⇒ STOP for human (`review→human · blocked`)**.
- **Step 2 → Step 3 default progression:** `ACCEPT` or `ACCEPT WITH NITS` in Step 2 proceeds
  **directly to Step 3** (`plan-review→impl · completed`) by default. Only when `control.json`
  explicitly sets `"pause_after_plan": true` does Step 2 halt for human plan sign-off before Step 3.
- **Step 4 plan/env short-circuit:** a Step 4 `REJECT` with `CAUSE: plan_fact | plan_design | env`
  bypasses the impl retry loop and **immediately stops for human** (`review→human · blocked`) —
  never burn Step 3 retries against a broken plan or broken environment.
- **Only `pipeline-review` merges**, and ONLY after explicit direct human confirmation in the
  reviewer's own session (`go`/`merge`/`confirm` GO-gate). No runner, coordinator, or implementer
  has a merge path.
- **Never force-push trunk or any shared/published ref**; never delete anything beyond the
  feature's own `feat/<feature>` branch on merge.

## Structured verdict grammar (mandatory for Step 2 & Step 4)

Every review artifact (`plan-review-NN.md` / `code-review-NN.md`) MUST open with line 1 matching
the exact machine-parseable header:

- **Step 2 (`plan-review`)**:
  `VERDICT: <ACCEPT|ACCEPT WITH NITS|REJECT> | CAUSE: <none|plan_fact|plan_design|scope|env>`
  - `ACCEPT` and `ACCEPT WITH NITS` MUST carry `CAUSE: none` (`ACCEPT WITH NITS` = 1–2 minor doc
    nits that do not affect correctness/scope; passes without burning a planner rework round).
  - `REJECT` MUST carry a non-`none` cause.
- **Step 4 (`review`)**:
  `VERDICT: <ACCEPT|REJECT> | CAUSE: <none|impl_bug|weak_test|scope|plan_fact|plan_design|env>`
  - Strictly binary (`ACCEPT` or `REJECT` — no `ACCEPT WITH NITS` on code).
  - `ACCEPT` MUST carry `CAUSE: none`; `REJECT` MUST carry a non-`none` cause.
- Malformed or missing header ⇒ **fail closed, STOP for human** (never infer `ACCEPT`).

## Layout (`.pipeline/` lives in the TARGET repo, on trunk)

```text
.pipeline/
  roles.yaml                      per-repo slot→skill bindings (no model names)
  current.json                    {repo, branch, pr?, feature, stage, plan_rejects?, review_rejects?, planner_model?, approved_plan_blob?}
  <feature>/
    journal.md                    append-only run log — physically-last entry is authoritative
    plan.md                       frontmatter (feature, verify[], forbid-paths[], planner_model?) + evidence + ≤10 specs + T1–T10
    CONTEXT.md  docs/adr/*.md     optional domain/ADR docs when irreversible decisions exist
    impl-report.md                implementer assumptions + tests run + verify output + Not run
    reviews/plan-review-NN.md     Step 2 verdicts
    reviews/code-review-NN.md     Step 4 verdicts
    control.json                  optional coordinated-mode authorization
```

One feature in flight at a time (`current.json` is a single pointer).

## State authority & write-sets

**Trunk is the single state authority.** All `.pipeline/` metadata lives on trunk (`main`/`master`).
`feat/<feature>` carries **ONLY reviewable product code + unit/integration tests** — zero
`.pipeline/` clutter in the PR diff.

**Authoritative-state precedence:** **pushed trunk (remote git) > local clone/working tree >
running process state > model summary**. Never trust a self-report without Git + verify evidence.

`current.json.stage` is a fast bootstrap cache (`plan|plan-review|impl|review|hunt|done|blocked`); on
disagreement the **journal tail wins**. "Disagrees" means `stage ∉ {tail.from, tail.to}`.

| stage | write-set (may create/modify) | must NOT touch |
|---|---|---|
| `plan` | `plan.md`, optional `CONTEXT.md`, `docs/adr/*`, `current.json` (`plan_rejects` preserved, `stage: plan`), optional `control.json` (on explicit operator request) | product `src/**`, product tests |
| `plan-review` | `reviews/plan-review-NN.md`, `current.json` (`stage`, `plan_rejects`) | `plan.md`, product `src/**`, tests |
| `impl` | on `feat/<feature>`: product `src/**` + unit/integration tests covering T1–T10; on trunk: `impl-report.md`, `current.json` (`stage: impl`, `pr`) | `plan.md`, any path in `plan.md` `forbid-paths` |
| `review` | `reviews/code-review-NN.md`, `current.json` (`stage`, `review_rejects`); squash-merge `feat/<feature>` on GO-gate | product code/tests (except temporary uncommitted mutation spot-check reverted immediately) |
| `hunt` | diagnosis appended to `journal.md` / `reviews/hunt-NN.md`; reset `plan_rejects` or `review_rejects` to `0` on informed re-queue | product code, `plan.md` |

Every stage appends one entry to `.pipeline/<feature>/journal.md` in its single metadata commit.

## Anti-cheat & quality gates (replaces v1 pre-impl red-test freeze)

1. **Zero-inference-space `plan.md` (Step 1 + Step 2):** `plan.md` declares structured YAML
   frontmatter (`verify: [...]` non-empty, `forbid-paths: [...]`), codebase baseline evidence
   (`file:line` citations), **≤10 atomic specs**, and a **T1–T10 behavioral acceptance matrix**
   (observable behaviors, NOT hardcoded private function names). Step 2 spot-checks ≥3 baseline
   citations against real files before approving.
2. **Same-step implementation + tests + deterministic `verify` pre-gate (Step 3):** `pipeline-impl`
   writes product code AND unit/integration tests covering every T1–T10 row on `feat/<feature>`.
   ALL `verify` commands in `plan.md` MUST exit 0 BEFORE Step 4 is invoked. Red code never enters
   Step 4.
3. **`forbid-paths` check + mandatory mutation/revert spot-check (Step 4):** `pipeline-review`
   verifies zero edits under `forbid-paths`, re-runs `plan.md`'s `verify` commands green, verifies
   every T1–T10 row has a real test, and performs a **mutation/revert spot-check** — deliberately
   breaking 1–2 core logic branches and re-running the targeted test (must fail; revert immediately).
   If tests stay green ⇒ immediate `VERDICT: REJECT | CAUSE: weak_test`. (Inside a read-only
   sandbox where edits are blocked, Step 4 performs an adversarial assertion audit proving each
   T1–T10 test would fail on concrete logic inversion and flags `weak_test` on any tautological or
   mocked-away test.)
4. **Implementer `## Assumptions` (`impl-report.md`):** Step 3 records any un-pinned decision under
   `## Assumptions` (or literally `none` when none exist) plus tests run, green verify output, and
   `Not run`. Missing `## Assumptions` ⇒ `REJECT | CAUSE: scope`; contradicting `plan.md` ⇒ `REJECT`.

## Handoff block & append-only run journal

Every stage appends ONE entry to the **physical END** of `.pipeline/<feature>/journal.md` in its
metadata commit (never prepend, never edit prior entries):

```text
## seq=N · <YYYY-MM-DDTHH:MM:SSZ> · <from>→<to> · <completed|failed|blocked> · by=<role/model tag|?>
done:   <1–3 lines: what this stage actually produced>
output: <artifact path(s)>
--- handoff ---
>>> NEXT
Run pipeline-<next> on a FRESH session (rebuild state from git + CONTRACT.md).
repo=<url> branch=<branch> pr=<url|none>
Role: <planner | reviewer (distinct model) | implementer (distinct from reviewer)>
First: git pull --rebase; load repo config (.env if present).
Read: <repo>/AGENTS.md, .pipeline/<feature>/plan.md, latest review (if any).
Task: <concrete numbered steps>
Done when: <success criterion>.
<<< END
```

Legal transitions (`<from>→<to> · <status>`):
- `plan→plan-review · completed` (NEXT `Run pipeline-plan-review`)
- `plan-review→impl · completed` (NEXT `Run pipeline-impl`; records `approved_plan_blob`)
- `plan-review→plan · failed` (NEXT `Run pipeline-plan`, `plan_rejects < 3`)
- `plan-review→human · blocked` (`plan_rejects >= 3` or `pause_after_plan: true`)
- `human→impl · completed` (NEXT `Run pipeline-impl`; explicit human approval after `pause_after_plan: true`, preserving `approved_plan_blob`)
- `impl→review · completed` (NEXT `Run pipeline-review`)
- `impl→human · blocked` (unrecoverable impl/verify failure or implementer crash/timeout)
- `review→review · completed` (NEXT `Await human-direct merge confirmation in this reviewer session.` for interactive/multica sessions, OR `Headless Step 4 ACCEPT for feat/<feature>@<sha40> — open interactive pipeline-review to arm GO-gate.` for headless CLI runs)
- `review→done · completed` (post-merge terminal)
- `review→impl · failed` (NEXT `Run pipeline-impl`, `review_rejects < 3`)
- `review→human · blocked` (`review_rejects >= 3`, OR `CAUSE: plan_fact|plan_design|env` short-circuit)
- `hunt→plan · completed` / `hunt→impl · completed` (resets the relevant reject counter to `0`) / `hunt→human · blocked` (unresolved `env` outage)

## Operating transports & coordinated mode

**Default remains human-relayed.** Three L1 transports share this exact contract without changing a
single invariant:
1. **Human-relayed (default):** operator copies handoffs between agent sessions.
2. **Headless CLI runner (`skills/pipeline-coordinate/scripts/run-loop.py`):** stateless,
   synchronous subprocess loop for Phase II (`Step 3 Impl CLI ↔ deterministic verify ↔ Step 4
   Reviewer CLI`) and single-shot Step 2 (`review-plan`). Configured via operator-local
   `~/.config/pipeline/runners.json` (JSON `argv` lists, never `shell=True`, mechanically enforces
   `model_id` separation, zero planner tokens in Phase II, zero merge path).
3. **Coordinated mode (`pipeline-coordinate` playbook over `multica` or `herdr`):** authorized per
   feature by `.pipeline/<feature>/control.json`:
   ```json
   { "schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct" }
   ```
   Optional `"pause_after_plan": false` (default `false`). `schema_version == 1`,
   `mode ∈ {"human","coordinated"}`, `merge_gate == "human-direct"`. Absence = human mode. Created
   ONLY by `pipeline-plan` on explicit operator request; later stages preserve it untouched.

**Coordinator role is ASSIGNED, never inferred:** a stage node NEVER dispatches other agents.

### Dispatch envelope & pre-write stale-dispatch guard (coordinated mode)

```text
repo=<abs clone path>  branch=<trunk>  feature=<slug>  expected_seq=<N>  expected_commit=<sha40>
```

When present, `preflight.sh` runs BEFORE any file write: `git fetch`, exact match on `repo`,
`branch`, `feature`, `expected_commit` (40-hex == `FETCH_HEAD` == `HEAD`), `expected_seq`, tail
`>>> NEXT` first token == `pipeline-<stage>`, and `control.json` authorization tuple. Any mismatch
⇒ `STALE_DISPATCH <field> observed=<v> expected=<v>` and STOP with zero writes.

### Atomic review outcome (coordinated mode)

`pipeline-review` publishes its verdict and disposition in ONE trunk commit:
- **Approved:** verify green + all checks passed ⇒ ONE commit (`code-review-NN.md` +
  `review→review · completed` with `Await human-direct merge confirmation in this reviewer session.`),
  then arm the GO-gate and STOP.
- **Rejected:** ONE commit (`code-review-NN.md` + updated `current.json` + `review→impl · failed`
  or `review→human · blocked`).

## Forge adapter (review/merge only)

Keyed on `git config --get remote.origin.url`:
- **github.com** → `gh` (`gh pr diff` / `gh pr merge`)
- **gitee** → `gitee-cli`
- **anything else / no forge** → `git fetch && git diff base..branch` (fail-open; same gates hold)

## Self-improvement — skills propose, review gates (never self-edit)

Never edit a live/installed skill or `~/.agents/pipeline` in place (`pipeline-update` is ff-only
pull). Gaps emit `SKILL-PROPOSAL: <skill> — <what + why>` and land ONLY via `pipeline-improve` →
meta-PR against `jackypanster/pipeline` → `pipeline-review` in **meta-PR mode** (semantic review +
**size-budget axis**: any `skills/*/SKILL.md` with net growth > 0 AND post-merge `wc -l > 120`
requires explicit justification in the PR body; **evidence tier**: `observed` vs `hypothetical`,
only `observed` or invariant violations block) → direct human confirm → reviewer squash-merge.

The **frozen invariants** (3-role model separation · 4-step state machine & 3-reject breaker ·
only-reviewer-merges + human-direct GO-gate · never-force-push trunk) are NOT auto-improvable —
touching them requires explicit human decision.
