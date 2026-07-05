---
name: pipeline-arch
description: "Pipeline stage 2 — turn a PRD into an architecture. Uses grill-with-docs to walk the design tree against the existing codebase and emit arch.md + CONTEXT.md + ADRs. Use after pipeline-prd. Args: repo, branch."
---

# pipeline-arch

Stage 2. Follow the **shim loop in CONTRACT.md** with slot = `arch`.

**Skill:** `arch` slot resolves to `grill-with-docs` — it walks each branch of the design tree,
challenges the plan against the repo's existing domain model, sharpens terminology, and updates
`CONTEXT.md` + ADRs inline. YOU ensure the artifacts land under `.pipeline/<feature>/`.

## Steps

1. `git pull --rebase`. Read `.pipeline/current.json` (STOP if missing) and `<feature>/PRD.md`.
2. Resolve `arch` slot from `roles.yaml`; verify installed (else STOP).
3. **grill-with-docs** against the PRD + codebase: resolve cross-decision dependencies one at a
   time, record irreversible/surprising choices as ADRs, sharpen the domain language in CONTEXT.md.
   **Code-first verification — check every PRD claim against real code BEFORE asking the human:**

   | Verify | How |
   |---|---|
   | Cited code patterns exist as described | `grep` the named function/file (e.g. "group_get uses urlencoding" → read `group.rs`) |
   | Encoding / URL construction consistency | cross-check ALL sites building the same path; variants may differ (`urlencoding::encode` vs `replace('/', "%2F")`) |
   | File-organization conventions | if PRD says "add to X.rs", check whether the pattern uses separate `_detail.rs` files |
   | CLI flag conventions | existing structs may use `short = 'v'`, specific defaults, `conflicts_with` — PRD must match |
   | Error-handling claims | verify the quirk exists in `error.rs`/`client.rs` (e.g. "500 not 404" → check `friendly_error(500)`) |

   **External-reference behavior — when local code can't settle a claim, TABLE it, don't guess.** Some
   PRD claims are about an EXTERNAL reference the frozen tests can never exercise (a third-party API, a
   dependency's field/method semantics, a wire protocol, another service — no live dependency in the
   test env). Code-first grep cannot verify these. For them, emit a **reference-behavior artifact**: a
   reviewable table mapping each external-contract element the code relies on → the reference's
   documented/observed semantics → our value / deliberate divergence → a **verification tier**
   (`✅ probed` / `📖 doc-cited` / `⚠️ unverified`), shipping the `⚠️` rows as a risk register. This is
   the ONLY thing that guards the bug class a frozen test cannot: the test freezes the team's
   *understanding* of the reference, so a wrong understanding is locked in, not caught. Land it in
   `CONTEXT.md` / an ADR / a durable repo doc, and name it in the handoff so `pipeline-task` does not
   freeze that feature's red test until it exists.

   Only ask the human on genuine ambiguity that code cannot resolve.
4. Write `.pipeline/<feature>/arch.md` (the chosen shape + component boundaries) and let
   grill-with-docs land `CONTEXT.md` + `docs/adr/*.md`. Set `current.json.stage: arch` (most-recently-
   completed = arch). **Append your handoff to `journal.md`** (CONTRACT §Run journal). `git add` those
   artifacts **+ `current.json` + `journal.md`**, commit **once** (the shim loop pushes).
5. **Print the handoff** to **pipeline-task** (already journaled in step 4):
   `do: decompose into atomic cards, write a red test per card`.

## Hard rules

- HITL stage — ask the human on blocking ambiguity, wait. Don't invent domain terms; ground them.
- Write architecture/ADRs only. No task cards, no code, no tests here.
- Irreversible decisions (migrations, concurrency model, data shape) → an ADR, not a coder card.
- **External-reference features gate on a reference-behavior artifact** (step 3): when correctness
  depends on behavior the frozen tests can't exercise, arch produces the tier-marked table and hands
  `pipeline-task` the gate — no red test for that behavior is frozen until the reference is tabled.
  Purely additive to the freeze gate; it changes nothing in the spec-rev protocol, state machine, merge,
  or force-push rules.
