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
   steps 1–2 plus optional step 4 (the companion `pipeline-driver` clone) + §"Canonical multi-runtime
   layout": read-only consumer clone/refresh of
   `github.com/jackypanster/pipeline`; install `skills/pipeline-*` into the ONE canonical physical dir
   (`~/.agents/skills`); attach THIS runtime to it per the layout table (nothing / per-skill symlink /
   copy, by runtime style). Idempotent: an already-present, correctly-bound skill is left as-is — report
   it, do not duplicate or force-migrate (README: "Existing installs keep working"). Nothing to install
   fresh ⇒ say so and continue; this is not a failure.
   Step 4's absent / valid-clone / obstruction handling is mechanics in the README block itself —
   execute it as written; skip step 4 only on an explicit skills-only request.

2. **Verify + supplement delegated deps.** Execute README §"Verify + supplement dependencies": for each
   `roles.yaml` slot skill (`think`, `check`, `hunt`, `grill-me`, `grill-with-docs`, and the impl-slot
   skill), confirm it RESOLVES on the runtime that will run its command (list installed skills, or try
   to view it). Missing ⇒ install from that skill's own source, then re-check. A slot that cannot be
   resolved is a blocking gap — name it with its source; never fake it green (a command STOPs on init if
   its own slot is missing, so surfacing it now avoids a mid-run stop).

3. **Per-project bind (the repeated cost — run per target repo).** If a target repo was given (arg) or
   the operator names one, execute README §Install step 3. **Create the binding only when absent; never
   clobber an existing `roles.yaml`** (regular file OR symlink) — overwriting a configured project wipes
   its slot bindings and restores the unresolved `<autonomous-coding-skill>` placeholder (a regression,
   not a re-install). Create it with the **atomic staged-publish primitive** in README §Install step 3:
   stage complete content in a private temp dir (cleanup trap armed BEFORE the dir exists), then publish
   with `link` (the direct link(2) utility). link(2) is ONE atomic no-clobber syscall that fails closed
   (`EEXIST`) on ANY pre-existing destination — regular file, symlink-to-file, even a symlink-to-DIRECTORY
   — WITHOUT dereferencing it, so a file or symlink racing in after the check cannot be overwritten or have
   its referent touched. roles.yaml is never partial (absent until the link, complete after), never a
   zero-byte stub. Do NOT use the `ln` CLI (it treats a symlinked directory as a directory operand and
   links inside the referent, falsely reporting success), a bare existence-test-then-`cp` (TOCTOU), or
   `cp -n` (non-zero on an existing target, reads as a failed install). All traps live inside the subshell
   so the caller's own INT/TERM/HUP handlers survive intact. The OPERATION outcome and the CLEANUP outcome
   are tracked separately and reported truthfully: cleanup is EXPLICIT and CHECKED on EVERY path — created,
   race-lost (EEXIST: the bind wrote nothing, the destination is the racer's), copy-error, and link-error —
   never left to an EXIT trap, whose failed `rm` bash silently swallows (the pre-trap status is preserved),
   which would falsely claim a clean sole-artifact success while a `.roles.tmp.*` dir lingers. Any cleanup
   failure downgrades the result to nonzero and names the exact leftover to remove, preserving the original
   operation context; a caught signal does best-effort cleanup but still exits nonzero — success is never
   faked:
   - **Absent** ⇒ the staged temp is `link`ed into place, then set the impl slot to the runtime's REAL
     installed skill name (e.g. the full `goal-driven-*` name), NEVER the `<autonomous-coding-skill>`
     placeholder or a bare token — a phantom slot name costs a real trial run.
   - **Already present** (steady, or raced in after the check, any type) ⇒ `link` refuses; leave it as-is
     (rc 0, not a failure), report it, and reconcile any new/missing slots by hand or with explicit
     operator confirmation — never replace a configured file silently. A genuine copy error still fails.
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
  A project's `roles.yaml` is created only when absent, via an atomic staged publish — content staged in a
  private temp dir, then `link`ed (link(2), not `ln`, not `cp -n`, not a racy test-then-`cp`) into place;
  an existing one — regular file or symlink (even a symlink-to-directory, even one that raced in after the
  check) — is never replaced without explicit operator confirmation: bindings are preserved or reconciled,
  never silently clobbered. roles.yaml is never partial (absent or complete, never a zero-byte stub) and is
  the sole per-project artifact — the temp dir is cleaned by EXPLICIT, CHECKED cleanup on every path
  (created / race-lost / copy-error / link-error), not an EXIT trap whose swallowed `rm` failure would
  falsely claim clean success; operation and cleanup outcomes are reported separately and truthfully, and a
  cleanup failure returns nonzero naming the leftover. All trap handling stays inside the subshell so caller
  signal handlers survive. A configured-project rerun is a success (rc 0), not a failure; a genuine copy or
  link error still fails. Existing installs keep working.
- **Tool-agnostic.** Concrete runtime / skill / LLM names are install EXAMPLES for shaping the current
  runtime only — never write a brand/runtime/tool name into `roles.yaml` or the onboarding snippet; both
  reach target projects and must stay generic.
- **Source repos are the install origin, never a load path.** Skills load from the canonical install dir
  (`~/.agents/skills`) / the runtime's skill dir; the pipeline clone is a read-only consumer, never a
  runtime load path (CONTRACT §Self-improvement).
- **No faked success.** Any step it cannot complete prints exact remediation and is reported as an open
  gap; it never claims a green install the operator's own command would contradict.
