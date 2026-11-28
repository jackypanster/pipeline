---
name: pipeline-plan-review
description: "Pipeline Step 2 (reviewer role, independent frontier model B != planner A) — adversarially verify .pipeline/<feature>/plan.md, spot-check >=3 codebase citations, and emit structured VERDICT | CAUSE. ACCEPT / ACCEPT WITH NITS proceeds to pipeline-impl by default; 3rd REJECT stops for human. Args: repo, branch."
---

# pipeline-plan-review

Step 2 of the 3-Agent 4-Step pipeline. Follow the **shim loop in CONTRACT.md** with slot = `plan-review`.
**Role:** `reviewer` (independent frontier model B; MUST satisfy `model(reviewer) ≠ model(planner)`).

**Coordinated dispatch guard:** with a five-field envelope, step 0 runs CONTRACT §Pre-write
stale-dispatch guard BEFORE any write; a mismatch ⇒ `STALE_DISPATCH` and STOP (zero writes).
Preserve `control.json`; never modify it.

**Skill:** `plan-review` slot resolves to `check` — adversarial design/evidence review.

## Steps

0. **Preflight** — run `bash <this skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage plan-review` (+ five envelope fields if present). `0` ⇒ pull, `current.json`, slot resolve, guard, and `plan.md` frontmatter check DONE; `3` ⇒ verify named check(s) yourself, STOP on failure; else ⇒ STOP.
1. **Model-independence check:** require a known `planner_model` (from `plan.md` or `current.json`). If `planner_model` is missing/unknown OR matches your own model, **STOP** — never grade your own plan or proceed without verified model separation.
2. **Read committed `plan.md` blob:** read `git cat-file blob "$(git rev-parse HEAD:.pipeline/<feature>/plan.md)"` (the exact immutable blob you will pin as `approved_plan_blob`, unaffected by checkout filters) plus `CONTEXT.md` / ADRs / prior `plan-review-*.md` if present.
3. **Adversarial verification (read-only against code):**
   - **Evidence spot-check (mandatory):** open the real files in the repo and verify **≥3 citations** from `## Baseline Evidence`. Any fabricated/stale symbol, path, or flag ⇒ `REJECT | CAUSE: plan_fact`.
   - **Spec & testability check:** `## Specifications` has ≤10 atomic items; every `T1–T10` row states observable behavior (not brittle private names) with clear pass/fail criteria; no vague "etc."/"use judgment" gaps that force the implementer to guess.
   - **Verify & safety check:** `verify:` commands are concrete and cover build/lint/tests; `forbid-paths:` protects sensitive/out-of-scope files; security/rollback boundaries match repo rules (`AGENTS.md`).
4. **Decide verdict — line 1 MUST match the exact machine-parseable header:**
   - `VERDICT: ACCEPT | CAUSE: none` — plan is sound and implementation-ready.
   - `VERDICT: ACCEPT WITH NITS | CAUSE: none` — 1–2 minor doc/wording nits that do NOT affect correctness, scope, or testability; list the nits concisely and **pass straight to Step 3** (do not burn a planner rework round).
   - `VERDICT: REJECT | CAUSE: <plan_fact|plan_design|scope|env>` — blocking defect(s) with exact `file:line` or spec-item citations and concrete fix requirements.
5. **Write `.pipeline/<feature>/reviews/plan-review-NN.md`** (next sequential `NN`, line 1 = exact `VERDICT: ... | CAUSE: ...` header, followed by verified citations and findings).
6. **Disposition — ONE atomic trunk commit** (`plan-review-NN.md` + `current.json` + `journal.md`):
   - **`ACCEPT` or `ACCEPT WITH NITS`:** record `current.json.approved_plan_blob` (`git rev-parse HEAD:.pipeline/<feature>/plan.md`):
     - If `.pipeline/<feature>/control.json` explicitly sets `"pause_after_plan": true`: set `current.json.stage = "plan-review"`, append `plan-review→human · blocked` (NEXT `Await human plan approval.`), commit, push, STOP. (Human approves by recording `human→impl · completed` or running `run-loop.py approve-plan`.)
     - **Default (proceed to Step 3):** set `current.json.stage = "plan-review"`, append `plan-review→impl · completed` (NEXT `Run pipeline-impl`), commit, push.
   - **`REJECT`:** increment `current.json.plan_rejects` (`0 → 1 → 2 → 3`):
     - `plan_rejects < 3` ⇒ set `current.json.stage = "plan-review"`, append `plan-review→plan · failed` (NEXT `Run pipeline-plan`), commit, push.
     - **`plan_rejects >= 3` ⇒ circuit breaker!** Set `current.json.stage = "blocked"`, append `plan-review→human · blocked` (3rd plan rejection — human intervention required), commit, push, STOP.
7. **Print handoff** (plain text, no markdown tables in chat verdict).

## Hard rules

- Never edit `plan.md`, product code, or tests — review only.
- Always spot-check ≥3 codebase citations against real files before approving.
- Exact line-1 verdict header is mandatory; a malformed verdict fails closed downstream.
- On the **3rd rejection** (`plan_rejects >= 3`), STOP for human — never auto-loop a 4th plan.
