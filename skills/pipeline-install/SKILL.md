---
name: pipeline-install
description: "Maintenance command — install the pipeline command-skills onto THIS machine and optionally bind a target project's .pipeline/roles.yaml by executing README §Quick Start. NOT a pipeline stage: no shim loop, no roles.yaml slot. Args: optional target repo path."
---

# pipeline-install

Maintenance command (**not a stage**): no shim loop, no `current.json`, no `roles.yaml` slot, no
journal. Writes only machine skill symlinks and (if a target repo is given) `<target>/.pipeline/roles.yaml`.

[README.md](../../README.md) §Quick Start is the single source of truth — execute it directly; do
not fork its commands here.

## Steps

1. **Machine install (idempotent):** execute README §Quick Start step 1:
   - Clone `https://github.com/jackypanster/pipeline.git` to `~/.agents/pipeline` if absent (present ⇒ keep).
   - Symlink every `~/.agents/pipeline/skills/*/` (containing `SKILL.md`, incl. `pipe` and `pipeline-*`) to `~/.agents/skills/<name>` (`../pipeline/skills/<name>`).
   - Attach runtime links (`.claude/skills/`, `.codex/skills/`, and `.pi/agent/skills/` for `pipe` + `pipeline-impl` + `pipeline-preflight`). Runtimes reading `~/.agents/skills` directly need no extra links.
2. **Verify delegated skills** (README §Dependencies): confirm `think`, `check`, `hunt`, `grill-me`,
   and the `impl` autonomous coding skill exist on the runtimes that execute those stages. Report
   missing skills with their source repo.
3. **Bind target project (if path given):** execute README §Quick Start step 2 — copy `roles.yaml`
   to `<target>/.pipeline/roles.yaml` ONLY when absent (**never clobber an existing `roles.yaml`**).
   Replace `<autonomous-coding-skill>` with the runtime's real installed skill name.
4. **Report** what was cloned, linked, already present, or missing.

## Hard rules

- Wrap README §Quick Start; never fork it.
- `~/.agents/pipeline` is a read-only consumer clone — never edit skills inside it.
- Keep `roles.yaml` and project onboarding snippets 100% model- and vendor-agnostic.
