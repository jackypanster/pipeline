# pipeline

Agent-facing skill collection. Consumers are LLM/agents, not humans — read [CONTRACT.md](CONTRACT.md).

**What:** a forge-agnostic, machine-agnostic dev pipeline as 7 thin command-skills over a git+md
state bus. Human-relayed (no scheduler); each command prints a handoff the operator copies to the
next bot. The only durable asset is the orchestration contract; the skill behind each command is a
swappable `roles.yaml` slot.

## Files

- `CONTRACT.md` — frozen protocol every command follows: shim loop · state machine · anti-cheat · handoff · forge adapter.
- `roles.yaml` — per-target-repo slot→skill bindings (copy into the target repo's `.pipeline/`).
- `skills/pipeline-*/SKILL.md` — the 7 command shims.

| command | slot → skill | in → out |
|---|---|---|
| pipeline-prd | grill-me → think | rough idea → `PRD.md` |
| pipeline-arch | grill-with-docs | PRD → `arch.md` + `CONTEXT.md` + ADRs |
| pipeline-task | think | arch → atomic cards + frozen red test |
| pipeline-impl | goal-driven-implementation | card → green + PR (zero spec tests) |
| pipeline-review | check | diff/PR → review + merge (only stage that merges) |
| pipeline-hunt | hunt | blocked card → root cause → re-route |
| pipeline-improve | think | skill gap → reviewed PR on THIS repo (never self-edits, never auto-merges) |

## Onboard a target project (paste into its `AGENTS.md` / `CLAUDE.md`)

So any agent touching a project knows it is pipeline-driven, paste this block verbatim into the
project's `AGENTS.md` / `CLAUDE.md`. It is the canonical onboarding snippet — copy it as-is (absolute
repo references are intentional so it works from any project):

> **This project is developed via the `pipeline` + `pipeline-dashboard` toolchain — a forge-agnostic,
> machine-agnostic, LLM-agnostic agent dev pipeline whose only durable asset is a git+markdown state
> bus under `.pipeline/`.**
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
> [`jackypanster/pipeline-driver`](https://github.com/jackypanster/pipeline-driver) — a deterministic,
> **human-operated** loop (**an agent cannot run it unattended**: its GATE 1 blocks on a human reading the
> frozen red test and echoing its `spec-rev`) that runs `pipeline-impl` on a cheap model and **HALTS at
> every gate** (it never merges; the human runs `pipeline-review`). It is the human-operated write-side
> twin of the dashboard, scoped to `impl` ONLY. Every other stage stays human-relayed — **do not build any
> other scheduler**; the pipeline deliberately has none (see `DESIGN.md`).

## Install (agent: execute this; it is written for you, not a human)

You are an agent installing this collection on your runtime. Do it once, then verify dependencies.

```bash
# 1. Get the repo as a READ-ONLY consumer clone (never edit skills here — see CONTRACT §Self-improvement).
git clone https://github.com/jackypanster/pipeline.git ~/workspace/pipeline   # or: git -C ~/workspace/pipeline fetch && git reset --hard origin/main

# 2. Install the command shims into the runtime that will RUN them:
#    Claude Code:
cp -r ~/workspace/pipeline/skills/pipeline-* ~/.claude/skills/
#    Hermes: add "~/workspace/pipeline/skills" to skills.external_dirs (a YAML LIST item — NOT a
#    JSON-encoded string, which fails silently), then reload the gateway.

# 3. Per target project, point the slots at your chosen skills:
mkdir -p <target-repo>/.pipeline && cp ~/workspace/pipeline/roles.yaml <target-repo>/.pipeline/roles.yaml
```

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
| `goal-driven-implementation` | impl | your Hermes runtime's `hermes-skills` (`devops/goal-driven-implementation`) |

**Check procedure:** for each skill, confirm it loads on the runtime (list installed skills, or try to
`skill_view` it). Missing ⇒ install from its source into that runtime's skill dir ⇒ re-check.
**Cross-runtime trap:** a skill in `~/.hermes/skills` is NOT resolvable from Claude Code's
`~/.claude/skills` (and vice versa) — install it where the command actually runs. `pipeline-prd`
re-verifies every slot resolves on init and STOPs if one is missing — but verify up front to avoid a
mid-run stop. Names matter: `roles.yaml` says `goal-driven-implementation`, not bare `goal`.

## State

Contract + 7 command shims (prd/arch/task/impl/review/hunt/improve). Proven end-to-end once on a real
project (a GET endpoint shipped via prd→arch→task→impl→review→merge). Rationale: [DESIGN.md](DESIGN.md).
