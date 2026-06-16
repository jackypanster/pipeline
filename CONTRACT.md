# CONTRACT.md — the frozen pipeline protocol

Single source of truth for every `pipeline-*` skill. Commands carry **no logic of their own** —
they follow this. (See [DESIGN.md](DESIGN.md) for rationale.)

## The shim loop (every command runs exactly this)

1. **`git pull --rebase`** — always your first act (no shared memory; rebuild from git).
2. **Read `.pipeline/current.json`** → `{repo, branch, pr?, feature, stage}`. Missing + you are
   `pipeline-prd` ⇒ create it. Missing + any other command ⇒ STOP, ask the operator.
3. **Resolve your skill**: read `.pipeline/roles.yaml`, look up your slot. Verify the named skill is
   installed on this runtime. Not installed ⇒ STOP and report (no silent fallback).
4. **Invoke that skill** — it does the REASONING/interview. It does NOT write files.
5. **Write exactly one artifact** under `.pipeline/<feature>/` (the I/O is YOURS, not the skill's),
   `git add <that one file>`, commit.
6. **Print the handoff block** (below) and stop. The human relays it to the next bot.

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

## Handoff block (TG-friendly: no tables, short lines, conclusion then bullets)

```
>>> NEXT
Run pipeline-<next>.
repo=<git-remote-url> branch=<branch> pr=<url|none>
- artifact: <path just written>
- do: <one-line instruction for the next command>
- attempts=<n>/3 — two more failures ⇒ blocked ⇒ run pipeline-hunt
- on success: <status transition> then run pipeline-<after>
<<< END
```

Carry the path, never the body — git is the bus.

## Forge adapter (review/merge only)

Keyed on `git config --get remote.origin.url`:
- **github.com** → `gh` (`gh pr diff` / `gh pr merge`)
- **gitee** → `gitee-cli` against the instance's PR API
- **anything else / no forge** → `git fetch && git diff base..branch`

Merge is always human-confirmed. The pipeline performs no destructive forge operations.
