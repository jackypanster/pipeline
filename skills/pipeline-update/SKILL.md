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

1. **Locate the install.** Resolve `SKILLS_DIR` — the directory that holds every `pipeline-*` shim:
   - If the runtime exposes this skill's own base dir, set `SELF_DIR` to it (e.g.
     `…/skills/pipeline-update`) and derive `SKILLS_DIR="$(dirname "$SELF_DIR")"` (its parent).
   - Else if the operator passed a skills-dir override arg, use it **verbatim** as the skills dir:
     `SKILLS_DIR="$arg"` — the arg already points at the shim directory, so do **NOT** `dirname` it.
   - Else STOP and ask the operator for the runtime skill dir — never guess.

   Then set `PROBE="${SELF_DIR:-$SKILLS_DIR}"` — a path inside the pipeline clone iff this is a Mode 2
   install — for the mode detection in step 2.

2. **Detect the install mode** (README §Install ships two) — guard the empty case or you misdetect:

   ```bash
   TOP="$(git -C "$PROBE" rev-parse --show-toplevel 2>/dev/null || true)"
   if [ -n "$TOP" ] && git -C "$TOP" remote get-url origin 2>/dev/null | grep -qE '(^|[@/])github\.com[:/]jackypanster/pipeline(\.git)?/?$'; then
     MODE=2   # external_dirs: the runtime loads skills straight from the clone
   else
     MODE=1   # skills were cp'd as copies — PROBE is not inside the pipeline clone
   fi
   ```

   **Critical — the `[ -n "$TOP" ]` guard is load-bearing:** an empty `$TOP` must fall to Mode 1. Never
   run `git -C "$TOP" …` with `$TOP` unset/empty — `git -C ""` silently runs in the *current directory*
   (often a target project or the clone itself) and would falsely report the pipeline remote ⇒ misdetect
   Mode 1 as Mode 2 and skip the re-copy.

   **Critical — the remote match must pin the full GitHub repo identity, not just a path suffix:** anchor
   host *and* owner/repo (`(^|[@/])github\.com[:/]jackypanster/pipeline(\.git)?/?$`), never a bare
   `grep 'jackypanster/pipeline'` nor a suffix-only `[/:]jackypanster/pipeline…$`. A looser pattern also
   matches sibling repos (`jackypanster/pipeline-dashboard`, `pipeline-driver`), any host whose nested
   path merely ends in `/jackypanster/pipeline` (e.g. `git.example.com/foo/jackypanster/pipeline.git`),
   and spoofed hosts (`evilgithub.com`, `github.com.evil.com`). If `PROBE` lands inside any such
   checkout, detection misfires into Mode 2 and step 3's `reset --hard origin/main` blows away that
   unrelated repo. The next operation is destructive — do not loosen this pattern.

3. **Update to origin/main.**
   - **Mode 2:** `OLD="$(git -C "$TOP" rev-parse HEAD)"`;
     `git -C "$TOP" fetch origin && git -C "$TOP" reset --hard origin/main`
     (the sanctioned read-only-consumer refresh, CONTRACT §Self-improvement — bare `fetch origin`, not
     `fetch origin main`, so the `origin/main` tracking ref is reliably updated before the reset);
     `NEW="$(git -C "$TOP" rev-parse HEAD)"`.
   - **Mode 1 (atomic — never corrupt the live install on a network failure):** shallow-clone to a temp
     dir FIRST, copy only on success:
     `TMP="$(mktemp -d)"; git clone --depth 1 https://github.com/jackypanster/pipeline.git "$TMP"` →
     on success `cp -r "$TMP"/skills/pipeline-* "$SKILLS_DIR"/` → `NEW="$(git -C "$TMP" rev-parse HEAD)"`.
     **Keep `$TMP` until step 4 has read its log; cleanup happens there.** A failed clone leaves the
     installed shims untouched ⇒ report the error and STOP.

4. **Report what changed.** Mode 2: `git -C "$TOP" log --oneline "$OLD".."$NEW"`. Mode 1: print `NEW` +
   `git -C "$TMP" log --oneline -10`, **then** `rm -rf "$TMP"` (Mode 1 only — this is the deferred
   cleanup from step 3). Either way list which `pipeline-*/SKILL.md` (and `CONTRACT.md` / `README.md`)
   the update moved. Nothing moved ⇒ say "already latest (`$NEW`)".

5. **Re-verify delegated deps.** Re-run README §"Verify + supplement dependencies": for each `roles.yaml`
   slot skill (`think`, `check`, `hunt`, `grill-me`, `grill-with-docs`, the `goal-driven-*` impl slot),
   confirm it still resolves on THIS runtime. A newly-missing or newly-added slot ⇒ **report it; do not
   auto-install** (install is the operator's, from the skill's own source).

6. **State the scope boundary.** Print: refreshed the runtime-shared `pipeline-*` shims only; **no target
   project's `.pipeline/` was touched** (roles.yaml, current.json, cards, journal all untouched). If the
   new version changed the canonical `roles.yaml` schema (e.g. a new slot), print a one-line note to
   reconcile each project's `roles.yaml` **by hand** (diff canonical vs project) — never auto-edit a
   project. Sibling repos (`pipeline-dashboard`, `pipeline-driver`) are out of scope — they update
   themselves.

## Hard rules

- **Not a stage.** No shim loop, no `roles.yaml` slot, no journal/handoff, and it NEVER writes to any
  target repo's `.pipeline/`. Skills-only.
- **Pull, never push.** No PR, no merge, no proposal — that direction is `pipeline-improve`'s alone.
- **Atomic on failure.** Mode 1 clones to a temp dir and copies only after a successful clone; a network
  failure leaves the existing install intact. Bad version landed ⇒ re-run after the upstream fix. Rollback
  differs by mode: **Mode 2** is a git checkout, so `git reset --hard <prev-sha>` reverts it; **Mode 1**
  copies are *not* a git checkout, so roll back by re-copying from a clone checked out at the prev-sha (or
  just re-run update once upstream is fixed).
- **Never clobber a project's bindings.** `roles.yaml` and `.pipeline/` state are the user's; report a
  schema change, do not apply it.
