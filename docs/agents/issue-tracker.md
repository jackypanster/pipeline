# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues (`jackypanster/pipeline`). Use the `gh` CLI for all operations.

> **Scope.** This file configures the *issue tracker* only, for the `mattpocock/skills` engineering
> commands that read it: `/wayfinder`, `/to-tickets`, `/to-spec`, `/triage`.
> (`/implement` does not — it takes a spec or tickets already in hand and never touches the tracker.)
> It does **not** govern domain docs: `CONTEXT.md` and `docs/adr/*` are frozen by
> [CONTRACT.md](../../CONTRACT.md) to live under `.pipeline/<feature>/` in the **target** repo, and the
> `arch` stage write-set is scoped to exactly those paths. Do not infer a repo-root
> `CONTEXT.md` / `docs/adr/` layout from this file.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Triage labels

**Not configured.** This repo carries only GitHub's nine stock labels (`bug`, `documentation`,
`duplicate`, `enhancement`, `good first issue`, `help wanted`, `invalid`, `question`, `wontfix`).
Of the five canonical triage roles only `wontfix` exists, and only because it is a stock label;
`needs-triage`, `needs-info`, `ready-for-agent` and `ready-for-human` do not exist.

No skill creates them. `/triage` reads a mapping it expects to have been provided and otherwise tells
you to run `/setup-matt-pocock-skills`; `/to-tickets` and `/to-spec` apply `ready-for-agent` directly,
which fails while the label is missing. Create the four with `gh label create <name>` before using
those commands, then record the mapping in `docs/agents/triage-labels.md` and delete this section.

## Pull requests as a triage surface

**PRs as a request surface: no.** *(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)*

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label wayfinder:map`.
- **Child ticket**: an issue linked to the map as a GitHub sub-issue (`gh api` on the sub-issues endpoint). Where sub-issues aren't enabled, add the child to a task list in the map body and put `Part of #<map>` at the top of the child body. Labels: `wayfinder:<type>` (`research`/`prototype`/`grilling`/`task`). Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. Add an edge with `gh api --method POST repos/<owner>/<repo>/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`, where `<blocker-db-id>` is the blocker's numeric **database id** (`gh api repos/<owner>/<repo>/issues/<n> --jq .id`, *not* the `#number` or `node_id`). The `GET` form of the same endpoint lists current edges and was confirmed live on this repo. Where dependencies aren't available, fall back to a `Blocked by: #<n>, #<n>` line at the top of the child body. A ticket is unblocked when every blocker is closed. (Upstream's template reads blockers via `issue_dependencies_summary.blocked_by`; that key is **absent** from this repo's REST issue payload, so the frontier bullet below uses `gh issue view --json blockedBy` instead. Re-check on the next template sync.)
- **Frontier query**: list the map's open children (`gh issue list --state open`, scoped to the map's sub-issues / task list), drop any with an open blocker or an assignee; first in map order wins. Read blockers with `gh issue view <n> --json blockedBy` — verified present in `gh` 2.96.0, shape `{"blockedBy":{"nodes":[],"totalCount":N}}`. **`totalCount` counts every blocker, open and closed**, so gating on it alone never unblocks anything; filter `blockedBy.nodes` down to `state == "OPEN"`. Fall back to counting open issues named in the `Blocked by` line when dependencies aren't available.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.

**The `wayfinder:map` and `wayfinder:<type>` labels do not exist on this repo.** `/wayfinder` only ever
*applies* them (`gh issue create --label wayfinder:map`) — it carries no label-creation step — and `gh`
fails when a named label is missing. Create them before the first run:

```sh
gh label create wayfinder:map --description "Wayfinder map issue"
for t in research prototype grilling task; do gh label create "wayfinder:$t"; done
```
