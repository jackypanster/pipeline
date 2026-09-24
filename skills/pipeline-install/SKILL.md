---
name: pipeline-install
description: "Maintenance command — install the pipeline command-skills onto THIS machine and bind a target project, by executing README §Install. NOT a pipeline stage: no shim loop, no roles.yaml slot, never runs a target repo's .pipeline/ loop. The setup-side twin of pipeline-update (update refreshes an existing install; install stands one up). Args: optional target repo path to bind after the machine install."
---

# pipeline-install

Maintenance command, **not a stage**: no shim loop, no `current.json`, no `roles.yaml` slot, no
journal. It writes the machine's skill links and, if a target is given, that project's
`.pipeline/roles.yaml` — nothing else.

README §Install is the single source of truth. Read it (`~/.agents/pipeline/README.md`, or the
GitHub copy before the clone exists) and execute it; do not keep a second copy of the steps here.

## Steps

1. **Machine install (idempotent).** Execute README §Install:
   - `git clone` the pipeline repo to `~/.agents/pipeline` if absent (present ⇒ leave it;
     `pipeline-update` refreshes it).
   - Run the link loop: each `~/.agents/skills/pipeline-<name>` becomes the relative symlink
     `../pipeline/skills/pipeline-<name>`. An existing correct link ⇒ report it. A real directory
     there ⇒ an old install: run README §Update (U1 → N) first.
   - Add the runtime attachments for every runtime on this machine (`.claude`, `.codex`, and `.pi`
     for impl/preflight only), exactly as the README shows. Every machine that runs a stage gets the
     full `pipeline-*` set.
   - Optional: the driver clone (README §Install), unless the operator asked for skills only.
2. **Verify delegated deps** (README §"Verify + supplement dependencies"): each `roles.yaml` slot
   skill must resolve on the runtime that runs its command. Missing ⇒ install it from its own
   source, then re-check. An unresolvable slot is a blocking gap — name it with its source.
3. **Bind a project** (only when a target is given): README §Install step 5 (after the machine block printed `install OK`) — copy the canonical
   `roles.yaml` only when absent. **Never clobber an existing `roles.yaml`.** When newly created, set
   the impl slot to the runtime's real installed skill name, never the `<autonomous-coding-skill>`
   placeholder.
4. **Report** what was cloned / linked / attached / already present / skipped, and every unresolved
   slot. No blanket "installed OK": the first real stage run (each stage self-checks its slot) is the
   ground truth.

## Hard rules

- **Wrap README §Install; never fork it.** A wrong step gets fixed there via `pipeline-improve`.
- **The clone is read-only.** Never edit skills in `~/.agents/pipeline` (CONTRACT §Self-improvement).
- **Tool-agnostic.** Runtime/skill names are install examples; never write one into `roles.yaml` or
  the onboarding snippet.
