---
name: pipeline-plan
description: "Pipeline Step 1 (planner role, frontier model A) — clarify requirements, inspect codebase evidence, and write .pipeline/<feature>/plan.md with <=10 specs, T1-T10 acceptance matrix, verify commands, and forbid-paths. Args: repo, branch, rough idea (or revision after plan-review REJECT)."
---

# pipeline-plan

Step 1 of the 3-Agent 4-Step pipeline. Follow the **shim loop in CONTRACT.md** with slot = `plan`.
**Role:** `planner` (frontier model A). Once Step 2 (`pipeline-plan-review`) passes, this role
exits — Phase II consumes **zero planner tokens**.

**Coordinated dispatch guard:** with a five-field envelope, step 0 runs CONTRACT §Pre-write
stale-dispatch guard BEFORE any write; a mismatch ⇒ `STALE_DISPATCH` and STOP (zero writes).
Preserve `control.json`; never modify it.

**Skill(s):** `plan` slot resolves to `[grill-me, think]` — clarify blocking unknowns and turn
codebase facts into a zero-inference-space plan. Neither writes files; YOU own all file I/O.

## Steps

0. **Preflight** — run `bash <this skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage plan` (+ five envelope fields if present). `0` ⇒ pull, `current.json`, slot resolve + install check DONE; `3` ⇒ do the named check(s) yourself, STOP on failure; else ⇒ STOP. Note `UPSTREAM newer` in your final report if printed.
1. `CURRENT absent` ⇒ create `.pipeline/current.json` (`repo`, `branch`, `feature`, `stage: "plan"`, `plan_rejects: 0`, `review_rejects: 0`, optional `planner_model`). On rework after Step 2 `REJECT`, read the latest `.pipeline/<feature>/reviews/plan-review-NN.md` and preserve `plan_rejects`.
2. **Code-first survey BEFORE asking the human:** read real source/test/config files to verify every claim (target ~5 code-verified facts per 1 human question). For external APIs/protocols that local tests cannot exercise, include an external-reference table marked `probed` / `doc-cited` / `unverified`.
3. **Question quality gate & subtraction pass:** ask the human ONLY questions that are **Material** (changes scope/data/API/acceptance), **Grounded** (cites real code/docs), and **Answerable** (concrete options + recommended default). Low-risk defaults become labeled assumptions (`assumed`); load-bearing unconfirmed scope moves to `## Deferred`.
4. **Mode recommendation (first run only):** consult README §Choosing the mode. Create `.pipeline/<feature>/control.json` (`{"schema_version":1,"mode":"coordinated","merge_gate":"human-direct"}`, optional `"pause_after_plan":true` only if requested) **ONLY on explicit operator request** — never by default.
5. **Write `.pipeline/<feature>/plan.md`** (and optional `CONTEXT.md` / `docs/adr/*.md` only for irreversible architecture choices). `plan.md` MUST begin with exact YAML frontmatter:
   ```yaml
   ---
   feature: <slug>
   planner_model: <your model id, if known>
   verify:
     - "<exact deterministic build/lint command>"
     - "<exact deterministic test command>"
   forbid-paths:
     - "<path or prefix implementer must not touch>"
   ---
   ```
   Body sections (dense, agent-first, zero inference space for the implementer):
   - `## Baseline Evidence` — ≥3 verified `file:line` citations grounding existing behavior.
   - `## Scope & Non-Scope` (+ `## Deferred`) — exact files/modules to change and explicit out-of-scope.
   - `## Specifications` — **at most 10 atomic numbered items** (S1–S10). No "etc." / "as appropriate".
   - `## Acceptance Matrix (T1–T10)` — observable behavior, inputs, and expected outputs/exit codes (NO hardcoded private function names; every T-row must be unit/integration testable).
   - `## Assumptions` — labeled `human-confirmed` / `code-verified` / `assumed`.
6. **Commit + push once to trunk:** set `current.json.stage = "plan"`, append `plan→plan-review · completed` to `.pipeline/<feature>/journal.md` (physical END), `git add` `plan.md` (+ optional `CONTEXT.md`/`docs/adr/*`/`control.json`) + `current.json` + `journal.md`, commit once, push.
7. **Print handoff** to **`pipeline-plan-review`** (independent reviewer model B ≠ planner model A). If running local headless Step 2 (`run-loop.py review-plan`), invoke it now; loop up to 2 revisions (3rd `REJECT` ⇒ STOP for human).

## Hard rules

- No product code and no product tests in Step 1.
- Every `verify:` command must be concrete, runnable, and deterministic (never prose).
- Never exceed 10 specs in `## Specifications`; split oversized work into a follow-up feature.
- Never self-review your own plan — Step 2 MUST run on an independent reviewer model.
