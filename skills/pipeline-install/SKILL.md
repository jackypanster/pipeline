---
name: pipeline-install
description: "Maintenance command — install the pipeline command-skills onto THIS runtime and bind a target project, by executing README §Install. NOT a pipeline stage: no shim loop, no roles.yaml slot, never runs a target repo's .pipeline/ loop. The agent-executed twin of pipeline-update (update refreshes an existing install; install stands one up). Args: optional target repo path to bind after the machine install."
---

# pipeline-install

Lifecycle/maintenance command: stand up the `pipeline-*` command shims + their delegated skills on the
runtime that RUNS them, then bind a target project's `roles.yaml`. It is the **setup-side twin of
`pipeline-update`** — update refreshes an install that already exists; install creates one. Both are
maintenance, neither is a stage.

**This command is agent-executed prose, NOT a script — deliberately.** `pipeline-update`'s refresh is
mechanical, so it lives in `scripts/update.sh`. Install is the opposite: it varies by runtime (where
skills load, symlink vs copy, which delegated deps are already present) and by project — that variance
is agent judgment, not a fixed code path. Encoding it as a deterministic installer is exactly the trap a
prior attempt fell into (a bash `setup` wizard that could not converge — lost an abort signal across a
`$(...)` subshell, followed symlinks on its backup sinks). So this skill does not reinvent the steps: it
**executes the canonical, already-agent-legible README §Install** and adds only idempotency + honest
reporting on top.

**Not a feature stage.** There is no `.pipeline/` here, so this command does **NOT** run the CONTRACT
shim loop (no `git pull --rebase` of a target, no `current.json`, no `roles.yaml` slot, no journal, no
handoff). It writes runtime-shared skill files and, if a target is given, that project's `roles.yaml` —
nothing else. It is deliberately absent from the 7-command table, from `roles.yaml`, and from the
onboarding snippet — same positioning as `pipeline-update`.

## Steps

README §Install is the single source of truth for the mechanics; this skill orchestrates it. Read that
section on the pipeline repo (`~/workspace/pipeline/README.md`) and execute it against the current
runtime — do not paste a second copy of the steps here, so the two can never drift.

1. **Per-machine install (idempotent — do once per machine; safe to re-run).** Execute README §Install
   steps 1–2 + §"Canonical multi-runtime layout": read-only consumer clone/refresh of
   `github.com/jackypanster/pipeline`; install `skills/pipeline-*` into the ONE canonical physical dir
   (`~/.agents/skills`); attach THIS runtime to it per the layout table (nothing / per-skill symlink /
   copy, by runtime style). Idempotent: an already-present, correctly-bound skill is left as-is — report
   it, do not duplicate or force-migrate (README: "Existing installs keep working"). Nothing to install
   fresh ⇒ say so and continue; this is not a failure.

2. **Verify + supplement delegated deps.** Execute README §"Verify + supplement dependencies": for each
   `roles.yaml` slot skill (`think`, `check`, `hunt`, `grill-me`, `grill-with-docs`, and the impl-slot
   skill), confirm it RESOLVES on the runtime that will run its command (list installed skills, or try
   to view it). Missing ⇒ install from that skill's own source, then re-check. A slot that cannot be
   resolved is a blocking gap — name it with its source; never fake it green (a command STOPs on init if
   its own slot is missing, so surfacing it now avoids a mid-run stop).

3. **Per-project bind (the repeated cost — run per target repo).** If a target repo was given (arg) or
   the operator names one, execute README §Install step 3. **Create the binding only when absent; never
   clobber an existing `roles.yaml`** — overwriting a configured project wipes its slot bindings and
   restores the unresolved `<autonomous-coding-skill>` placeholder (a regression, not a re-install).
   So: `mkdir -p <target>/.pipeline`, then `cp -n ~/workspace/pipeline/roles.yaml
   <target>/.pipeline/roles.yaml` (`-n` = no-clobber: writes only if the target is missing).
   - **Freshly created** ⇒ set the impl slot to the runtime's REAL installed skill name (e.g. the full
     `goal-driven-*` name), NEVER the `<autonomous-coding-skill>` placeholder or a bare token — a phantom
     slot name costs a real trial run.
   - **Already present** (`cp -n` skipped it) ⇒ leave it as-is; report it and reconcile any new/missing
     slots by hand or with explicit operator confirmation — never replace a configured file silently.
   This is the ONLY per-project artifact; everything else was the one-time machine install above. No
   target given ⇒ skip this step and say the machine is ready to bind projects.

4. **Report — install is the operator's ground truth.** State what was installed / attached / already
   present / skipped, and every unresolved slot with its source. Do NOT print a blanket "installed OK":
   leave verification to the operator running an actual command (each stage self-checks its slot; a
   sibling `drive.sh doctor`-style checker, where present, is the ground truth) — honest degrade over
   faked success.

## Hard rules

- **Not a stage.** No shim loop, no `roles.yaml` slot, no journal/handoff; it never runs a target repo's
  `.pipeline/` loop. It writes only runtime-shared skills + (if asked) one project's `roles.yaml`.
- **Wrap README §Install; never fork it.** The mechanics live in that one section — execute it, do not
  duplicate or hand-reimplement the steps. A wrong step gets fixed there via `pipeline-improve`, not
  worked around here. (This is the install-side analogue of update's "mechanics live in the script".)
- **Idempotent + additive.** Re-running never duplicates a skill or force-migrates an existing install.
  A project's `roles.yaml` is created only when absent (`cp -n`); an existing one is never replaced
  without explicit operator confirmation — bindings are preserved or reconciled, never silently clobbered.
  Existing installs keep working.
- **Tool-agnostic.** Concrete runtime / skill / LLM names are install EXAMPLES for shaping the current
  runtime only — never write a brand/runtime/tool name into `roles.yaml` or the onboarding snippet; both
  reach target projects and must stay generic.
- **Source repos are the install origin, never a load path.** Skills load from the canonical install dir
  (`~/.agents/skills`) / the runtime's skill dir; the pipeline clone is a read-only consumer, never a
  runtime load path (CONTRACT §Self-improvement).
- **No faked success.** Any step it cannot complete prints exact remediation and is reported as an open
  gap; it never claims a green install the operator's own command would contradict.
