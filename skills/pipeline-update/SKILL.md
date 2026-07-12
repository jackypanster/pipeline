---
name: pipeline-update
description: "Maintenance command — refresh the pipeline command-skills installed on THIS runtime to the latest github.com/jackypanster/pipeline main. NOT a pipeline stage: no shim loop, no roles.yaml slot, never touches a target repo's .pipeline/ state. The pull-down counterpart to Install; the opposite direction from pipeline-improve (which pushes a proposal UP via PR). Args: optional skills-dir override if this runtime does not expose the skill's own base dir."
---

# pipeline-update

Lifecycle/maintenance command: pull the latest `pipeline-*` command shims from
`github.com/jackypanster/pipeline` into the runtime that RUNS them. It is the inverse of Install
(README §Install is `cp` + register; this re-`cp`s the newer source) and the read-side twin of
`pipeline-improve` — improve pushes a proposal UP through a gated PR, update pulls the merged result
DOWN. **It opens no PR and merges nothing.**

**Not a feature stage.** There is no `.pipeline/` here, so this command does **NOT** run the CONTRACT
shim loop (no `git pull --rebase` of a target, no `current.json`, no `roles.yaml` slot, no journal, no
handoff). It refreshes runtime-shared skill files only. It is deliberately absent from the 7-command
table, from `roles.yaml`, and from the onboarding snippet.

## Steps

1. **Run the update script.** The locate/detect/refresh/report mechanics are deterministic bash, not
   agent judgment — the install-mode detection, the pinned remote-identity guard, and Mode 1's
   temp-clone atomicity are enforced in code under `set -euo pipefail`:

   ```bash
   bash "<this skill's base dir>/scripts/update.sh"          # normal: refresh THIS install
   bash ".../scripts/update.sh" <skills-dir>                 # override: refresh a DIFFERENT physical copy
                                                             # (arg used verbatim as the shim dir)
   ```

   Mode 2 additionally sweeps the canonical multi-runtime dir (`~/.agents/skills`, override with
   `PIPELINE_CANON_SKILLS`): stale COPIES there are refreshed from the just-updated clone; a symlink
   counts as fresh only when it resolves to the SAME skill's source dir (dangling or misbound links
   are surfaced, never blessed) — so a clone-side run can no longer report "already latest" while a
   runtime attachment still serves an old or wrong shim.

   The sweep is **run-atomic, restart-safe and single-flight**: it fires for ANY installed
   canonical `pipeline-*` entry (a partial install without `pipeline-update` is still swept); the
   whole transaction runs under interprocess locks taken in fixed order before any mutation — a
   per-clone lock guarding every Mode-2 fetch/reset, then the canonical lock (distinct clones can
   target the same canon). A concurrent run aborts with rc 1 having changed nothing; a dead
   holder's lock is reclaimed automatically (takeover is serialized by its own atomic reclaim
   mutex that re-checks the holder; liveness compares pid AND process start time, so a reused
   pid cannot masquerade as a live holder; and both deletion and release verify a unique per-run
   ownership token, so no process can ever remove a lock that changed hands). It
   detects first with zero mutation; stages every refresh before touching anything live; on ANY
   failure rolls back every completed swap AND the clone HEAD, with every rollback step guarded and
   verified. Exit contract: `0` = the install is correct (a failed backup cleanup only warns — the
   janitor retries next run); `1` = not updated — fully rolled back, or the output names exactly
   what a failed recovery step left behind for the janitor to finish on the next run. A startup
   janitor recovers interrupted runs (restores a live copy left missing between swap steps —
   including when the interrupted entry is `pipeline-update` itself — and drops leftover
   backups/staging); it is scoped strictly to the `.pipeline-*.update-*` artifact namespace, never
   touching other names, and any recovery failure aborts the run before mutation.

   If the runtime does not expose this skill's base dir, locate the installed `pipeline-update/`
   directory (it contains this file) and run the script from there; cannot locate it ⇒ STOP and ask
   the operator for the runtime skill dir — never guess. **Relay the script's output verbatim** (mode,
   old→new shas, which `pipeline-*` moved, or "already latest"). Non-zero exit ⇒ the install is
   untouched — report the error and STOP.

2. **Re-verify delegated deps.** Re-run README §"Verify + supplement dependencies": for each
   `roles.yaml` slot skill (`think`, `check`, `hunt`, `grill-me`, `grill-with-docs`, the impl slot),
   confirm it still resolves on THIS runtime. A newly-missing or newly-added slot ⇒ **report it; do
   not auto-install** (install is the operator's, from the skill's own source).

3. **State the scope boundary.** The script prints it: runtime-shared `pipeline-*` shims only; **no
   target project's `.pipeline/` was touched** (roles.yaml, current.json, cards, journal all
   untouched). If the new version changed the canonical `roles.yaml` schema (e.g. a new slot), add a
   one-line note to reconcile each project's `roles.yaml` **by hand** (diff canonical vs project) —
   never auto-edit a project. Sibling repos (`pipeline-dashboard`, `pipeline-driver`) are out of
   scope — they update themselves.

## Hard rules

- **Not a stage.** No shim loop, no `roles.yaml` slot, no journal/handoff, and it NEVER writes to any
  target repo's `.pipeline/`. Skills-only.
- **Pull, never push.** No PR, no merge, no proposal — that direction is `pipeline-improve`'s alone.
- **Atomic on failure** (enforced by the script): Mode 1 clones to a temp dir and copies only after a
  successful clone; a network failure leaves the existing install intact. Bad version landed ⇒ re-run
  after the upstream fix. Rollback differs by mode: **Mode 2** is a git checkout, so
  `git reset --hard <prev-sha>` reverts it; **Mode 1** copies are *not* a git checkout, so roll back
  by re-copying from a clone checked out at the prev-sha (or just re-run update once upstream is fixed).
- **Never clobber a project's bindings.** `roles.yaml` and `.pipeline/` state are the user's; report a
  schema change, do not apply it.
- **The mechanics live in `scripts/update.sh`.** Do not re-implement them ad hoc in prose or by hand —
  a wrong script gets fixed via `pipeline-improve`, not worked around.
