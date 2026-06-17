# pipeline — design contract

A thin skill-aggregation pipeline. The **only durable asset is this contract** (command sequence +
handoff format + git+md state convention). Each command is a ~20-line shim delegating to a
swappable skill. Forge-agnostic, machine-agnostic, human-relayed, no scheduler.

## Core principle: skills reason, the shim does I/O

The aggregated skills are **reasoning/interview engines** — they do not write the artifacts. Writing
`PRD.md` / the red test / the review report, running the freeze gate, opening a PR, committing — all
of that is the **shim's** contract, never assumed of the skill. (Verified: `think` is explicitly
"No code"; `grill-me` only interviews.)

Each command body (~20 lines):
`git pull --rebase → read current.json + md → resolve skill via roles.yaml → invoke skill →
write only your stage's declared write-set + commit → print handoff`.

## Commands

| command | delegates to (skill) | shim / agent I/O |
|---|---|---|
| pipeline-prd | `grill-me` (clarify) → `think` (plan) | agent writes `PRD.md` + commit |
| pipeline-arch | `grill-with-docs` (walks the design tree; emits CONTEXT.md + ADRs) | land `arch.md` / `CONTEXT.md` / ADRs |
| pipeline-task | `think` (decompose into atomic cards) | **agent writes the red-test code** (think won't) + card frontmatter; freeze into `spec-paths:`; push |
| pipeline-impl | `goal` (think → design tests → code → check loop; white-box tests in `impl-paths:`) | shim freezes `spec-paths:`; opens PR/branch; status → review |
| pipeline-review | `check` (semantic review of the diff/PR) | shim adds the `spec-paths` freeze gate + drives merge after human confirm |
| pipeline-hunt | `hunt` (root-cause) | **entry for `blocked` cards** — root-cause before re-queue, never blind retry |

```yaml
# .pipeline/roles.yaml  (one per target repo; any line independently swappable to a best-of-breed skill)
prd:    [grill-me, think]
arch:   grill-with-docs
task:   think
impl:   goal-driven-implementation   # drives the Hermes /goal loop
review: check
hunt:   hunt
```

`roles.yaml` names the skill only; which agent/bot you paste into selects the runtime. On init, a
command verifies every slot resolves to an installed skill (hard gate — no silent mid-run failure).

## State convention (git + md, zero forge dependency)

```
.pipeline/
  current.json              {repo, branch, pr?, feature, stage}   # single pointer
  <feature>/
    PRD.md  arch.md  CONTEXT.md  docs/adr/*.md
    tasks/NN.md             frontmatter: status / attempts / verify / spec-paths / impl-paths / spec-rev
    reviews/review-NN.md
```

Status machine: `todo → in-progress → review → done`; `blocked` terminal; `attempts++`,
`>=3 ⇒ blocked ⇒ pipeline-hunt`. A context-less bot rebuilds full state from `git pull` +
`current.json` + a card scan. One feature in flight at a time (the human serializes; `current.json`
is a single pointer by design).

## Handoff string (human copies to the next bot)

```
>>> NEXT
Run pipeline-impl.
repo=<git-remote-url> branch=feat/login pr=none
- artifact: .pipeline/login/tasks/03.md (pushed)
- do: git pull --rebase, pick oldest todo card, make its red test green
- DO NOT touch spec-paths; white-box tests in impl-paths OK
- attempts=1/3 — two more failures ⇒ blocked ⇒ run pipeline-hunt
- on green: open PR, status=review, run pipeline-review
<<< END
```

Carries: next command, repo, branch, pr?, artifact path (never the body — git is the bus),
attempts/blocked path, the first action (`git pull`). Self-contained: the next bot shares no memory.

## Test ownership (anti-cheat)

`pipeline-task` writes the failing red test and freezes it into `spec-paths:` (recording `spec-rev`).
`pipeline-impl` only makes it green and may add white-box tests in `impl-paths:` — it must not touch
`spec-paths:`. `pipeline-review` enforces this with the two-commit `git diff <spec-rev> <review-tip> --
<spec-paths>` (review-tip = PR head) and **fails if the frozen spec changed**. Deterministic, git-only,
no CI required. The coder tool (`goal`) writes
its own tests by default — we don't fight it; the diff gate is what guarantees the exam wasn't edited.

## Forge / review surface

Review prefers the forge's PR (auditable thread + a clean human merge gate), falling back to a plain
branch diff when no forge PR exists. An adapter keyed on `git config --get remote.origin.url`:

- **GitHub** → `gh` (`gh pr diff` / `gh pr merge`).
- **Gitee** → `gitee-cli` against the instance's PR API.
- **anything else / no forge** → `git fetch && git diff base..branch → check` (forge-agnostic).

Merge is always human-confirmed (only the reviewer merges, after the human gate). The pipeline never
performs destructive operations (no repo/branch deletion beyond the task's own branch, no force-push).

## Thin aggregator + swappability

Each command is independently refactorable: it declares which skill slot it delegates to plus the
input/artifact contract, nothing more. A better skill drops in by editing one `roles.yaml` line; the
contract is unchanged. The frozen protocol (status machine, attempts, only-reviewer-merges) lives in
this file; commands carry no logic of their own.

## Borrowed / rejected

**Borrowed:** the ~20-line read-prior/write-next command shape (spec-kit); a truth-vs-proposal file
split (OpenSpec). **Rejected:** spec-kit's `specify` CLI / templates / constitution machinery;
heavy multi-subagent runtimes. We ship N markdown skill files — no CLI, no DB, no scheduler.

## Constraints

No cron (human relays) · not coupled to any machine · LLM-agnostic (reasoning commands want a
frontier model; `impl` tolerates a capable local LLM) · commands are extensible — a new verb is a new
~20-line shim + one `roles.yaml` line + the prior command's handoff naming it (e.g. `deploy`, `test`,
`learn` for unfamiliar-domain research).

## Open items

1. **Map the design-tree *shape* before walking its branches.** `grill-with-docs` walks branches but
   does not first establish the tree's shape — an open methodology gap, deferred.
2. **`pipeline-learn`** (a research stage before arch for unfamiliar external dependencies) — add when
   a domain-unfamiliar requirement actually appears, not before.
3. Skills are not yet scaffolded; this repo currently holds the contract only.
