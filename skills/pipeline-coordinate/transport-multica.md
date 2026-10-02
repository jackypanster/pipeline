# pipeline-coordinate — multica transport (opt-in, per role)

**Scope.** Two independent per-invocation selectors, each named ONLY by the operator's
`pipeline-coordinate` invocation, never inferred; either may be used alone; absent ⇒ Herdr exactly
as SKILL.md describes:

- `review=multica:<agent name>` — REVIEWER role, Profile A (meta-PR flow) only. Replaces only
  Preflight 1–3, send/readiness, and the merge-gate wording for the reviewer role.
- `impl=multica:<agent name>` — IMPLEMENTER role, Profile B (pipeline feature flow) only. Replaces
  only Preflight 1–3, send/readiness, and watch for the implementer role, plus Preflight 4's
  `doctor` (§Preflight 4 with the implementer on multica).

Optional `multica-profile=<name>`: run EVERY `multica` command below as
`multica --profile <name> …`. Profile A's implementer and Profile B's reviewer stay on Herdr / the
existing paths; a selector named for the other profile ⇒ stop and ask. Every SKILL.md hard rule
binds unchanged. Git and forge state stay the only truth.

## Write ban (absolute, both roles)

The coordinator writes to a review or impl issue EXACTLY ONCE — the create. It never comments on it,
re-assigns it, changes its status, reruns or cancels its runs: your CLI holds the operator's token,
so any comment you post is recorded `author_type: member`, indistinguishable from the human's GO
token. A re-review round after changes-requested is a NEW issue whose description names the
old→new head delta (SKILL.md step 5). Impl issues carry no GO token; they follow the same rule for
uniformity and auditability: the next card, or a retry after a failed card, is a NEW issue built
from a fresh observation. Read-only commands (`issue runs`, `issue get`, `issue comment list`) are
allowed any time. Needing to abort a run ⇒ stop and ask the human.

## Reviewer — `review=multica:<agent name>` (Profile A only)

### Reviewer preflight (replaces Preflight 1–3 for the reviewer; Pi and your pane still run them)

Each a command; any miss = stop and ask.

- `multica agent list --output json` (archived agents excluded by default): exactly ONE agent whose
  `name` equals the named agent exactly; record its `id`, `runtime_id`, `model`.
- `multica runtime list --output json`: the entry whose `id` == that `runtime_id` has
  `status: online`.
- **Model identity:** the agent's `model` is non-empty (empty = runtime default, unknown) and
  DISTINCT from both the Pi pane's model and this coordinator session's model. Empty or duplicate ⇒
  stop and ask the human to attest.
- The agent's `instructions` contain the marker line `pipeline-review-agent v2` (§Setup) — guards
  against an agent whose instructions forbid merging or allow dispatching.

### send (review dispatch) — one dispatch = ONE NEW issue

Take the edge-triggered verdict snapshot BEFORE dispatch exactly as SKILL.md Profile A step 4. No
per-send readiness check (the server queues); the Herdr send/readiness rules do not apply here.
Description = the step-4 dispatch line in slashless prose (no slash command can be delivered — a
preamble always precedes the text):

    printf '%s\n' "<description>" | multica issue create --title "review <repo>#<n> @<short sha>" \
      --description-stdin --assignee-id <agent id> --output json

`<description>`: "Read ~/.agents/skills/pipeline-review/SKILL.md in full and run it in meta-PR
mode on <pr-url> — toolchain meta-PR, no .pipeline state. base=<b> head=<full sha>. Your working
directory starts empty: clone the repo into it with the forge CLI and git fetch origin first.
<two or three review axes>. Verdict as a PR comment ONLY. This dispatch is a multica issue-thread
session: arm and consume the GO-gate in pipeline-review step 6's issue-thread form." Record the
returned `id` and `identifier`. A create error (including a duplicate refusal) ⇒ stop; never retry
with `--allow-duplicate` on your own. Writes to the issue after the create: §Write ban.

### wait (reviewer)

The forge verdict watcher (SKILL.md step 4) remains THE wait instrument. Each poll also takes ONE
sample of `multica issue runs <issue> --output json` and reads the run with the latest `created_at`:

- `failed`/`cancelled` ⇒ STOP, report its `error` (reviewer death; never auto-redispatch — the
  server may itself retry up to the run's `max_attempts`; that changes nothing for you).
- `completed` with no verdict comment newer than the snapshot ⇒ the stage ended without delivering
  ⇒ STOP (SKILL.md redelivery rule).
- still `queued` five minutes after the create ⇒ the runtime is not claiming ⇒ STOP.
- a CLI error or unparsable output ⇒ fail closed, STOP.

On this transport the verdict comment may have no distinct author: the reviewer posts under the
forge account of its runtime's host, which may be the operator's own — the same author as the PR and
as your own comments. SKILL.md step 4's author filter therefore cannot reliably identify the
reviewer here; use it only to exclude bots. NEWER than the pre-dispatch snapshot, non-bot, and
naming the dispatched full head SHA are NECESSARY conditions, never sufficient: the verdict also
carries the reviewer's explicit decision (approve / changes requested) for that head. A status or
dispatch comment is not the verdict even when it names the head. A rejected candidate leaves every
rule above in force — `failed`/`cancelled`, `completed` with no verdict, `queued` five minutes, and
CLI error still STOP exactly as written; rejection never resets or extends anything.

A run status is never completion evidence by itself — the verdict PR comment bound to the dispatched
head SHA is.

### Merge gate

On an approved verdict, tell the human: post `go` (or `merge`/`confirm`) as the ENTIRE comment on
issue `<identifier>` (web UI, mobile, or their own CLI). The write ban already forbids every write,
so nothing extra freezes; while the gate is armed the forge's PR state is the ONLY wait instrument,
exactly as SKILL.md step 6 says. On merge, clean up and report as usual.

### Reviewer failover

Quota exhausted or repeated `failed` ⇒ STOP. The operator may name another registered agent; rerun
the reviewer preflight for it (canonical instructions, still a third distinct model), then dispatch
a NEW issue. The coordinator never picks a replacement on its own.

### Fallback when multica is unavailable

Trigger: a `multica` command fails with a network/auth error, the preflight cannot reach the
server, or an issue is never claimed (the `queued` rule). Each already STOPS you; never switch
transport on your own. The operator re-invokes `pipeline-coordinate` WITHOUT `review=multica:…`,
putting the reviewer back on the local path: a Herdr reviewer pane when Herdr can authoritatively
monitor it, otherwise the default human-relayed handoff (CONTRACT §Coordinated mode) in which the
operator pastes the review dispatch line into the Codex terminal. Either way the GO token is typed
in the reviewer's terminal per SKILL.md step 6 and pipeline-review step 6 — nothing touches multica.
Leave a created-but-unrun review issue alone (write ban) and name it in the stop report so the
operator can cancel it; if it runs after the PR merged, the reviewer finds the PR not open and
stops. multica is an optional transport, never a pipeline dependency — removing it loses only
dispatch history.

### Setup (operator, once per reviewer agent)

Canonical instructions:

```text
pipeline-review-agent v2
You are a pipeline REVIEWER node. Act only on an issue whose description tells you to run
pipeline-review; otherwise reply that it is out of scope and stop. Read the SKILL.md path named in
the issue in full and follow it exactly. If the named PR is not open, or its head is not the
dispatched head, say so in one issue comment and stop. Review only: never modify product code or
tests, never push commits. Merge ONLY by consuming pipeline-review's GO-gate in its issue-thread
form. Never create or assign issues and never @mention another agent (CONTRACT: a stage node never
dispatches). Change issue status only on the ONE issue assigned to you, and only when its
description tells you to run pipeline-review; leave an out-of-scope issue untouched and never change
any other issue. When you start the review, set the issue to in_progress. On an approve verdict, set
in_review BEFORE posting the comment that arms the GO-gate; while the gate is armed, change nothing
on the issue. Set done after you post a changes-requested verdict (a re-review arrives as a new
issue), and done after you squash-merge and post the merge report. On any other stop on an in-scope
issue — the PR is not open, its head is not the dispatched head, or the GO-gate disarmed — set
blocked after the one comment that explains why. Set done and blocked as the LAST action of the run.
Pass --no-start on every status change; a status change must never start a run. Report only commands
you actually ran.
```

Apply: `multica agent update <agent id> --instructions "$(cat <file>)"`, then rerun the preflight.
Run transcripts, including tool inputs and outputs, are stored on the multica server.

*Accepted limitation:* token provenance is not mechanically enforced — the operator's token is also
on the coordinator's CLI. Mitigations: the write ban, the issue timeline as audit trail, and the
reviewer's own issue-thread checks — the same class as the Herdr limitation in SKILL.md step 6.

## Implementer — `impl=multica:<agent name>` (Profile B only)

Provenance: 2026-10-02, a target repo's feature run with no Pi pane — the operator directed impl
through a multica agent (pi runtime). Two cards ran green, one issue per card, one run attempt each
(12.6 and 16.8 min), dispatched as the slashless prose below; the coordinator waited on the origin
journal tail plus `multica issue runs`. A detached background poll hung silently during the first
wait (the journal had advanced; nothing woke the coordinator) — hence the watcher rule below.

### Implementer preflight (replaces Preflight 1–3 for Pi; Codex and your pane still run them)

Each a command; any miss = stop and ask.

- `multica agent list --output json` (archived agents excluded by default): exactly ONE agent whose
  `name` equals the named agent exactly; record its `id`, `runtime_id`, `model`.
- `multica runtime list --output json`: the entry whose `id` == that `runtime_id` has
  `status: online`.
- **Model identity:** the agent's `model` is non-empty (empty = runtime default, unknown) and
  DISTINCT from both the Codex pane's model and this coordinator session's model. Empty or
  duplicate ⇒ stop and ask the human to attest.
- No instruction marker is required: a generic executor instruction set was observed to work, and
  the dispatch description carries the role limits.

Push access to origin and the forge token on the runtime host are operator setup; a missing one
surfaces as a failed stage (STOP), never as a coordinator workaround.

### send (impl dispatch) — one dispatch = ONE NEW issue = ONE card

Build the five-field envelope from ONE fresh observation of the remote trunk (journal tail seq +
the full 40-hex trunk commit) exactly as SKILL.md Profile B requires; cannot build it ⇒ stop. No
per-send readiness check (the server queues); the Herdr send/readiness rules do not apply here.

    printf '%s\n' "<description>" | multica issue create --title "impl <repo> <feature> @<short sha>" \
      --description-stdin --assignee-id <agent id> --output json

`<description>` (slashless prose): "Read ~/.agents/skills/pipeline-impl/SKILL.md in full and run
that stage exactly. Your working directory starts empty: clone the repo into it and git fetch origin
first. Dispatch envelope: branch=<b> feature=<f> expected_seq=<N> expected_commit=<full sha>, and
repo=<the absolute path of the clone you just made> (the only field you fill in). Run the stage
preflight with all five fields; a STALE_DISPATCH line means stop with zero writes and say so in one
comment here. You are the pipeline IMPL node only: implement exactly ONE card, never edit
spec-paths, never review, never merge, never create or assign issues. The deliverable is in Git —
the code on feat/<feature> and the stage's ONE metadata commit on trunk with its journal entry —
not in this thread." Record the returned `id` and `identifier`. A create error (including a
duplicate refusal) ⇒ stop; never retry with `--allow-duplicate` on your own. Writes to the issue
after the create: §Write ban.

### wait (implementer)

The remote journal tail is THE wait instrument: completion = the tail's seq is `expected_seq + 1`
with one of the impl transition forms of CONTRACT §Coordinated mode (`impl→impl · completed`,
`impl→review · completed`, `impl→impl · failed`, `impl→hunt · blocked`). Each poll also takes ONE
sample of `multica issue runs <issue> --output json` and reads the run with the latest `created_at`:

- `failed`/`cancelled` ⇒ STOP, report its `error` (SKILL.md hard rule 2, implementer death; never
  auto-redispatch, never take over).
- `completed` with no new journal entry ⇒ the stage ended without delivering ⇒ STOP (SKILL.md
  redelivery rule).
- still `queued` five minutes after the create ⇒ the runtime is not claiming ⇒ STOP.
- a CLI error or unparsable output ⇒ fail closed, STOP.

The server may itself retry a failed run (the run's `max_attempts`): the retry re-executes the SAME
envelope, and once the first attempt has pushed the card's in-progress flip, the stale-dispatch
guard refuses it (trunk commit moved) with zero writes — expected, changes nothing for you, still
STOP for the human.

The watcher itself must report EVERY terminal state above and its own death: a poll that can die
silently is not a wait instrument (the detached poll in the provenance note). A run status is never
completion evidence by itself — the new journal tail is. After completion your own verification is
unchanged (SKILL.md hard rule 3): rerun the card's verify, check the freeze diff and diff scope
yourself.

### Preflight 4 with the implementer on multica

`pipeline-driver`'s `coordinate.sh doctor` resolves a Herdr pane for all three roles and has no
multica mode, so it cannot pass when the implementer has no pane. With `impl=multica:` run these
read-only checks yourself instead; any miss = stop:

- `git fetch` succeeds in an observer clone this session may fetch in.
- `.pipeline/<feature>/control.json` at the trunk head carries the complete coordinated tuple.
- The journal tail parses: seq + its `>>> NEXT` first line.
- The forge CLI works.

A multica-aware `doctor` is a separate `pipeline-driver` change.

### Implementer failover and fallback

Quota exhausted or repeated `failed` ⇒ STOP. The operator may name another registered agent; rerun
the implementer preflight for it (still three distinct models), then dispatch a NEW issue from a
fresh observation. The coordinator never picks a replacement on its own. multica unavailable (the
same triggers as the reviewer fallback) ⇒ STOP; never switch transport on your own. The operator
re-invokes `pipeline-coordinate` WITHOUT `impl=multica:…`: a Herdr Pi pane, or the human-relayed
handoff. Leave a created-but-unrun impl issue alone (write ban) and name it in the stop report so
the operator can cancel it; a late run against a moved trunk is refused by the stale-dispatch guard
with zero writes. multica is an optional transport, never a pipeline dependency.
