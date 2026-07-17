---
name: pipeline-coordinate
description: "Coordinator playbook — a CC session dispatches Pi (implementation) and Codex (review) through Herdr panes to run a pipeline feature or a toolchain meta-PR end to end. NOT a pipeline stage and NOT a scheduler daemon: CC is the requirements/coordination role of a three-role, three-model team; it never implements product code, never reviews, never merges. Args: repo, what to coordinate (a feature idea, or a meta-PR task)."
---

# pipeline-coordinate — the coordinator playbook

You are CC, the coordinator. This playbook makes a COLD session able to run the loop a human
otherwise runs by hand: dispatch work to the implementer pane, verify what comes back, relay review
rounds, and stop at every gate that belongs to a human. It replaces manual handoff TYPING — never
human judgment, never stage work that belongs to another role.

**Why three roles on three models (the founding constraint):** requirements (CC/Claude),
implementation (Pi), review (Codex) are deliberately DIFFERENT models so no model grades its own
work — LLM self-preference is real and was observed here (an author-written test suite stubbed the
exact integration its own implementation got wrong; the cross-model reviewer caught it in one round).
The human is the fourth, final gate.

## Hard rules (violating any one voids the run)

1. **Three roles, three models.** CC coordinates and authors requirements artifacts only; it does NOT
   write product/implementation code and does NOT review. Pi implements only; it does NOT review and
   does NOT merge. Codex reviews only; it is the ONLY role that merges, and only on a direct human
   token typed in ITS OWN session. Any two roles collapsing into one model is a violation that VOIDS
   the coordinated run.
2. **Implementer death = STOP.** If the implementer pane dies (quota exhausted, crash, blocked), stop
   the run, report the exact break point (committed vs uncommitted work, last green state), and wait.
   Do NOT take over the implementation. A compromise directive ("CC, take over") can come ONLY from
   the human, and it does not bend rule 1 — it ENDS the coordinated run for that work item and starts
   a separately attributed TWO-MODEL fallback workflow (CC implements with honest attribution in
   every commit and PR body; Codex still independently reviews AND remains the only role that
   executes the merge, on the human's direct token — only-reviewer-merges holds in the fallback
   too). Resume coordinated mode only when three distinct models are available again.
3. **Git is the only truth.** Verify the implementer's work yourself (rerun the tests, check the diff
   scope) before pushing anything; never trust a self-report. Never paste artifact bodies between
   panes — git is the bus, handoffs carry paths.
4. **Fail closed to the human.** Anything outside this playbook's routes — an unexpected journal
   state, a review that will not converge, an ambiguous instruction — means stop and surface it.
   The coordinator's failure mode is ALWAYS "stop and ask", never "guess and proceed". Never merge,
   never force-push, never expand scope on your own.

## Preflight (once per session)

1. `herdr pane list` — identify your OWN pane (exclude it from every dispatch), the Pi pane, and the
   Codex pane by agent + cwd. Exactly one pane per role; zero or many = stop and ask.
2. `herdr agent explain <pane> --json` per role pane — require an AUTHORITATIVE state source
   (`.fallback_reason == null` and a matched rule / lifecycle hook). Non-authoritative = stop: you
   cannot safely tell working from idle.
3. **Model identities.** Pane agent type is not model identity. Read each role pane's visible
   footer/title (`herdr pane read <pane> --source visible --lines 3` — Pi and Codex TUIs show their
   model there) and confirm three DISTINCT underlying models. Unknown or duplicate → stop and ask
   the human to attest which models back each pane; do not proceed on assumption.
4. Coordinating a pipeline feature (Profile B)? Run `pipeline-driver`'s read-only preflight. Cold
   start: `git clone https://github.com/jackypanster/pipeline-driver` (or reuse an existing clone),
   `cp coordinate.config.example coordinate.config`, fill EVERY non-optional field per the example's
   comments — `OBSERVER_WORKDIR` = a clone this session may fetch in,
   `CC_WORKDIR`/`PI_WORKDIR`/`CODEX_WORKDIR` = the three role clones matching the panes from step 1,
   `BRANCH`, the five command-prefix fields, and the three timeout fields (pane pins / `STATE_DIR` /
   `ON_HALT_EXEC` are optional). **Run `bash coordinate.sh doctor --config coordinate.config` from a
   NON-ROLE shell** — a plain terminal or a fourth utility pane, never from the CC/Pi/Codex panes:
   the shipped tool captures its own `HERDR_PANE_ID` as "self" and excludes that pane from role
   resolution (a dispatcher must never type into itself), so under this playbook's same-session
   topology a doctor run inside the CC pane can never resolve the CC role and fails
   `PANE_NOT_FOUND` by design. Any MISS blocks the run; `coordinate.sh status --config …` shows the
   last observed state at any time.

## Transport verbs (Herdr today; the two verbs are the swappable seam)

- **send** — `herdr pane run <pane> '<single line>'` (atomic text+Enter); never into your own pane.
  **Claude Code TUI gotcha (field-verified 2026-07-17):** a line starting with `/` opens the
  slash-command completion popup, which EATS the trailing Enter — the text sits unsubmitted on the
  input line. After sending a slash command to a Claude Code pane, read the pane back; if the text
  is still on the input line, send one empty follow-up (`herdr pane run <pane> ''`) and verify it
  submitted.
  **Per-send readiness, immediately before every send:** take ONE sample —
  `herdr agent explain <pane> --json` — and require BOTH `authority` (matched rule / lifecycle hook,
  `.fallback_reason == null`) AND `state == idle` from that SAME sample (state without authority is
  the fallback's always-idle lie); then `herdr pane read <pane> --source visible --lines 3` and
  verify the input line is EMPTY (an idle pane can hold draft text your Enter would submit). Either
  check failing = do not send; re-sample or stop.
- **status** — same single-sample discipline: read state AND authority together; lost authority at
  ANY poll fails closed (stop, tell the human), never "keep polling state".
- **watch** (background, so you are woken instead of polling by hand). Arm it only AFTER a send you
  observed leave the idle state — the first post-send `idle` sample may be the PRE-dispatch state
  (`pane run` proves delivery to the TUI, not processing):

  ```bash
  # bounded_ms <ms> <cmd…>: the reviewed process-group-killing deadline (drive.sh pattern) — a
  # wedged `herdr agent explain` must not hang the loop; its own counters cannot bound a stuck
  # command substitution.
  bounded_ms() { local ms=$1; shift; perl -e '
    use POSIX ":sys_wait_h"; use Time::HiRes qw(ualarm);
    my $ms=shift; my $p=fork; if(!$p){setpgrp(0,0); exec @ARGV or exit 127;}
    $SIG{ALRM}=sub{kill "KILL",-$p; waitpid $p,0; exit 124};
    ualarm($ms*1000); waitpid $p,0; my $rc=$?>>8; my $sg=$?&127; ualarm(0);
    kill "KILL",-$p; exit($sg?128+$sg:$rc)' -- "$ms" "$@"; }
  # sample(): ONE explain → "<authority> <state>", the exact fail-closed predicate the reviewed
  # driver uses: (full lifecycle hook OR matched rule) AND no fallback. The transport rc is checked
  # BEFORE parsing — piping bounded_ms straight into jq would let jq consume JSON that arrived
  # before a hang and erase the 124 timeout; timeout/nonzero/malformed all yield "0 unknown".
  sample() {
    local j
    if ! j=$(bounded_ms 5000 herdr agent explain <pane> --json 2>/dev/null); then
      printf '0 unknown'; return 0
    fi
    printf '%s' "$j" | jq -r '
      (if (((.screen_detection_skip_reason == "full_lifecycle_hook_authority")
            or (.matched_rule != null)) and (.fallback_reason == null)) then "1" else "0" end)
      + " " + ((.state // "unknown") | tostring)' 2>/dev/null || printf '0 unknown'
  }
  # phase 1: wait for the pane to START (working) — no start within ~2min = stop and inspect;
  # phase 2: wait for idle. Authoritative `blocked` and any state outside idle|working fail
  # IMMEDIATELY in either phase (a fast permission/quota prompt can skip the sampled working state).
  started=0
  for i in $(seq 1 135); do
    set -- $(sample); a=$1; s=$2
    [ "$a" = 1 ] || exit 4                          # lost/never-had authority — fail closed
    case "$s" in blocked) exit 2 ;; idle|working) ;; *) exit 4 ;; esac
    if [ "$started" = 0 ]; then
      [ "$s" = working ] && started=1
      [ "$i" -ge 6 ] && [ "$s" = idle ] && exit 5   # never started processing
    else
      [ "$s" = idle ] && exit 0
    fi
    sleep 20
  done; exit 3
  ```

  Exit 0 means the pane went working→idle — it is NOT completion by itself: completion additionally
  requires the expected Git/deliverable evidence (new commits in the worktree, an advanced journal
  seq, a posted verdict). Idle WITHOUT that evidence = the stage ended without delivering → stop and
  inspect (never re-send on a hunch; see the redelivery rule below). Exit 2 (blocked) → read the
  pane to see the blocker; quota/crash → hard rule 2.

**Redelivery is NOT generally safe.** With no delivery ledger, an unconsumed dispatch cannot be
distinguished among "never delivered", "delivered, not yet started", and "ran and ended without
delivering" (the load-bearing PR #12 lesson recorded in coordinator-design v1.2/§25). An ambiguous
send, or unchanged Git after a send, always STOPS for human inspection of the pane transcript. Only
a dispatch whose effect is OBSERVED (journal advanced / commits landed / verdict posted) is
replay-safe to move past.

## Profile A — meta-PR flow (toolchain, docs, small changes; quality via adversarial review)

For changes with no `.pipeline` feature state (CONTRACT §Self-improvement lane). No envelope needed:
review pins the head sha, which is the staleness guard. **Coordinated Profile A requires a forge
with a PR thread** (github via `gh`, gitee via `gitee-cli`) — the relay loop below depends on a PR
URL, durable verdict comments, and observable PR state. No forge ⇒ the change goes by the normal
human-relayed plain-diff review (CONTRACT §Forge adapter), not by this profile.

1. **Handoff.** Write ONE disposable markdown file: context, constraints (files it may touch, files
   it must NOT touch), deliverables, done-when, "commit locally, do NOT push". Create a git worktree
   for the implementer; state its absolute path and forbid touching the pane's own cwd.
2. **Dispatch.** send Pi: `Read <handoff path> in full and implement it completely. Work ONLY in
   <worktree>. Commit locally as you go; do not push.` Then watch.
3. **Verify (acceptance, not review).** On idle: rerun every test yourself, `git diff --stat` against
   the base, check no forbidden file moved. Broken → send a correction handoff (this is acceptance
   rejection, not review). Green → push, open the PR via the CONTRACT forge adapter (github → `gh pr
   create`; gitee → `gitee-cli`), with an honest authorship note.
4. **Review dispatch.** send Codex:
   `$pipeline-review meta-PR <pr-url> — toolchain meta-PR, no .pipeline state. base=<b> head=<sha>.
   git fetch origin first. <two or three review axes specific to the change>. Verdict as a PR
   comment ONLY — do not type it into any other pane (the coordinator watches the PR; a pane
   write-back is redundant and is often blocked by the unsent-text guard anyway). Arm the GO-gate;
   merge ONLY on a direct human token in this session.`
   **Immediately after dispatching, arm your own verdict watcher** — a background loop polling the
   PR for the reviewer's comment (filter by author; forge bots comment too). Never rely on an
   operator relay or a reviewer pane write-back to wake you: git/the forge is the bus, panes are
   command transport.
5. **Relay loop** (the heart of the flow):
   - Verdict = changes requested → save it VERBATIM to a file; write a fix handoff for Pi: the
     verdict file path + per-finding evidence requirements + the standing constraints. Evidence is
     TYPED: a behavioral defect needs a regression test that FAILS on the reviewed head <sha>
     (proven in a temp worktree); a documentation/contract defect needs deterministic old-head
     evidence instead (the contradictory text quoted at the old head, resolved in the new diff) —
     never a hollow test manufactured to satisfy a blanket rule. Dispatch, watch, VERIFY (rerun
     everything; re-prove each claimed evidence item yourself), push.
   - Re-dispatch: `re-review <pr-url> — findings fixed; new head <sha>. Review ONLY the delta
     <old>..<new>. <finding→fix→evidence mapping>. Verdict as PR comment ONLY (no pane
     write-back); GO-gate; direct human token only.` Re-arm your verdict watcher after every
     re-dispatch.
   - Three rounds without convergence → stop, hand the human the verdict trail (hard rule 4).
6. **Merge gate.** Verdict = approved → tell the human: reply `go`/`merge`/`confirm` as the ENTIRE
   message in the Codex pane. **From the moment a GO-gate is armed until the PR merges (or the human
   says otherwise), the coordinator MUST NOT send ANYTHING to the reviewer pane** — no re-review, no
   status ping, nothing: while the gate is armed, every keystroke into that pane is
   indistinguishable from the human token, so the only safe coordinator behavior is silence toward
   that pane (dispatching a NEW review round after a changes-requested verdict is fine — the gate is
   not armed then). Watch the PR state via the forge; on merge, clean up the worktree and report.

   *Accepted limitation (known, deliberate):* transport-level enforcement of human-only token
   provenance does not exist in the current Herdr — a malicious coordinator could type the token.
   The mitigations are this send-freeze rule, the human's own view of the reviewer TUI (a forged
   token is visible in scrollback), and the audit trail; a channel/ACL that mechanically denies the
   coordinator write access to an armed reviewer pane is future work for the transport layer.

## Profile B — pipeline feature flow (product code; quality via the frozen-spec contract)

For a real feature in a target repo, driven by `.pipeline/<feature>/journal.md`. Everything in
CONTRACT.md binds; this profile only adds who types what.

- **Authorization.** Coordinate ONLY a feature whose `control.json` says
  `{"schema_version":1,"mode":"coordinated","merge_gate":"human-direct"}` (created by `pipeline-prd`
  on the operator's explicit request). Absent/human mode → observe only. Malformed, or vanishing
  after dispatches started → stop, tell the human (never treat as a pause).
- **Stage split (three models, unchanged):** the reasoning stages — `prd` (operator-started),
  `arch`, `task`, `hunt` — are CC-role work: run them IN THIS SESSION with their stage skills,
  obeying each stage's write-set. Implementation is dispatched to the Pi pane; review to the Codex
  pane. Coordinating and executing the CC-role stages in one session is the sanctioned
  simplification of the old design's separate CC pane — the model separation is what matters.
  **In-session stages carry the envelope too:** invoke every coordinated stage — including your own
  `arch`/`task`/`hunt` — with all five fields (`repo= branch= feature= expected_seq=
  expected_commit=`) built from ONE fresh journal observation, so the stage's mandatory pre-write
  stale-dispatch guard and control-tuple check run rather than the human-relay path. Cannot
  construct the tuple (unreadable tail, missing control) → stop; never invoke bare.
- **Routing.** After every stage: `git pull --rebase`, read the journal tail's header and its
  `>>> NEXT` line, and follow CONTRACT §Coordinated mode's transition forms. The tail is the ONLY
  authority. A tail that matches no known form → stop and show the human (never force a route).
- **Dispatching a stage to a pane.** Compose the stage command + the envelope, e.g. send Pi:
  `<PI_IMPL_CMD> repo=<pi-clone> branch=<b> feature=<f> expected_seq=<N> expected_commit=<full sha>`
  — the stage's pre-write stale guard (CONTRACT §Coordinated mode) verifies it; the guard refuses
  CONSUMED seqs, which protects observed-complete dispatches — it does NOT make blind redelivery
  safe (see the redelivery rule above). Watch the pane; when it idles, pull and read the tail. An
  idle pane with NO new journal entry = the stage broke its promise → stop, surface it (hard rule 4).
- **Retries and hunt** follow the CONTRACT state machine as written (attempts, `>=3 ⇒ blocked ⇒
  hunt`); you validate the card evidence by READING it, and stop on any disagreement between journal
  and cards.
- **Merge gate**: identical to Profile A — Codex arms, the human types the token in the Codex pane.
- **drive.sh (optional).** For a long multi-card impl stretch the operator MAY start `drive.sh`
  themselves (its own gates apply). While it runs, you only observe the journal; never type into the
  Pi pane a drive.sh span is using.

## Templates (copy, fill, keep to one line for sends)

- **Review axes line** (step 4): name the 2–3 things this specific diff could get wrong — scope
  creep, a weakened rule, an untested path. Generic "please review" wastes the reviewer's round.
- **Fix handoff skeleton**: verdict path · per-finding fix requirement · TYPED old-head evidence per
  finding (behavioral ⇒ a regression test failing on `<old head>`; doc/contract ⇒ the contradictory
  old-head text quoted, resolved in the new diff) · unchanged constraints · "commit locally, do not
  push".
- **Honest attribution**: every commit/PR body names who implemented and who verified (e.g.
  "Implemented by Pi against <handoff>; verified and pushed by the coordinator").

## What this playbook is not

No daemon, no ledger, no state files beyond disposable handoffs, no unattended operation, no
parallel features, no merges by anyone but Codex-with-a-human-token. If the loop needs something
this file does not describe, that is a signal to stop and talk to the human — or to propose a
change to THIS file via the normal meta-PR lane (never edit the installed copy mid-run).
