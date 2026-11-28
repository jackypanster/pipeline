#!/usr/bin/env bash
# Hermetic self-test for preflight.sh + check-plan.py (v2: 3-Agent 4-Step).
# Run: bash skills/pipeline-preflight/scripts/preflight-test.sh
# Also run under /bin/bash on macOS to verify bash-3.2 compatibility.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PREFLIGHT="$HERE/preflight.sh"
BASH_BIN="${BASH:-$(command -v bash)}"
FEATURE="demo-feature"
ZERO_SHA="0000000000000000000000000000000000000000"

ROOT="$(cd "$(mktemp -d "${TMPDIR:-/tmp}/preflight-test.XXXXXX")" && pwd -P)"
trap 'rm -rf "$ROOT"' EXIT

export HOME="$ROOT/home"
export XDG_CONFIG_HOME="$ROOT/home/.config"
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
for sk in grill-me think check hunt impl-skill; do
  mkdir -p "$SKILLS_FAKE/$sk"
  printf -- '---\nname: %s\n---\n' "$sk" > "$SKILLS_FAKE/$sk/SKILL.md"
done
export PIPELINE_SKILL_DIRS="$SKILLS_FAKE"

# Local bare upstream repo for UPSTREAM advisory checks
UPSTREAM_BARE="$ROOT/upstream.git"
UPSTREAM_SEED="$ROOT/upstream-seed"
git init --quiet --bare "$UPSTREAM_BARE"
git clone --quiet "$UPSTREAM_BARE" "$UPSTREAM_SEED" 2>/dev/null
echo seed > "$UPSTREAM_SEED/seed.txt"
git -C "$UPSTREAM_SEED" add -A
git -C "$UPSTREAM_SEED" commit --quiet -m "upstream seed"
git -C "$UPSTREAM_SEED" push --quiet origin main
export PIPELINE_UPSTREAM_URL="$UPSTREAM_BARE"

DEFAULT_ROLES='plan:        [grill-me, think]
plan-review: check
impl:        impl-skill
review:      check
hunt:        hunt
improve:     think'

DEFAULT_CONTROL='{ "schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct" }'

DEFAULT_PLAN='---
feature: demo-feature
planner_model: frontier-a
verify:
  - "true"
forbid-paths:
  - "secrets/"
---
## Baseline Evidence
- README.md:1
## Specifications
1. Demo spec
## Acceptance Matrix (T1-T10)
- T1: demo'

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
  printf '%s\n' '.env' '.env.local' '.envrc' > "$w/.gitignore"
  if [ -n "${FX_CURRENT_JSON:-}" ]; then
    printf '%s\n' "$FX_CURRENT_JSON" > "$w/.pipeline/current.json"
  elif [ "${FX_CURRENT:-yes}" = yes ]; then
    cat > "$w/.pipeline/current.json" <<JSON
{ "repo": "${FX_CUR_REPO:-$base/remote.git}", "branch": "main", "feature": "$FEATURE", "stage": "plan", "plan_rejects": 0, "review_rejects": 0 }
JSON
  fi
  printf '%s\n' "${FX_ROLES:-$DEFAULT_ROLES}" > "$w/.pipeline/roles.yaml"
  if [ "${FX_PLAN:-yes}" = yes ]; then
    printf '%s\n' "${FX_PLAN_BODY:-$DEFAULT_PLAN}" > "$w/.pipeline/$FEATURE/plan.md"
  fi
  if [ -n "${FX_JOURNAL:-}" ]; then
    printf '%s\n' "$FX_JOURNAL" > "$w/.pipeline/$FEATURE/journal.md"
  else
    nextline="${FX_NEXT_LINE:-Run pipeline-${FX_NEXT:-plan-review}.}"
    cat > "$w/.pipeline/$FEATURE/journal.md" <<JRN
## seq=1 · 2026-11-27T00:00:00Z · plan→plan-review · completed · by=planner
done: plan authored
output: .pipeline/$FEATURE/plan.md
--- handoff ---
>>> NEXT
$nextline
<<< END
JRN
  fi
  if [ "${FX_CONTROL:-yes}" = yes ]; then
    printf '%s\n' "${FX_CONTROL_JSON:-$DEFAULT_CONTROL}" > "$w/.pipeline/$FEATURE/control.json"
  fi
  git -C "$w" add -A
  git -C "$w" commit --quiet -m "fixture $name"
  if [ -z "${FX_CURRENT_JSON:-}" ] && [ "${FX_CURRENT:-yes}" = yes ] && [ "${FX_PLAN:-yes}" = yes ] && [ "${FX_PIN_BLOB:-yes}" = yes ]; then
    plan_blob="$(git -C "$w" rev-parse "HEAD:.pipeline/$FEATURE/plan.md")"
    cat > "$w/.pipeline/current.json" <<JSON
{ "repo": "${FX_CUR_REPO:-$base/remote.git}", "branch": "main", "feature": "$FEATURE", "stage": "plan", "plan_rejects": 0, "review_rejects": 0, "approved_plan_blob": "$plan_blob" }
JSON
    git -C "$w" add .pipeline/current.json
    git -C "$w" commit --quiet -m "pin approved_plan_blob"
  fi
  git -C "$w" push --quiet origin main
  WORK="$w"
  COMMIT="$(git -C "$w" rev-parse HEAD)"
}

fails=0
passed=0
run_case() {
  local name="$1" want="$2" expect_csv="$3"; shift 3
  local out rc=0 ok=1 tok
  set +e
  out="$("$BASH_BIN" "$PREFLIGHT" "$@" 2>&1)"
  rc=$?
  set -e
  if [ "$rc" != "$want" ]; then ok=0; fi
  if [ -n "$expect_csv" ]; then
    while IFS= read -r tok; do
      [ -n "$tok" ] || continue
      printf '%s\n' "$out" | grep -Fq -- "$tok" || ok=0
    done <<EXP_EOF
$(printf '%s' "$expect_csv" | tr '|' '\n')
EXP_EOF
  fi
  if [ "$ok" = 1 ]; then
    echo "PASS $name"
    passed=$((passed + 1))
  else
    echo "FAIL $name (rc=$rc want=$want)"
    printf '%s\n' "$out" | sed 's/^/  | /'
    fails=$((fails + 1))
  fi
}

# 1. Human-relay happy path for plan-review
build ok-plan-review
run_case 1-ok-plan-review 0 "PULL ok|ENV none|CURRENT ok|SLOT plan-review=check|INSTALLED check path=|GUARD n/a|PLAN ok feature=$FEATURE verify=1 forbid=1|PREFLIGHT OK stage=plan-review" \
  --stage plan-review --repo "$WORK"

# 2. plan stage with missing current.json succeeds
FX_CURRENT=no FX_PLAN=no build plan-bootstrap
run_case 2-plan-bootstrap 0 "CURRENT absent (plan creates it)|SLOT plan=grill-me,think|PREFLIGHT OK stage=plan" \
  --stage plan --repo "$WORK"
unset FX_CURRENT FX_PLAN

# 3. non-plan stage with missing current.json stops
FX_CURRENT=no build missing-current
run_case 3-missing-current 2 "PREFLIGHT STOP current.json-missing" \
  --stage impl --repo "$WORK"
unset FX_CURRENT

# 4. invalid stage name (old v1 prd/arch/task rejected as usage error 64)
build bad-stage
run_case 4-old-stage-rejected 64 "must be one of: plan plan-review impl review hunt" \
  --stage prd --repo "$WORK"

# 5. dotenv reports count only, never names or values
build dotenv-count
printf 'API_SECRET=supersecretvalue\nexport OTHER_KEY=123\n' > "$WORK/.env"
out_env="$("$BASH_BIN" "$PREFLIGHT" --stage plan --repo "$WORK" 2>&1)"
if printf '%s\n' "$out_env" | grep -Fq "ENV file=.env keys=2" && ! printf '%s\n' "$out_env" | grep -Eq "API_SECRET|supersecretvalue|OTHER_KEY"; then
  echo "PASS 5-dotenv-count-no-leak"; passed=$((passed + 1))
else
  echo "FAIL 5-dotenv-count-no-leak"; printf '%s\n' "$out_env" | sed 's/^/  | /'; fails=$((fails + 1))
fi

# 6. placeholder slot stops
FX_ROLES='impl: <autonomous-coding-skill>' build placeholder-slot
run_case 6-placeholder-slot 2 "PREFLIGHT STOP slot-placeholder impl=<autonomous-coding-skill>" \
  --stage impl --repo "$WORK"
unset FX_ROLES

# 7. invalid slot name (path traversal) stops
FX_ROLES='impl: ../outside' build traversal-slot
run_case 7-traversal-slot 2 "PREFLIGHT STOP slot-invalid-name impl=../outside" \
  --stage impl --repo "$WORK"
unset FX_ROLES

# 8. undeclared PIPELINE_SKILL_DIRS yields exit 3 UNVERIFIED install-check
build unverified-install
PIPELINE_SKILL_DIRS="" run_case 8-unverified-install 3 "PREFLIGHT UNVERIFIED install-check" \
  --stage review --repo "$WORK"

# 9. coordinated envelope happy path
FX_NEXT=plan-review build coord-ok
run_case 9-coord-ok 0 "GUARD ok seq=1 commit=$COMMIT|PLAN ok|PREFLIGHT OK stage=plan-review" \
  --stage plan-review --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$COMMIT"
unset FX_NEXT

# 10. partial envelope stops
build partial-env
run_case 10-partial-env 2 "PREFLIGHT STOP envelope-incomplete" \
  --stage impl --repo "$WORK" "repo=$WORK" "feature="

# 11. stale expected_seq stops
FX_NEXT=impl build stale-seq
run_case 11-stale-seq 2 "STALE_DISPATCH expected_seq observed=1 expected=99|PREFLIGHT STOP stale-dispatch" \
  --stage impl --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=99" "expected_commit=$COMMIT"
unset FX_NEXT

# 12. stale next command stops
FX_NEXT=plan-review build stale-next
run_case 12-stale-next 2 "STALE_DISPATCH next|PREFLIGHT STOP stale-dispatch" \
  --stage impl --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$COMMIT"
unset FX_NEXT

# 13. short expected_commit rejected
FX_NEXT=impl build short-sha
run_case 13-short-sha 2 "STALE_DISPATCH expected_commit observed=<short-sha-rejected>" \
  --stage impl --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=${COMMIT:0:7}"
unset FX_NEXT

# 14. missing control.json stops coordinated dispatch
FX_CONTROL=no FX_NEXT=impl build no-control
run_case 14-no-control 2 "STALE_DISPATCH control.json observed=<absent>" \
  --stage impl --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$COMMIT"
unset FX_CONTROL FX_NEXT

# 15. control.json with pause_after_plan boolean accepted; non-bool rejected
FX_CONTROL_JSON='{ "schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct", "pause_after_plan": true }' FX_NEXT=plan-review build pause-bool-ok
run_case 15a-pause-bool-ok 0 "GUARD ok|PREFLIGHT OK stage=plan-review" \
  --stage plan-review --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$COMMIT"
FX_CONTROL_JSON='{ "schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct", "pause_after_plan": "yes" }' FX_NEXT=plan-review build pause-bool-bad
run_case 15b-pause-bool-bad 2 "STALE_DISPATCH control.pause_after_plan" \
  --stage plan-review --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$COMMIT"
unset FX_CONTROL_JSON FX_NEXT

# 16. missing plan.md stops on impl/review/plan-review, advisory on hunt
FX_PLAN=no build missing-plan
run_case 16a-missing-plan-impl 2 "PLAN stop plan-missing|PREFLIGHT STOP plan-missing" \
  --stage impl --repo "$WORK"
run_case 16b-missing-plan-hunt 0 "PLAN advisory stage=hunt|PREFLIGHT OK stage=hunt" \
  --stage hunt --repo "$WORK"
unset FX_PLAN

# 16c. impl/review refuse unapproved plan (missing approved_plan_blob)
FX_PIN_BLOB=no build unapproved-plan
run_case 16c-unapproved-plan-impl 2 "PLAN stop plan-not-approved|PREFLIGHT STOP plan-not-approved" \
  --stage impl --repo "$WORK"
unset FX_PIN_BLOB

# 16d. modifying plan.md after approval (keeping approved handoff) STOPs both impl and review!
FX_NEXT=impl build post-approval-edit
printf '\n# sneaky verify edit\n' >> "$WORK/.pipeline/$FEATURE/plan.md"
git -C "$WORK" add ".pipeline/$FEATURE/plan.md"
git -C "$WORK" commit --quiet -m "edit plan after approval"
git -C "$WORK" push --quiet origin main
run_case 16d-modified-plan-impl 2 "PLAN stop plan-blob-mismatch|PREFLIGHT STOP plan-blob-mismatch" \
  --stage impl --repo "$WORK"
run_case 16e-modified-plan-review 2 "PLAN stop plan-blob-mismatch|PREFLIGHT STOP plan-blob-mismatch" \
  --stage review --repo "$WORK"
unset FX_NEXT

# 16f. uncommitted local edit to plan.md STOPs plan-review/impl/review (coordinated mode where HEAD==expected_commit skips pull)
FX_NEXT=plan-review build dirty-local-plan
printf '\n# uncommitted edit\n' >> "$WORK/.pipeline/$FEATURE/plan.md"
run_case 16f-dirty-local-plan 2 "PLAN stop plan-dirty-local|PREFLIGHT STOP plan-dirty-local" \
  --stage plan-review --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$COMMIT"
unset FX_NEXT

# 17. plan.md with empty verify stops
FX_PLAN_BODY='---
feature: demo-feature
verify: []
forbid-paths: []
---
body' build empty-verify
run_case 17-empty-verify 2 "PLAN stop plan-verify-empty|PREFLIGHT STOP plan-verify-empty" \
  --stage plan-review --repo "$WORK"
unset FX_PLAN_BODY

# 17b. plan.md with bare scalar verify stops
FX_PLAN_BODY='---
feature: demo-feature
verify: pytest
forbid-paths: []
---
body' build scalar-verify
run_case 17b-scalar-verify 2 "PLAN stop plan-verify-invalid|PREFLIGHT STOP plan-verify-invalid" \
  --stage plan-review --repo "$WORK"
unset FX_PLAN_BODY

# 17c. plan.md missing forbid-paths stops; empty list [] succeeds
FX_PLAN_BODY='---
feature: demo-feature
verify:
  - "true"
---
body' build missing-forbid
run_case 17c-missing-forbid 2 "PLAN stop plan-forbid-paths-missing|PREFLIGHT STOP plan-forbid-paths-missing" \
  --stage impl --repo "$WORK"
FX_PLAN_BODY='---
feature: demo-feature
verify:
  - "true"
forbid-paths: []
---
body' build empty-forbid-ok
run_case 17d-empty-forbid-ok 0 "PLAN ok feature=$FEATURE verify=1 forbid=0|PREFLIGHT OK stage=impl" \
  --stage impl --repo "$WORK"
unset FX_PLAN_BODY

# 17e. plan.md with bare `forbid-paths:` (no items, no []) stops; duplicate key stops
FX_PLAN_BODY='---
feature: demo-feature
verify:
  - "true"
forbid-paths:
---
body' build bare-forbid
run_case 17e-bare-forbid 2 "PLAN stop plan-forbid-paths-invalid|PREFLIGHT STOP plan-forbid-paths-invalid" \
  --stage impl --repo "$WORK"
FX_PLAN_BODY='---
feature: demo-feature
verify:
  - "true"
forbid-paths:
  - "secrets/"
forbid-paths: []
---
body' build dup-key
run_case 17f-dup-key 2 "PLAN stop plan-duplicate-key forbid-paths|PREFLIGHT STOP plan-duplicate-key" \
  --stage impl --repo "$WORK"
unset FX_PLAN_BODY

# 18. plan.md without frontmatter stops
FX_PLAN_BODY='# Plan without frontmatter' build no-frontmatter
run_case 18-no-frontmatter 2 "PLAN stop plan-no-frontmatter|PREFLIGHT STOP plan-no-frontmatter" \
  --stage review --repo "$WORK"
unset FX_PLAN_BODY

# 19. negative plan_rejects in current.json rejected
FX_CURRENT_JSON="{ \"repo\": \"$ROOT/fx-bad-rejects/remote.git\", \"branch\": \"main\", \"feature\": \"$FEATURE\", \"stage\": \"plan\", \"plan_rejects\": -1 }" build bad-rejects
run_case 19-bad-rejects 2 "PREFLIGHT STOP current.json-invalid-field plan_rejects" \
  --stage plan-review --repo "$WORK"
unset FX_CURRENT_JSON

# 20. zero-writes proof inside target repo (and stale dispatch on behind checkout does NOT advance HEAD)
build zero-writes
before_head="$(git -C "$WORK" rev-parse HEAD)"
"$BASH_BIN" "$PREFLIGHT" --stage review --repo "$WORK" >/dev/null
after_head="$(git -C "$WORK" rev-parse HEAD)"
dirty="$(git -C "$WORK" status --porcelain)"
if [ "$before_head" = "$after_head" ] && [ -z "$dirty" ]; then
  echo "PASS 20a-zero-writes"; passed=$((passed + 1))
else
  echo "FAIL 20a-zero-writes (head=$after_head dirty=$dirty)"; fails=$((fails + 1))
fi

# Advance remote via a second clone so $WORK is behind; pass stale expected_commit=$before_head
OTHER="$ROOT/fx-zero-writes/other"
git clone --quiet "$ROOT/fx-zero-writes/remote.git" "$OTHER" 2>/dev/null
echo advanced > "$OTHER/advanced.txt"
git -C "$OTHER" add advanced.txt
git -C "$OTHER" commit --quiet -m "remote advance"
git -C "$OTHER" push --quiet origin main
set +e
stale_out="$("$BASH_BIN" "$PREFLIGHT" --stage plan-review --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$before_head" 2>&1)"
stale_rc=$?
set -e
still_head="$(git -C "$WORK" rev-parse HEAD)"
if [ "$stale_rc" = 2 ] && [ "$still_head" = "$before_head" ] && printf '%s\n' "$stale_out" | grep -Fq "STALE_DISPATCH expected_commit"; then
  echo "PASS 20b-stale-dispatch-zero-pull-mutation"; passed=$((passed + 1))
else
  echo "FAIL 20b-stale-dispatch-zero-pull-mutation (rc=$stale_rc head=$still_head want=$before_head)"
  printf '%s\n' "$stale_out" | sed 's/^/  | /'
  fails=$((fails + 1))
fi

# 20c. remote switches active feature in current.json while old feature's journal/control stay intact;
# behind checkout with envelope for old feature MUST stop STALE_DISPATCH feature with ZERO HEAD move!
cat > "$OTHER/.pipeline/current.json" <<JSON
{ "repo": "$ROOT/fx-zero-writes/remote.git", "branch": "main", "feature": "other-feature", "stage": "plan" }
JSON
git -C "$OTHER" add .pipeline/current.json
git -C "$OTHER" commit --quiet -m "switch active feature to other-feature"
git -C "$OTHER" push --quiet origin main
switched_sha="$(git -C "$OTHER" rev-parse HEAD)"
set +e
stale_feat_out="$("$BASH_BIN" "$PREFLIGHT" --stage plan-review --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$switched_sha" 2>&1)"
stale_feat_rc=$?
set -e
still_head2="$(git -C "$WORK" rev-parse HEAD)"
if [ "$stale_feat_rc" = 2 ] && [ "$still_head2" = "$before_head" ] && printf '%s\n' "$stale_feat_out" | grep -Fq "STALE_DISPATCH feature observed=other-feature expected=$FEATURE"; then
  echo "PASS 20c-stale-feature-zero-pull-mutation"; passed=$((passed + 1))
else
  echo "FAIL 20c-stale-feature-zero-pull-mutation (rc=$stale_feat_rc head=$still_head2 want=$before_head)"
  printf '%s\n' "$stale_feat_out" | sed 's/^/  | /'
  fails=$((fails + 1))
fi

# 20d. local uncommitted edit to .pipeline/current.json changes feature while remote advances another file;
# behind checkout MUST stop STALE_DISPATCH feature with ZERO HEAD move!
FX_NEXT=plan-review build dirty-local-cur
base_head_d="$(git -C "$WORK" rev-parse HEAD)"
OTHER_D="$ROOT/fx-dirty-local-cur/other"
git clone --quiet "$ROOT/fx-dirty-local-cur/remote.git" "$OTHER_D" 2>/dev/null
echo other-change > "$OTHER_D/unrelated.txt"
git -C "$OTHER_D" add unrelated.txt
git -C "$OTHER_D" commit --quiet -m "unrelated remote advance"
git -C "$OTHER_D" push --quiet origin main
adv_sha_d="$(git -C "$OTHER_D" rev-parse HEAD)"
cat > "$WORK/.pipeline/current.json" <<JSON
{ "repo": "$ROOT/fx-dirty-local-cur/remote.git", "branch": "main", "feature": "local-edited-feat", "stage": "plan" }
JSON
git -C "$WORK" update-index --assume-unchanged .pipeline/current.json
set +e
dirty_out="$("$BASH_BIN" "$PREFLIGHT" --stage plan-review --repo "$WORK" \
  "repo=$WORK" "branch=main" "feature=$FEATURE" "expected_seq=1" "expected_commit=$adv_sha_d" 2>&1)"
dirty_rc=$?
set -e
still_head_d="$(git -C "$WORK" rev-parse HEAD)"
if [ "$dirty_rc" = 2 ] && [ "$still_head_d" = "$base_head_d" ] && printf '%s\n' "$dirty_out" | grep -Fq "STALE_DISPATCH feature observed=local-edited-feat expected=$FEATURE"; then
  echo "PASS 20d-dirty-local-current-assume-unchanged-zero-pull-mutation"; passed=$((passed + 1))
else
  echo "FAIL 20d-dirty-local-current-assume-unchanged-zero-pull-mutation (rc=$dirty_rc head=$still_head_d want=$base_head_d)"
  printf '%s\n' "$dirty_out" | sed 's/^/  | /'
  fails=$((fails + 1))
fi
unset FX_NEXT

echo "---"
echo "$passed/$((passed + fails)) cases passed ($fails failed)"
[ "$fails" = 0 ]
