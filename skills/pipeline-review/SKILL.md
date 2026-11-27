---
name: pipeline-review
description: "Pipeline Step 4 (reviewer role, independent frontier model B) — semantic + anti-cheat review of feat/<feature> (forbid-paths, verify, T1-T10 coverage, mutation spot-check), structured VERDICT | CAUSE, 3rd-reject breaker, and human-direct GO-gate squash-merge. Only this command merges. Args: repo, base, branch, optional pr."
---

# pipeline-review

Step 4 of the 3-Agent 4-Step pipeline. Follow the **shim loop in CONTRACT.md** with slot = `review`.
**This is the ONLY command that merges, and ONLY after an explicit direct human confirm.**

**Coordinated dispatch guard:** with a five-field envelope, step 0 runs CONTRACT §Pre-write
stale-dispatch guard BEFORE any write; a mismatch ⇒ `STALE_DISPATCH` and STOP (zero writes).
Preserve `control.json`; never modify it.

**Skill:** `review` slot resolves to `check` — semantic + adversarial review of the diff.

## Meta-PR mode (pipeline self-improvement) — decide this BEFORE step 0

If the PR is a **toolchain meta-PR** — a diff to *this pipeline repo's* own files (`skills/*`,
`CONTRACT.md`, `DESIGN.md`, `README.md`, `roles.yaml`, etc.), or a sibling toolchain repo
(`pipeline-driver` / `pipeline-dashboard` / `pipeline-dispatch`) with **no `.pipeline/` feature
state** — run **meta-PR mode ONLY** (skip feature steps 0–6):
1. Run `check` on the diff: is it a real improvement (not a weakening)? Does it preserve every hard
   rule and frozen invariant (explicit human approval required to change frozen invariants)?
2. **Size-budget axis:** for each `skills/*/SKILL.md` with net growth `(added - deleted) > 0` AND
   post-merge `wc -l > 120`, the PR body MUST carry an explicit size-budget justification (why not
   net-neutral, why not in `CONTRACT.md` / reference file), else request changes.
3. **Evidence tier:** tag every blocking finding `observed` (repro/citation) or `hypothetical`
   (reasoned-only). For this single-operator toolchain, `hypothetical` does NOT block unless it
   violates a hard rule or frozen invariant.
4. Post the verdict as a PR comment, then on direct human confirm via the same GO-gate (step 6),
   **squash-merge**. Only-reviewer-merges, human-confirm, never-force-push all still bind.

## Feature mode steps

0. **Preflight** — run `bash <this skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage review` (+ five envelope fields if present). `0` ⇒ pull, `current.json`, slot resolve, guard, and `plan.md` check DONE; `3` ⇒ verify named check(s) yourself, STOP on failure; else ⇒ STOP.
1. **Inputs:** read `.pipeline/<feature>/plan.md`, `.pipeline/<feature>/impl-report.md`, and the diff via the forge adapter (`gh pr diff` / `gitee-cli` / `git diff <base>..feat/<feature>`). Verify model independence (`reviewer != implementer` and `reviewer != planner`).
2. **Deterministic gates (run FIRST — fail immediately on violation):**
   - **`forbid-paths` gate:** diff `git diff --name-only <base>..<review-tip>` MUST NOT touch any path in `plan.md` `forbid-paths:` nor `plan.md` itself ⇒ else `REJECT | CAUSE: scope`.
   - **`impl-report.md` assumptions check:** missing `## Assumptions` section ⇒ `REJECT | CAUSE: scope`; assumption contradicting `plan.md` ⇒ `REJECT`.
   - **`verify` re-run gate:** run EVERY command in `plan.md` `verify:` on `feat/<feature>` HEAD — all MUST exit 0 ⇒ else `REJECT | CAUSE: impl_bug`.
3. **Anti-cheat & semantic review (`check`):**
   - **T1–T10 coverage audit:** verify every `T1–T10` row in `plan.md` maps to a real test in the diff.
   - **Mandatory mutation / revert spot-check:** deliberately break 1–2 core logic branches (in a temporary uncommitted edit, reverted immediately after running) and re-run the targeted test — if tests stay green ⇒ immediate **`VERDICT: REJECT | CAUSE: weak_test`**. *(In a strict read-only sandbox where file writes are blocked, perform an adversarial assertion audit proving each T1–T10 test fails under concrete logic inversion and flag `weak_test` on any tautological or mocked-away assertion.)*
   - **Semantic review:** real bugs, edge cases, regressions, security/rollback boundaries.
4. **Strict binary verdict — line 1 MUST match:**
   `VERDICT: <ACCEPT|REJECT> | CAUSE: <none|impl_bug|weak_test|scope|plan_fact|plan_design|env>`
   (`ACCEPT` pairs only with `CAUSE: none`; no `ACCEPT WITH NITS` on code). Write `.pipeline/<feature>/reviews/code-review-NN.md`.
5. **Atomic trunk outcome (ONE commit on trunk: `code-review-NN.md` + `current.json` + `journal.md`):**
   - **`ACCEPT`:** set `current.json.stage = "review"`, append `review→review · completed` whose first line after `>>> NEXT` is exactly `Await human-direct merge confirmation in this reviewer session.`, commit, push, then arm the GO-gate (step 6) and STOP.
   - **`REJECT` with `CAUSE: plan_fact | plan_design | env` (short-circuit):** set `current.json.stage = "blocked"`, append `review→human · blocked` (plan/env defect — do NOT loop Step 3), commit, push, STOP.
   - **`REJECT` with `CAUSE: impl_bug | weak_test | scope`:** increment `current.json.review_rejects`:
     - `review_rejects < 3` ⇒ set `current.json.stage = "review"`, append `review→impl · failed` (NEXT `Run pipeline-impl`), commit, push.
     - **`review_rejects >= 3` ⇒ circuit breaker!** Set `current.json.stage = "blocked"`, append `review→human · blocked` (3rd code review rejection — human intervention required), commit, push, STOP.
6. **GO-gate & squash-merge (ONLY on `ACCEPT`):**
   - **Terminal session:** print `APPROVED head=<full 40-hex SHA> — reply IN THIS session with ENTIRE trimmed text 'go' (or 'merge'/'confirm', case-insensitive) to squash-merge; anything else disarms.` STOP. Consume ONLY on that exact direct operator reply in this same session (never relayed/forwarded, never substring) AND after re-verifying the PR/branch head still equals the exact armed 40-hex SHA (moved head ⇒ disarm, re-review). When taking over after a headless `run-loop.py phase2` `ACCEPT`, read the full 40-hex SHA from the `journal.md` tail, confirm it matches `code-review-NN.md` (`VERDICT: ACCEPT` + `Reviewed-Head`) and the live PR/branch head, then arm this GO-gate.
   - **Multica issue-thread form (headless):** post the arming comment naming the approved full head SHA and telling the operator to comment `go`/`merge`/`confirm`. Consume ONLY after ONE complete unfolded `multica issue comment list <issue> --full --output json` (no cursor truncation, ordered by `created_at` with no tie) proving your arming comment is your latest, the first comment after it is `author_type: member` with exact trimmed token, and PR head == armed SHA; any failure disarms and replies once with the reason.
   - **On confirm:** squash-merge the exact armed SHA via forge adapter (`gh pr merge --squash --match-head-commit <sha40>` where supported; delete `feat/<feature>`), set `current.json.stage = "done"`, append `review→done · completed` to `journal.md`, commit + push trunk.

## Completion checklist (do ALL in order on merge)

- [ ] `forbid-paths` clean + `verify:` green + T1–T10 covered + mutation/assertion spot-check passed
- [ ] wrote `.pipeline/<feature>/reviews/code-review-NN.md` with exact `VERDICT | CAUSE` line 1
- [ ] squash-merged PR and deleted `feat/<feature>`
- [ ] set `current.json.stage = "done"`, appended `review→done · completed` to `journal.md`, pushed trunk

## Hard rules

- Only `pipeline-review` merges, strictly on a direct human GO-gate token. Never force-push.
- Chat verdict must be plain text (short bullets, NO markdown tables, NO ATX `#` headings).
- A merge without `code-review-NN.md`, `stage: done`, and `review→done` journal entry is incomplete.
