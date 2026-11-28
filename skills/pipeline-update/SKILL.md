---
name: pipeline-update
description: "Maintenance command — refresh the pipeline command-skills on THIS machine to latest origin/main via ff-only pull of ~/.agents/pipeline. NOT a pipeline stage: no shim loop, no roles.yaml slot, never touches target repos. Args: optional clone path (default ~/.agents/pipeline)."
---

# pipeline-update

Maintenance command (**not a stage**): run **between stages, never mid-stage**. Opens no PR and
merges nothing.

## Steps

1. **Run the update script** and relay its output verbatim:
   ```bash
   bash ~/.agents/skills/pipeline-update/scripts/update.sh [clone]   # PIPELINE_SKILLS_DIR overrides ~/.agents/skills
   ```
   - `STOP: …` ⇒ report verbatim and stop (never reset, stash, or re-clone).
   - `LINKED <name>` ⇒ a new skill appeared; re-run README §Quick Start step 1 (idempotent) to add runtime attachments.
   - `LEGACY <name>` / `MISLINKED <name>` ⇒ report to operator to replace copy/wrong link with canonical symlink.
2. **Clean up dangling symlinks** (if upgrading after retired skills were removed): check for broken
   symlinks matching `~/.agents/skills/pipeline-*`, `~/.claude/skills/pipeline-*`,
   `~/.codex/skills/pipeline-*` and report/remove broken ones on operator confirmation.
3. **Re-verify delegated skills** (README §Dependencies) and remind the operator to reconcile each
   target project's `.pipeline/roles.yaml` if canonical slots changed.
