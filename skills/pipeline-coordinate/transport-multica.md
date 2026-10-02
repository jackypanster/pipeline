# pipeline-coordinate — multica reviewer transport (opt-in)

**Scope.** REVIEWER role only, Profile A (meta-PR flow) only. Selected ONLY when the operator's
`pipeline-coordinate` invocation names it: `review=multica:<agent name>`, optionally
`multica-profile=<name>` (then run EVERY `multica` command below as `multica --profile <name> …`).
Never inferred; absent ⇒ Herdr. The implementer role and Profile B stay on Herdr exactly as SKILL.md
describes. Every SKILL.md hard rule binds unchanged; this file replaces only Preflight 1–3,
send/readiness, and the merge-gate wording for the reviewer role. Forge state stays the only truth.

## Reviewer preflight (replaces Preflight 1–3 for the reviewer; Pi and your own pane still run them)

Each a command; any miss = stop and ask.

- `multica agent list --output json` (archived agents excluded by default): exactly ONE agent whose
  `name` equals the named agent exactly; record its `id`, `runtime_id`, `model`.
- `multica runtime list --output json`: the entry whose `id` == that `runtime_id` has
  `status: online`.
- **Model identity:** the agent's `model` is non-empty (empty = runtime default, unknown) and
  DISTINCT from both the Pi pane's model and this coordinator session's model. Empty or duplicate ⇒
  stop and ask the human to attest.
- The agent's `instructions` contain the marker line `pipeline-review-agent v1` (§Setup) — guards
  against an agent whose instructions forbid merging or allow dispatching.

## send (review dispatch) — one dispatch = ONE NEW issue

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
with `--allow-duplicate` on your own.

## Write ban (absolute)

The coordinator writes to a review issue EXACTLY ONCE — the create. It never comments on it,
re-assigns it, changes its status, reruns or cancels its runs: your CLI holds the operator's token,
so any comment you post is recorded `author_type: member`, indistinguishable from the human's GO
token. A re-review round after changes-requested is a NEW issue whose description names the
old→new head delta (SKILL.md step 5). Read-only commands (`issue runs`, `issue get`,
`issue comment list`) are allowed any time. Needing to abort a run ⇒ stop and ask the human.

## wait

The forge verdict watcher (SKILL.md step 4) remains THE wait instrument. Each poll also takes ONE
sample of `multica issue runs <issue> --output json` and reads the run with the latest `created_at`:

- `failed`/`cancelled` ⇒ STOP, report its `error` (reviewer death; never auto-redispatch — the
  server may itself retry up to the run's `max_attempts`; that changes nothing for you).
- `completed` with no verdict comment newer than the snapshot ⇒ the stage ended without delivering
  ⇒ STOP (SKILL.md redelivery rule).
- still `queued` five minutes after the create ⇒ the runtime is not claiming ⇒ STOP.
- a CLI error or unparsable output ⇒ fail closed, STOP.

On this transport the verdict comment has no distinct author: the reviewer posts under the forge
account of its runtime's host, which may be the operator's own — the same author as the PR and as
your own comments. SKILL.md step 4's author filter therefore does not identify the reviewer here;
use it only to exclude bots. The verdict IS a non-bot PR comment NEWER than the pre-dispatch
snapshot whose text names the dispatched full head SHA; a newer non-bot comment that does not name
that head is not the verdict ⇒ do not route on it; keep waiting under the same rules.

A run status is never completion evidence by itself — the verdict PR comment bound to the dispatched
head SHA is.

## Merge gate

On an approved verdict, tell the human: post `go` (or `merge`/`confirm`) as the ENTIRE comment on
issue `<identifier>` (web UI, mobile, or their own CLI). The write ban already forbids every write,
so nothing extra freezes; while the gate is armed the forge's PR state is the ONLY wait instrument,
exactly as SKILL.md step 6 says. On merge, clean up and report as usual.

## Reviewer failover

Quota exhausted or repeated `failed` ⇒ STOP. The operator may name another registered agent; rerun
the reviewer preflight for it (canonical instructions, still a third distinct model), then dispatch
a NEW issue. The coordinator never picks a replacement on its own.

## Fallback when multica is unavailable

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

## Setup (operator, once per reviewer agent)

Canonical instructions:

```text
pipeline-review-agent v1
You are a pipeline REVIEWER node. Act only on an issue whose description tells you to run
pipeline-review; otherwise reply that it is out of scope and stop. Read the SKILL.md path named in
the issue in full and follow it exactly. If the named PR is not open, or its head is not the
dispatched head, say so in one issue comment and stop. Review only: never modify product code or
tests, never push commits. Merge ONLY by consuming pipeline-review's GO-gate in its issue-thread
form. Never create or assign issues and never @mention another agent (CONTRACT: a stage node never
dispatches). Report only commands you actually ran.
```

Apply: `multica agent update <agent id> --instructions "$(cat <file>)"`, then rerun the preflight.
Run transcripts, including tool inputs and outputs, are stored on the multica server.

*Accepted limitation:* token provenance is not mechanically enforced — the operator's token is also
on the coordinator's CLI. Mitigations: the write ban, the issue timeline as audit trail, and the
reviewer's own issue-thread checks — the same class as the Herdr limitation in SKILL.md step 6.
