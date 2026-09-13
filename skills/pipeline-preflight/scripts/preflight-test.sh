#!/usr/bin/env bash
# Self-test for preflight.sh. No external framework — bash + git + python3 only.
#
# Run: bash scripts/preflight-test.sh   (from any cwd; < 30s)
# Exit 0 iff every case matches its frozen expectation. One PASS/FAIL line per case.
#
# Isolation, and why each part matters:
#   * every case gets its OWN bare "remote" + clone under one mktemp -d, so a case that
#     advances the remote (13) cannot perturb another;
#   * $HOME is redirected to a temp dir — otherwise the real ~/.claude/skills and
#     ~/.agents/skills leak in and case 4's "grill-me UNVERIFIED" passes or fails
#     depending on WHOSE machine runs the suite;
#   * $PIPELINE_SKILL_DIRS points at a fake skills dir holding only `think/SKILL.md`;
#   * after EVERY run the clone's `git status --porcelain` must be empty — that is case 14,
#     the standing proof that preflight.sh writes no files.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFLIGHT="$HERE/preflight.sh"
FEATURE="demo-feature"
ZERO_SHA="0000000000000000000000000000000000000000"

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/preflight-test.XXXXXX")"
trap 'rm -rf "$ROOT"' EXIT

# --- hermetic environment -------------------------------------------------------------
export HOME="$ROOT/home"
export XDG_CONFIG_HOME="$ROOT/home/.config"   # git also reads XDG; redirect it too
mkdir -p "$HOME" "$XDG_CONFIG_HOME"
cat > "$HOME/.gitconfig" <<'GITCFG'
[user]
	name = preflight test
	email = preflight@example.invalid
[init]
	defaultBranch = main
[commit]
	gpgsign = false
[advice]
	detachedHead = false
GITCFG
export GIT_CONFIG_NOSYSTEM=1

SKILLS_FAKE="$ROOT/skills-fake"
mkdir -p "$SKILLS_FAKE/think"
printf -- '---\nname: think\n---\n' > "$SKILLS_FAKE/think/SKILL.md"
export PIPELINE_SKILL_DIRS="$SKILLS_FAKE"

DEFAULT_ROLES='prd:    [grill-me, think]   # grill-me clarifies, then think plans
arch:   grill-with-docs
task:   think               # decomposes into atomic cards
impl:   <autonomous-coding-skill>   # REQUIRED: the real installed name on your runtime
review: check
hunt:   hunt'
DEFAULT_CONTROL='{ "schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct" }'

# --- fixture builder ------------------------------------------------------------------
# Knobs (unset = default): FX_CURRENT=no · FX_ROLES=<yaml> · FX_NEXT=<stage> · FX_CONTROL=none|<json>
# Sets globals: WORK (the clone) and COMMIT (its pushed trunk sha).
WORK=""
COMMIT=""
build() {
  local name="$1" base w
  base="$ROOT/fx-$name"
  mkdir -p "$base"
  git init --quiet --bare "$base/remote.git"
  git clone --quiet "$base/remote.git" "$base/work" 2>/dev/null
  w="$base/work"
  mkdir -p "$w/.pipeline/$FEATURE"
  # .env* are gitignored so case 6 can drop one without dirtying the tree (case 14).
  printf '%s\n' '.env' '.env.local' '.envrc' > "$w/.gitignore"
  if [ "${FX_CURRENT:-yes}" = yes ]; then
    cat > "$w/.pipeline/current.json" <<JSON
{ "repo": "$w", "branch": "main", "feature": "$FEATURE", "stage": "arch" }
JSON
  fi
  printf '%s\n' "${FX_ROLES:-$DEFAULT_ROLES}" > "$w/.pipeline/roles.yaml"
  cat > "$w/.pipeline/$FEATURE/journal.md" <<MD
# Run journal — $FEATURE

## seq=1 · 2026-09-13T00:00:00Z · arch→task · completed · by=fixture
done:   arch landed
output: .pipeline/$FEATURE/arch.md
--- handoff ---
>>> NEXT

Run \`pipeline-${FX_NEXT:-task}\` repo=$w branch=main feature=$FEATURE
<<< END
MD
  if [ "${FX_CONTROL:-default}" != none ]; then
    if [ "${FX_CONTROL:-default}" = default ]; then
      printf '%s\n' "$DEFAULT_CONTROL" > "$w/.pipeline/$FEATURE/control.json"
    else
      printf '%s\n' "$FX_CONTROL" > "$w/.pipeline/$FEATURE/control.json"
    fi
  fi
  git -C "$w" add -A
  git -C "$w" commit --quiet -m "fixture: $name"
  git -C "$w" push --quiet -u origin main
  WORK="$w"
  COMMIT="$(git -C "$w" rev-parse HEAD)"
  # bash keeps `VAR=x func` assignments in scope after the function returns; clear the knobs
  # here so a later case cannot silently inherit an earlier one's fixture shape.
  unset FX_CURRENT FX_ROLES FX_NEXT FX_CONTROL
}

# --- harness --------------------------------------------------------------------------
TOTAL=0
PASSED=0
DIRTY=""
OUT=""
RC=0

run() {  # run <workdir> <args…> — captures OUT/RC and enforces the zero-writes invariant
  local wd="$1"; shift
  set +e
  OUT="$(cd "$wd" && bash "$PREFLIGHT" "$@" 2>&1)"
  RC=$?
  set -e
  local dirty
  dirty="$(git -C "$wd" status --porcelain)"
  [ -z "$dirty" ] || DIRTY="$DIRTY$wd:$dirty
"
}

report() {  # report <name> <ok 0|1>
  TOTAL=$((TOTAL + 1))
  if [ "$2" = 1 ]; then
    PASSED=$((PASSED + 1))
    echo "PASS $1"
  else
    echo "FAIL $1"
    echo "  rc=$RC output:"
    printf '%s\n' "$OUT" | sed 's/^/    /'
  fi
}

expect() {  # expect <name> <exit> [substring that MUST appear …]
  local name="$1" want="$2"; shift 2
  local ok=1 s
  [ "$RC" = "$want" ] || ok=0
  for s in "$@"; do
    printf '%s\n' "$OUT" | grep -Fq -- "$s" || ok=0
  done
  report "$name" "$ok"
}

# --- 1. prd with no current.json: the one stage allowed to create it -------------------
FX_CURRENT=no; FX_ROLES="prd: think"; build 01
run "$WORK" --stage prd
expect "1-prd-no-current" 0 "CURRENT absent" "PREFLIGHT OK stage=prd"

# --- 2. any other stage with no current.json: STOP ------------------------------------
FX_CURRENT=no; build 02
run "$WORK" --stage impl
expect "2-impl-no-current" 2 "PREFLIGHT STOP current.json-missing"

# --- 3. the roles.yaml placeholder is never resolved as a skill name ------------------
build 03
run "$WORK" --stage impl
expect "3-slot-placeholder" 2 "PREFLIGHT STOP slot-placeholder impl=<autonomous-coding-skill>"

# --- 4. one slot skill missing ⇒ UNVERIFIED (exit 3), never a false "installed" -------
build 04
run "$WORK" --stage prd
expect "4-install-unverified" 3 "INSTALLED grill-me UNVERIFIED" \
  "INSTALLED think path=$SKILLS_FAKE/think" "PREFLIGHT UNVERIFIED install-check"

# --- 5. happy path, human-relay -------------------------------------------------------
build 05
run "$WORK" --stage task
expect "5-task-ok" 0 "SLOT task=think" "GUARD n/a (human-relay)" "PREFLIGHT OK stage=task"

# --- 6. dotenv: key NAMES are reported, the value never is ----------------------------
build 06
printf 'gitee_token=SECRET_VALUE_XYZ\nexport GITEE_USER=someone\n' > "$WORK/.env"
run "$WORK" --stage task
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "ENV file=.env keys=gitee_token,GITEE_USER" || ok=0
# a leaked value fails the suite (an `x && ok=0` chain would abort it under set -e)
if printf '%s\n' "$OUT" | grep -Fq -- "SECRET_VALUE_XYZ"; then ok=0; fi
report "6-dotenv-names-not-values" "$ok"

# --- 7. envelope that matches reality in every field ----------------------------------
build 07
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "7-guard-ok" 0 "GUARD ok seq=1 commit=$COMMIT" "PREFLIGHT OK stage=task"

# --- 8. wrong trunk commit ------------------------------------------------------------
build 08
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$ZERO_SHA"
expect "8-stale-commit" 2 "STALE_DISPATCH expected_commit" "PREFLIGHT STOP stale-dispatch"

# --- 9. wrong journal tail seq --------------------------------------------------------
build 09
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=99 "expected_commit=$COMMIT"
expect "9-stale-seq" 2 "STALE_DISPATCH expected_seq observed=1 expected=99"

# --- 10. the tail's >>> NEXT names a different stage ----------------------------------
FX_NEXT=review; build 10
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "10-stale-next" 2 "STALE_DISPATCH next" "expected=pipeline-task"

# --- 11. control.json must carry the COMPLETE authorization tuple ---------------------
FX_CONTROL='{ "schema_version": 1, "mode": "human", "merge_gate": "human-direct" }'; build 11a
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "STALE_DISPATCH control.mode observed=human expected=coordinated" || ok=0
FX_CONTROL=none; build 11b
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "STALE_DISPATCH control.json observed=<absent> expected=present" || ok=0
report "11-control-tuple" "$ok"

# --- 12. a partial envelope is a STOP, never a silent downgrade to human-relay --------
build 12
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  "expected_commit=$COMMIT"
expect "12-envelope-incomplete" 2 "PREFLIGHT STOP envelope-incomplete missing=expected_seq"

# --- 13. the remote moved after the coordinator observed it ---------------------------
build 13
SECOND="$ROOT/fx-13/second"
git clone --quiet "$ROOT/fx-13/remote.git" "$SECOND"
echo "later" > "$SECOND/advanced.txt"
git -C "$SECOND" add -A
git -C "$SECOND" commit --quiet -m "remote advanced past the dispatch"
git -C "$SECOND" push --quiet origin main
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "13-remote-advanced" 2 "STALE_DISPATCH expected_commit" "PREFLIGHT STOP stale-dispatch"

# --- 14. zero writes: no run above left the checkout dirty ----------------------------
OUT="$DIRTY"; RC=0
if [ -z "$DIRTY" ]; then report "14-zero-writes" 1; else report "14-zero-writes" 0; fi

echo "---"
echo "$PASSED/$TOTAL cases passed"
[ "$PASSED" = "$TOTAL" ] || exit 1
exit 0
