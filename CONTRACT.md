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
6. **Write exactly one artifact** under `.pipeline/<feature>/` (the I/O is YOURS, not the skill's),
   `git add <that one file>`, commit.
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

## Test ownership (anti-cheat)

`pipeline-task` writes the **failing red test** into `spec-paths:` and records the commit as
`spec-rev:` in the card. `pipeline-impl` only makes it green, may add white-box tests in
`impl-paths:`, and must NOT touch `spec-paths:`. `pipeline-review` runs
`git diff <spec-rev> -- <spec-paths>` and **FAILS if the frozen spec changed**. Git-only, no CI.

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
