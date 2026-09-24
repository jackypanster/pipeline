#!/usr/bin/env bash
# pipeline-update — fast-forward the read-only consumer clone and check the canonical links.
# Layout (README §Install): <skills-dir>/pipeline-<name> -> ../pipeline/skills/pipeline-<name>
# Usage: update.sh [clone]   (default ~/.agents/pipeline; skills dir: $PIPELINE_SKILLS_DIR, default ~/.agents/skills)
set -euo pipefail

clone="${1:-$HOME/.agents/pipeline}"
skills="${PIPELINE_SKILLS_DIR:-$HOME/.agents/skills}"
# Pin host AND owner/repo: siblings (pipeline-driver) and spoofed hosts must not match.
REMOTE_RE='(^|[@/])github\.com[:/]jackypanster/pipeline(\.git)?/?$'

stop() { echo "STOP: $*" >&2; exit 1; }
origin="$(git -C "$clone" config --get remote.origin.url)" || stop "$clone is not a git clone with an origin"
printf '%s\n' "$origin" | grep -Eq "$REMOTE_RE" || stop "$clone origin is $origin, not github.com/jackypanster/pipeline"
[ -z "$(git -C "$clone" status --porcelain --untracked-files=no)" ] \
  || stop "$clone has tracked-file changes — it is a read-only consumer clone; inspect by hand (never reset/stash)"

before="$(git -C "$clone" rev-parse HEAD)"
git -C "$clone" pull --ff-only --quiet || stop "git pull --ff-only failed in $clone (see git's message above; never reset)"
after="$(git -C "$clone" rev-parse HEAD)"

legacy=0
mkdir -p "$skills"
rel=0   # relative links only when the clone is <parent>/pipeline beside <parent>/<skills-dir>
[ "$(basename "$clone")" = pipeline ] && [ "$(cd "$clone/.." && pwd -P)" = "$(cd "$skills/.." && pwd -P)" ] && rel=1
for src in "$clone"/skills/pipeline-*/; do
  name="$(basename "$src")"; entry="$skills/$name"; real="$(cd "$src" && pwd -P)"
  if [ "$rel" = 1 ]; then want="../pipeline/skills/$name"; else want="$real"; fi
  if [ ! -e "$entry" ] && [ ! -L "$entry" ]; then
    ln -s "$want" "$entry"
    echo "LINKED $name — add its runtime attachments by hand (.claude/.codex; .pi only for impl/preflight)"
  elif [ -L "$entry" ] && [ "$(cd "$entry" 2>/dev/null && pwd -P)" = "$real" ]; then
    echo "ok $name"
  elif [ -L "$entry" ]; then
    echo "MISLINKED $name -> $(readlink "$entry") (expected $want) — fix by hand"; legacy=1
  else
    echo "LEGACY $name (copy install — migrate per README §Install)"; legacy=1
  fi
done

echo "HEAD $after"
if [ "$before" = "$after" ]; then echo "already latest"; else echo "updated $before -> $after"; fi
exit "$legacy"
