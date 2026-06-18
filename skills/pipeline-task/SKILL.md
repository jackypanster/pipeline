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
5. **Freeze-coverage check.** Can the *meaningful* correctness test live in `spec-paths:`? Some test
   architectures can't freeze it — e.g. a **pure binary crate** (no `lib.rs`): `tests/` can only
   black-box-invoke the binary, so unit/formatter correctness lives **inline in `src/`** (= `impl-paths`,
   coder-owned, not frozen). When that happens the freeze gate only protects the **CLI/black-box
   contract**, not the core logic. Note what the frozen test covers vs **what pipeline-review must verify
   by reading** (e.g. "frozen: `--help`/CLI contract; review must read: formatter output logic + inline
   tests") — you record it in the card's `## Freeze coverage` section in step 6b.
6. **Freeze the spec in TWO ordered commits to `main`** (CONTRACT §spec-rev double-commit protocol).
   NEVER mix the test and the card in one commit — that breaks the freeze the gate relies on:
   a. **Freeze commit** — `git add` ONLY the `spec-paths:` test file(s) for **ALL the feature's cards**,
      commit **once**. Its sha = the **feature's single `spec-rev`** that every card records — one commit
      for the whole feature, NOT per-card (per-card freezes make a shared test file falsely trip an
      earlier card's freeze gate; CONTRACT §Test ownership).
   b. **Record commit** — now write **every card's** `.pipeline/<feature>/tasks/NN.md` frontmatter
      (`status: todo`, `attempts: 0`, `verify: [<build cmd>, <test cmd>]`, `spec-paths`, `impl-paths`
      — disjoint from `spec-paths` — `spec-rev: <the shared sha from 6a>`) + any `## Freeze coverage`
      note, set `current.json.stage: task`, and **append your handoff to `journal.md`**. `git add` the
      cards **+ `current.json` + `journal.md`** (metadata only — **never the test / `spec-paths`**), commit.
      **`<test cmd>` MUST be card-scoped** — run only THIS card's frozen test(s) (a test-name filter
      `cargo test smoke_login_help` / `pytest -k` / `go test -run`, or a dedicated test file), **never the
      whole suite** (CONTRACT §State authority): all cards are frozen RED up front, so a full-suite
      `verify` can never go green while sibling cards are still red. The full suite is review's gate, not
      the card's — so **also set `current.json.full-verify`** = `[<build cmd>, <whole-suite test cmd>]`
      (the UNFILTERED runner, e.g. `["cargo build", "cargo test"]`) once for the feature, so
      `pipeline-review` runs its integration gate from an exact recorded command, never a guess.
   Push to `main` (queue authoring, distinct from the only-reviewer-merges rule).

   **Initial authoring vs re-freeze.** Steps 4–6 — and the `status: todo` / `attempts: 0` defaults in 6b
   — are for **initial authoring** (creating the cards). If you were re-routed here to **re-freeze a
   wrong spec** (review/hunt named the offending target), it is NOT initial authoring: make a NEW single
   freeze commit for the corrected test(s) and update **only `spec-rev`** on every card to the new sha.
   **Preserve each card's existing `status` / `attempts` / `verify` / `impl-paths` / `## Freeze coverage`** —
   change other fields ONLY on the card(s) the handoff names as re-spec'd. **NEVER blanket-reset siblings
   to `todo` / `0`** — that destroys in-flight state (a card mid-impl or in review would silently restart).

   **Append-card (third mode).** If re-routed here to **add a card to an in-flight feature** (hunt routes
   an integration fix, or a new sub-task surfaced), it is a **re-freeze variant**: write the new card's
   red test, make a NEW single freeze commit covering the **whole feature** (new shared `spec-rev`),
   **create ONLY the new card** (`status: todo`, `attempts: 0`), and **preserve every existing card's
   `status` / `attempts` / `verify` / `impl-paths`** (their `spec-rev` updates to the new sha; nothing
   else). Both this and re-freeze advance trunk's spec under the in-flight branch, so the impl handoff
   MUST say **rebase `feat/<feature>` onto trunk + force-push** first (CONTRACT §State authority).
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
