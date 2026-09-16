#!/usr/bin/env bash
# Self-test for preflight.sh. No external framework — bash + git + python3 only.
#
# Run: bash scripts/preflight-test.sh   (from any cwd; < 60s)
# Exit 0 iff every case matches its frozen expectation. One PASS/FAIL/SKIP line per case.
#
# Isolation, and why each part matters:
#   * every case gets its OWN bare "remote" + clone under one mktemp -d, so a case that
#     advances the remote cannot perturb another;
#   * $HOME is redirected to a temp dir — otherwise the real ~/.claude/skills and
#     ~/.agents/skills leak in and the install cases pass or fail depending on WHOSE machine
#     runs the suite;
#   * $PIPELINE_SKILL_DIRS points at a fake skills dir holding only `think/SKILL.md`;
#   * after EVERY run the clone must be byte-identical: `git status --porcelain` empty, HEAD
#     unmoved, and NO file newer than a marker stamped immediately before the run. That triple
#     is the standing proof that preflight.sh writes no files IN THE TARGET REPO (final case); the
#     only other write it makes anywhere is its own throttle stamp
#     ($XDG_CACHE_HOME/pipeline/upstream-head, redirected into $ROOT with the rest of the environment).
#     The one sanctioned exception is CONTRACT step 1's own `git pull` advancing HEAD — a case opts
#     into it with ZW_EXPECT_HEAD and must then land exactly on the tip it pushed.
#
# The suite runs preflight.sh under the SAME bash that runs this file, so
# `/bin/bash scripts/preflight-test.sh` exercises the bash-3.2 path macOS still ships.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFLIGHT="$HERE/preflight.sh"
SKILLS_REPO="$(cd "$HERE/../.." && pwd)"       # …/skills — the canonical install layout
BASH_BIN="${BASH:-$(command -v bash)}"
FEATURE="demo-feature"
ZERO_SHA="0000000000000000000000000000000000000000"

# -P: the guard's `repo` field compares PHYSICAL paths, and $TMPDIR is a symlink on macOS.
ROOT="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/preflight-test.XXXXXX")" && pwd -P)"
trap 'rm -rf "$ROOT"' EXIT

# --- hermetic environment -------------------------------------------------------------
export HOME="$ROOT/home"
# BOTH XDG roots are redirected, and XDG_CACHE_HOME matters as much as git's: the upstream-check
# throttle stamp lives at ${XDG_CACHE_HOME:-$HOME/.cache}/pipeline/upstream-head, so an inherited
# real XDG_CACHE_HOME would make this suite read and WRITE the operator's own cache.
# HOME alone is not enough.
export XDG_CONFIG_HOME="$ROOT/home/.config"   # git also reads XDG; redirect it too
export XDG_CACHE_HOME="$ROOT/home/.cache"
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

# --- upstream fixture: a LOCAL bare repo stands in for github.com/jackypanster/pipeline --------
# Every case points PIPELINE_UPSTREAM_URL at it, so the once-a-day `UPSTREAM …` check never touches
# GitHub and a case can advance "main" on demand. UP_SHA is its tip; the mode-1 stamp cases compare
# against exactly it.
UPSTREAM_BARE="$ROOT/upstream.git"
UPSTREAM_SEED="$ROOT/upstream-seed"
git init --quiet --bare "$UPSTREAM_BARE"
git clone --quiet "$UPSTREAM_BARE" "$UPSTREAM_SEED" 2>/dev/null
echo seed > "$UPSTREAM_SEED/seed.txt"
git -C "$UPSTREAM_SEED" add -A
git -C "$UPSTREAM_SEED" commit --quiet -m "upstream seed"
git -C "$UPSTREAM_SEED" push --quiet origin main
UP_SHA="$(git -C "$UPSTREAM_SEED" rev-parse HEAD)"
export PIPELINE_UPSTREAM_URL="$UPSTREAM_BARE"
# The throttle stamp under test, and the mode-1 install stamp path.
UP_CACHE="$XDG_CACHE_HOME/pipeline/upstream-head"
STAMP=".pipeline-update.head"
upstream_url() { export PIPELINE_UPSTREAM_URL="$1"; }

DEFAULT_ROLES='prd:    [grill-me, think]   # grill-me clarifies, then think plans
arch:   grill-with-docs
task:   think               # decomposes into atomic cards
impl:   <autonomous-coding-skill>   # REQUIRED: the real installed name on your runtime
review: check
hunt:   hunt'
DEFAULT_CONTROL='{ "schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct" }'

# --- fixture builder ------------------------------------------------------------------
# Knobs (unset = default):
#   FX_CURRENT=no          — omit current.json entirely
#   FX_CURRENT_JSON=<json> — write this current.json verbatim
#   FX_CUR_REPO=<v>        — current.json's `repo` value. Default = the fixture's own bare repo
#                            path, which is BYTE-IDENTICAL to what `git clone` stored in
#                            remote.origin.url, so the remote-identity check matches.
#   FX_ROLES=<yaml> · FX_NEXT=<stage> · FX_NEXT_LINE=<line> · FX_JOURNAL=<md> · FX_CONTROL=none|<json>
# Sets globals: WORK (the clone) and COMMIT (its pushed trunk sha).
WORK=""
COMMIT=""
build() {
  local name="$1" base w nextline
  base="$ROOT/fx-$name"
  mkdir -p "$base"
  git init --quiet --bare "$base/remote.git"
  git clone --quiet "$base/remote.git" "$base/work" 2>/dev/null
  w="$base/work"
  mkdir -p "$w/.pipeline/$FEATURE"
  # .env* are gitignored so the dotenv case can drop one without dirtying the tree.
  printf '%s\n' '.env' '.env.local' '.envrc' > "$w/.gitignore"
  if [ -n "${FX_CURRENT_JSON:-}" ]; then
    printf '%s\n' "$FX_CURRENT_JSON" > "$w/.pipeline/current.json"
  elif [ "${FX_CURRENT:-yes}" = yes ]; then
    cat > "$w/.pipeline/current.json" <<JSON
{ "repo": "${FX_CUR_REPO:-$base/remote.git}", "branch": "main", "feature": "$FEATURE", "stage": "arch" }
JSON
  fi
  printf '%s\n' "${FX_ROLES:-$DEFAULT_ROLES}" > "$w/.pipeline/roles.yaml"
  if [ -n "${FX_JOURNAL:-}" ]; then
    printf '%s\n' "$FX_JOURNAL" > "$w/.pipeline/$FEATURE/journal.md"
  else
    nextline="${FX_NEXT_LINE:-Run pipeline-${FX_NEXT:-task} on a FRESH session (rebuild from the repo + CONTRACT.md).}"
    cat > "$w/.pipeline/$FEATURE/journal.md" <<MD
# Run journal — $FEATURE

## seq=1 · 2026-09-13T00:00:00Z · arch→task · completed · by=fixture
done:   arch landed
output: .pipeline/$FEATURE/arch.md
--- handoff ---
>>> NEXT

$nextline
repo=$w branch=main feature=$FEATURE
<<< END
MD
  fi
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
  unset FX_CURRENT FX_CURRENT_JSON FX_CUR_REPO FX_ROLES FX_NEXT FX_NEXT_LINE FX_JOURNAL FX_CONTROL
}

# Point origin at an arbitrary URL and add the `insteadOf` that rewrites it to the fixture's own
# bare repo, so pull/fetch stay hermetic. `remote.origin.url` then holds the RAW url while
# `ls-remote --get-url origin` reports the REWRITTEN one — the two sides case 20 compares.
declare_remote_url() {  # declare_remote_url <workdir> <url> — $WORK's bare repo is the transport
  git -C "$1" config "url.${WORK%/work}/remote.git.insteadOf" "$2"
  git -C "$1" remote set-url origin "$2"
}

# --- harness --------------------------------------------------------------------------
TOTAL=0
PASSED=0
SKIPPED=0
ZW=""              # accumulated zero-write violations, reported by the final case
OUT=""
RC=0
ZW_EXPECT_HEAD=""  # a case that legitimately expects step 1's pull to move HEAD sets the tip
RUN_PATH=""        # override $PATH for one run (python3-missing simulation)
RUN_SCRIPT=""      # invoke preflight.sh through another path (symlinked-install case)

run() {  # run <workdir> <args…> — captures OUT/RC and enforces the zero-writes invariant
  local wd="$1"; shift
  local marker head_before head_after newer dirty script
  script="${RUN_SCRIPT:-$PREFLIGHT}"
  marker="$ROOT/.zw-marker"
  : > "$marker"
  head_before="$(git -C "$wd" rev-parse HEAD)"
  set +e
  if [ -n "$RUN_PATH" ]; then
    OUT="$(cd "$wd" && PATH="$RUN_PATH" "$BASH_BIN" "$script" "$@" 2>&1)"
  else
    OUT="$(cd "$wd" && "$BASH_BIN" "$script" "$@" 2>&1)"
  fi
  RC=$?
  set -e
  head_after="$(git -C "$wd" rev-parse HEAD)"
  dirty="$(git -C "$wd" status --porcelain || true)"
  if [ -n "$dirty" ]; then ZW="$ZW  dirty $wd: $dirty
"; fi
  if [ -n "$ZW_EXPECT_HEAD" ]; then
    # The ONLY sanctioned HEAD move is CONTRACT step 1's own pull, and it must land exactly on
    # the tip the case pushed. Every file it touched is that checkout's, so the newer-file
    # sweep says nothing here and is skipped.
    if [ "$head_after" != "$ZW_EXPECT_HEAD" ]; then
      ZW="$ZW  head $wd: $head_after is not the pulled tip $ZW_EXPECT_HEAD
"
    fi
  else
    if [ "$head_after" != "$head_before" ]; then
      ZW="$ZW  head $wd: moved $head_before -> $head_after
"
    fi
    newer="$(find "$wd" -path "$wd/.git" -prune -o -type f -newer "$marker" -print 2>/dev/null || true)"
    if [ -n "$newer" ]; then ZW="$ZW  wrote $wd: $newer
"; fi
  fi
  ZW_EXPECT_HEAD=""; RUN_PATH=""; RUN_SCRIPT=""
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

skip() {  # skip <name> <reason> — an honestly unrunnable case, never a silent PASS
  TOTAL=$((TOTAL + 1))
  SKIPPED=$((SKIPPED + 1))
  echo "SKIP $1 — $2"
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

refute() {  # refute <substring> — sets `ok=0` if it appears (caller owns $ok)
  if printf '%s\n' "$OUT" | grep -Fq -- "$1"; then ok=0; fi
}

# --- 1. prd with no current.json: the one stage allowed to create it -------------------
FX_CURRENT=no; FX_ROLES="prd: think"; build 01
run "$WORK" --stage prd
expect "01-prd-no-current" 0 "CURRENT absent" "PREFLIGHT OK stage=prd"

# --- 2. any other stage with no current.json: STOP ------------------------------------
FX_CURRENT=no; build 02
run "$WORK" --stage impl
expect "02-impl-no-current" 2 "PREFLIGHT STOP current.json-missing"

# --- 3. the roles.yaml placeholder is never resolved as a skill name ------------------
build 03
run "$WORK" --stage impl
expect "03-slot-placeholder" 2 "PREFLIGHT STOP slot-placeholder impl=<autonomous-coding-skill>"

# --- 4. a slot skill found NOWHERE ⇒ UNVERIFIED, never a false "installed" ------------
build 04
run "$WORK" --stage prd
expect "04-install-not-found" 3 "INSTALLED grill-me UNVERIFIED searched=" \
  "INSTALLED think path=$SKILLS_FAKE/think" "PREFLIGHT UNVERIFIED install-check"

# --- 5. happy path, human-relay — a PIPELINE_SKILL_DIRS hit is a VERIFIED install ------
build 05
run "$WORK" --stage task
expect "05-task-ok" 0 "SLOT task=think" "INSTALLED think path=$SKILLS_FAKE/think" \
  "GUARD n/a (human-relay)" "PREFLIGHT OK stage=task"

# --- 6. same layout, PIPELINE_SKILL_DIRS UNSET: a default-dir hit is EVIDENCE, not proof
# A readable SKILL.md under ~/.claude/skills does not prove THIS runtime loads skills there.
mkdir -p "$HOME/.claude/skills/think"
printf -- '---\nname: think\n---\n' > "$HOME/.claude/skills/think/SKILL.md"
build 06
saved_dirs="$PIPELINE_SKILL_DIRS"; unset PIPELINE_SKILL_DIRS
run "$WORK" --stage task
export PIPELINE_SKILL_DIRS="$saved_dirs"
expect "06-install-undeclared" 3 "INSTALLED think found=$HOME/.claude/skills/think UNVERIFIED" \
  "PIPELINE_SKILL_DIRS" "PREFLIGHT UNVERIFIED install-check"
rm -rf "$HOME/.claude/skills/think"

# --- 7. dotenv: a COUNT is reported — never a key name, never a value -----------------
# The last two data lines are a multi-line quoted value whose CONTINUATION line looks exactly
# like an assignment. Printing key names leaked it verbatim; a count cannot.
build 07
printf '%s\n' 'gitee_token=SECRET_VALUE_XYZ' \
              'export GITEE_USER=someone' \
              'CERT="-----BEGIN KEY-----' \
              'SECRET_VALUE_XYZ=leak' \
              '-----END KEY-----"' > "$WORK/.env"
run "$WORK" --stage task
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Eq '^ENV file=\.env keys=[0-9]+$' || ok=0
refute "SECRET_VALUE_XYZ"     # the value AND the look-alike continuation key
refute "gitee_token"
refute "GITEE_USER"
report "07-dotenv-count-only" "$ok"

# --- 8. current.json fields must be non-empty STRINGS; `pr` is echoed when present ----
FX_CURRENT_JSON='{ "repo": "fx-08a/remote", "branch": "main", "feature": "", "stage": "arch" }'
build 08a
run "$WORK" --stage task
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT STOP current.json-invalid-field feature" || ok=0
FX_CURRENT_JSON='{ "repo": "fx-08b/remote", "branch": null, "feature": "'"$FEATURE"'", "stage": "arch" }'
build 08b
run "$WORK" --stage task
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT STOP current.json-invalid-field branch" || ok=0
FX_CURRENT_JSON='{ "repo": "fx-08c/remote", "branch": "main", "feature": "'"$FEATURE"'", "stage": "arch", "pr": "https://forge/pr/7" }'
build 08c
run "$WORK" --stage task
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "pr=https://forge/pr/7" || ok=0
FX_CURRENT_JSON='{ "repo": "fx-08d/remote ", "branch": "main", "feature": "'"$FEATURE"'", "stage": "arch" }'
build 08d
run "$WORK" --stage task
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT STOP current.json-invalid-field repo" || ok=0   # surrounding whitespace is never trimmed
report "08-current-json-fields" "$ok"

# --- 9. an ALL-EMPTY envelope is an incomplete envelope, not the absence of one --------
build 09
run "$WORK" --stage task repo= branch= feature= expected_seq= expected_commit=
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- \
  "PREFLIGHT STOP envelope-incomplete missing=repo,branch,feature,expected_seq,expected_commit" || ok=0
refute "GUARD n/a"      # never a silent downgrade to unguarded human-relay
report "09-envelope-all-empty" "$ok"

# --- 10. a partial envelope is a STOP, never a silent downgrade to human-relay --------
build 10
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  "expected_commit=$COMMIT"
expect "10-envelope-incomplete" 2 "PREFLIGHT STOP envelope-incomplete missing=expected_seq"

# --- 11. envelope that matches reality in every field, remote identity included -------
build 11
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "11-guard-ok" 0 "GUARD ok seq=1 commit=$COMMIT remote=" "PREFLIGHT OK stage=task"

# --- 12. wrong trunk commit ------------------------------------------------------------
build 12
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$ZERO_SHA"
expect "12-stale-commit" 2 "STALE_DISPATCH expected_commit" "PREFLIGHT STOP stale-dispatch"

# --- 13. wrong journal tail seq --------------------------------------------------------
build 13
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=99 "expected_commit=$COMMIT"
expect "13-stale-seq" 2 "STALE_DISPATCH expected_seq observed=1 expected=99"

# --- 14. the tail's >>> NEXT names a different stage ----------------------------------
FX_NEXT=review; build 14
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "14-stale-next-other-stage" 2 "STALE_DISPATCH next" "expected=pipeline-task"

# --- 15. the TAIL entry has no handoff; an OLDER entry does ---------------------------
# Taking the last `>>> NEXT` anywhere in an append-only journal reuses a consumed handoff.
FX_JOURNAL="# Run journal — $FEATURE

## seq=1 · 2026-09-13T00:00:00Z · arch→task · completed · by=fixture
done:   arch landed
--- handoff ---
>>> NEXT

Run pipeline-task on a FRESH session (rebuild from the repo + CONTRACT.md).
<<< END

## seq=2 · 2026-09-13T01:00:00Z · task→impl · completed · by=fixture
done:   cards landed, handoff relayed out of band
output: .pipeline/$FEATURE/tasks/01.md"
build 15
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=2 "expected_commit=$COMMIT"
expect "15-next-absent-in-tail" 2 \
  "STALE_DISPATCH next observed=<absent-handoff-in-tail> expected=pipeline-task"

# --- 16. prose that MENTIONS this stage is not a handoff TO this stage -----------------
FX_NEXT_LINE="Run pipeline-review after pipeline-task finishes"; build 16
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "16-next-substring-rejected" 2 "STALE_DISPATCH next" "expected=pipeline-task"

# --- 17. the shapes a real command legitimately arrives in ARE accepted ---------------
FX_NEXT_LINE="/pipeline-task repo=x branch=main feature=$FEATURE"; build 17a
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=task" || ok=0
FX_NEXT_LINE="\$pipeline-task repo=x"; build 17b
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
[ "$RC" = 0 ] || ok=0
FX_NEXT_LINE="pipeline-task"; build 17c
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
[ "$RC" = 0 ] || ok=0
report "17-next-command-forms" "$ok"

# --- 18. remote identity: BYTE EQUAL to `git ls-remote --get-url <remote>` ⇒ verified --
build 18
URL="$(git -C "$WORK" ls-remote --get-url origin)"
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "18-remote-byte-equal" 0 "GUARD ok seq=1 commit=$COMMIT remote=$URL" \
  "PREFLIGHT OK stage=task"

# --- 19. ANY other string ⇒ UNVERIFIED, never a match and never a STOP ----------------
# `file://<path>` is the very same repo, spelled as a URL. The deleted normaliser called that
# a match; nothing but Git knows for sure, so the stage is told to check it instead.
FX_CUR_REPO="file://$ROOT/fx-19/remote.git"; build 19
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 3 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- \
  "REMOTE unverified observed=$ROOT/fx-19/remote.git current.json.repo=file://$ROOT/fx-19/remote.git" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT UNVERIFIED remote-identity" || ok=0
refute "STALE_DISPATCH"  # unverifiable is not wrong: it degrades, it never STOPs
report "19-remote-differs" "$ok"

# --- 20. `insteadOf`: observed is what GIT resolves the remote to, not the raw config --
# (a) current.json.repo written as the REWRITTEN url ⇒ match (this is `--get-url` semantics).
build 20a
declare_remote_url "$WORK" "forge:acme/demo"
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "remote=$ROOT/fx-20a/remote.git" || ok=0
# (b) the same remote, current.json.repo written as the RAW config url ⇒ UNVERIFIED.
FX_CUR_REPO="forge:acme/demo"; build 20b
declare_remote_url "$WORK" "forge:acme/demo"
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
[ "$RC" = 3 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- \
  "REMOTE unverified observed=$ROOT/fx-20b/remote.git current.json.repo=forge:acme/demo" || ok=0
refute "STALE_DISPATCH"
report "20-remote-insteadof" "$ok"

# --- 21. a failed fetch STOPs; no ref left on disk is compared in its place -----------
# The clone's local `main` tracks the remote's `trunk`, so CONTRACT step 1's pull succeeds and
# the guard's `git fetch origin main` cannot: the remote has no `main`. Both cached refs are
# left pointing at the RIGHT commit — refs/remotes/origin/main, and the FETCH_HEAD step 1's own
# pull just wrote — so swallowing the fetch failure would print a false GUARD ok either way.
build 21
git -C "$WORK" push --quiet origin main:trunk
git -C "$ROOT/fx-21/remote.git" symbolic-ref HEAD refs/heads/trunk
git -C "$WORK" push --quiet origin :main
git -C "$WORK" update-ref refs/remotes/origin/main "$COMMIT"
git -C "$WORK" config branch.main.merge refs/heads/trunk
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "FETCH fail origin/main" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT STOP fetch-failed" || ok=0
refute "GUARD ok"
report "21-fetch-failed" "$ok"

# --- 22. a broken remote URL fails at step 1, before the guard is even reached --------
build 22
git -C "$WORK" remote set-url origin "$ROOT/does-not-exist.git"
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "22-pull-failed" 2 "PULL fail" "PREFLIGHT STOP pull-failed"

# --- 23. control.json must carry the COMPLETE authorization tuple ---------------------
FX_CONTROL='{ "schema_version": 1, "mode": "human", "merge_gate": "human-direct" }'; build 23a
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "STALE_DISPATCH control.mode observed=human expected=coordinated" || ok=0
FX_CONTROL=none; build 23b
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "STALE_DISPATCH control.json observed=<absent> expected=present" || ok=0
report "23-control-tuple" "$ok"

# --- 24. the remote moved after the coordinator observed it ---------------------------
build 24
SECOND="$ROOT/fx-24/second"
git clone --quiet "$ROOT/fx-24/remote.git" "$SECOND"
echo "later" > "$SECOND/advanced.txt"
git -C "$SECOND" add -A
git -C "$SECOND" commit --quiet -m "remote advanced past the dispatch"
git -C "$SECOND" push --quiet origin main
ZW_EXPECT_HEAD="$(git -C "$SECOND" rev-parse HEAD)"   # step 1's pull legitimately lands here
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "24-remote-advanced" 2 "STALE_DISPATCH expected_commit" "PREFLIGHT STOP stale-dispatch"

# --- 25. python3 missing ⇒ exit 4 SKIPPED: nothing ran, the caller does steps 1–4 -----
NOPY="$ROOT/nopy-bin"
mkdir -p "$NOPY"
nopy_ok=1
for t in git sed grep tail head cut paste tr wc cat find xcrun; do
  p="$(command -v "$t" 2>/dev/null || true)"
  if [ -n "$p" ]; then
    ln -sf "$p" "$NOPY/$t"
  elif [ "$t" != xcrun ]; then
    nopy_ok=0
  fi
done
if [ -e "$NOPY/python3" ]; then nopy_ok=0; fi
if [ "$nopy_ok" != 1 ]; then
  skip "25-python3-missing" "a PATH without python3 but with git/coreutils is not constructible here"
else
  build 25
  RUN_PATH="$NOPY"
  run "$WORK" --stage task
  if [ "$RC" = 4 ]; then
    expect "25-python3-missing" 4 "PREFLIGHT SKIPPED python3-missing"
  elif printf '%s\n' "$OUT" | grep -q 'git:.*not found\|command not found'; then
    skip "25-python3-missing" "the stripped PATH cannot run git on this machine: $OUT"
  else
    expect "25-python3-missing" 4 "PREFLIGHT SKIPPED python3-missing"
  fi
fi

# --- 26. a stage skill attached by SYMLINK still resolves its preflight sibling -------
# `<symlink>/../pipeline-preflight/…` is resolved by the kernel against the symlink's TARGET,
# so a runtime that attaches skills by symlink reaches the real script next to the real skill.
mkdir -p "$HOME/.claude/skills"
ln -sfn "$SKILLS_REPO/pipeline-task" "$HOME/.claude/skills/pipeline-task"
build 26
RUN_SCRIPT="$HOME/.claude/skills/pipeline-task/../pipeline-preflight/scripts/preflight.sh"
run "$WORK" --stage task
expect "26-symlinked-install-layout" 0 "PREFLIGHT OK stage=task"

# --- 27. the handoff markers are WHOLE LINES, never substrings ------------------------
# (a) a body line that MENTIONS `>>> NEXT`, with a decoy command under it. The real handoff
#     hands off to review; matching the mention as the marker let stage `task` walk right in.
FX_JOURNAL="# Run journal — $FEATURE

## seq=1 · 2026-09-13T00:00:00Z · arch→review · completed · by=fixture
done:   arch landed
note:   the coordinator relays the >>> NEXT block below verbatim
Run pipeline-task on a FRESH session (prose, not the handoff)
--- handoff ---
>>> NEXT

Run pipeline-review on a FRESH session (rebuild from the repo + CONTRACT.md).
<<< END"
build 27a
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- \
  "STALE_DISPATCH next observed=Run pipeline-review" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "expected=pipeline-task" || ok=0
# (b) `--- handoff ---` is there but the next non-empty line is prose ⇒ the tail has no handoff.
#     Scanning on to a LATER `>>> NEXT` would resurrect exactly the decoy of case (a).
FX_JOURNAL="# Run journal — $FEATURE

## seq=1 · 2026-09-13T00:00:00Z · arch→task · completed · by=fixture
done:   arch landed
--- handoff ---
(handoff relayed out of band)

>>> NEXT

Run pipeline-task on a FRESH session (rebuild from the repo + CONTRACT.md).
<<< END"
build 27b
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- \
  "STALE_DISPATCH next observed=<absent-handoff-in-tail> expected=pipeline-task" || ok=0
report "27-handoff-markers-exact" "$ok"

# --- 28. the fetched tip is FETCH_HEAD, never the remote-tracking ref -----------------
# `git fetch origin main` does not have to update refs/remotes/origin/main: here the fetch
# refspec is narrowed to trunk. Local `main` tracks `trunk` (== the dispatched commit) so step
# 1's pull is a no-op, the cached origin/main still says "all is well", and only FETCH_HEAD
# knows the real `main` has moved on.
build 28
git -C "$WORK" push --quiet origin main:trunk
git -C "$WORK" config branch.main.merge refs/heads/trunk
git -C "$WORK" config remote.origin.fetch '+refs/heads/trunk:refs/remotes/origin/trunk'
SECOND="$ROOT/fx-28/second"
git clone --quiet "$ROOT/fx-28/remote.git" "$SECOND"
echo "later" > "$SECOND/advanced.txt"
git -C "$SECOND" add -A
git -C "$SECOND" commit --quiet -m "remote main advanced past the dispatch"
git -C "$SECOND" push --quiet origin main
ADVANCED="$(git -C "$SECOND" rev-parse HEAD)"
git -C "$WORK" update-ref refs/remotes/origin/main "$COMMIT"
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
expect "28-fetch-head-not-cached-ref" 2 \
  "STALE_DISPATCH expected_commit observed=$ADVANCED expected=$COMMIT"

# --- 29. a slot name is a DIRECTORY NAME, never a path --------------------------------
# `../outside` reaches a SKILL.md outside every declared PIPELINE_SKILL_DIRS.
mkdir -p "$ROOT/outside"
printf -- '---\nname: outside\n---\n' > "$ROOT/outside/SKILL.md"
FX_ROLES="task: ../outside"; build 29
run "$WORK" --stage task
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT STOP slot-invalid-name task=../outside" || ok=0
refute "INSTALLED"
report "29-slot-invalid-name" "$ok"

# --- 30. a handoff body far larger than the pipe buffer still parses ------------------
# The reader stops at the command line and never reads the rest. Fed through `tail … | awk`
# that closed the pipe under a still-writing tail: SIGPIPE, and `set -o pipefail` turned it
# into a bare exit 141. ~100 KiB of trailing body is comfortably past the 64 KiB buffer.
FILLER="$(awk 'BEGIN { for (i = 0; i < 900; i++)
  print "pad " i " — trailing handoff body the reader never reaches, past the 64 KiB pipe buffer" }')"
FX_JOURNAL="# Run journal — $FEATURE

## seq=1 · 2026-09-13T00:00:00Z · arch→task · completed · by=fixture
done:   arch landed
--- handoff ---
>>> NEXT

Run pipeline-task on a FRESH session (rebuild from the repo + CONTRACT.md).
<<< END

$FILLER"
build 30
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=1 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 0 ] || ok=0
[ "$(wc -c < "$WORK/.pipeline/$FEATURE/journal.md")" -gt 65536 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "GUARD ok seq=1 commit=$COMMIT" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=task" || ok=0
report "30-large-handoff-no-sigpipe" "$ok"

# --- 32..37. the advisory `UPSTREAM …` line (never a STOP, never a changed exit code) ----------
# The dev tree is a pipeline clone, so the suite's own runs are mode 2. Mode 1 needs a script OUTSIDE
# any git repo: $ROOT is under $TMPDIR, so a copy of preflight.sh there has no clone to read a HEAD
# from and must fall back to the install stamp.
FAKE_SKILLS="$ROOT/fake-skills"
mkdir -p "$FAKE_SKILLS/pipeline-preflight/scripts"
cp "$PREFLIGHT" "$FAKE_SKILLS/pipeline-preflight/scripts/preflight.sh"
FAKE_PREFLIGHT="$FAKE_SKILLS/pipeline-preflight/scripts/preflight.sh"
DRIFT_SHA="1111111111111111111111111111111111111111"

# --- 32. mode 1, no stamp: nothing was ever verified against main ⇒ say so, never guess --------
build 32
rm -f "$FAKE_SKILLS/$STAMP" "$UP_CACHE"
RUN_SCRIPT="$FAKE_PREFLIGHT"
run "$WORK" --stage task
expect "32-upstream-mode1-no-stamp" 0 "UPSTREAM unverified no-install-stamp" \
  "PREFLIGHT OK stage=task"

# --- 33. mode 1, stamp == upstream HEAD ⇒ ok (and the value came from the fresh cache) ---------
build 33
printf '%s\n' "$UP_SHA" > "$FAKE_SKILLS/$STAMP"
RUN_SCRIPT="$FAKE_PREFLIGHT"
run "$WORK" --stage task
expect "33-upstream-mode1-ok" 0 "UPSTREAM ok head=$UP_SHA" "PREFLIGHT OK stage=task"

# --- 34. mode 1, stamp = an unrelated 40-hex sha ⇒ newer + the exact remedy --------------------
# MUST stay immediately before 35: that case proves the throttle by reusing THIS run's cache.
build 34
printf '%s\n' "$DRIFT_SHA" > "$FAKE_SKILLS/$STAMP"
RUN_SCRIPT="$FAKE_PREFLIGHT"
run "$WORK" --stage task
expect "34-upstream-mode1-newer" 0 \
  "UPSTREAM newer head=$UP_SHA installed=$DRIFT_SHA run=pipeline-update" "PREFLIGHT OK stage=task"

# --- 35. a fresh cache means NO network: an unreachable URL changes nothing --------------------
build 35
upstream_url "$ROOT/does-not-exist.git"
RUN_SCRIPT="$FAKE_PREFLIGHT"
run "$WORK" --stage task
upstream_url "$UPSTREAM_BARE"
expect "35-upstream-cache-no-network" 0 \
  "UPSTREAM newer head=$UP_SHA installed=$DRIFT_SHA run=pipeline-update cached" \
  "PREFLIGHT OK stage=task"

# --- 36. a STALE cache + an unreachable URL ⇒ unverified, exit unchanged, and the failed fetch
#         is itself throttled (the cache now holds an empty sha, so the next 24h costs no wait) --
build 36
printf '%s %s\n' "$(( $(date +%s) - 90000 ))" "$UP_SHA" > "$UP_CACHE"
upstream_url "$ROOT/does-not-exist.git"
RUN_SCRIPT="$FAKE_PREFLIGHT"
run "$WORK" --stage task
upstream_url "$UPSTREAM_BARE"
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "UPSTREAM unverified network" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=task" || ok=0
grep -Eq '^[0-9]+ $' "$UP_CACHE" || ok=0
report "36-upstream-cache-stale-unverified" "$ok"

# --- 37. mode 2: the skills live in a pipeline CLONE, whose HEAD is the installed version ------
MODE2="$ROOT/mode2-clone"
git clone --quiet "$UPSTREAM_BARE" "$MODE2" 2>/dev/null
git -C "$MODE2" remote set-url origin https://github.com/jackypanster/pipeline.git   # no network is used
mkdir -p "$MODE2/skills/pipeline-preflight/scripts"
cp "$PREFLIGHT" "$MODE2/skills/pipeline-preflight/scripts/preflight.sh"
MODE2_PREFLIGHT="$MODE2/skills/pipeline-preflight/scripts/preflight.sh"
# (a) clone HEAD == upstream main ⇒ ok.
build 37a
rm -f "$UP_CACHE"
RUN_SCRIPT="$MODE2_PREFLIGHT"
run "$WORK" --stage task
expect "37a-upstream-mode2-ok" 0 "UPSTREAM ok head=$UP_SHA" "PREFLIGHT OK stage=task"
# (b) upstream advanced by one commit (cache wiped) ⇒ newer, naming the installed clone HEAD.
SECOND="$ROOT/upstream-second"
git clone --quiet "$UPSTREAM_BARE" "$SECOND" 2>/dev/null
echo advanced > "$SECOND/advanced.txt"
git -C "$SECOND" add -A
git -C "$SECOND" commit --quiet -m "upstream advanced"
git -C "$SECOND" push --quiet origin main
NEW_SHA="$(git -C "$SECOND" rev-parse HEAD)"
build 37b
rm -f "$UP_CACHE"
RUN_SCRIPT="$MODE2_PREFLIGHT"
run "$WORK" --stage task
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- \
  "UPSTREAM newer head=$NEW_SHA installed=$UP_SHA run=pipeline-update" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=task" || ok=0
report "37b-upstream-mode2-newer" "$ok"
# (c) the clone is AHEAD of upstream main: a local commit is not staleness ⇒ ok, never `newer`.
git -C "$MODE2" fetch --quiet "$UPSTREAM_BARE" main
git -C "$MODE2" reset --hard --quiet FETCH_HEAD
echo local > "$MODE2/local.txt"
git -C "$MODE2" add -A
git -C "$MODE2" commit --quiet -m "local ahead"
build 37c
rm -f "$UP_CACHE"
RUN_SCRIPT="$MODE2_PREFLIGHT"
run "$WORK" --stage task
expect "37c-upstream-mode2-ahead" 0 "UPSTREAM ok head=$NEW_SHA" "PREFLIGHT OK stage=task"

# --- 38. a STOP writes NOTHING — not even the advisory throttle cache ------------------------
# CONTRACT §Pre-write stale-dispatch guard: the guard runs before ANY file write and a mismatch
# leaves zero writes. The freshness advisory WRITES its cache, so it must run after the guard:
# with a coordinated envelope whose expected_seq mismatches the journal tail, the stale-dispatch
# STOP must print no UPSTREAM line and must not create the cache file at all.
build 38
rm -f "$UP_CACHE"
run "$WORK" --stage task "repo=$WORK" branch=main "feature=$FEATURE" \
  expected_seq=99 "expected_commit=$COMMIT"
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "STALE_DISPATCH expected_seq observed=1 expected=99" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT STOP stale-dispatch" || ok=0
refute "UPSTREAM"                                             # no advisory line on a STOP
if [ -e "$UP_CACHE" ]; then ok=0; fi                         # and no cache write before the guard
report "38-stale-dispatch-writes-nothing" "$ok"

# --- 39. …but an exit-3 (UNVERIFIED) run still gets the advisory: it did NOT stop ------------
# UNVERIFIED is not a STOP: the run proceeds, so the advisory still reports freshness. The remote
# tip is read live — 37b advanced upstream main past the seed sha.
build 39
rm -f "$UP_CACHE"
TODAY_SHA="$(git -C "$UPSTREAM_BARE" rev-parse HEAD)"
run "$WORK" --stage prd
expect "39-unverified-still-advisory" 3 "PREFLIGHT UNVERIFIED install-check" \
  "UPSTREAM newer head=$TODAY_SHA installed="

# --- 40..49. the card-invariant check (CARDS …) --------------------------------------------
# Cards + their frozen spec file are written AFTER build() and PUSHED, so the clone is clean and
# its HEAD matches the remote before run() stamps the zero-write marker. $COMMIT (build's pushed
# trunk sha) is a real, resolvable commit and stands in for a feature's shared spec-rev.
CARD_FV='{ "repo": "%s", "branch": "main", "feature": "'"$FEATURE"'", "stage": "task", "full-verify": ["make build", "make test"] }'
CARD_ROLES='impl: think
review: think
hunt: think'

card_write() {  # card_write <workdir> <NN> <status> <verify> <spec-paths> <impl-paths> <spec-rev>
  mkdir -p "$1/.pipeline/$FEATURE/tasks"
  cat > "$1/.pipeline/$FEATURE/tasks/$2.md" <<CARD
---
status: $3
attempts: 0
verify: $4
spec-paths: $5
impl-paths: $6
spec-rev: $7
---

# card $2
CARD
}

cards_push() {  # cards_push <workdir> — the frozen spec file + the cards, in one pushed commit
  mkdir -p "$1/tests"
  echo red > "$1/tests/spec.txt"
  git -C "$1" add -A
  git -C "$1" commit --quiet -m cards
  git -C "$1" push --quiet origin main
}

# --- 40. two valid cards sharing one resolvable spec-rev ⇒ ok, exit unchanged ----------------
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-40/remote.git")"; build 40
card_write "$WORK" 01 todo '["make test A"]' '["tests/spec.txt"]' '["src/a.rs"]' "$COMMIT"
card_write "$WORK" 02 todo '["make test B"]' '["tests/spec.txt"]' '["src/b.rs"]' "$COMMIT"
cards_push "$WORK"
run "$WORK" --stage impl
expect "40-cards-ok" 0 "CARDS ok feature=$FEATURE n=2 spec-rev=$(printf %.7s "$COMMIT")" \
  "PREFLIGHT OK stage=impl"

# --- 41. a missing required field STOPs impl — but is ADVISORY on hunt (hunt repairs cards) --
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-41/remote.git")"; build 41
mkdir -p "$WORK/.pipeline/$FEATURE/tasks"
cat > "$WORK/.pipeline/$FEATURE/tasks/01.md" <<CARD
---
status: todo
attempts: 0
verify: ["make test A"]
spec-paths: ["tests/spec.txt"]
spec-rev: $COMMIT
---
CARD
cards_push "$WORK"
run "$WORK" --stage impl
ok=1
[ "$RC" = 2 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- \
  "PREFLIGHT STOP card-missing-field impl-paths card=$FEATURE/01" || ok=0
run "$WORK" --stage hunt
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "CARDS stop card-missing-field impl-paths card=$FEATURE/01" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "CARDS advisory stage=hunt findings=1" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=hunt" || ok=0
report "41-cards-missing-field" "$ok"

# --- 42. spec-paths ∩ impl-paths ≠ ∅ (CONTRACT §Test ownership) ------------------------------
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-42/remote.git")"; build 42
card_write "$WORK" 01 todo '["make test A"]' '["tests/spec.txt"]' '["tests/spec.txt", "src/a.rs"]' "$COMMIT"
cards_push "$WORK"
run "$WORK" --stage impl
expect "42-cards-spec-impl-overlap" 2 \
  "PREFLIGHT STOP card-spec-impl-overlap tests/spec.txt card=$FEATURE/01"

# --- 43. a card's verify IS the full suite ⇒ the multi-card deadlock CONTRACT forbids --------
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-43/remote.git")"; build 43
card_write "$WORK" 01 todo '["make build", "make test"]' '["tests/spec.txt"]' '["src/a.rs"]' "$COMMIT"
cards_push "$WORK"
run "$WORK" --stage impl
expect "43-cards-verify-full-suite" 2 "PREFLIGHT STOP card-verify-full-suite card=$FEATURE/01"

# --- 44. spec-rev that is not a commit in this repo -----------------------------------------
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-44/remote.git")"; build 44
card_write "$WORK" 01 todo '["make test A"]' '["tests/spec.txt"]' '["src/a.rs"]' "$ZERO_SHA"
cards_push "$WORK"
run "$WORK" --stage impl
expect "44-cards-spec-rev-unresolvable" 2 \
  "PREFLIGHT STOP card-spec-rev-unresolvable $ZERO_SHA card=$FEATURE/01"

# --- 45. two cards, two DIFFERENT resolvable revs — the shared-baseline rule -----------------
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-45/remote.git")"; build 45
echo first > "$WORK/tests-seed.txt"
git -C "$WORK" add -A; git -C "$WORK" commit --quiet -m seed
git -C "$WORK" push --quiet origin main
REV2="$(git -C "$WORK" rev-parse HEAD)"
card_write "$WORK" 01 todo '["make test A"]' '["tests/spec.txt"]' '["src/a.rs"]' "$COMMIT"
card_write "$WORK" 02 todo '["make test B"]' '["tests/spec.txt"]' '["src/b.rs"]' "$REV2"
cards_push "$WORK"
run "$WORK" --stage impl
expect "45-cards-spec-rev-not-shared" 2 \
  "PREFLIGHT STOP feature-spec-rev-not-shared" "feature=$FEATURE"

# --- 46. no tasks/ dir yet (a fresh feature) ⇒ nothing runs, nothing printed -----------------
FX_ROLES="$CARD_ROLES"; build 46
run "$WORK" --stage impl
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=impl" || ok=0
refute "CARDS"
report "46-cards-absent-noop" "$ok"

# --- 47. advisory: a `review` card with no `## Assumptions` never changes the exit -----------
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-47/remote.git")"; build 47
card_write "$WORK" 01 review '["make test A"]' '["tests/spec.txt"]' '["src/a.rs"]' "$COMMIT"
cards_push "$WORK"
run "$WORK" --stage review
expect "47-cards-assumptions-note" 0 "CARDS note assumptions-missing card=$FEATURE/01" \
  "CARDS ok feature=$FEATURE n=1" "PREFLIGHT OK stage=review"

# --- 48. a frontmatter SHAPE the parser cannot read ⇒ note + no judgement, never a STOP -----
# `verify:` followed by an indented line with no `- ` is not valid YAML but is plausible LLM
# output; the naive read is `verify: []` ⇒ a false `card-verify-empty` STOP that would block impl.
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-48/remote.git")"; build 48
mkdir -p "$WORK/.pipeline/$FEATURE/tasks"
cat > "$WORK/.pipeline/$FEATURE/tasks/01.md" <<CARD
---
status: todo
attempts: 0
verify:
  make test A
spec-paths: ["tests/spec.txt"]
impl-paths: ["src/a.rs"]
spec-rev: $COMMIT
---

# card 01
CARD
cards_push "$WORK"
run "$WORK" --stage impl
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "CARDS note card-frontmatter-unrecognized card=$FEATURE/01" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "CARDS ok feature=$FEATURE n=1" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=impl" || ok=0
refute "CARDS stop"
report "48-cards-frontmatter-unrecognized" "$ok"

# --- 49. list VALUES this parser cannot read ⇒ note, the checks needing them are skipped ----
# Both forms are in the live corpus: a bracket that is not JSON, and a comma-separated scalar
# (splitting it would invent a grammar; treating it as ONE path false-STOPs `card-spec-path-absent`).
FX_ROLES="$CARD_ROLES"; FX_CURRENT_JSON="$(printf "$CARD_FV" "$ROOT/fx-49/remote.git")"; build 49
card_write "$WORK" 01 todo '[make test A, make build]' 'tests/spec.txt, tests/other.txt' '["src/a.rs"]' "$COMMIT"
cards_push "$WORK"
run "$WORK" --stage impl
ok=1
[ "$RC" = 0 ] || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "CARDS note card-list-unparsed verify card=$FEATURE/01" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "CARDS note card-list-unparsed spec-paths card=$FEATURE/01" || ok=0
printf '%s\n' "$OUT" | grep -Fq -- "PREFLIGHT OK stage=impl" || ok=0
refute "CARDS stop"
report "49-cards-list-unreadable" "$ok"

# --- 31. zero writes: not one run above moved HEAD, dirtied the tree, or wrote a file --
OUT="$ZW"; RC=0
if [ -z "$ZW" ]; then report "31-zero-writes" 1; else report "31-zero-writes" 0; fi

echo "---"
echo "$PASSED/$TOTAL cases passed${SKIPPED:+ ($SKIPPED skipped)}"
[ $((PASSED + SKIPPED)) = "$TOTAL" ] || exit 1
exit 0
