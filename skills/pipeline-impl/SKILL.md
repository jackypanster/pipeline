---
name: pipeline-impl
description: "Pipeline Step 3 (implementer role, coding CLI C != reviewer B) — implement approved .pipeline/<feature>/plan.md AND unit/integration tests covering T1-T10 on feat/<feature>, pass every verify command green, record impl-report.md, and hand off to pipeline-review. Zero planner tokens. Args: repo, branch."
---

# pipeline-impl

Step 3 of the 3-Agent 4-Step pipeline. Follow the **shim loop in CONTRACT.md** with slot = `impl`.
**Role:** `implementer` (capable/cost-efficient coding CLI C; MUST differ from reviewer model B).

**Coordinated dispatch guard:** with a five-field envelope, step 0 runs CONTRACT §Pre-write
stale-dispatch guard BEFORE any write; a mismatch ⇒ `STALE_DISPATCH` and STOP (zero writes).
Preserve `control.json`; never modify it.

**Skill:** `impl` slot resolves to your runtime's real installed autonomous coding skill (never a
placeholder like `<autonomous-coding-skill>`).

## Steps

0. **Preflight** — run `bash <this skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage impl` (+ five envelope fields if present). `0` ⇒ pull, `current.json`, slot resolve, guard, and `plan.md` check DONE; `3` ⇒ verify named check(s) yourself, STOP on failure; else ⇒ STOP.
1. **Verify Step 2 approval & plan pin:** verify `current.json.approved_plan_blob` matches `HEAD:.pipeline/<feature>/plan.md` (enforced by step 0 `preflight.sh`), and read the immutable approved plan via `git cat-file blob <approved_plan_blob>` plus the tail of `journal.md`. Proceed ONLY when the tail is `plan-review→impl`, `human→impl` (operator approval after `pause_after_plan: true`), `review→impl`, or `hunt→impl`. On rework, read the latest `.pipeline/<feature>/reviews/code-review-NN.md` in full.
2. **Prepare `feat/<feature>` branch** (one feature branch, carries reviewable code + tests ONLY — NEVER commit `.pipeline/` files onto `feat/<feature>`):
   - First run: create `feat/<feature>` from trunk (`main`/`master`).
   - Rework run: check out existing `feat/<feature>` (rebase onto trunk only if trunk advanced cleanly).
3. **Implement code + T1–T10 tests in the same step:**
   - Implement all specs in `plan.md` within scope; **NEVER touch any path matching `forbid-paths:`** and NEVER edit `plan.md`.
   - Write real unit/integration tests covering **every `T1–T10` row** in `plan.md` (1 rule = ≥1 real test; assert real behavior and failure modes — zero hollow/tautological tests; Step 4 runs a mutation spot-check and rejects `weak_test`).
   - On rework rounds, add a regression test reproducing every behavioral finding from `code-review-NN.md`.
4. **Deterministic `verify` pre-gate (MANDATORY before finishing):** run EVERY command listed in `plan.md`'s `verify:` frontmatter. All MUST exit 0 (green). Never push red code or skip a verify command.
5. **Commit + push `feat/<feature>`**, and open/update a PR via the forge adapter (`gh pr create` / `gitee-cli`; if forge token is absent, push the branch and note manual PR in handoff).
6. **Commit trunk metadata ONCE** (switch to trunk or update trunk checkout — never leave `.pipeline/` edits on `feat/<feature>`):
   - Write `.pipeline/<feature>/impl-report.md` containing:
     - `## Assumptions` — every un-pinned decision made (or literally `none`; missing section is a mechanical review rejection)
     - `## Tests & Verify` — test names added/run, exact `verify:` command exit statuses, and `Not run` (explicitly `none` if all ran)
   - Set `current.json.stage = "impl"` (and `pr` if opened).
   - Append `impl→review · completed` to the **physical END** of `.pipeline/<feature>/journal.md` (`cat >>`), self-verify the tail header matches:
     `^## seq=[0-9]+ · [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z · impl→(review · completed|human · blocked) · by=.+$`
     using `WRITE_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)` captured immediately before writing.
   - `git add` `impl-report.md` + `current.json` + `journal.md`, commit once, push trunk.
7. **Unrecoverable failure (cannot pass `verify` within budget):** do NOT push broken code to review. On trunk, append `impl→human · blocked` to `journal.md` with the exact failing command and error output, set `current.json.stage = "blocked"`, commit, push, STOP.

## Hard rules

- Never edit `plan.md` or any path in `forbid-paths:`. Never merge. Never force-push trunk.
- Never deliver code without real T1–T10 tests and 100% green `verify:` output.
- Never write `.pipeline/` metadata onto `feat/<feature>`; keep `feat/<feature>` pure code+tests.
- Always append `journal.md` at physical END and self-verify before printing the handoff.
