---
name: pipeline-task
description: "Pipeline stage 3 — decompose an architecture into atomic, landable task cards, each carrying a FAILING red test (the frozen spec). Use after pipeline-arch. Args: repo, branch."
---

# pipeline-task

Stage 3. Follow the **shim loop in CONTRACT.md** with slot = `task`.

**Skill:** `task` slot resolves to `think` — it produces the decomposition (think writes NO code).
**YOU write the red-test code and the card frontmatter** — that is the shim's I/O, not think's.

## Steps

1. `git pull --rebase`. Read `current.json` (STOP if missing), `<feature>/arch.md`, `CONTEXT.md`.
2. Resolve `task` slot; verify installed (else STOP).
3. **think** to split the work into atomic sub-tasks. **Concreteness gate:** if you cannot write a
   failing red test for a sub-task, it is not atomic — split it further (re-think). One card = one
   observable behaviour.
4. For each card, **write the failing red test** into `spec-paths:` (happy path + key errors/boundaries,
   ~3–5 assertions — appropriate, not 100% coverage). The test must compile and **FAIL** now (a green
   "spec" is a no-op). Do NOT write the card yet — the card is a separate commit (step 6b).

   **Verbatim-compile pre-check (MANDATORY when the red IS a compile-fail):** when the spec imports
   not-yet-existing target symbols (the unresolved-import red), the compiler STOPS at resolution — type
   errors in the test BODY are masked and surface only after impl lands, when the coder is forbidden to
   touch the spec (freeze gate) and the only exit is a whole-feature re-freeze (new `spec-rev`, every
   card updated). Before the freeze commit (6a): copy the spec file **VERBATIM** into a temporary
   scratch test target, replace ONLY the not-yet-existing imports with local stubs matching the frozen
   signatures, then run — on that scratch — **every gate the card's `verify` (and the review full-verify)
   will later demand over `spec-paths`: the compile AND the lint gate** (e.g. `cargo test --test
   <scratch> --no-run` AND `cargo clippy --all-targets -- -D warnings`); all must pass. Then DELETE the
   scratch — it never enters the freeze commit. Re-typing or reconstructing fixture fragments is NOT
   this check: it misses body-level patterns (observed: a `{value:?}` format capture AFTER the value
   moved — E0382 only reachable past name resolution — cost one full re-freeze cycle; the verbatim
   stub-compile then ran clean for two consecutive features). **The lint gate is the same failure class
   and equally load-bearing:** a spec that trips a lint fatal under the card's `-D warnings` verify
   (observed: `clippy::doc_lazy_continuation` on a frozen test whose module-doc put a markdown bullet
   list immediately before prose) is INVISIBLE while the file is compile-RED — the compiler stops at
   name resolution before the linter runs — and surfaces only after impl makes the file compile,
   cornering the coder into an impossible bind (pass `-D warnings` OR leave the frozen spec untouched)
   whose only exit is a task re-freeze. Linting the stub catches it pre-freeze at ~zero extra cost.
5. **Freeze-coverage check** (CONTRACT §Freeze coverage): can the meaningful correctness test live in
   `spec-paths:`? If not (e.g. binary-only crate), record what IS frozen vs what review must verify by
   reading, in the card's `## Freeze coverage` section (step 6b). **"review must read" is a narrow
   exception, not a coverage escape:** a REQUIRED behaviour a hermetic test could detect must carry at
   least a frozen **dry-run / command-construction** assertion (unfrozen ⇒ impl ships it hollow-but-green,
   caught only late in review). Before ruling a required behaviour unfreezable, FIRST seek/design a
   seam — a **PTY harness**, a **temp dir + injected failure**, a **symlinked/pre-created destination**,
   or **stubbed executables** (freeze the invocation + args) make most "hard" cases testable; admit
   "review must read" only after the card records **why no seam exists**. A genuinely un-seamable
   required behaviour that `arch` did not resolve (or a feature dominated by them) **FAILS CLOSED** —
   stop before freeze commit 6a, record the risk, route back through `arch`/design-review; not a silent
   note (CONTRACT §Freeze coverage).
6. **Freeze the spec in TWO ordered commits to `main`** (CONTRACT §spec-rev double-commit protocol).
   NEVER mix the test and the card in one commit — that breaks the freeze the gate relies on:
   a. **Freeze commit** — `git add` ONLY the `spec-paths:` test file(s) for **ALL the feature's cards**,
      commit **once**. Its sha = the **feature's single `spec-rev`** recorded by every card. One commit
      for the whole feature, NOT per-card — see CONTRACT §Test ownership for the shared-file mis-flag.
   b. **Record commit** — now write **every card's** `.pipeline/<feature>/tasks/NN.md` frontmatter
      (`status: todo`, `attempts: 0`, `verify: [<build cmd>, <test cmd>]`, `spec-paths`, `impl-paths`
      — disjoint from `spec-paths` — `spec-rev: <the shared sha from 6a>`) + any `## Freeze coverage`
      note, set `current.json.stage: task`, and **append your handoff to `journal.md`**. `git add` the
      cards **+ `current.json` + `journal.md`** (metadata only — **never the test / `spec-paths`**), commit.
      `verify` MUST be card-scoped (test-name filter or dedicated file) and you MUST also set
      `current.json.full-verify` (the unfiltered whole-suite runner) — both defined in CONTRACT
      §State authority (card-scoped verify + full-verify).

   **Re-freeze / append-card** (re-routed by review/hunt): NOT initial authoring. Which fields to
   preserve on siblings, when to reset vs keep, and the trunk-rebase handoff — all defined in CONTRACT
   §Test ownership (re-freeze + append-card variant). Task action: make a NEW freeze commit (whole
   feature), update `spec-rev` on every card, create ONLY the new/changed card per the handoff.
7. **Print the handoff** to **pipeline-impl** per CONTRACT §handoff (already journaled in step 6b) —
   point at the card + arch.md + CONTEXT.md,
   give concrete steps (pick card, branch, make verify green, don't touch spec-paths, open PR), and put
   the freeze-coverage note in **Feature gotchas** so review knows what to scrutinize.

## Pitfalls

### Binary-only crate: `tests/` cannot import internal modules

If the target crate has no `lib.rs` (binary-only, modules are `mod` not `pub mod`), integration tests
in `tests/` **cannot** call internal formatter/utility functions — the frozen red test is limited to
**smoke/CLI-subprocess assertions**. Internal-logic tests (formatter with sample JSON, parsing) live
in `#[cfg(test)] mod tests` **inside** the source file (`impl-paths`), written by the coder, NOT frozen.

- **Detect:** `src/lib.rs` present? If only `src/main.rs` → binary-only. Also check `mod foo;` (private)
  vs `pub mod foo;`.
- **Impact:** card granularity may coarsen (1 card, not 2-3) since the formatter layer has no frozen
  `tests/` spec. The smoke help test is still a valid red — observable CLI behaviour.
- **Concrete red-test pair (gitee-cli pattern):** (1) `smoke_<parent>_help` → `"<parent> --help"`
  stdout contains the new subcommand name (fails: not registered); (2) `smoke_<parent>_<sub>_help` →
  `"<parent> <sub> --help"` exits success (fails: clap rejects the unknown subcommand). Both freeze,
  both fail genuinely, both go green when impl adds the enum variant + opts.
- This is the freeze-coverage case from step 5 — record it in the card's `## Freeze coverage`.

## Hard rules

- The red test you write IS the spec and gets frozen — the coder cannot edit it.
- No implementation. No prose acceptance the coder could misread — the test is the contract.
