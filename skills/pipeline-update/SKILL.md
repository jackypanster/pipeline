---
name: pipeline-update
description: "Maintenance command — refresh the pipeline command-skills on THIS machine to the latest github.com/jackypanster/pipeline main: an ff-only pull of the read-only consumer clone the canonical skill entries symlink into. NOT a pipeline stage: no shim loop, no roles.yaml slot, never touches a target repo's .pipeline/ state. The pull-down counterpart to pipeline-install; the opposite direction from pipeline-improve (which pushes a proposal UP via PR). Args: optional clone path (default ~/.agents/pipeline)."
---

# pipeline-update

Maintenance command, **not a stage**: no shim loop, no `current.json`, no `roles.yaml` slot, no
journal. Run it **between stages, never mid-stage**. It opens no PR and merges nothing.

Layout (README §Install): `~/.agents/pipeline` is a read-only `git clone` of the pipeline repo;
each canonical entry `~/.agents/skills/pipeline-<name>` is a relative symlink
`../pipeline/skills/pipeline-<name>`; runtimes attach to the canonical entries.

## Steps

1. **Run the script** and relay its output verbatim:

   ```bash
   bash ~/.agents/skills/pipeline-update/scripts/update.sh [clone]   # PIPELINE_SKILLS_DIR overrides ~/.agents/skills
   ```

   It checks the clone's origin is the pipeline repo, it is on `main` with no tracked-file edits or
   local-only commits, fast-forwards to `origin/main`, then checks every `skills/pipeline-*` entry. `HEAD <sha>` +
   `updated …` / `already latest` is the result.
   - `STOP: …` ⇒ report it and stop. Never reset, stash or re-clone to force it through.
   - `LINKED <name>` ⇒ a new skill was linked; run the README §Install machine block (idempotent; adds only what is missing).
   - `LEGACY <name>` / `MISLINKED <name>`, or this script is absent ⇒ the machine predates this layout:
     execute README §Update from U1 (state detection → migration → verify). The operator asking for an
     update authorizes that migration; it backs up before moving anything.
2. **Re-verify delegated deps** (README §"Verify + supplement dependencies"): a newly missing or
   added `roles.yaml` slot skill ⇒ report it; do not auto-install.
3. **Scope.** No target `.pipeline/` was touched. If the canonical `roles.yaml` schema changed, note
   that each project's `roles.yaml` must be reconciled by hand. The driver clone updates itself
   (README §Update).
