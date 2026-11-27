---
name: plite
description: "Thin natural-language front door to scripts/plite.py — runs a local 2-agent x 3-step coding loop (Worker + external verify + independent cross-model Reviewer) for low-risk tasks, or resumes, marks, and summarizes pilot runs. Use when the user invokes /plite, $plite, or /skill:plite, asks for a lightweight 2-agent local coding+review loop across agy/claude/codex by quota, wants to resume an interrupted plite run, backfill regression status, or check the plite pilot scorecard."
---

# plite — Local 2-Agent × 3-Step Pilot Skill

You are a **thin natural-language front door** over `<skill-dir>/scripts/plite.py` (where `<skill-dir>` is the directory containing this `SKILL.md`).

## Hard Rules (Non-Negotiable)

1. **Delegate ALL execution to `scripts/plite.py`.** Never implement the task in-session, never perform manual review, never skip external verification, and never bypass the script — even when `--worker` or `--reviewer` is the same CLI you are running in.
2. **Never auto-merge `main`.** Leave integration to the human after `plite.py` reports `APPROVED`.
3. **Never downgrade risk (87.199 admission gate).** Classify `--risk high` and **STOP** (recommend full `pipeline`: `prd → arch → task → impl → review`) if **any 1 HARD** or **≥2 SOFT** triggers match:
   - **HARD**: H1 unclear/non-objective acceptance; H2 ≥2 independently mergeable cards (or multiple Engineer sessions); H3 new/changed module boundary, interface, schema, or shared state; H4 money/trading, secrets/auth, prod deploy, external comms, or permissions; H5 ≥3 structurally distinct designs; H6 cross-repo/system/multi-agent.
   - **SOFT**: S1 >8 files or new service; S2 conflicting requirements; S3 needs live account/external dep check before design; S4 recurring area in `LESSONS.md`; S5 migration/multi-stage change; S6 ~1h of agent work with fuzzy scope.
4. **Never pass `--allow-protected-edit` unless the user explicitly requested editing build/test/CI config.**
5. **Never attempt to bypass `integrity_violation`.** Report the quarantined worktree and stop.
6. **Never fabricate `mark` evidence.** Only pass `--merged-sha` / `--regression` from confirmed git history and post-merge observation; `APPROVED` alone is NOT `none_observed`.

## Route the User's Request

- **New task** → complete Preflight below, then run `python3 <skill-dir>/scripts/plite.py run ...`.
- **Resume / swap quota** → apply Model Selection rule below, then run `python3 <skill-dir>/scripts/plite.py resume --run-id <id> [--worker ...] [--reviewer ...]`.
- **Post-merge backfill** → verify real merge SHA & regression observation first, then `python3 <skill-dir>/scripts/plite.py mark --run-id <id> --merged-sha <sha> --regression <none_observed|confirmed> [--human-time <sec>]`.
- **Pilot scorecard** → `python3 <skill-dir>/scripts/plite.py summary`.

## New-Task Preflight & Model Selection

1. **Repo & base**: resolve `--repo` (default: current git repo root) and `--base` (default: `main` or repo default branch).
2. **Real `--verify`**: inspect repo config/tests read-only (`pyproject.toml`, `package.json`, `Cargo.toml`, `Makefile`, etc.). Never invent an unverified command; ask if none exists.
3. **Concrete `--acceptance`**: write objective, testable criteria (no placeholders).
4. **Risk gate**: evaluate HARD/SOFT checklist (Hard Rule 3). High ⇒ STOP and route to full `pipeline`.
5. **Model selection (applies to BOTH `run` and `resume`)**: choose `--worker` and `--reviewer` from `agy` / `claude` / `codex` with **distinct model families** (`anthropic` vs `openai` vs `google`; on `resume`, reviewer family must also differ from all prior `contributing_families`). Always pass explicit `--worker-model` / `--reviewer-model` for `agy` (e.g. `gemini-4-neon`), and pass `--*-model` for `codex`/`claude` whenever the user named one (e.g. `gpt-6.2-sol`).
6. **Launch**: ask one concise question only if repo, `--verify`, `--acceptance`, or agent pairing is genuinely ambiguous; otherwise print a 3-line summary (`repo/base/risk`, `worker vs reviewer`, `acceptance + verify`) and execute `scripts/plite.py` (with long/background timeout).

## Reporting Back

Report the exact status (`APPROVED` / `BLOCKED` / `ESCALATED` / `INFRA_ERROR` / `INTEGRITY_VIOLATION`), `run_id`, `approved_sha` (if approved), and verbatim `inspect` / `resume` / `mark` commands printed by `plite.py`.
