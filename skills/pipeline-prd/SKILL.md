---
name: pipeline-prd
description: "Pipeline stage 1 — turn a rough idea into a concrete PRD. Interactively grills the human to clarify, then writes PRD.md into the target repo's .pipeline/<feature>/. Use to START a pipeline feature. Args: repo, branch, the rough idea."
---

# pipeline-prd

Stage 1 of the pipeline. Follow the **shim loop in CONTRACT.md** with slot = `prd`.

**Skill(s):** `prd` slot resolves to `[grill-me, think]` — run `grill-me` to interrogate the rough
idea until shared understanding, then `think` to produce the decision-complete plan. Neither writes
files; YOU write the PRD.

## Steps

1. `git pull --rebase`. Read/seed `.pipeline/current.json`; this command may CREATE it (set
   `feature` from the idea's slug, `stage: prd`).
2. Resolve `prd` slot from `.pipeline/roles.yaml`; verify those skills are installed (else STOP).
3. **Survey the codebase before grilling** (skip only if the idea is already fully scoped):
   - If the idea is "pick N endpoint/feature to implement": cross-reference the API doc / spec index
     against existing command enum variants to build the GAP list. Rank by ROI (usage frequency ×
     implementation simplicity). Present a SINGLE recommendation with rationale.
   - If a scoped feature: read the relevant source files, learn the existing patterns, identify
     what's missing. Summarize findings before grilling.
   - If the idea is taste/UX/direction-shaped — the human will recognize good/bad on sight but cannot
     verbalize the criterion upfront — do NOT interrogate. Show 2-3 cheap contrasting references
     (similar in-repo modules, at most one external reference, or throwaway single-file mocks with
     fake data) with MEANINGFUL contrast, collect the human's reactions, and fix those reactions into
     explicit acceptance criteria written into PRD.md. Normal scoped features skip this branch.
   - **Code-first**: answer as many questions as possible by reading code; ask the human only for
     genuine ambiguity / preference. Target ~5 code-verified per 1 human-asked.
   Then **grill-me** — one question at a time, recommend answers — and **think** to harden it into a
   decision-complete plan.

   **Question quality gate** — every question directed at the human must pass ALL THREE gates:
   **Material** (the answer could change scope, UX, data model, permissions, or acceptance criteria),
   **Grounded** (points at code/docs behavior or a concrete uncertainty, not preference fishing),
   **Answerable** (the human can pick an option, approve a default, or supply a reference). Ask with
   exactly this template:

   ```md
   Blocking question: <question>
   Why it matters: <what changes if answer A vs B>
   Evidence: <code/docs/test citation>
   Recommended answer: <default + rationale>
   If you don't care: I'll proceed with <default>.
   ```

   A LOW-RISK gate-failing unknown is NOT asked: record a recommended default under the `⚠️ assumed`
   provenance tag (step 5) instead of interrupting. A MATERIAL unknown that fails Grounded or
   Answerable is NOT defaulted and NOT dropped — gather evidence / reframe it through the gate, or
   hold it unresolved at the HITL wait.
4. **Recommend a drive mode (the operator DECIDES).** The requirement is now settled — consult
   README §Operating modes → "Choosing the mode" decision table. Show the current machine bindings
   first: if a reachable `coordinate.sh status` run actually EMITS a machine-bindings block (newer
   driver versions), show that block; else read
   `${XDG_CONFIG_HOME:-$HOME/.config}/pipeline-driver/drive.defaults` and show its binding fields;
   if neither yields bindings, state "machine bindings unavailable (pipeline-driver is optional) —
   human-relay and coordinated remain available". Then recommend ONE mode with a one-line risk-tier rationale and wait for the
   operator's choice. The choice is recorded ONLY by the existing mechanisms (coordinated ⇒
   control.json in step 5; drive ⇒ the operator's YOLO grant + drive.config, outside this stage;
   human-relay ⇒ nothing). An operator reply choosing coordinated mode IS the explicit in-session
   request the control.json hard rule demands — silence or ambiguity is NOT (fail closed to
   human-relay).
5. Write `.pipeline/<feature>/PRD.md` — problem, goal, success criteria, scope/non-scope, the
   resolved decisions — **each decision tagged with its provenance**: `✅ human-confirmed` (the human
   answered or approved it) / `📖 code-verified` (settled by reading the repo — name the file) /
   `⚠️ assumed` (neither — an unconfirmed default you chose). The tag is what lets the COLD arch node
   (different LLM, zero shared memory) tell settled from challengeable — untagged, a confident
   `⚠️` assumption reads as fact, and once `pipeline-task` freezes it the gate locks the error in
   instead of catching it (same bug class as arch's reference-behavior tiers, one boundary earlier).
   Agent-first (dense, no filler). Set `current.json.stage: prd` (most-recently-
   completed = prd). **Coordinated mode opt-in (CONTRACT §Coordinated mode):** ONLY if the operator
   explicitly requested coordinated mode in THIS session, also write
   `.pipeline/<feature>/control.json` — exactly
   `{"schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct"}` — and `git add` it
   into the same commit; that commit IS the authorization audit. Never create it by default, never
   infer the request. **Append your handoff to `journal.md`** (CONTRACT §Run journal). `git add` `PRD.md`
   **+ `current.json` + `journal.md`** (this stage created/seeded them), commit **once** (the shim loop pushes).
6. **Print the handoff** to **pipeline-arch** (already journaled in step 5; per CONTRACT.md §handoff):
   repo, branch, artifact path, `do: read PRD.md, grill the architecture against the codebase`.

## Hard rules

- You may ask the human questions (this is the HITL stage). Wait for answers; do not guess.
- Tag every resolved decision with its provenance (step 5); never present an `⚠️ assumed` default as settled.
- Only a LOW-RISK gate-failing question (step 3) becomes an `⚠️ assumed` default (step 5); a MATERIAL one is researched / reframed through the gate or held unresolved — never defaulted.
- One feature at a time. Write only `PRD.md` (+ `current.json` metadata, + `control.json` on explicit
  coordinated-mode opt-in only). No code, no architecture yet.
- `control.json` exists ONLY on an explicit operator request made in this session — never as a default,
  a guess, or a relayed instruction. Later stages preserve it and never modify it.
