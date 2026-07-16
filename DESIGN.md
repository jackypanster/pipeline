# pipeline — design contract

A thin skill-aggregation pipeline. The **only durable asset is this contract** (command sequence +
handoff format + git+md state convention). Each command is a ~20-line shim delegating to a
swappable skill. Forge-agnostic, machine-agnostic, human-relayed, no scheduler.

## Core principle: skills reason, the shim does I/O

The aggregated skills are **reasoning/interview engines** — as a rule they do not write the artifacts.
Writing `PRD.md` / the red test / the review report, running the freeze gate, opening a PR, committing —
all of that is the **shim's** contract, never assumed of the skill. (Verified: `think` is explicitly
"No code"; `grill-me` only interviews.) The one **exception** is `grill-with-docs` (arch), which lands
`CONTEXT.md`/ADRs inline by design — even then commit/journal/write-set enforcement stays the shim's;
only those files' authorship is the skill's. So the precise invariant is **the shim owns commit +
journal + handoff + write-set enforcement**, not "no skill ever writes a file".

Each command body (~20 lines):
`git pull --rebase → read current.json + md → resolve skill via roles.yaml → invoke skill →
write your stage's write-set + append handoff to journal.md → commit (one) → push → print handoff`.

## Commands

| command | delegates to (skill) | shim / agent I/O |
|---|---|---|
| pipeline-prd | `grill-me` (clarify) → `think` (plan) | agent writes `PRD.md` + commit |
| pipeline-arch | `grill-with-docs` (walks the design tree; emits CONTEXT.md + ADRs) | land `arch.md` / `CONTEXT.md` / ADRs |
| pipeline-task | `think` (decompose into atomic cards) | **agent writes the red-test code** (think won't) + card frontmatter; freeze into `spec-paths:`; push |
| pipeline-impl | the bound `<autonomous-coding-skill>` (think → design tests → code → check loop; white-box tests in `impl-paths:`) | shim freezes `spec-paths:`; opens PR/branch; status → review |
| pipeline-review | `check` (semantic review of the diff/PR) | shim adds the `spec-paths` freeze gate + drives merge after human confirm |
| pipeline-hunt | `hunt` (root-cause) | **entry for `blocked` cards** — root-cause before re-queue, never blind retry |

```yaml
# .pipeline/roles.yaml  (one per target repo; any line independently swappable to a best-of-breed skill)
prd:    [grill-me, think]
arch:   grill-with-docs
task:   think
impl:   <autonomous-coding-skill>   # autonomous think→design-tests→code→check loop; set to your runtime's real installed skill name
review: check
hunt:   hunt
```

`roles.yaml` names the skill only; which agent/bot you paste into selects the runtime. On init, each
command verifies its OWN slot resolves to an installed skill (hard gate — no silent mid-run failure).

## State convention (git + md, zero forge dependency)

```text
.pipeline/
  current.json              {repo, branch, pr?, feature, stage, full-verify?}  # fast cache (journal tail authoritative); full-verify = whole-suite cmd for review's integration gate
  <feature>/
    journal.md              append-only run log — one entry per completed stage
    PRD.md  arch.md  CONTEXT.md  docs/adr/*.md
    tasks/NN.md             frontmatter: status / attempts / verify / spec-paths / impl-paths / spec-rev
    reviews/review-NN.md
```

Status machine: `todo → in-progress → review → done`; `blocked` terminal; `attempts++`,
`>=3 ⇒ blocked ⇒ pipeline-hunt`. A context-less bot rebuilds full state from `git pull` +
`current.json` + a card scan. One feature in flight at a time (the human serializes; `current.json`
is a single pointer by design).

**Why a journal (append-only).** The handoff string below is the only artifact that was never on git —
it lived in chat, so a dead chat lost the one thing a cold node most needs (next command + gotchas +
steps). `journal.md` persists every stage's handoff as an append-only entry. The pattern is the
convergent one across durable-execution systems (Temporal event history, LangGraph checkpoints,
12-factor-agents' event log): **the append-only log is the source of truth; the live position is a fold
over its tail, never a separately stored "current stage".** We take the minimal subset — persist the
handoff we already produce, derive position from the tail — and skip replay/determinism (an LLM cannot
replay deterministically) and any scheduler (still human-relayed). `current.json.stage` stays only as a
fast bootstrap cache; on disagreement the journal tail wins.

## Handoff string (human copies to the next bot)

```text
>>> NEXT
Run pipeline-impl.
repo=<git-remote-url> branch=feat/login pr=none
- model: capable-local OK here (impl); reasoning stages want a frontier SOTA model — operator assigns the bot
- artifact: .pipeline/login/tasks/03.md (pushed)
- do: git pull --rebase, pick oldest todo card, make its red test green
- DO NOT touch spec-paths; white-box tests in impl-paths OK
- attempts=1/3 — two more failures ⇒ blocked ⇒ run pipeline-hunt
- on green: open PR, status=review, run pipeline-review
<<< END
```

Carries: next command, repo, branch, pr?, artifact path (never the body — git is the bus),
attempts/blocked path, the first action (`git pull`). Self-contained: the next bot shares no memory.

## Test ownership (anti-cheat)

`pipeline-task` writes the failing red test and freezes it into `spec-paths:` (recording `spec-rev`).
`pipeline-impl` only makes it green and may add white-box tests in `impl-paths:` — it must not touch
`spec-paths:`. `pipeline-review` enforces this with the two-commit `git diff <spec-rev> <review-tip> --
<spec-paths>` (review-tip = PR head) and **fails if the frozen spec changed**. Deterministic, git-only,
no CI required. The autonomous coder skill writes
its own tests by default — we don't fight it; the diff gate is what guarantees the exam wasn't edited.

## Forge / review surface

Review prefers the forge's PR (auditable thread + a clean human merge gate), falling back to a plain
branch diff when no forge PR exists. An adapter keyed on `git config --get remote.origin.url`:

- **GitHub** → `gh` (`gh pr diff` / `gh pr merge`).
- **Gitee** → `gitee-cli` against the instance's PR API.
- **anything else / no forge** → `git fetch && git diff base..branch → check` (forge-agnostic).

Merge is always human-confirmed (only the reviewer merges, after the human gate). The pipeline never
performs destructive operations (no repo/branch deletion beyond the task's own branch, no force-push).

## Thin aggregator + swappability

Each command is independently refactorable: it declares which skill slot it delegates to plus the
input/artifact contract, nothing more. A better skill drops in by editing one `roles.yaml` line; the
contract is unchanged. The frozen protocol (status machine, attempts, only-reviewer-merges) lives in
this file; commands carry no logic of their own.

## Borrowed / rejected

**Borrowed:** the ~20-line read-prior/write-next command shape (spec-kit); a truth-vs-proposal file
split (OpenSpec). **Rejected:** spec-kit's `specify` CLI / templates / constitution machinery;
heavy multi-subagent runtimes. We ship N markdown skill files — no CLI, no DB, no scheduler.

**Borrowed (Trellis):** spec auto-injection, reduced to pipeline's idiom — the handoff's "Read for
context" cites the target repo's existing agent-doc (`AGENTS.md`/`CLAUDE.md`), reusing pipeline's own
injection channel; no new `.trellis/spec` tree, no new stage, no new file. **Rejected (Trellis):** the
`trellis init` multi-platform CLI (same `no-CLI` rule as spec-kit's `specify`; install is already
`cp` + one runtime-registration line); the `.trellis/spec/` multi-file convention tree (a second convention source
that drifts from the agent-doc — revisit only when the agent-doc can't hold it); per-developer
`workspace/` parallel journals (violates one-feature-in-flight); `update-spec` auto-promotion
(deferred — payoff needs many features re-hitting the same convention; until a real signal, a learning
goes via an opportunistic `SKILL-PROPOSAL` + human-applied edit, the existing §Self-improvement path,
not a built-in mechanism).

## Constraints

No cron, no scheduler in the contract (human relays) — an OPTIONAL external driver (`pipeline-driver`,
the write-side twin of the read-only `pipeline-dashboard`) MAY auto-advance exactly TWO bounded,
human-bracketed spans plus ONE feature-authorized coordinated mode, and nothing else: (1) the `impl`
multi-card loop on a cheap model, STOPPING
before the review/merge gate, which stays human-run (the merge itself is a human step; trunk may
additionally be protected against force-push/deletion server-side where the plan allows); (2) the
review↔fix RELAY of a toolchain-repo meta-PR (the CONTRACT §Self-improvement lane): the review is
still `pipeline-review` in meta-PR mode, the verdict still lands on the PR, and the human-confirm +
reviewer-only squash-merge gate is untouched — the relay automates the TYPING between verdicts under
capped rounds and fail-closed halts, never a review judgment and never a merge. Both spans begin and
end at a human read; they are never chained to each other or to anything
else. (3) **Coordinated mode** (CONTRACT §Coordinated mode): under explicit per-feature authorization
(`.pipeline/<feature>/control.json`, created by `pipeline-prd` ONLY on an explicit operator request),
the deterministic watcher `coordinate.sh` MAY type every NORMAL stage handoff — it observes remote Git
only, validates the journal tail against a frozen route allowlist, makes no semantic decision, halts
fail-closed on anything outside the allowlist, and can neither merge nor confirm a merge (the review
GO-gate rejects relayed tokens; the human-direct merge confirm is untouched). The scheduler
prohibition stays the DEFAULT — coordinated mode is its one explicit, opt-in, journal-audited
exception (normative design: `coordinator-design.md` in `pipeline-driver`). The
contract itself is unchanged and stays scheduler-free and human-relayable · not coupled to
any machine · LLM-agnostic (reasoning commands want a
frontier model; `impl` tolerates a capable local LLM) · commands are extensible — a new verb is a new
~20-line shim + one `roles.yaml` line + the prior command's handoff naming it (e.g. `deploy`, `test`,
`learn` for unfamiliar-domain research).

## Open items

1. **Map the design-tree *shape* before walking its branches.** `grill-with-docs` walks branches but
   does not first establish the tree's shape — an open methodology gap, deferred.
2. **`pipeline-learn`** (a research stage before arch for unfamiliar external dependencies) — add when
   a domain-unfamiliar requirement actually appears, not before.
3. **Feature-level impl-loop budget ceiling** — the loop-engineering canon (`WHEN TO STOP` is a
   mandatory loop-charter field; every loop needs a budget ceiling) wants a stop condition above the
   per-card `attempts >= 3` breaker: impl's next-card routing auto-advances `card→card` with no
   feature-level bound on cumulative attempts/cost. **Not added:** under the default human-relay mode the
   operator IS the per-card ceiling; the only unbounded path is the **optional, not-yet-used
   `pipeline-driver`** unattended loop — no real runaway signal exists, so adding a mechanism now would
   violate the ratchet ("every line traces to a specific failure"). **Add when** the driver is first run
   unattended across ≥N cards past a single freeze gate (no per-card human checkpoint), OR a real
   cost-overrun is recorded in a `journal.md` entry — then specify it as a driver-honored clause in
   §Constraints: halt-and-report-to-human (route=human, NOT hunt — no single owner) when the feature's
   cumulative impl `attempts` (computed from the journal — evidence, not estimate) crosses
   `current.json.impl-budget`. Not before.
   *(Data point 2026-07-08: the driver's first real run crossed 2 cards past one freeze gate with
   no per-card checkpoint — bounded fine by `CARD_TIMEOUT` + the consecutive-failure breaker + the
   impl model's own quota ceiling; no overrun, so still deferred pending a real cost signal.)*

## Rejected

- **A default, mandatory, front-of-pipeline "research/discovery" stage** (clarify-questions + OSS
  prior-art/build-vs-buy + feasibility challenge + deploy-env survey). Rejected as a *required stage* —
  not the underlying lookups. Clarify-questions is already `pipeline-prd` (`grill-me → think`, which
  surveys the codebase first); feasibility-challenge and official-solution/OSS lookup are already latent
  in `think` (its "check for official solutions first" + evaluation modes) and would also fit a future
  `pipeline-learn` — they should fire **only when a specific requirement warrants it, never as a hard
  pipeline dependency**. A mandatory online OSS-search gate fails *closed* in air-gapped/intranet/non-IT
  contexts; the forge adapter is different — it uses `gh`/`gitee-cli` only when a forge exists and
  otherwise degrades *open* to a plain `git diff`, so optional fail-open network is in-contract while a
  mandatory fail-closed network stage is not. It also loads heavy OSS quality-vetting onto the earliest
  stage and adds a position to the otherwise-frozen state machine for a one-feature pipeline.
  Deployment-environment discovery for non-IT users is out of scope (this pipeline ends at review→merge
  and owns no `deploy` stage); if it ever matters it belongs in a separate `pipeline-deploy`/onboarding
  tool driven by a real deployment. Net: no new stage, no prd change; OSS/build-vs-buy stays an
  opportunistic `think`/`pipeline-learn` capability, not a gate.
