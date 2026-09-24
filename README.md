# pipeline

Agent-facing skill collection. Consumers are LLM/agents, not humans — read [CONTRACT.md](CONTRACT.md).

**What:** a forge-agnostic, machine-agnostic dev pipeline as 7 thin command-skills over a git+md
state bus. Human-relayed by default (no scheduler); each command prints a handoff the operator copies
to the next bot. An opt-in per-feature **coordinated mode** lets a coordinator type those handoffs
(CONTRACT §Coordinated mode; see §Operating modes — v1 is an attended CC playbook; why not a
dispatcher: DESIGN.md §Provenance). The only durable asset is the orchestration contract; the skill
behind each command is a swappable `roles.yaml` slot.

## Files

- `CONTRACT.md` — frozen protocol every command follows: shim loop · state machine · anti-cheat · handoff · forge adapter.
- `roles.yaml` — per-target-repo slot→skill bindings (copy into the target repo's `.pipeline/`).
- `skills/pipeline-*/SKILL.md` — the 7 command shims.
- `skills/pipeline-coordinate/SKILL.md` — playbook (**not** a stage, no `roles.yaml` slot): a CC session coordinates Pi/Codex panes through a feature or meta-PR (see §Operating modes). Needs `herdr` + `python3` ≥3.9 — see §Verify + supplement dependencies.
- `skills/pipeline-install/SKILL.md` — maintenance command (**not** a stage): stand up the shims on a runtime + bind a target project's `roles.yaml`, by executing README §Install. The setup-side twin of `pipeline-update`. See [§Install](#install-agent-execute-this-it-is-written-for-you-not-a-human).
- `skills/pipeline-update/SKILL.md` — maintenance command (**not** a stage): pull the latest shims from GitHub onto this runtime. See [§Update](#update-agent-execute-this-on-the-machine-being-updated).

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
| pipeline-preflight | (helper script, not a stage) | deterministic executor of shim steps 1/3/4 + the stale-dispatch guard + the card-invariant check (impl/review/hunt entry, task 6b); stage skills call `scripts/preflight.sh` at step 0 — never invoke by hand; set `PIPELINE_SKILL_DIRS` per runtime for a verified install check, otherwise exit 3 and the stage verifies it |

## Operating modes — the three-track SOP (base decision 2026-07-08; duty track added 2026-08-19)

**Default = the normal human-relayed mode** for every feature: the human reads each handoff
and relays each stage; use it for anything important or write-path (e.g. trading behavior).
Model split (all modes): frontier for prd/arch/task + review; a capable cheap model for impl
(per-stage requirement in `roles.yaml`).

**Second track — coordinated mode (opt-in per feature, CONTRACT §Coordinated mode).** The operator
explicitly requests it in the PRD session; `pipeline-prd` then commits
`.pipeline/<feature>/control.json` as the authorization audit. From the first journal entry on, a
coordinator types every NORMAL stage handoff into the right long-lived agent pane — **v1 is a CC
session running the `pipeline-coordinate` playbook skill** (CC coordinates and runs the reasoning
stages; Pi implements; Codex reviews — three roles on three different models so no model grades its
own work), routing ONLY on the journal tail, halting fail-closed on anything else. Judgment stays
where it was: stages do their own work, review still verdicts, and the **merge confirm is still a
direct operator token in the same reviewer session** — the coordinator has no merge path and the
GO-gate rejects relayed tokens. (`pipeline-driver`'s `coordinate.sh` ships read-only `doctor`/`status` preflight only; see
DESIGN.md §Provenance.)

**Third track — duty mode (值守: queued timed re-entry of coordinated mode; pilot 2026-08-19,
target repo `oh-my-wiki`).** Coordinated mode with the operator AWAY: the operator opens a dedicated
CC session **in its own duty/observer clone — never a role pane's checkout** (the playbook's
per-role-clone discipline; the duty tick's `git switch`/`pull` must not move HEAD under an in-flight
impl) and types one standing invocation — `/loop 1h /pipeline-coordinate <repo> duty tick` —
whose every timed re-entry re-presents that invocation, keeping the coordinator role ASSIGNED, never
inferred. Each tick executes the target repo's `.pipeline/duty-tick.md`: `git pull --ff-only` → read
the human-ordered `.pipeline/queue.md` → advance AT MOST the queue head, and ONLY through the
post-freeze half of Profile B (impl multi-card loop → review dispatch → verdict). Everything upstream
of the freeze — prd/arch/task, the GATE 1 spec-rev read, queue order — stays in attended day
sessions, so every feature remains human-bracketed: frozen and read BEFORE it may start; ended by the
human-direct merge token in the reviewer pane (armed-gate rules unchanged — once the GO-gate is
armed the duty session sends NOTHING to the reviewer pane and the forge PR state is the only wait
instrument). Gates notify the
operator out-of-band (`hermes send` → Telegram), once per condition, plus ONE guaranteed daily digest
as a dead-man switch: a silent day means the duty session is down, never that nothing happened. A
blocked, spec-drift, or over-budget head halts the WHOLE queue (linear by design);
`max-features-per-day` plus a per-feature `impl-budget` (cumulative-attempts halt, DESIGN
Constraint (3)) cap spend. The duty session stays READ-ONLY toward the target repo — CONTRACT's
coordinator write ban holds: `queue.md` is human-owned, run state is derived each tick from
journal + cards + forge, and the session's only private state is a local, disposable notification
ledger whose loss at worst repeats a notification.

### Choosing the mode — the agent recommends, the operator decides

When a requirement settles (end of `pipeline-prd`; same table for a bugfix flow that skips prd), the
agent consults this table, shows the current machine bindings (the `coordinate.sh status` bindings
block where the installed version emits one, else drive.defaults, else "bindings unavailable —
pipeline-driver is optional"), and recommends ONE mode with a one-line
rationale. The operator's reply decides. The decision is recorded only by the existing mechanisms —
coordinated ⇒ `control.json` (pipeline-prd), human-relay ⇒ nothing — a recommendation never becomes an authorization by itself.

| situation | recommend |
|---|---|
| dangerous surface — write-path / trading / external side effects | human-relay (mandatory; coordinated and duty are forbidden here) |
| new feature, unit-testable spec, multi-card impl | human-relay, or coordinated mode when low-risk |
| small feature / bugfix on an existing project, low-risk | human-relay (coordinated if the operator wants zero relay typing) |
| operator present but wants zero relay typing | coordinated mode (`control.json`; visible panes; merge token still human-direct) |
| operator away for hours; frozen, low-risk features queued up | duty mode (`/loop` + `queue.md` + `duty-tick.md`; day sessions freeze, read GATE 1, and enqueue) |

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
> command is a thin shim that runs the same
> loop: `git pull --rebase` → read `.pipeline/current.json` + the feature's `journal.md` → resolve the
> stage's skill via `.pipeline/roles.yaml` (pull, `current.json`, slot resolution and the coordinated
> stale-dispatch guard run as `pipeline-preflight/scripts/preflight.sh`) → invoke that skill (it
> *reasons*; the shim owns all I/O) →
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
> **Do not build any other scheduler**; the pipeline deliberately has none (see `DESIGN.md`). The
> single sanctioned exception is the opt-in **coordinated mode** (CONTRACT §Coordinated mode): a feature whose
> `.pipeline/<feature>/control.json` authorizes it may have its normal stage handoffs typed by a
> coordinator — v1 is a CC session running the `pipeline-coordinate` playbook skill — with no stage
> work belonging to another role, no merge path, and the human-direct merge confirm unchanged.

## Install (agent: execute this; it is written for you, not a human)

You are an agent installing this collection on your runtime. Do it once, then verify dependencies.
To run this as a triggerable command instead of following it by hand, invoke the `pipeline-install`
skill — it executes exactly the steps below (idempotent machine install + per-project `roles.yaml`
bind), the setup-side twin of `pipeline-update`.
Every machine that runs a pipeline stage (including a remote agent reached over herdr) must install the full `pipeline-*` set — stage skills locate preflight as a sibling dir, and an absent preflight is a STOP.

```bash
( set -e   # MACHINE install — ANY failure (clone, mkdir, ln — e.g. a dangling attachment) aborts every later step
# 1. A READ-ONLY consumer clone (never edit skills here — see CONTRACT §Self-improvement).
[ -e ~/.agents/pipeline ] || git clone https://github.com/jackypanster/pipeline.git ~/.agents/pipeline

# 2. Canonical entries: one RELATIVE symlink per skill into the clone (existing entries are left alone).
mkdir -p ~/.agents/skills
for d in ~/.agents/pipeline/skills/pipeline-*/; do n=$(basename "$d")
  [ -e ~/.agents/skills/$n ] || [ -L ~/.agents/skills/$n ] || ln -s ../pipeline/skills/$n ~/.agents/skills/$n
done

# 3. Runtime attachments → the canonical entries, for each runtime installed on this machine
#    (its home dir exists); its skills/ dir is created if missing.
for d in ~/.agents/pipeline/skills/pipeline-*/; do n=$(basename "$d")
  if [ -d ~/.claude ]; then mkdir -p ~/.claude/skills                                # claude: relative
    [ -e ~/.claude/skills/$n ] || ln -s ../../.agents/skills/$n ~/.claude/skills/$n; fi
  if [ -d ~/.codex ]; then mkdir -p ~/.codex/skills                                  # codex: absolute
    [ -e ~/.codex/skills/$n ] || ln -s ~/.agents/skills/$n ~/.codex/skills/$n; fi
done
if [ -d ~/.pi/agent ]; then mkdir -p ~/.pi/agent/skills                              # pi: impl + preflight only
  for n in pipeline-impl pipeline-preflight; do
    [ -e ~/.pi/agent/skills/$n ] || ln -s ../../../.agents/skills/$n ~/.pi/agent/skills/$n
  done
fi

# 4. (optional; coordinated mode needs it) The companion driver provides `coordinate.sh doctor/status`.
#    Runs in place, no install step. Absent ⇒ clone; present ⇒ keep it (§Update pulls it).
[ -e ~/workspace/pipeline-driver ] || git clone https://github.com/jackypanster/pipeline-driver.git ~/workspace/pipeline-driver
echo "install OK" )
```

```bash
# 5. Per target project, bind the slots — ONLY after the machine block printed "install OK".
#    Never clobber an existing roles.yaml — overwriting a configured project wipes its bindings
#    and restores the unresolved <autonomous-coding-skill> placeholder.
#    When newly created, set the impl slot to your runtime's real installed skill name.
cd <target-repo>
mkdir -p .pipeline
[ -e .pipeline/roles.yaml ] || [ -L .pipeline/roles.yaml ] || cp ~/.agents/pipeline/roles.yaml .pipeline/roles.yaml
```

**Migrate from a copy install** (one time; `pipeline-update` reports `LEGACY <name>` until done). The
runtime attachments keep working: they point at `~/.agents/skills/<name>`, which becomes a link.

```bash
( set -e   # any failure stops BEFORE the live copies move; a usable clone is the precondition
  [ -e ~/.agents/pipeline ] || git clone https://github.com/jackypanster/pipeline.git ~/.agents/pipeline
  [ -f ~/.agents/pipeline/skills/pipeline-preflight/scripts/preflight.sh ]   # clone is usable
  bak=~/.agents/skill-backups/$(date +%Y%m%d)-copy-install; mkdir -p "$bak"
  mv ~/.agents/skills/pipeline-* "$bak"/
  for f in ~/.agents/skills/.pipeline-update*; do if [ -e "$f" ]; then mv "$f" "$bak"/; fi; done   # old stamp
  for d in ~/.agents/pipeline/skills/pipeline-*/; do n=$(basename "$d")               # = step-2 link loop
    ln -s ../pipeline/skills/$n ~/.agents/skills/$n; done
  echo "migrated; backup at $bak" )
```

### Canonical multi-runtime layout — one clone, links all the way down

Every runtime on the machine loads the same files through a two-level symlink chain — never
maintain per-runtime copies. (Field lesson: scattered copies meant the impl runtime had neither its
shim nor its slot skill. Verified 2026-09-24: Claude, Codex and Pi fresh sessions all discover a skill
through `~/.codex/skills/X -> ~/.agents/skills/X -> ../pipeline/skills/X`.)

```text
~/.agents/pipeline/                ← read-only git clone of this repo (pipeline-update pulls it)
~/.agents/skills/
  pipeline-* -> ../pipeline/skills/pipeline-*   ← canonical entries (step 2)
  think/ check/ hunt/ grill-*/     ← delegated skills from their source repos
  goal-driven-implementation/      ← the impl-slot skill
~/.claude/skills/pipeline-*        -> ../../.agents/skills/pipeline-*
~/.codex/skills/pipeline-*         -> ~/.agents/skills/pipeline-*  (invoke as `$<skill-name>`, not `/<name>`)
~/.pi/agent/skills/pipeline-{impl,preflight} -> ../../../.agents/skills/…
```

A runtime that reads `~/.agents/skills` directly needs no attachment.

**Names resolve by frontmatter `name:`, not directory name** (field-verified on Claude Code
2026-07-12: a symlinked directory under a different name does NOT register). When a runtime needs
the canonical slot name to resolve to a runtime-local twin, attach a 10-line name-shim wrapper
skill (frontmatter `name:` = the canonical name; body = "invoke the twin skill with all arguments") — same
pattern for every runtime whose skill registry is frontmatter-keyed.

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

**Non-skill runtime tools (NOT `roles.yaml` slots — no command self-checks them).** Verify these
before the first run that needs them, or the install reports green and the command dies at first use:

| tool | needed by | when | source |
|---|---|---|---|
| `gh` / `gitee-cli` | impl, improve, review, coordinate | only when the target repo has a forge | the forge's own CLI. Review degrades to a plain `git diff` without one (CONTRACT §Forge adapter); opening a PR does not — `pipeline-impl` step 4 falls back only on a missing **token**, and `pipeline-improve` step 5 has no CLI-less path |
| `herdr` | pipeline-coordinate | only for coordinated runs (pane transport) | `https://herdr.dev` |
| `python3` ≥3.9 | pipeline-coordinate | only for coordinated runs (`scripts/watch-pane.py`, stdlib only) | base system package on macOS/Linux |

**Brand names are install examples only.** The concrete agent/runtime/skill names in this Install
section (skill dirs, `goal-driven-*`) illustrate how to set up YOUR runtime — they are not part of the
contract. Never copy a specific tool/framework/agent/LLM name into the onboarding snippet or
`roles.yaml`: both reach target projects and must stay tool-agnostic.

## Update (agent: execute this on the machine being updated)

**Operator — paste this to the agent on any machine:**
`Update pipeline on this machine: fetch https://github.com/jackypanster/pipeline/blob/main/README.md and execute its §Update runbook step by step.`
Enter through this URL, not through the locally installed `pipeline-update` skill: an old machine's
installed skill predates this layout and does not know it. Run between stages, never mid-stage. The
runbook touches only this machine's skill install — never a project's `.pipeline/` state or `roles.yaml`.

**U1. Detect the install state** (read-only):

```bash
for d in ~/.agents/skills ~/.claude/skills ~/.codex/skills ~/.pi/agent/skills; do
  for e in "$d"/pipeline-*; do [ -e "$e" ] || [ -L "$e" ] || continue
    if [ -L "$e" ]; then echo "LINK $e -> $(readlink "$e")"; else echo "REAL $e"; fi; done; done
[ -d ~/.agents/pipeline/.git ] && echo "CLONE $(git -C ~/.agents/pipeline log --oneline -1)" || echo "NO-CLONE"
```

Pick the FIRST row that matches:

| state | signature | do |
|---|---|---|
| **A current** | every `~/.agents/skills/pipeline-*` is `LINK … -> ../pipeline/skills/…`, `CLONE` present | U2 |
| **B copy install** (before 2026-09-24) | `REAL ~/.agents/skills/pipeline-*` | §Install *Migrate from a copy install* block, then U2 |
| **C per-runtime copies** (before 2026-07-29) | `REAL` entries under `~/.claude`/`~/.codex`/`~/.pi` skills, not only under `~/.agents` | move each `REAL` runtime entry to `~/.agents/skill-backups/$(date +%Y%m%d)-runtime-copies/<runtime>/`; then the B block if `~/.agents/skills` holds `REAL` entries; then the §Install machine block (idempotent — it creates only what is missing); then U2 |
| **D nothing installed** | no `pipeline-*` rows | §Install, then U2 |
| **E anything else** (a `LINK` pointing elsewhere, a dev clone as source, …) | — | STOP: report the U1 output to the operator; do not improvise |

**U2. Update + verify** — all of these must hold:

```bash
bash ~/.agents/skills/pipeline-update/scripts/update.sh      # ⇒ every entry "ok", then "updated …" or "already latest", exit 0
for n in $(ls ~/.agents/pipeline/skills); do                  # every installed runtime resolves every attachment
  for d in ~/.claude/skills ~/.codex/skills; do [ -d "${d%/skills}" ] && { [ -f "$d/$n/SKILL.md" ] || echo "MISSING $d/$n"; }; done
done
for n in pipeline-impl pipeline-preflight; do [ -d ~/.pi/agent ] && { [ -f ~/.pi/agent/skills/$n/SKILL.md ] || echo "MISSING pi $n"; }; done
```

- `STOP: …` from `update.sh` ⇒ report it verbatim and stop. Never reset, stash, or re-clone to force it.
- `LINKED <name>` (a new upstream skill) or `MISSING …` ⇒ run the §Install machine block (idempotent; adds only what is missing), then re-run U2.
- `LEGACY` / `MISLINKED` ⇒ you are not in state A — go back to U1.
- Freshness: in any target repo, `preflight.sh --stage impl --repo <repo>` must print `UPSTREAM ok`.
  `UPSTREAM unverified network` right after a bad-network spell is the 24h throttle cache
  (`~/.cache/pipeline/upstream-head`); deleting that file is safe and forces a recheck.
- Runtime discovery (optional, strongest): a FRESH session of each runtime lists every `pipeline-*`
  skill — e.g. `codex exec --skip-git-repo-check "<list your pipeline-* skills>"`, `pi -p "…"`,
  `claude -p "…"`. Already-running sessions keep the skill list they loaded at start.

**U3. Report**: the U1 state, what you moved (backup paths), the `update.sh` tail (`HEAD <sha>` +
`updated`/`already latest`), and the verification results. A new `roles.yaml` slot in the upstream
template is reconciled by hand per project — say so if `~/.agents/pipeline/roles.yaml` changed.

**Rollback** (B/C migrations): delete the new `~/.agents/skills/pipeline-*` links and move the entries
back from the `~/.agents/skill-backups/<date>-*` dirs the migration printed.

**Driver clone** (only on machines that run coordinated mode; it holds `coordinate.sh`): a read-only
consumer clone — refuse on local tracked edits, then fast-forward only:

```bash
if git -C ~/workspace/pipeline-driver status --porcelain --untracked-files=no | grep -q .; then
  echo "STOP: driver clone has local edits to tracked files — inspect by hand" >&2
else git -C ~/workspace/pipeline-driver pull --ff-only; fi
```

## State

Contract + 7 command shims (prd/arch/task/impl/review/hunt/improve). Proven end-to-end on real
multi-repo, multi-runtime projects since 2026-06. Rationale: [DESIGN.md](DESIGN.md).
