# pipeline

Agent-facing skill collection. Consumers are LLM/agents, not humans — read [CONTRACT.md](CONTRACT.md).

**What:** a forge-agnostic, machine-agnostic dev pipeline as 7 thin command-skills over a git+md
state bus. Human-relayed by default (no scheduler); each command prints a handoff the operator copies
to the next bot. An opt-in per-feature **coordinated mode** lets a coordinator type those handoffs
(CONTRACT §Coordinated mode; see §Operating modes — v1 is an attended CC playbook; a deterministic
watcher remains a future option). The only durable asset is the orchestration contract; the skill
behind each command is a swappable `roles.yaml` slot.

## Files

- `CONTRACT.md` — frozen protocol every command follows: shim loop · state machine · anti-cheat · handoff · forge adapter.
- `roles.yaml` — per-target-repo slot→skill bindings (copy into the target repo's `.pipeline/`).
- `skills/pipeline-*/SKILL.md` — the 7 command shims.
- `skills/pipeline-update/SKILL.md` — maintenance command (**not** a stage): pull the latest shims from GitHub onto this runtime. See [§Update](#update).

| command | slot → skill | in → out |
|---|---|---|
| pipeline-prd | grill-me → think | rough idea → `PRD.md` |
| pipeline-arch | grill-with-docs | PRD → `arch.md` + `CONTEXT.md` + ADRs |
| pipeline-task | think | arch → atomic cards + frozen red test |
| pipeline-impl | `<autonomous-coding-skill>` | card → green + PR (zero spec tests) |
| pipeline-review | check | diff/PR → review + merge (only stage that merges) |
| pipeline-hunt | hunt | blocked card → root cause → re-route |
| pipeline-improve | think | skill gap → reviewed PR on THIS repo (never self-edits, never auto-merges) |
| pipeline-coordinate | (playbook, not a stage) | a CC session coordinates Pi/Codex panes through a feature or meta-PR — see §Operating modes |

## Operating modes — the two-track SOP (operator decision, 2026-07-08)

**Default = the normal human-relayed mode** for every feature: the human reads each handoff
and relays each stage; use it for anything important or write-path (e.g. trading behavior).
**The drive mode must be EXPLICITLY requested by the operator** ("用 drive 范式") and is
reserved for read-only / low-risk / ergonomics features. Risk-tiering happens when the mode
is chosen — picking drive IS the ex-ante trust grant, so the driving agent may type the
GATE 1 spec-rev itself after reading the spec it froze.

Drive mode runs end-to-end with exactly ONE human touchpoint:

```text
[auto]  cc: prd → arch → task → freeze → GATE 1 (types spec-rev) → starts pipeline-driver
[auto]  driver: card 01 → card 02 → … → HALT at review        (stop-points.md enumerates every halt)
[auto]  cc: dispatches the review bot → freeze gate + full-verify + semantic review → verdict
──────────────────────────────────────────────────────────────
[HUMAN] merge confirm after ACCEPT — never delegated (CONTRACT frozen invariant)
[auto]  review bot: squash-merge → delete feat branch → journal wrap-up → done
```

A review REJECTION is a "problem found" → stop and show the human the verdict; never
silently restart the loop. Model split (both modes): frontier for prd/arch/task + review;
a capable cheap model for impl (per-stage requirement in `roles.yaml`).

**Third track — coordinated mode (opt-in per feature, CONTRACT §Coordinated mode).** The operator
explicitly requests it in the PRD session; `pipeline-prd` then commits
`.pipeline/<feature>/control.json` as the authorization audit. From the first journal entry on, a
coordinator types every NORMAL stage handoff into the right long-lived agent pane — **v1 is a CC
session running the `pipeline-coordinate` playbook skill** (CC coordinates and runs the reasoning
stages; Pi implements; Codex reviews — three roles on three different models so no model grades its
own work), routing ONLY on the journal tail, halting fail-closed on anything else. Judgment stays
where it was: stages do their own work, review still verdicts, and the **merge confirm is still a
direct operator token in the same reviewer session** — the coordinator has no merge path and the
GO-gate rejects relayed tokens. (A deterministic `coordinate.sh` watcher in `pipeline-driver` remains
a future option; its `doctor`/`status` preflight ships today and the playbook uses it.)

## Onboard a target project (paste into its `AGENTS.md` / `CLAUDE.md`)

So any agent touching a project knows it is pipeline-driven, paste this block verbatim into the
project's `AGENTS.md` / `CLAUDE.md`. It is the canonical onboarding snippet — copy it as-is (absolute
repo references are intentional so it works from any project):

> **This project is developed via the `pipeline` + `pipeline-dashboard` toolchain — a forge-agnostic,
> machine-agnostic, LLM-agnostic agent dev pipeline whose only durable asset is a git+markdown state
> bus under `.pipeline/`. Any capable agent runs its commands — the pipeline is not bound to any tool,
> framework, agent or LLM — and a different agent/LLM may run each stage (reasoning stages want a
> frontier SOTA model; `impl` tolerates a capable local model).**
>
> **How it works.** All work flows through staged commands `pipeline-prd → pipeline-arch →
> pipeline-task → pipeline-impl → pipeline-review`, plus `pipeline-hunt` for blocked cards. Each
> command is a ~20-line shim that does the same
> loop: `git pull --rebase` → read `.pipeline/current.json` + the feature's `journal.md` → resolve the
> stage's skill via `.pipeline/roles.yaml` → invoke that skill (it *reasons*; the shim owns all I/O) →
> write only its stage's write-set → append one entry to `.pipeline/<feature>/journal.md` → commit once
> → git push → print a self-contained handoff for the next (cold, possibly different-LLM) node. There is **no
> shared memory, no scheduler, no DB**: a human relays the printed handoff between bots, and any agent
> rebuilds full state from `git pull` alone.
>
> **The source of truth is `journal.md`** (append-only; its physically-last entry = the live position).
> `current.json` is only a fast cache — on disagreement the journal tail wins. The state machine is
> frozen: `todo → in-progress → review → done`, `blocked` terminal, `attempts ≥ 3 ⇒ blocked ⇒ hunt`.
> **Hard invariants you must never violate:** only `pipeline-review` merges, and only after explicit
> human confirmation; never edit a card's frozen `spec-paths` (the test gate — re-route to
> `pipeline-task` to re-freeze instead); never force-push trunk/shared refs; stay inside your stage's
> write-set; metadata lives on trunk, reviewed code on a `feat/<feature>` branch via PR.
>
> **To act:** read `CONTRACT.md` in [`jackypanster/pipeline`](https://github.com/jackypanster/pipeline)
> first (it is the single normative spec), then this repo's `.pipeline/<feature>/PRD.md` + `arch.md` +
> the journal tail. Do **not** hand-edit work out of band — run the stages.
>
> **To observe:** [`jackypanster/pipeline-dashboard`](https://github.com/jackypanster/pipeline-dashboard)
> is a read-only static-site generator. Run `node dist/cli.js /path/to/repo --out board.html` to render
> any `.pipeline/`-bearing checkout as a single `board.html` — feature stage flow, card lanes, and the
> run-journal timeline (who ran each stage, what transitioned, what failed, what's next), with a
> feature-level blocked banner. It never writes to the observed repo.
>
> **To auto-advance the `impl` loop (optional):** instead of hand-relaying each `impl` card, the
> repetitive `impl` multi-card loop can be run by
> [`jackypanster/pipeline-driver`](https://github.com/jackypanster/pipeline-driver) — a deterministic
> loop that runs `pipeline-impl` on a cheap model and **HALTS at every gate** (it never merges; review
> + the merge confirm stay gated). Its GATE 1 binds the run to a read of the frozen red test (echoing
> its `spec-rev`); **who performs that read is the operator's risk-tier call** — default a human,
> delegable to the coordinating agent only when the operator explicitly chose the drive mode for a
> low-risk feature (see §Operating modes above). It is the write-side twin of the dashboard, scoped to
> `impl` ONLY. Every other stage stays human-relayed by default — **do not build any other
> scheduler**; the pipeline deliberately has none (see `DESIGN.md`). The single sanctioned exception
> is the opt-in **coordinated mode** (CONTRACT §Coordinated mode): a feature whose
> `.pipeline/<feature>/control.json` authorizes it may have its normal stage handoffs typed by a
> coordinator — v1 is a CC session running the `pipeline-coordinate` playbook skill — with no stage
> work belonging to another role, no merge path, and the human-direct merge confirm unchanged.

## Install (agent: execute this; it is written for you, not a human)

You are an agent installing this collection on your runtime. Do it once, then verify dependencies.
To run this as a triggerable command instead of following it by hand, invoke the `pipeline-install`
skill — it executes exactly the steps below (idempotent machine install + per-project `roles.yaml`
bind), the setup-side twin of `pipeline-update`.

```bash
# 1. Get the repo as a READ-ONLY consumer clone (never edit skills here — see CONTRACT §Self-improvement).
git clone https://github.com/jackypanster/pipeline.git ~/workspace/pipeline   # or: git -C ~/workspace/pipeline fetch && git reset --hard origin/main

# 2. Install the command shims into the runtime that will RUN them (any capable agent — the pipeline
#    is framework-agnostic). Install where THAT runtime loads skills; two concrete examples follow —
#    substitute your own runtime:
#    - a runtime that loads skills from a directory (e.g. ~/.claude/skills):
cp -r ~/workspace/pipeline/skills/pipeline-* ~/.claude/skills/
#    - a runtime configured via a skills.external_dirs list: add "~/workspace/pipeline/skills" as a
#      YAML LIST item (NOT a JSON-encoded string, which fails silently), then reload the gateway.

# 3. Per target project, point the slots at your chosen skills:
mkdir -p <target-repo>/.pipeline && cp ~/workspace/pipeline/roles.yaml <target-repo>/.pipeline/roles.yaml
```

### Canonical multi-runtime layout — ONE physical copy (adopted 2026-07-08)

When several agent runtimes share one machine, install every skill into **one shared
physical directory** and attach each runtime to it — never maintain per-runtime copies.
(Field lesson: scattered copies meant the impl runtime had neither its shim nor its
slot skill; the run survived only on the journal/handoff fallback. A skill is just a
directory — `<name>/SKILL.md` + optional `references/` — so one copy serves everyone.)

```text
~/.agents/skills/                ← THE single physical install dir; all sources land here
  pipeline-*/                    ← step 2 above targets THIS dir
  think/ check/ hunt/ grill-*/   ← delegated skills from their source repos
  goal-driven-implementation/    ← the impl-slot skill
```

Attach each runtime to that one copy:

| runtime style | attachment |
|---|---|
| reads `~/.agents/skills` directly | nothing to do |
| per-skill symlink dir (pi-style `~/.pi/agent/skills`) | `ln -s ../../../.agents/skills/<name>` per skill |
| own skills dir (claude-style `~/.claude/skills`) | symlink entries in (or copy — then keep it fresh via `pipeline-update`) |
| own skills dir, `$`-invoked (codex ≥0.144 `~/.codex/skills`) | symlink entries in (field-verified 2026-07-12); invoke as `$<skill-name>`, NOT `/<name>` — quote `$` in shell configs. The old `~/.codex/prompts` thin-wrapper row is obsolete |

**Names resolve by frontmatter `name:`, not directory name** (field-verified on Claude Code
2026-07-12: a symlinked directory under a different name does NOT register). When a runtime needs
the canonical slot name to resolve to a runtime-local twin, attach a 10-line name-shim wrapper
skill (frontmatter `name:` = the canonical name; body = "invoke the twin skill with all arguments") — same
pattern for every runtime whose skill registry is frontmatter-keyed.

**Existing installs keep working — do not force-migrate.** Use this layout for every NEW
install and whenever attaching a new runtime; `pipeline-update` refreshes whatever
copies exist. Source repos (this repo, your skill collections) are the update origin,
never a load path.

### Verify + supplement dependencies (do this BEFORE running any command)

The commands delegate to the skills named in `roles.yaml`. Each must RESOLVE on the runtime that runs
its command. **Check every one; install any that is missing from its source:**

| delegated skill | used by | source |
|---|---|---|
| `think` | prd, task, improve | Waza — `github.com/tw93/Waza` (`skills/think`) |
| `check` | review | Waza (`skills/check`) |
| `hunt` | hunt | Waza (`skills/hunt`) |
| `grill-me` | prd | `github.com/mattpocock/skills` (`skills/productivity/grill-me`) |
| `grill-with-docs` | arch | `github.com/mattpocock/skills` (`skills/engineering/grill-with-docs`) |
| `<autonomous-coding-skill>` (impl slot) | impl | your runtime's autonomous think→design-tests→code→check skill — e.g. `goal-driven-implementation` (`devops/` in a `hermes-skills`-style set). ONE physical install under the canonical layout serves every runtime (see above); bind the skill's real installed name — do NOT invent per-runtime twin names (a phantom twin name in roles.yaml cost a real trial run) |

**Check procedure:** for each skill, confirm it loads on the runtime (list installed skills, or try to
`skill_view` it). Missing ⇒ install from its source into that runtime's skill dir ⇒ re-check.
**Cross-runtime trap:** a skill installed for one runtime is NOT resolvable from another (e.g. a skill
under one runtime's skill dir is invisible to a runtime that loads from a different dir, and vice versa) —
install it where the command actually runs. Each command verifies its OWN slot on init and STOPs if that
slot is missing — so verify all slots up front to avoid a mid-run stop. Names matter: set each slot to the
skill's real installed name on your runtime (for `impl`, the full `goal-driven-*` name your runtime
ships), never a bare/abstract token like `goal` or the `<autonomous-coding-skill>` placeholder.

**Brand names are install examples only.** The concrete agent/runtime/skill names in this Install
section (skill dirs, `goal-driven-*`) illustrate how to set up YOUR runtime — they are not part of the
contract. Never copy a specific tool/framework/agent/LLM name into the onboarding snippet or
`roles.yaml`: both reach target projects and must stay tool-agnostic.

## Update

Refresh the installed shims to the latest `main` **without re-reading the Install steps**: run the
`pipeline-update` command on the runtime that runs them. It is a **maintenance command, NOT one of the
stages** — it runs no shim loop and touches no project `.pipeline/` state. It self-locates the install,
pulls `github.com/jackypanster/pipeline` main, re-applies the `pipeline-*` shims, re-verifies the
delegated deps below, and reports what moved. The equivalent by hand (what the command wraps):

```bash
# Mode A — skills were cp'd as copies: re-copy from a freshly reset clone into EACH physical
# install — ONE run per PHYSICAL copy. Canonical layout = the single shared dir (runtimes
# attached by symlink/direct-read pick the refresh up for free); any legacy standalone copy
# (e.g. a claude-style own dir) needs its own re-copy.
git -C ~/workspace/pipeline fetch && git -C ~/workspace/pipeline reset --hard origin/main
cp -r ~/workspace/pipeline/skills/pipeline-* ~/.agents/skills/    # canonical shared copy
cp -r ~/workspace/pipeline/skills/pipeline-* ~/.claude/skills/    # legacy standalone copy, if kept
# Mode B — runtime loads skills straight from the clone (external_dirs): the reset alone suffices.
```

Runtime-shared skills only. A project's `.pipeline/roles.yaml` (your slot bindings) is never touched —
if a new version adds a slot, reconcile it by hand. Sibling repos (`pipeline-dashboard`,
`pipeline-driver`) update themselves.

## State

Contract + 7 command shims (prd/arch/task/impl/review/hunt/improve). Proven end-to-end once on a real
project (a GET endpoint shipped via prd→arch→task→impl→review→merge). Rationale: [DESIGN.md](DESIGN.md).
