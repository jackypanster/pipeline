---
name: pipeline-hunt
description: "Pipeline escalation — root-cause a blocked feature (plan_rejects >= 3, review_rejects >= 3, or Step 4 plan_fact/plan_design/env short-circuit) before re-queue. Wraps the hunt skill. Args: repo, branch."
---

# pipeline-hunt

Escalation stage (not on the happy path). Follow the **shim loop in CONTRACT.md** with slot = `hunt`.
Entry point for any `blocked` feature — **never blind-retry**.

**Coordinated dispatch guard:** with a five-field envelope, step 0 runs CONTRACT §Pre-write
stale-dispatch guard BEFORE any write; a mismatch ⇒ `STALE_DISPATCH` and STOP (zero writes).
Preserve `control.json`; never modify it.

**Skill:** `hunt` slot resolves to `hunt` — systematic root-cause diagnosis before proposing fixes.

## Steps

0. **Preflight** — run `bash <this skill's base dir>/../pipeline-preflight/scripts/preflight.sh --stage hunt` (+ five envelope fields if present). `0` ⇒ pull, `current.json`, slot resolve + guard DONE; `3` ⇒ verify named check(s) yourself, STOP on failure; else ⇒ STOP.
1. **Read full evidence:** `.pipeline/<feature>/plan.md`, `impl-report.md`, all `.pipeline/<feature>/reviews/*.md`, and `journal.md` tail.
2. **Invoke `hunt`** to establish the single confirmed root cause and classify the route:
   - **Plan flaw (`plan_fact` / `plan_design` / oversized scope)** ⇒ reset `current.json.plan_rejects = 0`, set `stage = "hunt"`, route **`hunt→plan · completed`** (`Run pipeline-plan`) with exact corrections.
   - **Implementer defect or weak tests (`impl_bug` / `weak_test` / `scope`) on a sound plan** ⇒ reset `current.json.review_rejects = 0`, set `stage = "hunt"`, route **`hunt→impl · completed`** (`Run pipeline-impl`) with the confirmed root cause and exact constraints.
   - **Environment / dependency / external outage (`env`)** ⇒ document the operator fix; stay `blocked` until the operator confirms the environment is repaired, then re-queue.
3. **Commit + push once to trunk:** write `.pipeline/<feature>/reviews/hunt-NN.md` (confirmed root cause + route decision), update `current.json`, append `hunt→plan · completed` or `hunt→impl · completed` (or `hunt→human · blocked` for unresolved `env`) to `journal.md`, commit once, push.
4. **Print handoff** naming `hunt-NN.md` and the exact next stage.

## Hard rules

- Confirm root cause before proposing any fix. No speculative re-queue.
- You diagnose and re-route; you do NOT edit product code, edit `plan.md`, or merge.
