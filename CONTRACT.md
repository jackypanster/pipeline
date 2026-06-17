# CONTRACT.md — the frozen pipeline protocol

Single source of truth for every `pipeline-*` skill. Commands carry **no logic of their own** —
they follow this. (See [DESIGN.md](DESIGN.md) for rationale.)

## The shim loop (every command runs exactly this)

1. **`git pull --rebase`** — always your first act (no shared memory; rebuild from git).
2. **Load the repo's local config if present.** Most projects keep config/secrets in a dotenv-style
   file (`.env`, sometimes `.env.local`/`.envrc`). The delegated skill, the build, and any forge CLI
   may need it. If such a file exists, load its vars into the environment before step 5. Do NOT assume
   a fixed name or casing — read what the repo actually uses and adapt (e.g. a lowercase `gitee_token`
   may need exporting as the upper-case `GITEE_TOKEN` a tool expects). **Never print secret values.**
3. **Read `.pipeline/current.json`** → `{repo, branch, pr?, feature, stage}`. Missing + you are
   `pipeline-prd` ⇒ create it. Missing + any other command ⇒ STOP, ask the operator.
4. **Resolve your skill**: read `.pipeline/roles.yaml`, look up your slot. Verify the named skill is
   installed on this runtime. Not installed ⇒ STOP and report (no silent fallback).
5. **Invoke that skill** — it does the REASONING/interview. It does NOT write files.
6. **Write only within your stage's declared write-set** (see *State authority & write-sets* below) —
   the I/O is YOURS, not the skill's. `git add` only those paths, commit. Writing outside your
   write-set is a contract violation, symmetric to the freeze gate.
7. **Print the handoff block** (below) and stop. The human relays it to the next bot.

## State machine (frozen — do not change)

`status: todo → in-progress → review → done`; `blocked` = terminal.
`attempts` starts at 0; on any failure or review rejection `attempts++`; `attempts >= 3 ⇒ blocked`,
and a `blocked` card routes to `pipeline-hunt`, never blind retry.
**Only `pipeline-review` merges**, and only after an explicit human confirm.
**Never force-push; never delete anything beyond a task's own branch; never touch another card.**

## Layout (`.pipeline/` lives in the TARGET repo)

```
.pipeline/
  current.json            {repo, branch, pr?, feature, stage}   # single global pointer
  <feature>/
    PRD.md  arch.md  CONTEXT.md  docs/adr/*.md
    tasks/NN.md           frontmatter: status / attempts / verify / spec-paths / impl-paths / spec-rev
    reviews/review-NN.md
```

One feature in flight at a time (the human serializes; `current.json` is a single pointer).

## State authority & write-sets

**Trunk is the single state authority.** All `.pipeline/` metadata (`current.json`, `PRD.md`,
`arch.md`, `CONTEXT.md`, ADRs, cards, reviews) AND the frozen red test live on trunk (`main`/`master`),
committed straight there. Metadata is the orchestration audit log, not reviewed product code — it is
never gated through PR review. `current.json` MUST be on trunk: it is the cold-node bootstrap pointer,
read before the node knows any branch name.

**A feature branch carries ONLY the reviewable code diff.** Name it `feat/<feature>`. `pipeline-impl`
cuts it from trunk, writes `src` + white-box tests there, opens the PR. `pipeline-review` squash-merges
it (the only merge). One branch convention, one merge style — no `task/*` names, no local non-PR merges.

**The frozen red test is committed to trunk by `pipeline-task`**, so `spec-rev` is a trunk commit the
branch inherits. Consequence: trunk's test suite is RED from the task commit until the impl merge.
Accepted ONLY under two load-bearing assumptions — **no blocking CI gate on trunk, and one feature in
flight at a time**. If either stops holding (CI added, or parallel features), move the red test onto the
feature branch and make `spec-rev` a branch commit instead.

**Each stage writes only its declared set.** Every stage also advances `current.json.stage` to name the
**most recently completed stage** (`prd|arch|task|impl|review`, or `done` once the feature's PR is
merged) — a cold node reads it to see where the pipeline last left off; the *next* node to run is named
in the handoff, not inferred from `stage`. Beyond `stage`, the artifact write-sets are:

| stage | write-set (may create/modify) | must NOT touch |
|---|---|---|
| prd | `PRD.md` | src, tests |
| arch | `arch.md`, `CONTEXT.md`, `docs/adr/*` | src, tests |
| task | spec-paths (the red test), `tasks/*` | src implementation |
| impl | impl-paths, `src/**`, the card's `status` field | **spec-paths** (the freeze gate) |
| review | `reviews/*`, card `status`→done | any product code (it merges, never authors) |

The freeze gate is just the impl row enforced. Enforcement is a documented invariant + one
`git diff --name-only` eyeball at review — NOT a hook, CI, or script.

## Test ownership (anti-cheat) — the spec-rev double-commit protocol

`pipeline-task` freezes the spec in **two ordered commits**:
1. **Freeze commit** — write the failing red test, touching **only `spec-paths`**. Its hash = `spec-rev`.
   The test must compile and FAIL here (a green "spec" is a no-op).
2. **Record commit** — write `spec-rev`, `spec-paths`, `impl-paths` (all exact paths) into the card and
   advance `current.json.stage`. This commit touches **metadata only (the card + `current.json`), never
   `spec-paths`** — so the freeze stays intact (the load-bearing rule is "never `spec-paths`", not "card
   alone"; `current.json` is metadata and is safe to ride along).

Invariant: `spec-paths ∩ impl-paths = ∅` (task asserts, review re-checks). `pipeline-impl` makes the
test green via `src` + `impl-paths` only, and must NOT create/modify/delete anything under `spec-paths`.
`pipeline-review` runs the **two-commit** diff `git diff <spec-rev> <review-tip> -- <spec-paths>`
(deterministic, not working-tree) FIRST; **non-empty ⇒ reject** (`attempts++`, route to impl, or hunt
at ≥3). If the spec itself is wrong, that is NOT an impl fix — re-route to `pipeline-task` to re-freeze
(new `spec-rev`); the coder never edits the frozen spec. Git-only, no CI.

## Handoff block — a self-contained briefing for a COLD next node

**The next node is a FRESH session — possibly a different TG bot / different frontier LLM — with ZERO
prior context.** It has only: this repo (via `git pull`), `CONTRACT.md`, and your handoff. So the
handoff must carry everything it needs to ACT, not a one-liner. Point at artifacts (git is the bus —
never paste bodies), give **concrete numbered steps**, and name **feature-specific gotchas**. A cold
frontier bot with a thin handoff guesses wrong — err toward MORE next-step detail, not less.
TG-friendly: plain text, short lines, no tables.

```
>>> NEXT
Run pipeline-<next> on a FRESH session (assume you know nothing — rebuild from the repo + CONTRACT.md).
repo=<url> branch=<branch> pr=<url|none>
First: git pull --rebase; load repo config (.env if present, per CONTRACT step 2).
Read for context (before acting):
  - <path/PRD.md>      — what
  - <path/arch.md>     — how / component boundaries / data flow
  - <path/CONTEXT.md>  — domain glossary + conventions
  - <path/tasks/NN.md , docs/adr/*> — the card / binding decisions
Your task (concrete, numbered):
  1. <step>
  2. <step>
  3. <step>
Feature gotchas (project-specific traps the next node MUST know):
  - <e.g. binary crate → review carries formatter correctness; mirror <existing pattern>;
    config var is lowercase X; this repo uses branch `master` not `main`>
Done when: <success criterion>. On success: <status transition>, then run pipeline-<after>.
On failure: attempts++; >=3 ⇒ blocked ⇒ run pipeline-hunt.
<<< END
```

Carry artifact PATHS, never bodies — git is the bus. The "Your task" + "Feature gotchas" sections are
what let a different LLM on a different bot execute this stage correctly with no shared memory.

## Forge adapter (review/merge only)

Keyed on `git config --get remote.origin.url`:
- **github.com** → `gh` (`gh pr diff` / `gh pr merge`)
- **gitee** → `gitee-cli` against the instance's PR API
- **anything else / no forge** → `git fetch && git diff base..branch`

Merge is always human-confirmed. The pipeline performs no destructive forge operations.

## Self-improvement — skills propose, review gates (never self-edit)

A running bot must **NEVER edit a live/installed skill in place** — that mutates the contract
mid-flight, untracked and ungated, and can silently break every future run. A bot's pipeline clone is
a **read-only consumer**: `git fetch && git reset --hard origin/main` each run; it does not carry local
skill edits.

When a run reveals a skill gap, **emit a proposal — do not apply it**: add a line to your report/handoff
`SKILL-PROPOSAL: <skill> — <what to change + why, one line>`. A proposal reaches `main` ONLY through the
gated path: **`pipeline-improve` opens a PR against the pipeline repo → `pipeline-review` reviews the
skill diff (real improvement, not a weakening? every existing hard rule preserved?) → a human confirms
the merge.** No auto-merge; the agent never merges its own proposal.

The **frozen invariants** (state machine · only-reviewer-merges · the freeze gate · never-force-push)
are **not auto-improvable** — a proposal touching them is STOPPED for explicit human decision. Skills are
markdown + git: a bad edit only mis-guides the next run (caught by review), and is one `git revert` away.
