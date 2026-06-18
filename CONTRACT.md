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
   the I/O is YOURS, not the skill's — **and append your composed handoff block as an entry to
   `.pipeline/<feature>/journal.md`** (it is part of your metadata write-set — see *Run journal*).
   `git add` those paths **+ `journal.md`**, commit **once** (the journal entry rides this same commit —
   never a separate/orphan commit, never an amend). Writing outside your write-set is a contract
   violation, symmetric to the freeze gate.
7. **Print the handoff block** (already persisted to the journal in step 6) and stop. The human relays
   the printed block to the next bot; the journal is what survives if the chat does not (see *Run
   journal* below).

## State machine (frozen — do not change)

`status: todo → in-progress → review → done`; `blocked` = terminal.
`attempts` starts at 0; on any failure or review rejection `attempts++`; `attempts >= 3 ⇒ blocked`,
and a `blocked` card routes to `pipeline-hunt`, never blind retry.
**A review rejection (freeze-gate or semantic) sends the offending card `review → todo`** — the retry
edge, so `pipeline-impl` (which picks the oldest `todo`) has an actionable target — or `→ blocked` at
`attempts >= 3`. Without this flip a rejected feature leaves every card at `review` and impl has nothing
to pick. (This completes the already-implied "reject routes to impl" semantics; the `>= 3 ⇒ blocked`
circuit-breaker is unchanged.)
**Only `pipeline-review` merges**, and only after an explicit human confirm.
**Never force-push; never delete anything beyond a task's own branch; never touch another card.**

## Layout (`.pipeline/` lives in the TARGET repo)

```
.pipeline/
  current.json            {repo, branch, pr?, feature, stage, full-verify?}  # fast pointer (cache — journal tail is authoritative); full-verify = [<build>, <whole-suite test>], set by task
  <feature>/
    journal.md            append-only run log — one entry per completed stage (see Run journal)
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

**Multi-card consequence — a card's `verify` MUST be card-scoped, never the full suite.** `pipeline-task`
freezes ALL of a feature's cards up front, so trunk's suite is RED across *every* not-yet-done card. If
card 1's `verify` ran the whole suite it could never pass while cards 2..N are still red — the loop
deadlocks. So a card's `verify` test command runs **only that card's own frozen test(s)**. The mechanism
is the task author's choice — a **test-name filter** (`cargo test smoke_login_help`, `pytest -k`,
`go test -run`; preferred, works even when several cards share one test file) or a **dedicated test
file** — the invariant is *card-scoped, not full-suite*. The cross-card integration check is a separate
**final full-suite gate**: `pipeline-review` runs the WHOLE suite once on the `feat/<feature>` branch HEAD
(which carries all frozen tests inherited from trunk + all cards' code) and must see it GREEN before the
squash-merge. The whole-suite command is **not derived/guessed** — `pipeline-task` records it as
`current.json.full-verify` (`[<build cmd>, <whole-suite test cmd>]`, the unfiltered runner) so a cold
review node runs an exact command, never "drop the filter" or read project docs. Red ⇒ the feature is
not done ⇒ do not merge; flip a card back ONLY if the failing test(s)/diff attribute the break to a
specific card (then `attempts++`, that card → `todo`/`blocked`, route impl/hunt) — a cross-card
integration failure with no single owner ⇒ **STOP and route `pipeline-hunt`**, never blind-flip a card.

**Each stage writes only its declared set.** Every stage also advances `current.json.stage` to name the
**most recently completed stage** (`prd|arch|task|impl|review`, or `done` once the feature's PR is
merged) — a cold node reads it to see where the pipeline last left off; the *next* node to run is named
in the handoff, not inferred from `stage`. `current.json.stage` is a denormalized **cache** for fast
bootstrap; the authoritative run position is the **tail entry of `journal.md`** — derive the live state
from the tail, never trust a stored `stage` that disagrees with it. Beyond `stage`, the artifact
write-sets are:

| stage | write-set (may create/modify) | must NOT touch |
|---|---|---|
| prd | `PRD.md` | src, tests |
| arch | `arch.md`, `CONTEXT.md`, `docs/adr/*` | src, tests |
| task | spec-paths (the red test), `tasks/*` | src implementation |
| impl | impl-paths, `src/**`, the card's `status` field | **spec-paths** (the freeze gate) |
| review | `reviews/*`, card `status`→done | any product code (it merges, never authors) |

The freeze gate is just the impl row enforced. Enforcement is a documented invariant + one
`git diff --name-only` eyeball at review — NOT a hook, CI, or script.

Every stage additionally **appends one entry to `journal.md`** (append-only metadata, same class as
`current.json` — not gated, rides the metadata commit). This is universal, so it is omitted from the
per-stage rows above.

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

## Run journal — the handoff, persisted (append-only)

The handoff block above is the **most load-bearing artifact in the pipeline** (it carries all cross-stage
context to a cold node) and was the **only one not on git** — it lived solely in chat. `journal.md` fixes
that: **at step 6, append your handoff to `.pipeline/<feature>/journal.md` as part of your stage's
metadata commit** (one atomic commit with the rest of your write-set — never a separate/orphan commit,
never an amend); step 7 only prints what is already journaled. This makes the run **resumable** (chat
dies ⇒ read the tail), **auditable** (the append sequence IS the run history), and **orchestratable by
anyone** (a human or another LLM reads the tail to take over).

One entry per completed stage, appended (never edited or deleted — the git history is the audit trail):

```
## seq=N · <ISO8601 UTC> · <from-stage>→<to-stage> · <completed|failed|blocked> · by=<bot/LLM tag|?>
done:   <1–3 lines: what this stage actually produced>
output: <artifact path(s)>        # paths, never bodies — git is the bus
--- handoff ---
>>> NEXT
…(the exact handoff block you print, verbatim)…
<<< END
```

Rules:
- **`seq`** — per-feature monotonic integer. Read the current tail, add 1 (first entry `seq=1`). It is
  the run ordinal: "we are at step N".
- **Append-only.** Never rewrite or delete a prior entry. A correction is a NEW entry, not an edit.
- **One commit.** The entry rides your stage's metadata commit (step 6) — `git add journal.md` alongside
  your other write-set paths and commit once. Never a separate/orphan commit, never `git commit --amend`.
- **Tail is authoritative.** The live position = the last entry's `to-stage` + its handoff `>>> NEXT`.
  `current.json.stage` is only a fast cache; on any disagreement the journal tail wins.
- **Resume protocol (cold start, chat gone):** `git pull --rebase` → open `journal.md` → read the LAST
  entry → its handoff IS your briefing. `current.json` is a hint, the journal is the source.
- A `failed`/`blocked` stage still appends an entry (status + the failure handoff to hunt) — the dead end
  is part of the auditable history, not silently dropped.

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

This review runs in **meta-PR mode**: the pipeline repo has **no `.pipeline/` state** (no `current.json`,
cards, `spec-rev`), so `pipeline-improve` does NOT run the feature shim loop, and `pipeline-review` skips
its feature steps (cards / freeze gate / full-suite gate) and does **semantic review only** — real
improvement + every hard rule and frozen invariant preserved — then human-confirm + squash-merge. The
feature freeze-gate machinery does not apply to a skill diff; only-reviewer-merges and human-confirm
still do.

The **frozen invariants** (state machine · only-reviewer-merges · the freeze gate · never-force-push)
are **not auto-improvable** — a proposal touching them is STOPPED for explicit human decision. Skills are
markdown + git: a bad edit only mis-guides the next run (caught by review), and is one `git revert` away.
