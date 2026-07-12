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

main() {
  local self_dir skills_dir probe top mode old new changed name src canon tgt
  local src_phys staging backup problems refresh_list done_list diff_err diff_rc n2
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
    # Canonical-layout sweep (README §Canonical multi-runtime layout): runtimes attach
    # by SYMLINKING into ONE shared dir of COPIES (~/.agents/skills); a clone-side run
    # must refresh those copies too (field-failed 2026-07-12: "already latest" while
    # every runtime served the old shim). Override location: PIPELINE_CANON_SKILLS.
    # RUN-ATOMIC: detect (zero mutation) -> stage ALL -> swap ALL (full rollback on any
    # failure, INCLUDING the clone HEAD) -> drop backups (cleanup failure = warn only).
    # Exit contract: rc=0 => install correct; rc=1 => nothing changed / fully rolled back.
    canon="${PIPELINE_CANON_SKILLS:-$HOME/.agents/skills}"
    if { [ -d "$canon/pipeline-update" ] || [ -L "$canon/pipeline-update" ]; } \
       && [ "$(cd "$canon" 2>/dev/null && pwd -P || echo __NO__)" != "$(cd "$top/skills" && pwd -P)" ]; then

      # Janitor: durable recovery from an interrupted previous sweep (crash between
      # old->backup and staging->live leaves the live path missing: restore it).
      for backup in "$canon"/.*.update-backup; do
        [ -e "$backup" ] || continue
        name="$(basename "$backup")"; name="${name#.}"; name="${name%.update-backup}"
        if [ -e "$canon/$name" ] || [ -L "$canon/$name" ]; then
          rm -rf "$backup" && echo "janitor: dropped leftover backup for $name" \
            || echo "WARNING: janitor could not drop leftover backup $backup" >&2
        else
          mv "$backup" "$canon/$name" \
            && echo "janitor: restored $name from an interrupted run" \
            || echo "ERROR: janitor could not restore $name from $backup" >&2
        fi
      done
      rm -rf "$canon"/.*.update-staging 2>/dev/null || true

      # Detect (NO mutation): classify every installed entry.
      refresh_list=""; problems=0
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
        [ "$old" != "$new" ] && git -C "$top" reset --hard "$old" >/dev/null
        echo "ERROR: attachment problems above — NOTHING changed this run (clone rolled back to $old). Fix, then re-run." >&2
        exit 1
      fi

      if [ -z "$refresh_list" ]; then
        echo "canonical copies in $canon already latest"
      else
        # Phase A: stage EVERYTHING before touching anything live.
        for name in $refresh_list; do
          if ! cp -r "$top/skills/$name" "$canon/.$name.update-staging"; then
            for n2 in $refresh_list; do rm -rf "$canon/.$n2.update-staging"; done
            [ "$old" != "$new" ] && git -C "$top" reset --hard "$old" >/dev/null
            echo "ERROR: staging failed for $name — canonical copies untouched; clone rolled back to $old." >&2
            exit 1
          fi
        done
        # Phase B: swap all; ANY failure restores every completed swap + the clone.
        done_list=""
        for name in $refresh_list; do
          staging="$canon/.$name.update-staging"; backup="$canon/.$name.update-backup"
          if mv "$canon/$name" "$backup" && mv "$staging" "$canon/$name"; then
            done_list="$done_list $name"
          else
            { [ -e "$canon/$name" ] || [ -L "$canon/$name" ]; } || { [ -e "$backup" ] && mv "$backup" "$canon/$name"; }
            for n2 in $done_list; do
              rm -rf "$canon/$n2"
              mv "$canon/.$n2.update-backup" "$canon/$n2" \
                || echo "ERROR: rollback failed for $n2 — previous copy preserved at $canon/.$n2.update-backup (janitor restores it next run)" >&2
            done
            for n2 in $refresh_list; do rm -rf "$canon/.$n2.update-staging"; done
            [ "$old" != "$new" ] && git -C "$top" reset --hard "$old" >/dev/null
            echo "ERROR: swap failed at $name — all canonical copies rolled back; clone rolled back to $old." >&2
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
    # Mode 1 (cp'd copies) — atomic: clone to a temp dir FIRST, copy only on success.
    # A failed clone exits here (set -e) and leaves the installed shims untouched.
    TMP="$(mktemp -d)"
    trap 'rm -rf "$TMP"' EXIT
    git clone --quiet --depth 1 "$REPO_URL" "$TMP"
    new="$(git -C "$TMP" rev-parse HEAD)"
    echo "mode=1 (copies in $skills_dir)"
    changed=0
    for src in "$TMP"/skills/pipeline-*/; do
      name="$(basename "$src")"
      diff -rq "$src" "$skills_dir/$name" >/dev/null 2>&1 && continue
      changed=1
      echo "updated: $name"
    done
    if [ "$changed" = 0 ]; then
      echo "already latest ($new)"
    else
      cp -r "$TMP"/skills/pipeline-* "$skills_dir/"
      echo "now at $new; latest upstream commits:"
      git -C "$TMP" log --oneline -10
    fi
  fi

  echo "scope: refreshed runtime-shared pipeline-* shims only; no target repo .pipeline/ state touched."
}

# Wrapper so bash parses the whole file before executing: Mode 1's cp may overwrite this
# very script while it runs (self-update); a fully-parsed main() makes that safe.
main "$@"
