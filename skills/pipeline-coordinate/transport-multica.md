# pipeline-coordinate — `multica` transport (per role)

Optional per-role transport replacing Herdr panes with `multica` issues while keeping every
`CONTRACT.md` and `pipeline-coordinate/SKILL.md` hard rule unchanged. Git and forge state stay the
only truth.

## Resolution (independent per role + optional `multica-profile`)

First match wins: (1) invocation selector (`review=multica:<agent>`, `impl=multica:<agent>`,
`multica-profile=<name>`, or `review=herdr` / `impl=herdr` override); (2) operator-local
`~/.config/pipeline/transport` (one `key=value` per line; `#` comments ignored; duplicate or unknown
line ⇒ STOP; coordinator reads only, NEVER edits); (3) Herdr default. With `multica-profile=<name>`,
pass `--profile <name>` to every `multica` command.

## Write ban (absolute, both roles)

Coordinator writes to a review or impl issue **EXACTLY ONCE — at `issue create`**. Never comment,
re-assign, change status, rerun, or cancel: the coordinator CLI holds the operator token, so any
comment looks like `author_type: member` (indistinguishable from a human GO token). Every rework
or re-review round is a **NEW issue** built from a fresh Git observation. Read-only queries
(`issue runs`, `issue get`, `issue comment list`) are always allowed.

## Preflight (per role on `multica`)

Any miss ⇒ STOP and ask:
- `multica agent list --output json`: exactly ONE active agent matching `<agent name>`; record `id`,
  `runtime_id`, `model`.
- `multica runtime list --output json`: that `runtime_id` has `status: online`.
- **Model independence:** `model` is non-empty and DISTINCT from the other roles' models
  (`reviewer ≠ planner` and `reviewer ≠ implementer`).
- **Reviewer only:** agent `instructions` contain marker `pipeline-review-agent v3` (see §Setup).
- **Local clones (Profile B):** every clone staying local is independent (distinct
  `git rev-parse --git-common-dir`), checked out on trunk, sharing one remote URL, with valid
  `control.json` and parseable `journal.md` tail.

## Reviewer (`review=multica:<agent>`)

**Send (one dispatch = ONE NEW issue):**
```bash
printf '%s\n' "<description>" | multica issue create --title "review <repo>#<n> @<short sha>" \
  --description-stdin --assignee-id <agent id> --output json
```
- **Profile A (meta-PR):** snapshot latest non-bot PR comment BEFORE create. Description tells
  reviewer to read `~/.agents/skills/pipeline-review/SKILL.md` in meta-PR mode for `<pr-url>`
  (`base=<b> head=<sha40>`), clone into its empty working directory, `cd` into the clone, post
  verdict as PR comment naming `<sha40>`, and arm/consume the issue-thread GO-gate.
- **Profile B (feature — Step 2 or Step 4):** build the 5-field envelope from ONE fresh remote trunk
  read. Description tells reviewer to read `~/.agents/skills/pipeline-plan-review/SKILL.md` (Step 2)
  or `~/.agents/skills/pipeline-review/SKILL.md` (Step 4) in FEATURE mode, clone, `cd` INTO the
  clone before running `preflight.sh` (`repo=<abs clone path>` filled by reviewer), publish ONE
  atomic trunk metadata commit, and (on Step 4 `ACCEPT`) arm the issue-thread GO-gate naming `<sha40>`.

**Wait:**
- **Profile A:** edge-triggered PR comment newer than snapshot, non-bot, naming `<sha40>` with
  explicit decision.
- **Profile B:** remote `journal.md` tail at `expected_seq + 1` matching a legal review transition
  (`plan-review→impl|plan|human`, or `review→review|impl|human`).
- On every poll, also check latest `multica issue runs <issue> --output json`: `failed`/`cancelled`,
  `completed` with no Git/PR delivery, still `queued` after 5 min, or CLI error ⇒ STOP.

**Merge gate:** tell the human to post `go` / `merge` / `confirm` as the ENTIRE comment on issue
`<identifier>`. Coordinator sends nothing; watch forge PR state until merged, then verify
`review→done · completed` landed on trunk.

### Reviewer agent setup (once per reviewer agent)

```text
pipeline-review-agent v3
You are a pipeline REVIEWER node. Act only on an issue instructing you to run pipeline-plan-review
or pipeline-review; otherwise reply out-of-scope and stop. Read the named SKILL.md in full and
follow it exactly. For pipeline-review, if the PR is closed or its head != dispatched SHA, comment
once and stop. Review only: never modify product code or tests (except a temporary uncommitted
mutation spot-check reverted immediately). Push commits ONLY for feature-mode metadata under
.pipeline/ (reviews/, journal.md, current.json) on trunk — never product code, never plan.md, never
force-push. Merge ONLY via pipeline-review's issue-thread GO-gate. Never create/assign issues or
@mention another agent. Set status with --no-start: in_progress on start; in_review before arming
GO-gate; done after changes-requested or post-merge commit; blocked on any other stop.
```

## Implementer (`impl=multica:<agent>`)

**Send (one dispatch = ONE NEW issue):**
- **Profile A (meta-PR):** snapshot `git ls-remote origin refs/heads/impl/<branch>` (must be absent
  on round 1; equals last promoted SHA on fix rounds). Description carries full handoff; implementer
  pushes ONLY to delivery ref `impl/<branch>`. Coordinator fetches, runs tests locally, checks diff
  scope, and fast-forward promotes `<verified sha>:refs/heads/<branch>` before opening/updating PR.
- **Profile B (feature — Step 3):** build 5-field envelope from fresh remote trunk read. Description
  tells implementer to read `~/.agents/skills/pipeline-impl/SKILL.md`, clone, `cd` INTO the clone,
  run `preflight.sh` with all 5 fields, implement `.pipeline/<f>/plan.md` + T1–T10 tests on
  `feat/<f>`, pass every `verify[]` green, never touch `plan.md` or `forbid-paths`, push `feat/<f>`,
  and publish trunk metadata (`impl-report.md`, `current.json`, `journal.md`).

**Wait:**
- **Profile A:** check ALL runs of the issue (ANY `failed`/`cancelled` ⇒ STOP, implementer death) +
  `git ls-remote origin refs/heads/impl/<branch>` advanced cleanly with snapshot as ancestor.
- **Profile B:** remote `journal.md` tail at `expected_seq + 1` (`impl→review · completed` or
  `impl→human · blocked`) + `multica issue runs` health check.

## Outage / failover

Never switch transport automatically. On outage/quota failure, STOP and report exact state + override
command (`review=herdr` / `impl=herdr`) or headless `run-loop.py` fallback for the operator to choose.
