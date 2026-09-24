#!/usr/bin/env bash
# Self-test for update.sh — real git, temp dirs, no network, no mocks.
# A local bare repo stands in for GitHub via url.<bare>.insteadOf, so the clone's configured origin
# is the real upstream URL (the identity check passes) while every fetch hits the bare repo.
# Run: bash skills/pipeline-update/scripts/update-test.sh
set -euo pipefail

UPDATE="$(cd "$(dirname "$0")" && pwd)/update.sh"
URL="https://github.com/jackypanster/pipeline.git"
ROOT="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/update-test.XXXXXX")" && pwd -P)"
trap 'rm -rf "$ROOT"' EXIT
export HOME="$ROOT/home" XDG_CONFIG_HOME="$ROOT/home/.config" GIT_CONFIG_NOSYSTEM=1
unset PIPELINE_SKILLS_DIR
mkdir -p "$HOME"
git config --global user.name test; git config --global user.email test@example.invalid
git config --global init.defaultBranch main; git config --global commit.gpgsign false
git config --global "url.$ROOT/upstream.git.insteadOf" "$URL"

git init --quiet --bare "$ROOT/upstream.git"
git clone --quiet "$URL" "$ROOT/seed" 2>/dev/null
for n in pipeline-impl pipeline-preflight; do
  mkdir -p "$ROOT/seed/skills/$n"; echo "name: $n" > "$ROOT/seed/skills/$n/SKILL.md"
done
git -C "$ROOT/seed" add -A; git -C "$ROOT/seed" commit --quiet -m seed; git -C "$ROOT/seed" push --quiet origin main

CLONE="$HOME/.agents/pipeline"; SKILLS="$HOME/.agents/skills"
git clone --quiet "$URL" "$CLONE"

fails=0
check() {  # check <name> <want-rc> <substring>… — runs update.sh with the defaults
  local name="$1" want="$2" out rc s ok=1; shift 2
  set +e; out="$(bash "$UPDATE" 2>&1)"; rc=$?; set -e
  [ "$rc" = "$want" ] || ok=0
  for s in "$@"; do printf '%s\n' "$out" | grep -Fq -- "$s" || ok=0; done
  if [ "$ok" = 1 ]; then echo "PASS $name"; else echo "FAIL $name (rc=$rc)"; printf '%s\n' "$out" | sed 's/^/  | /'; fails=$((fails+1)); fi
}

# 1. fresh: both canonical entries are created as RELATIVE symlinks into the clone.
check 1-fresh-link 0 "LINKED pipeline-impl" "LINKED pipeline-preflight" "already latest"
[ "$(readlink "$SKILLS/pipeline-impl")" = ../pipeline/skills/pipeline-impl ] || { echo "FAIL 1b-relative-target"; fails=$((fails+1)); }

# 2. rerun: idempotent, nothing re-linked.
check 2-idempotent 0 "ok pipeline-impl" "ok pipeline-preflight" "HEAD $(git -C "$CLONE" rev-parse HEAD)" "already latest"

# 3. upstream adds a skill ⇒ ff pull picks it up and links it.
mkdir -p "$ROOT/seed/skills/pipeline-new"; echo "name: pipeline-new" > "$ROOT/seed/skills/pipeline-new/SKILL.md"
git -C "$ROOT/seed" add -A; git -C "$ROOT/seed" commit --quiet -m new; git -C "$ROOT/seed" push --quiet origin main
check 3-ff-new-skill 0 "LINKED pipeline-new" "ok pipeline-impl" "updated" "HEAD $(git -C "$ROOT/seed" rev-parse HEAD)"

# 4. a tracked-file edit in the consumer clone ⇒ STOP, clone untouched.
echo edit >> "$CLONE/skills/pipeline-impl/SKILL.md"
check 4-dirty-stop 1 "STOP:" "tracked-file changes"
git -C "$CLONE" diff --quiet && { echo "FAIL 4b-edit-preserved"; fails=$((fails+1)); }
git -C "$CLONE" checkout --quiet -- .

# 5. a foreign origin ⇒ STOP before any pull.
git -C "$CLONE" remote set-url origin https://github.com/jackypanster/pipeline-driver.git
check 5-foreign-origin 1 "STOP:" "not github.com/jackypanster/pipeline"
git -C "$CLONE" remote set-url origin "$URL"

# 6. a real directory (copy install) ⇒ LEGACY, non-zero, left byte-for-byte untouched.
rm "$SKILLS/pipeline-preflight"; mkdir "$SKILLS/pipeline-preflight"; echo copy > "$SKILLS/pipeline-preflight/SKILL.md"
check 6-legacy 1 "LEGACY pipeline-preflight" "ok pipeline-impl"
[ ! -L "$SKILLS/pipeline-preflight" ] && [ "$(cat "$SKILLS/pipeline-preflight/SKILL.md")" = copy ] \
  || { echo "FAIL 6b-legacy-untouched"; fails=$((fails+1)); }

[ "$fails" = 0 ] && echo "--- all update cases passed" || { echo "--- $fails failed"; exit 1; }
