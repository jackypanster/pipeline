#!/usr/bin/env bash
# pipeline-update — the deterministic core: locate install, detect mode, refresh, report.
# Destructive ops (reset --hard / cp) run HERE under set -euo pipefail, never as LLM prose.
# Usage: update.sh [skills-dir]   # optional override: refresh a different physical copy
#                                 # (arg used verbatim as the shim dir — do NOT dirname it)
set -euo pipefail

REPO_URL="https://github.com/jackypanster/pipeline.git"
# Pin the FULL GitHub repo identity — host AND owner/repo. A looser pattern also matches
# sibling repos (pipeline-dashboard, pipeline-driver), nested paths that merely end in
# /jackypanster/pipeline (git.example.com/foo/jackypanster/pipeline.git), and spoofed hosts
# (github.com.evil.com) — and the next operation is a reset --hard on that repo.
REMOTE_RE='(^|[@/])github\.com[:/]jackypanster/pipeline(\.git)?/?$'

TMP=""   # global: the EXIT trap fires after main() returns, so it must not be a local
TXN=""   # global (same reason): Mode 1's private same-filesystem transaction dir
TXN_KEEP=""   # global: set when a Mode-1 rollback is INCOMPLETE, so the EXIT trap RETAINS $TXN's
              # private backups (deleting them would destroy the only recovery copy) instead of dropping it
M1_ROLLED=""  # global: a Mode-1 rollback sets this to 0 the moment any step is unproven; the caller
              # then reports INCOMPLETE + retains backups instead of falsely claiming UNTOUCHED
HELD_LOCKS=()                                # lock dirs THIS process acquired (EXIT-released);
                                             # an ARRAY: paths may contain spaces
LOCK_TOKEN="$$.$(date +%s).$RANDOM$RANDOM"   # unique ownership token: pid alone is reusable

# Interprocess mutexes for the Mode-2 transaction (per-clone lock + canonical lock).
# mkdir is the atomic arbiter. Each lock records the holder's pid (liveness probe),
# its process START TIME (generation — unmasks pid REUSE), and this run's unique
# token (ownership proof): deletion and release are only ever performed against
# VERIFIED state, never against a bare path — a path-only rm can destroy a lock
# that changed hands between probe and removal (TOCTOU).
drop_lock_dir() {  # remove a lock/reap dir this process created and VERIFY it is
                   # gone; chmod first — a dir born under a restrictive umask has no
                   # search bit and defeats a plain rm -rf (silently, reproduced).
  chmod u+rwx "$1" 2>/dev/null || true
  rm -rf "$1" 2>/dev/null || true
  ! { [ -e "$1" ] || [ -L "$1" ]; }
}
proc_gen() {   # a process's start time under a PINNED rendering environment.
               # ps lstart is TZ/locale-dependent prose: the same live process
               # renders differently under UTC vs Asia/Hong_Kong, so two observers
               # comparing raw renderings can "prove" a false generation mismatch
               # (reproduced) — pin LC_ALL/TZ so every observer produces the same
               # string. Empty output = "could not observe", NEVER "dead".
  LC_ALL=C TZ=UTC ps -o lstart= -p "$1" 2>/dev/null || true
}
try_lock() {   # atomically claim $1, stamp ownership, and VERIFY the stamp landed
  local path="$1" mygen
  mygen="$(proc_gen "$$")"
  mkdir "$path" 2>/dev/null || return 1
  { echo "$$" > "$path/pid" && echo "$LOCK_TOKEN" > "$path/token" \
      && printf '%s\n' "$mygen" > "$path/gen2"; } 2>/dev/null || true
  # gen2 (pinned rendering), not r7's gen (observer-local rendering): old-format
  # locks fall back to pid-only liveness instead of cross-version false mismatches.
  if [ -z "$mygen" ] \
     || [ "$(cat "$path/pid" 2>/dev/null)" != "$$" ] \
     || [ "$(cat "$path/token" 2>/dev/null)" != "$LOCK_TOKEN" ] \
     || [ "$(cat "$path/gen2" 2>/dev/null)" != "$mygen" ]; then
    # An UNSTAMPED lock could never be auto-reclaimed (an empty pid reads as live);
    # drop the fresh dir instead — provably ours: we just created it, nobody else
    # can have claimed it. If even that fails, say so: it needs a manual rm.
    if drop_lock_dir "$path"; then
      echo "ERROR: could not stamp lock $path (pid/token write failed) — not holding it" >&2
    else
      echo "ERROR: could not stamp lock $path AND could not remove it — remove it by hand before the next run" >&2
    fi
    return 1
  fi
  HELD_LOCKS+=("$path")
  return 0
}
holder_alive() {   # $1 = lock dir. Alive unless PROVEN dead — every unprovable
                   # state fails CLOSED (busy), because "dead" authorizes deletion:
                   # empty/mid-stamp pid => alive; kill -0 failure => dead (positive
                   # proof); no recorded generation => alive (pid-only); ps failing
                   # or returning nothing NOW => alive (cannot observe != gone);
                   # only a real, pinned-rendering start time that DIFFERS proves
                   # pid reuse => dead.
  local pid gen now
  pid="$(cat "$1/pid" 2>/dev/null || true)"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null || return 1
  gen="$(cat "$1/gen2" 2>/dev/null || true)"
  [ -n "$gen" ] || return 0
  now="$(proc_gen "$pid")"
  [ -n "$now" ] || return 0
  [ "$now" = "$gen" ]
}
acquire_lock() {
  local path="$1" reap
  if try_lock "$path"; then return 0; fi
  if holder_alive "$path"; then return 1; fi
  # Dead holder: reclaim is serialized by its own atomic mutex, and the holder is
  # RE-probed inside it — two contenders may both see the same dead pid, but only
  # the reaper may delete, so the loser can never rm the winner's fresh lock. A
  # busy (or crashed) reaper fails this run safely; a crashed reaper's .reap dir
  # (dead pid inside) is the one artifact an operator removes by hand.
  reap="$path.reap"
  mkdir "$reap" 2>/dev/null || return 1
  echo "$$" > "$reap/pid" 2>/dev/null || true
  if [ -e "$path" ] || [ -L "$path" ]; then
    if holder_alive "$path"; then
      drop_reap "$reap" "$path"            # someone re-acquired it alive meanwhile
      return 1
    fi
    rm -rf "$path" 2>/dev/null || true     # verified dead UNDER the reclaim mutex
  fi
  if try_lock "$path"; then
    drop_reap "$reap" "$path"
    return 0
  fi
  drop_reap "$reap" "$path"                # a normal acquirer won the mkdir race
  return 1
}
drop_reap() {  # $1 = reap dir, $2 = its lock. A reap residue silently BLOCKS all
               # future automatic stale-lock recovery for that lock — never swallow
               # the failure: name the residue and the consequence.
  drop_lock_dir "$1" \
    || echo "WARNING: could not remove reclaim mutex $1 — automatic stale-lock recovery for $2 is blocked until it is removed by hand" >&2
}
release_locks() {
  local l
  for l in ${HELD_LOCKS[@]+"${HELD_LOCKS[@]}"}; do   # set -u-safe empty-array expansion
    if [ "$(cat "$l/token" 2>/dev/null)" = "$LOCK_TOKEN" ]; then
      drop_lock_dir "$l" \
        || echo "WARNING: could not release lock $l — a dead holder's lock is reclaimed next run" >&2
    fi   # token mismatch: the lock changed hands (we were presumed dead) — NOT ours to remove
  done
  HELD_LOCKS=()
}

# _m1_rollback <done_list> — restore every completed Mode-1 swap from its PRIVATE backup
# ($TXN/.bak.<name>): a replaced entry is put back, a newly-added entry is removed. Reads $TXN
# (global) and main's $skills_dir via dynamic scoping. Every caller is an error path, so each step
# is guarded (never trips set -e) AND VERIFIED: a step whose result cannot be proven sets M1_ROLLED=0
# and names the retained artifact, so the caller reports INCOMPLETE instead of a false UNTOUCHED.
_m1_rollback() {
  local n
  for n in ${1:-}; do
    # ALWAYS clear the live slot first and PROVE it is empty — a failed rm would let the restore `mv`
    # NEST the backup inside the leftover dir, and a plain [ -e ] check would then read as "restored"
    # while the slot holds the wrong tree. If the slot cannot be cleared, fail closed + retain.
    rm -rf "$skills_dir/$n" 2>/dev/null || true
    if [ -e "$skills_dir/$n" ] || [ -L "$skills_dir/$n" ]; then
      M1_ROLLED=0
      if [ -e "$TXN/.bak.$n" ] || [ -L "$TXN/.bak.$n" ]; then
        echo "ERROR: rollback for $n could not clear $skills_dir/$n — previous copy RETAINED at $TXN/.bak.$n (restore by hand)" >&2
      else
        echo "ERROR: rollback for $n could not remove the newly-added $skills_dir/$n — remove it by hand" >&2
      fi
      continue
    fi
    # Slot is proven empty. A replaced entry restores its backup into it; a newly-added entry is done.
    if [ -e "$TXN/.bak.$n" ] || [ -L "$TXN/.bak.$n" ]; then
      if ! mv "$TXN/.bak.$n" "$skills_dir/$n" 2>/dev/null \
         || ! { [ -e "$skills_dir/$n" ] || [ -L "$skills_dir/$n" ]; }; then
        M1_ROLLED=0
        echo "ERROR: rollback failed for $n — previous copy RETAINED at $TXN/.bak.$n (restore by hand)" >&2
      fi
    fi
  done
}

# _m1_finish_fail — after a rollback, print the HONEST verdict and exit 1. If every rollback step was
# proven (M1_ROLLED=1) the install is UNTOUCHED and the EXIT trap drops $TXN; otherwise recovery is
# INCOMPLETE, so RETAIN $TXN's private backups (TXN_KEEP) and say so — never claim UNTOUCHED on faith.
_m1_finish_fail() {
  if [ "${M1_ROLLED:-0}" = 1 ]; then
    echo "      → all completed swaps rolled back; install in $skills_dir left UNTOUCHED." >&2
  else
    TXN_KEEP=1
    echo "      → rollback INCOMPLETE; private backups RETAINED in $TXN (restore by hand). Do NOT assume UNTOUCHED." >&2
  fi
  exit 1
}

# Drop every staging artifact of the current refresh_list and NAME each one that
# survives — the rc=1 contract promises the output names exactly what a failed
# recovery step left behind, so `rm || true` alone is not enough. Returns 1 when
# any residue remains (the janitor retries it next run).
drop_all_staging() {
  local n s residue=0
  for n in $refresh_list; do
    s="$canon/.$n.update-staging"
    if [ -e "$s" ] || [ -L "$s" ]; then
      rm -rf "$s" || true
      if [ -e "$s" ] || [ -L "$s" ]; then
        residue=1
        echo "ERROR: could not drop staging $s — janitor retries next run" >&2
      fi
    fi
  done
  return $residue
}

# Roll the Mode-2 clone back to the pre-update HEAD and VERIFY it landed; return 0
# only when HEAD is PROVEN back at $old (reads main()'s old/new/top via bash dynamic
# scoping). Every caller is an error path — this must never itself trip set -e, so
# the reset is guarded and the verdict comes from re-reading HEAD, not from rc.
rollback_clone() {
  [ "$old" = "$new" ] && return 0
  git -C "$top" reset --hard "$old" >/dev/null || true
  [ "$(git -C "$top" rev-parse HEAD 2>/dev/null)" = "$old" ]
}

main() {
  local self_dir skills_dir probe top mode old new changed name src canon tgt
  local src_phys staging backup problems refresh_list done_list diff_err diff_rc n2
  local sweep rolled leftover lock_path clone_lock attached skipped dst e ok
  self_dir="$(cd "$(dirname "$0")/.." && pwd)"   # …/skills/pipeline-update
  if [ $# -ge 1 ]; then
    skills_dir="$1"
  else
    skills_dir="$(dirname "$self_dir")"
  fi
  [ -d "$skills_dir" ] || { echo "ERROR: skills dir not found: $skills_dir" >&2; exit 1; }
  probe="$skills_dir/pipeline-update"

  # Mode detection (README §Install ships two). An empty $top MUST fall to Mode 1:
  # git -C "" silently runs in the CURRENT directory (often a target project or the clone
  # itself) and would falsely report the pipeline remote.
  mode=1
  top="$(git -C "$probe" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$top" ] && git -C "$top" remote get-url origin 2>/dev/null | grep -qE "$REMOTE_RE"; then
    mode=2   # external_dirs: the runtime loads skills straight from the clone
  fi

  if [ "$mode" = 2 ]; then
    # Canonical-layout sweep (README §Canonical multi-runtime layout): runtimes attach
    # by SYMLINKING into ONE shared dir of COPIES (~/.agents/skills); a clone-side run
    # must refresh those copies too (field-failed 2026-07-12: "already latest" while
    # every runtime served the old shim). Override location: PIPELINE_CANON_SKILLS.
    # RUN-ATOMIC: janitor (finish any interrupted sweep FIRST) -> detect (zero
    # mutation) -> stage ALL -> swap ALL (full rollback on any failure, INCLUDING the
    # clone HEAD; every rollback step guarded + verified) -> drop backups (cleanup
    # failure = warn only). Recovery artifacts live ONLY in the .pipeline-*.update-*
    # namespace; the janitor never touches other names. Exit contract: rc=0 => install
    # correct; rc=1 => not updated — fully rolled back, or the output names exactly
    # what a failed recovery step left for the janitor to finish next run.
    canon="${PIPELINE_CANON_SKILLS:-$HOME/.agents/skills}"
    # Gate on ANY installed canonical pipeline-* entry — a partial install without
    # pipeline-update still holds stale copies that must be swept — or on recovery
    # artifacts (the interrupted entry may be the ONLY installed one, its live path
    # missing; the janitor must still be reachable to restore it). Pure reads only.
    sweep=0
    if [ "$(cd "$canon" 2>/dev/null && pwd -P || echo __NO__)" != "$(cd "$top/skills" && pwd -P)" ]; then
      for leftover in "$canon"/pipeline-* "$canon"/.pipeline-*.update-backup "$canon"/.pipeline-*.update-staging; do
        if [ -e "$leftover" ] || [ -L "$leftover" ]; then sweep=1; break; fi
      done
    fi
    # Single-flight: the janitor cannot tell a RUNNING updater's in-flight backup/
    # staging from an interrupted run's leftovers — existence checks alone are TOCTOU
    # — so every mutation runs under interprocess locks taken BEFORE it, in a FIXED
    # order: (1) the per-clone lock — fetch/reset ALWAYS mutate the shared clone,
    # even with an empty canon, canon==clone/skills, or per-run PIPELINE_CANON_SKILLS
    # values, so it guards every Mode-2 run; (2) the canonical lock — distinct clones
    # can target the same canon. A live holder aborts this run with nothing changed;
    # a dead holder's lock is reclaimed (see acquire_lock).
    trap release_locks EXIT
    clone_lock="$(git -C "$top" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    [ -n "$clone_lock" ] || clone_lock="$top/.git"
    clone_lock="$clone_lock/pipeline-update.lock"
    if ! acquire_lock "$clone_lock"; then
      echo "ERROR: another pipeline-update run is active on this clone (lock: $clone_lock, holder pid: $(cat "$clone_lock/pid" 2>/dev/null || echo unknown)) — nothing changed this run; re-run after it finishes. A dead holder's lock is reclaimed automatically; only a leftover $clone_lock.reap with a dead pid needs removing by hand." >&2
      exit 1
    fi
    if [ "$sweep" = 1 ]; then
      lock_path="$canon/.pipeline-update.lock"
      if ! acquire_lock "$lock_path"; then
        echo "ERROR: another pipeline-update run is active on $canon (lock: $lock_path, holder pid: $(cat "$lock_path/pid" 2>/dev/null || echo unknown)) — nothing changed this run; re-run after it finishes. A dead holder's lock is reclaimed automatically; only a leftover $lock_path.reap with a dead pid needs removing by hand." >&2
        exit 1
      fi
    fi
    # `old` is read UNDER the clone lock: a pre-lock read can go stale while another
    # updater advances the shared clone, and a later failure rollback would then
    # reset the clone BEHIND that updater's legitimate new HEAD.
    old="$(git -C "$top" rev-parse HEAD)"
    # Bare `fetch origin`, not `fetch origin main`: reliably updates the origin/main
    # tracking ref before the reset (the sanctioned read-only-consumer refresh,
    # CONTRACT §Self-improvement).
    git -C "$top" fetch origin
    git -C "$top" reset --hard origin/main
    new="$(git -C "$top" rev-parse HEAD)"
    echo "mode=2 (runtime loads from clone at $top)"
    if [ "$old" = "$new" ]; then
      echo "already latest ($new)"
    else
      echo "updated $old -> $new; commits + files moved:"
      git -C "$top" log --oneline "$old..$new"
      git -C "$top" diff --name-only "$old" "$new" -- skills CONTRACT.md README.md
    fi
    if [ "$sweep" = 1 ]; then

      # Janitor: durable recovery from an interrupted previous sweep (crash between
      # old->backup and staging->live leaves the live path missing: restore it).
      # Any janitor failure is unresolved transaction state and ABORTS before mutation
      # (via the problems gate below): proceeding would compound it — an undroppable
      # backup would make the swap's mv NEST the live dir inside it, stale staging
      # would make Phase A's cp -r nest the source.
      problems=0
      for leftover in "$canon"/.pipeline-*.update-backup; do
        if ! { [ -e "$leftover" ] || [ -L "$leftover" ]; }; then continue; fi
        name="$(basename "$leftover")"; name="${name#.}"; name="${name%.update-backup}"
        if [ -e "$canon/$name" ] || [ -L "$canon/$name" ]; then
          if rm -rf "$leftover"; then
            echo "janitor: dropped leftover backup for $name"
          else
            echo "ERROR: janitor could not drop leftover backup $leftover" >&2; problems=1
          fi
        else
          if mv "$leftover" "$canon/$name" && { [ -e "$canon/$name" ] || [ -L "$canon/$name" ]; }; then
            echo "janitor: restored $name from an interrupted run"
          else
            echo "ERROR: janitor could not restore $name from $leftover" >&2; problems=1
          fi
        fi
      done
      for leftover in "$canon"/.pipeline-*.update-staging; do
        if ! { [ -e "$leftover" ] || [ -L "$leftover" ]; }; then continue; fi
        if ! rm -rf "$leftover"; then
          echo "ERROR: janitor could not drop stale staging $leftover" >&2; problems=1
        fi
      done

      # Detect (NO mutation): classify every installed entry.
      refresh_list=""
      for src in "$top"/skills/pipeline-*/; do
        name="$(basename "$src")"
        # -L counts as installed: a DANGLING symlink must reach the diagnostic below,
        # never be silently skipped as absent.
        [ -e "$canon/$name" ] || [ -L "$canon/$name" ] || continue
        src_phys="$(cd "$src" && pwd -P)"
        if [ -L "$canon/$name" ]; then
          tgt="$(cd "$canon/$name" 2>/dev/null && pwd -P || true)"
          [ "$tgt" = "$src_phys" ] && continue
          echo "WARNING: $canon/$name is a symlink bound to '${tgt:-<dangling>}', not this skill — fix the attachment" >&2
          problems=1; continue
        fi
        # Comparison trust: ANY stderr diagnostic voids the comparison — BSD diff can
        # print "Permission denied" yet still exit 0 (reproduced), so rc alone lies.
        diff_err="$(diff -rq "$src" "$canon/$name" 2>&1 >/dev/null)" && diff_rc=0 || diff_rc=$?
        if [ -n "$diff_err" ] || [ "$diff_rc" -ge 2 ]; then
          echo "WARNING: cannot trust the comparison for $canon/$name (rc=$diff_rc${diff_err:+, diagnostics on stderr}) — fix, then re-run" >&2
          problems=1; continue
        fi
        [ "$diff_rc" = 1 ] && refresh_list="$refresh_list $name"
      done

      if [ "$problems" != 0 ]; then
        if rollback_clone; then
          echo "ERROR: recovery/attachment problems above — canonical copies NOT updated this run (clone rolled back to $old). Fix, then re-run." >&2
        else
          echo "ERROR: recovery/attachment problems above — canonical copies NOT updated, and the clone FAILED to roll back to $old (see above). Fix, then re-run." >&2
        fi
        exit 1
      fi

      if [ -z "$refresh_list" ]; then
        echo "canonical copies in $canon already latest"
      else
        # Phase A: stage EVERYTHING before touching anything live. cp -r into a path
        # that already exists NESTS the source inside it (and Phase B would then swear
        # that malformed tree in as live) — staging must be PROVEN absent before copy.
        for name in $refresh_list; do
          staging="$canon/.$name.update-staging"
          if [ -e "$staging" ] || [ -L "$staging" ] || ! cp -r "$top/skills/$name" "$staging"; then
            rolled=1
            drop_all_staging || rolled=0
            if ! rollback_clone; then
              rolled=0
              echo "ERROR: clone FAILED to roll back to $old" >&2
            fi
            if [ "$rolled" = 1 ]; then
              echo "ERROR: staging failed for $name — canonical copies untouched; clone rolled back to $old." >&2
            else
              echo "ERROR: staging failed for $name — live canonical copies untouched, but recovery is INCOMPLETE (residue named above); re-run pipeline-update so the janitor can finish." >&2
            fi
            exit 1
          fi
        done
        # Phase B: swap all; ANY failure restores every completed swap + the clone.
        # A pre-existing backup path would make mv NEST live inside it — refuse the
        # swap instead. Every rollback step is guarded (a failure must run the REST
        # of the recovery, not trip set -e) and verified; the closing message states
        # only what was proven.
        done_list=""
        for name in $refresh_list; do
          staging="$canon/.$name.update-staging"; backup="$canon/.$name.update-backup"
          if [ ! -e "$backup" ] && [ ! -L "$backup" ] \
             && mv "$canon/$name" "$backup" && mv "$staging" "$canon/$name"; then
            done_list="$done_list $name"
          else
            rolled=1
            # This entry: live is gone only when old->backup landed but staging->live
            # did not — put the backup straight back.
            if ! { [ -e "$canon/$name" ] || [ -L "$canon/$name" ]; }; then
              if ! mv "$backup" "$canon/$name" \
                 || ! { [ -e "$canon/$name" ] || [ -L "$canon/$name" ]; }; then
                rolled=0
                echo "ERROR: could not restore $name — previous copy is at $backup (janitor restores it next run)" >&2
              fi
            fi
            for n2 in $done_list; do
              if ! rm -rf "$canon/$n2" || ! mv "$canon/.$n2.update-backup" "$canon/$n2" \
                 || ! { [ -e "$canon/$n2" ] || [ -L "$canon/$n2" ]; }; then
                rolled=0
                echo "ERROR: rollback failed for $n2 — previous copy preserved at $canon/.$n2.update-backup (janitor restores it next run)" >&2
              fi
            done
            drop_all_staging || rolled=0
            if ! rollback_clone; then
              rolled=0
              echo "ERROR: clone FAILED to roll back to $old" >&2
            fi
            if [ "$rolled" = 1 ]; then
              echo "ERROR: swap failed at $name — all canonical copies rolled back; clone rolled back to $old." >&2
            else
              echo "ERROR: swap failed at $name — rollback INCOMPLETE (details above); re-run pipeline-update so the janitor can finish recovery." >&2
            fi
            exit 1
          fi
        done
        # Phase C: drop backups. The install is already correct — a cleanup failure
        # only warns (truthfully) and the janitor retries next run; rc stays 0.
        for name in $done_list; do
          rm -rf "$canon/.$name.update-backup" \
            || echo "WARNING: could not drop the backup for $name — install itself is correct; janitor retries next run" >&2
          echo "refreshed canonical copy: $canon/$name"
        done
      fi
    fi
  else
    # Mode 1 (cp'd copies) — TRANSACTIONAL: stage every refresh, then swap all, rolling back on ANY
    # failure so a nonzero exit leaves the install UNTOUCHED (SKILL.md hard rule: "Non-zero exit ⇒ the
    # install is untouched"). Only the temp-clone precedes staging (clone FIRST; a failed clone exits
    # here via set -e, install untouched). A SYMLINK entry is a canonical attachment (pi-style
    # `~/.pi/agent/skills/<name> -> ~/.agents/skills/<name>`): LEFT UNTOUCHED + reported — cp-ing over
    # it fails ("Not a directory") / clobbers the attachment; refresh its canonical target instead. In
    # a symlink-attached dir an ABSENT skill is REPORTED, not materialized as a stray copy; a copy dir
    # still materializes absent skills (how a new upstream skill reaches a copy runtime).
    TMP="$(mktemp -d)"
    # EXIT cleanup: clone temp always; the private transaction dir ONLY when recovery was complete
    # (TXN_KEEP unset) — an INCOMPLETE rollback keeps $TXN so its private backups survive for manual
    # recovery. Then release the single-flight lock. release_locks is self-guarded + token-checked.
    trap 'rm -rf "$TMP" 2>/dev/null || true; [ -n "${TXN_KEEP:-}" ] || rm -rf ${TXN:+"$TXN"} 2>/dev/null || true; release_locks' EXIT
    # Single-flight lock on THIS install dir (reuse the shared lock helpers) — one refresh at a time,
    # so the swap below can never race a concurrent run; a dead holder is reclaimed automatically. This
    # is also where a non-writable $skills_dir surfaces (the lock mkdir fails).
    lock_path="$skills_dir/.pipeline-update.lock"
    if ! acquire_lock "$lock_path"; then
      echo "ERROR: could not acquire the single-flight lock $lock_path — another pipeline-update run is active on $skills_dir (holder pid: $(cat "$lock_path/pid" 2>/dev/null || echo unknown)), or the dir is not writable. Nothing changed; re-run after it finishes. A dead holder's lock is reclaimed automatically; only a leftover $lock_path.reap with a dead pid needs removing by hand." >&2
      exit 1
    fi
    git clone --quiet --depth 1 "$REPO_URL" "$TMP"
    new="$(git -C "$TMP" rev-parse HEAD)"
    echo "mode=1 (copies in $skills_dir)"
    # Attachment style: a dir holding ANY pipeline-* symlink is symlink-attached (don't materialize
    # absent skills as copies there). A no-match glob is literal and fails the -L test → attached=0.
    attached=0
    for e in "$skills_dir"/pipeline-*; do
      [ -L "$e" ] && { attached=1; break; }
    done
    # Classify — NO mutation. refresh_list = copies to (re)write; skipped counts attachments/absent.
    refresh_list=""; skipped=0
    for src in "$TMP"/skills/pipeline-*/; do
      name="$(basename "$src")"
      dst="$skills_dir/$name"
      if [ -L "$dst" ]; then
        echo "skipped: $name (symlink attachment -> $(readlink "$dst"); refresh its canonical copy instead)"
        skipped=$((skipped + 1)); continue
      fi
      if [ ! -e "$dst" ] && [ "$attached" = 1 ]; then
        echo "skipped: $name (not installed in this symlink-attached dir — run pipeline-install to add it)"
        skipped=$((skipped + 1)); continue
      fi
      diff -rq "$src" "$dst" >/dev/null 2>&1 && continue
      refresh_list="$refresh_list $name"
    done
    if [ -z "$refresh_list" ]; then
      # Only claim "already latest" when entries were actually COMPARED. If every entry was skipped
      # (attachments / absent-in-attached-dir), nothing here was verified against $new — say that,
      # never the blanket latest-success claim.
      if [ "$skipped" -gt 0 ]; then
        echo "nothing to refresh in $skills_dir ($skipped skipped: attachment/absent; not verified against $new)"
      else
        echo "already latest ($new)"
      fi
    else
      # PRIVATE, same-filesystem transaction dir. mktemp gives an unpredictable, 0700-mode name INSIDE
      # $skills_dir, so the staging AND backup paths cannot be pre-created or symlinked by a racing
      # process — the fixed .name.update-* paths were a check-then-mv TOCTOU (a planted backup symlink
      # was followed by mv, moving a live skill out of $skills_dir). Same filesystem ⇒ mv is an atomic
      # rename, not a cross-device copy. Sweep stale txn dirs from an interrupted run first (safe — we
      # hold the lock, so no live txn of another run exists).
      rm -rf "$skills_dir"/.pipeline-update.txn.* 2>/dev/null || true
      TXN="$(mktemp -d "$skills_dir/.pipeline-update.txn.XXXXXXXX")" \
        || { echo "ERROR: could not create a private transaction dir in $skills_dir — nothing changed." >&2; exit 1; }
      # Phase A — stage every refresh into the private dir. ANY failure ⇒ exit nonzero; the EXIT trap
      # drops the whole txn dir, so the LIVE copies are untouched.
      for name in $refresh_list; do
        if ! cp -r "$TMP/skills/$name" "$TXN/$name"; then
          echo "ERROR: staging failed for $name — live copies in $skills_dir untouched. Fix the cause and re-run pipeline-update." >&2
          exit 1
        fi
      done
      # Phase B — swap all. Backups go INTO the private txn dir (unpredictable), so no swap step targets
      # a predictable path a symlink could hijack, and the live slot is checked NON-symlink before the
      # move. `mv` is NOT an atomic no-follow rename on macOS, so the publish is VERIFIED after the fact:
      # if the destination is not the real staged directory (a symlink the non-atomic mv may have
      # followed), the raced symlink is removed WITHOUT following it, this entry's backup is restored,
      # and the run rolls back. RESIDUAL (documented, lock-mitigated): a non-cooperating writer that does
      # not take this lock could still win the sub-millisecond publish window — such a writer already has
      # write access to $skills_dir and could corrupt it directly; the post-verify guarantees this run
      # never prints success or drops the backup on such a race. Every failure rolls back with a VERIFIED,
      # honest UNTOUCHED-or-INCOMPLETE verdict (never a false UNTOUCHED); backups are retained if recovery
      # cannot be proven.
      done_list=""
      for name in $refresh_list; do
        dst="$skills_dir/$name"; backup="$TXN/.bak.$name"
        if [ -L "$dst" ]; then                        # a symlink raced into a to-refresh slot after classify
          echo "ERROR: $name became a symlink mid-run ($(readlink "$dst" 2>/dev/null)) — refused, not followed." >&2
          M1_ROLLED=1; _m1_rollback "$done_list"; _m1_finish_fail
        fi
        if [ -e "$dst" ]; then
          if ! mv "$dst" "$backup"; then
            echo "ERROR: could not back up $name before its swap." >&2
            M1_ROLLED=1; _m1_rollback "$done_list"; _m1_finish_fail
          fi
        fi
        # Publish, then VERIFY: $dst must now be the real staged dir, not a raced symlink mv followed.
        if ! mv "$TXN/$name" "$dst" || [ -L "$dst" ] || [ ! -d "$dst" ]; then
          [ -L "$dst" ] && { rm -f "$dst" 2>/dev/null || true; }             # drop a raced symlink (no-follow)
          [ -e "$backup" ] && { mv "$backup" "$dst" 2>/dev/null || true; }   # restore THIS entry's backup
          echo "ERROR: could not safely install $name — destination was not the expected directory." >&2
          M1_ROLLED=1; _m1_rollback "$done_list"; _m1_finish_fail
        fi
        done_list="$done_list $name"; echo "updated: $name"
      done
      # Success: the EXIT trap drops $TXN (which now holds only the .bak.* backups) — the install is
      # already correct, so backup cleanup is a trap concern, never a step that can fail the run.
      echo "now at $new; latest upstream commits:"
      git -C "$TMP" log --oneline -10
    fi
  fi

  echo "scope: refreshed runtime-shared pipeline-* shims only; no target repo .pipeline/ state touched."
}

# Self-update guard: Mode 1's cp may overwrite this very script while it runs. Bash reads
# scripts incrementally — after main() returns it would fetch the next command from the (now
# replaced) file at the old byte offset and execute a garbage fragment. `; exit` on the same
# physical line is parsed together with the call, so bash never reads past it; bare `exit`
# preserves main's exit status.
main "$@"; exit
