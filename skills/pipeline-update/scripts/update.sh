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
  local src_phys staging backup problems sweep_problems=0
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
    # Canonical-layout sweep (README §Canonical multi-runtime layout): runtimes commonly
    # attach by SYMLINKING into ONE shared physical dir of COPIES (~/.agents/skills).
    # Running this script from the clone self-detects mode=2 and refreshes only the
    # clone — field-failed 2026-07-12: "already latest" while every runtime attachment
    # still served the old shim. So after a mode-2 refresh, also refresh stale canonical
    # COPIES. Override the location with PIPELINE_CANON_SKILLS.
    canon="${PIPELINE_CANON_SKILLS:-$HOME/.agents/skills}"
    if [ -d "$canon/pipeline-update" ] \
       && [ "$(cd "$canon" && pwd -P)" != "$(cd "$top/skills" && pwd -P)" ]; then
      changed=0; problems=0
      for src in "$top"/skills/pipeline-*/; do
        name="$(basename "$src")"
        [ -e "$canon/$name" ] || continue                     # sweep only what is installed
        src_phys="$(cd "$src" && pwd -P)"
        tgt="$(cd "$canon/$name" 2>/dev/null && pwd -P || true)"
        if [ -L "$canon/$name" ]; then
          # A symlink is fresh ONLY when it resolves to THIS skill's source dir —
          # membership anywhere under the clone is not enough (a link misbound to a
          # sibling skill must be surfaced, never blessed as latest).
          [ "$tgt" = "$src_phys" ] && continue
          echo "WARNING: $canon/$name is a symlink bound to '${tgt:-<broken>}', not this skill — left untouched; fix the attachment" >&2
          problems=1; continue
        fi
        # diff semantics: 0 = identical (skip), 1 = genuinely differs (refresh),
        # >=2 = comparison ERROR — never destroy on a comparison we could not trust.
        if diff -rq "$src" "$canon/$name" >/dev/null 2>&1; then
          continue
        elif [ $? -ne 1 ]; then
          echo "WARNING: cannot compare $canon/$name (diff error) — left untouched" >&2
          problems=1; continue
        fi
        # Transactional replace: stage a sibling copy, move the old aside, swap in,
        # then drop the backup. Any failure leaves the installed skill in place (or
        # rolls it back) and exits non-zero — "non-zero exit => install untouched".
        staging="$canon/.$name.update-staging"; backup="$canon/.$name.update-backup"
        rm -rf "$staging" "$backup"
        if ! cp -r "$src" "$staging"; then
          rm -rf "$staging"
          echo "ERROR: staging copy failed for $name — canonical copy untouched" >&2
          exit 1
        fi
        if ! mv "$canon/$name" "$backup"; then
          rm -rf "$staging"
          echo "ERROR: cannot move aside $canon/$name — canonical copy untouched" >&2
          exit 1
        fi
        if ! mv "$staging" "$canon/$name"; then
          mv "$backup" "$canon/$name" || echo "ERROR: rollback ALSO failed — old copy preserved at $backup" >&2
          rm -rf "$staging"
          echo "ERROR: swap failed for $name — canonical copy restored" >&2
          exit 1
        fi
        rm -rf "$backup"
        changed=1
        echo "refreshed canonical copy: $canon/$name"
      done
      if [ "$changed" = 0 ] && [ "$problems" = 0 ]; then
        echo "canonical copies in $canon already latest"
      fi
      if [ "$problems" != 0 ]; then
        echo "WARNING: canonical sweep found problems (see above) — fix the attachment(s), re-run to verify" >&2
        sweep_problems=1
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
  [ "$sweep_problems" = 0 ] || exit 1
}

# Wrapper so bash parses the whole file before executing: Mode 1's cp may overwrite this
# very script while it runs (self-update); a fully-parsed main() makes that safe.
main "$@"
