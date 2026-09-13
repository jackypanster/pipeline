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
#     is the standing proof that preflight.sh writes no files (final case). The one sanctioned
#     exception is CONTRACT step 1's own `git pull` advancing HEAD — a case opts into it with
#     ZW_EXPECT_HEAD and must then land exactly on the tip it pushed.
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

# --- 31. zero writes: not one run above moved HEAD, dirtied the tree, or wrote a file --
OUT="$ZW"; RC=0
if [ -z "$ZW" ]; then report "31-zero-writes" 1; else report "31-zero-writes" 0; fi

echo "---"
echo "$PASSED/$TOTAL cases passed${SKIPPED:+ ($SKIPPED skipped)}"
[ $((PASSED + SKIPPED)) = "$TOTAL" ] || exit 1
exit 0
