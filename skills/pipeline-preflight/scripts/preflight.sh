#!/usr/bin/env bash
# pipeline-preflight — deterministic executor for CONTRACT v2 (3-Agent 4-Step) shim steps 1, 3, 4
# plus coordinated-mode pre-write stale-dispatch guard and plan.md frontmatter verification.
#
# Usage:
#   preflight.sh --stage <plan|plan-review|impl|review|hunt> [--repo <path>] \
#                [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]
#
# Exit codes:
#   0   PREFLIGHT OK           — steps 1/3/4 (+ guard + plan check) all passed
#   2   PREFLIGHT STOP <why>   — contract violation (incl. STALE_DISPATCH); zero writes on stale
#   3   PREFLIGHT UNVERIFIED <check>[,<check>]
#                              — everything ELSE passed; caller checks named item(s) itself
#   64  usage error (message on stderr)
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: preflight.sh --stage <plan|plan-review|impl|review|hunt> [--repo <path>]
                    [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]

The five k=v envelope fields are all-or-nothing (a partial envelope is a STOP, not a default).
EOF
  exit 64
}

stage=""
opt_repo=""
env_repo=""; env_branch=""; env_feature=""; env_seq=""; env_commit=""
has_repo=0; has_branch=0; has_feature=0; has_seq=0; has_commit=0

while [ $# -gt 0 ]; do
  case "$1" in
    --stage)           [ $# -ge 2 ] || usage; stage="$2"; shift 2 ;;
    --stage=*)         stage="${1#--stage=}"; shift ;;
    --repo)            [ $# -ge 2 ] || usage; opt_repo="$2"; shift 2 ;;
    --repo=*)          opt_repo="${1#--repo=}"; shift ;;
    -h|--help)         usage ;;
    repo=*)            env_repo="${1#repo=}";              has_repo=1;    shift ;;
    branch=*)          env_branch="${1#branch=}";          has_branch=1;  shift ;;
    feature=*)         env_feature="${1#feature=}";        has_feature=1; shift ;;
    expected_seq=*)    env_seq="${1#expected_seq=}";       has_seq=1;     shift ;;
    expected_commit=*) env_commit="${1#expected_commit=}"; has_commit=1;  shift ;;
    *) printf 'preflight.sh: unknown argument: %s\n' "$1" >&2; usage ;;
  esac
done

case "$stage" in
  plan|plan-review|impl|review|hunt) ;;
  *) printf 'preflight.sh: --stage must be one of: plan plan-review impl review hunt\n' >&2; usage ;;
esac

envelope=0
missing=""
if [ $((has_repo + has_branch + has_feature + has_seq + has_commit)) -gt 0 ]; then
  envelope=1
  if [ "$has_repo" != 1 ]    || [ -z "$env_repo" ];    then missing="${missing:+$missing,}repo"; fi
  if [ "$has_branch" != 1 ]  || [ -z "$env_branch" ];  then missing="${missing:+$missing,}branch"; fi
  if [ "$has_feature" != 1 ] || [ -z "$env_feature" ]; then missing="${missing:+$missing,}feature"; fi
  if [ "$has_seq" != 1 ]     || [ -z "$env_seq" ];     then missing="${missing:+$missing,}expected_seq"; fi
  if [ "$has_commit" != 1 ]  || [ -z "$env_commit" ];  then missing="${missing:+$missing,}expected_commit"; fi
  if [ -n "$missing" ]; then
    echo "PREFLIGHT STOP envelope-incomplete missing=$missing"
    exit 2
  fi
fi

if [ -n "$opt_repo" ]; then
  repo="$(cd "$opt_repo" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)"
else
  repo="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [ -z "$repo" ]; then
  echo "PREFLIGHT STOP not-a-git-repo"
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "PREFLIGHT STOP python3-missing"
  exit 2
fi

unverified=""
mark_unverified() {
  case ",$unverified," in *",$1,"*) return 0 ;; esac
  unverified="${unverified:+$unverified,}$1"
}

stop() {
  echo "PREFLIGHT STOP $1"
  exit 2
}

stale() {
  echo "STALE_DISPATCH $1 observed=$2 expected=$3"
  echo "PREFLIGHT STOP stale-dispatch"
  exit 2
}

trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# --------------------------- 1. Pre-write envelope check (BEFORE any pull/mutation) OR human pull
# When an envelope is present, verify repo/branch/expected_commit against the remote via read-only
# `git fetch` BEFORE advancing HEAD, so a stale dispatch leaves the working tree completely untouched.
guard_remote=""
if [ "$envelope" = 1 ]; then
  tgt_phys="$(cd "$repo" && pwd -P)"
  env_phys="$(cd "$env_repo" 2>/dev/null && pwd -P || true)"
  [ -n "$env_phys" ] || env_phys="<unreadable>"
  [ "$env_phys" = "$tgt_phys" ] || stale "repo" "$env_phys" "$tgt_phys"

  cur_br="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  [ -n "$cur_br" ] || cur_br="<absent>"
  [ "$cur_br" = "$env_branch" ] || stale "branch" "$cur_br" "$env_branch"

  if ! printf '%s' "$env_commit" | grep -Eq '^[0-9a-f]{40}$'; then
    stale "expected_commit" "<short-sha-rejected>" "$env_commit"
  fi

  remote="$(git -C "$repo" config "branch.$env_branch.remote" 2>/dev/null || true)"
  [ -n "$remote" ] || remote="origin"

  fetch_err=""
  if ! fetch_err="$(git -C "$repo" fetch --quiet "$remote" "$env_branch" 2>&1 >/dev/null)"; then
    echo "FETCH fail $remote/$env_branch"
    if [ -n "$fetch_err" ]; then printf '%s\n' "$fetch_err"; fi
    stop "fetch-failed"
  fi
  remote_sha="$(git -C "$repo" rev-parse FETCH_HEAD 2>/dev/null || true)"
  [ -n "$remote_sha" ] || remote_sha="<absent>"
  [ "$remote_sha" = "$env_commit" ] || stale "expected_commit" "$remote_sha" "$env_commit"

  # Read current.json + control.json + journal from the LOCKED expected_commit BEFORE touching HEAD!
  if cur_raw="$(git -C "$repo" show "$env_commit:.pipeline/current.json" 2>/dev/null)"; then
    locked_feat="$(printf '%s' "$cur_raw" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    f = d.get("feature") if isinstance(d, dict) else None
    print(f if isinstance(f, str) and f.strip() and f == f.strip() else "<invalid>")
except Exception:
    print("<invalid>")
')"
    [ "$locked_feat" = "$env_feature" ] || stale "feature" "$locked_feat" "$env_feature"
  else
    stale "feature" "<absent>" "$env_feature"
  fi
  # Also check working-tree .pipeline/current.json directly against HEAD blob (catches assume-unchanged too) BEFORE touching HEAD!
  if [ -f "$repo/.pipeline/current.json" ]; then
    wt_differs="$(python3 - "$repo" <<'PY'
import subprocess, sys
repo = sys.argv[1]
wt = open(repo + "/.pipeline/current.json", "rb").read()
r = subprocess.run(["git", "-C", repo, "show", "HEAD:.pipeline/current.json"], capture_output=True)
print("1" if r.returncode != 0 or r.stdout != wt else "0")
PY
)"
    if [ "$wt_differs" = 1 ]; then
      wt_feat="$(python3 -c 'import json,sys
try:
    d = json.load(open(sys.argv[1]))
    f = d.get("feature") if isinstance(d, dict) else None
    print(f if isinstance(f, str) and f.strip() and f == f.strip() else "<invalid>")
except Exception:
    print("<invalid>")
' "$repo/.pipeline/current.json")"
      [ "$wt_feat" = "$env_feature" ] || stale "feature" "$wt_feat" "$env_feature"
    fi
  elif git -C "$repo" cat-file -e "HEAD:.pipeline/current.json" 2>/dev/null; then
    stale "feature" "<absent>" "$env_feature"
  fi

  PY_CONTROL='import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("INVALID"); raise SystemExit(0)
if not isinstance(d, dict):
    print("INVALID"); raise SystemExit(0)
want = (("schema_version", 1), ("mode", "coordinated"), ("merge_gate", "human-direct"))
for k, exp in want:
    if k not in d:
        print("FIELD"); print(k); print("<absent>"); print(exp); raise SystemExit(0)
    v = d[k]
    if k == "schema_version":
        ok = isinstance(v, int) and not isinstance(v, bool) and v == exp
    else:
        ok = v == exp
    if not ok:
        print("FIELD"); print(k)
        print(v if isinstance(v, str) else json.dumps(v))
        print(exp); raise SystemExit(0)
if "pause_after_plan" in d and not isinstance(d["pause_after_plan"], bool):
    print("FIELD"); print("pause_after_plan"); print(json.dumps(d["pause_after_plan"])); print("bool"); raise SystemExit(0)
print("OK")
'
  if control_raw="$(git -C "$repo" show "$env_commit:.pipeline/$env_feature/control.json" 2>/dev/null)"; then
    control_out="$(printf '%s' "$control_raw" | python3 -c "$PY_CONTROL")"
    case "$control_out" in
      OK)      ;;
      INVALID) stale "control.json" "<invalid>" "present" ;;
      FIELD*)
        cf="$(printf '%s\n' "$control_out" | sed -n '2p')"
        cobs="$(printf '%s\n' "$control_out" | sed -n '3p')"
        cexp="$(printf '%s\n' "$control_out" | sed -n '4p')"
        stale "control.$cf" "$cobs" "$cexp"
        ;;
      *)       stale "control.json" "<invalid>" "present" ;;
    esac
  else
    stale "control.json" "<absent>" "present"
  fi

  j_raw=""
  if ! j_raw="$(git -C "$repo" show "$env_commit:.pipeline/$env_feature/journal.md" 2>/dev/null)"; then
    stale "expected_seq" "<absent>" "$env_seq"
  fi
  obs_seq="$(printf '%s\n' "$j_raw" | grep '^## seq=' | tail -1 | sed -E 's/^## seq=([0-9]+).*/\1/' || true)"
  [ -n "$obs_seq" ] || obs_seq="<absent>"
  [ "$obs_seq" = "$env_seq" ] || stale "expected_seq" "$obs_seq" "$env_seq"

  obs_next="<absent-handoff-in-tail>"
  next_tok=""
  cand="$(printf '%s\n' "$j_raw" | awk '
    /^## seq=/ { last = NR }
    { lines[NR] = $0 }
    END {
      if (!last) exit
      state = 0
      for (i = last; i <= NR; i++) {
        line = lines[i]; sub(/[ \t\r]+$/, "", line)
        if (state == 0) { if (line == "--- handoff ---") state = 1; continue }
        if (state == 1) { if (line == "") continue; if (line != ">>> NEXT") exit; state = 2; continue }
        if (state == 2) { if (line == "") continue; print line; exit }
      }
    }
  ')"
  if [ -n "$cand" ]; then
    obs_next="$cand"
    next_tok="$(printf '%s' "$cand" | sed -E \
      -e 's#^[[:space:]]+##' -e 's#^[/$]##' -e 's#^Run[[:space:]]+##' -e 's#[[:space:]].*$##' -e 's#\.$##')"
  fi
  if [ "$next_tok" != "pipeline-$stage" ]; then
    stale "next" "${obs_next:0:80}" "pipeline-$stage"
  fi

  # Now safe to sync local HEAD to the verified expected_commit (ff-only; never rewrite local commits)
  head_sha="$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)"
  if [ "$head_sha" = "$env_commit" ]; then
    echo "PULL ok head=$head_sha"
  elif git -C "$repo" merge-base --is-ancestor "$head_sha" "$env_commit" 2>/dev/null; then
    if pull_err="$(git -C "$repo" merge --ff-only --quiet "$env_commit" 2>&1 >/dev/null)"; then
      echo "PULL ok head=$(git -C "$repo" rev-parse HEAD)"
    else
      echo "PULL fail"
      if [ -n "$pull_err" ]; then printf '%s\n' "$pull_err"; fi
      stop "pull-failed"
    fi
  else
    stale "expected_commit" "$head_sha" "$env_commit"
  fi
else
  pull_err=""
  if pull_err="$(git -C "$repo" pull --rebase --quiet 2>&1 >/dev/null)"; then
    echo "PULL ok head=$(git -C "$repo" rev-parse HEAD)"
  else
    echo "PULL fail"
    if [ -n "$pull_err" ]; then printf '%s\n' "$pull_err"; fi
    stop "pull-failed"
  fi
fi

# -------------------------------------- 1b. advisory upstream freshness (called AFTER guard)
UPSTREAM_URL="${PIPELINE_UPSTREAM_URL:-https://github.com/jackypanster/pipeline.git}"
UPSTREAM_REMOTE_RE='(^|[@/])github\.com[:/]jackypanster/pipeline(\.git)?/?$'
upstream_check() {
  local self_dir top installed cache now c_epoch c_sha c_rest cached remote suffix
  self_dir="$(cd -P "$(dirname "$0")" && pwd -P)"
  installed=""
  top="$(git -C "$self_dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$top" ] && git -C "$top" remote get-url origin 2>/dev/null | grep -Eq "$UPSTREAM_REMOTE_RE"; then
    installed="$(git -C "$top" rev-parse HEAD 2>/dev/null || true)"
  fi
  if [ -z "$installed" ]; then
    echo "UPSTREAM unverified not-a-clone"
    return 0
  fi

  cache="${XDG_CACHE_HOME:-$HOME/.cache}/pipeline/upstream-head"
  now="$(date +%s)"
  cached=0
  remote=""
  if [ -r "$cache" ]; then
    c_epoch=""; c_sha=""; c_rest=""
    read -r c_epoch c_sha c_rest < "$cache" 2>/dev/null || true
    case "$c_epoch" in
      ''|*[!0-9]*) c_epoch="" ;;
    esac
    if [ -n "$c_epoch" ] && [ $((now - c_epoch)) -lt 86400 ]; then
      remote="$c_sha"; cached=1
    fi
  fi
  if [ "$cached" = 0 ]; then
    remote="$(GIT_TERMINAL_PROMPT=0 python3 - "$UPSTREAM_URL" <<'PY'
import subprocess, sys
try:
    r = subprocess.run(["git", "ls-remote", sys.argv[1], "HEAD"], capture_output=True, text=True, timeout=8)
    out = r.stdout.split()
    print(out[0] if r.returncode == 0 and out else "")
except Exception:
    print("")
PY
)"
    if mkdir -p "${cache%/*}" 2>/dev/null \
       && printf '%s\n' "$now $remote" > "$cache.tmp.$$" 2>/dev/null; then
      mv -f "$cache.tmp.$$" "$cache" 2>/dev/null || rm -f "$cache.tmp.$$" 2>/dev/null || true
    fi
  fi
  if [ "$cached" = 1 ]; then suffix=" cached"; else suffix=""; fi
  if [ -z "$remote" ]; then
    echo "UPSTREAM unverified network"
  elif [ "$remote" = "$installed" ]; then
    echo "UPSTREAM ok head=$remote$suffix"
  elif git -C "$top" cat-file -e "$remote" 2>/dev/null \
       && git -C "$top" merge-base --is-ancestor "$remote" HEAD 2>/dev/null; then
    echo "UPSTREAM ok head=$remote$suffix"
  else
    echo "UPSTREAM newer head=$remote installed=$installed run=pipeline-update$suffix"
  fi
  return 0
}

# -------------------------------------------- 2. CONTRACT step 2: dotenv DETECTION only
env_found=0
for f in .env .env.local .envrc; do
  [ -f "$repo/$f" ] || continue
  env_found=1
  keys="$(grep -cE '^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*=' "$repo/$f" || true)"
  keys="$(printf '%s' "$keys" | tr -cd '0-9')"
  echo "ENV file=$f keys=${keys:-0}"
done
[ "$env_found" = 1 ] || echo "ENV none"

# ------------------------------------------------- 3. CONTRACT step 3: .pipeline/current.json
PY_CURRENT='import json, sys
try:
    with open(sys.argv[1]) as fh:
        d = json.load(fh)
except Exception:
    print("INVALID"); raise SystemExit(0)
if not isinstance(d, dict):
    print("INVALID"); raise SystemExit(0)
KEYS = ("repo", "branch", "feature", "stage")
for k in KEYS:
    if k not in d:
        print("MISSING " + k); raise SystemExit(0)
    v = d[k]
    if not isinstance(v, str) or not v.strip() or v != v.strip():
        print("BADFIELD " + k); raise SystemExit(0)
for ck in ("plan_rejects", "review_rejects"):
    if ck in d and d[ck] is not None:
        cv = d[ck]
        if not isinstance(cv, int) or isinstance(cv, bool) or cv < 0:
            print("BADFIELD " + ck); raise SystemExit(0)
print("OK")
for k in KEYS:
    print(d[k])
pr = d.get("pr")
if pr is None or (isinstance(pr, str) and not pr.strip()):
    print("")
elif isinstance(pr, str):
    print(pr.strip())
else:
    print(json.dumps(pr))
'

current="$repo/.pipeline/current.json"
cur_present=0
cur_repo=""
cur_feature=""
if [ -f "$current" ]; then
  cur_present=1
  cur_out="$(python3 -c "$PY_CURRENT" "$current")"
  cur_head="$(printf '%s\n' "$cur_out" | sed -n '1p')"
  case "$cur_head" in
    INVALID)      stop "current.json-invalid" ;;
    "MISSING "*)  stop "current.json-missing-field ${cur_head#MISSING }" ;;
    "BADFIELD "*) stop "current.json-invalid-field ${cur_head#BADFIELD }" ;;
  esac
  cur_repo="$(printf '%s\n' "$cur_out" | sed -n '2p')"
  cur_branch="$(printf '%s\n' "$cur_out" | sed -n '3p')"
  cur_feature="$(printf '%s\n' "$cur_out" | sed -n '4p')"
  cur_stage="$(printf '%s\n' "$cur_out" | sed -n '5p')"
  cur_pr="$(printf '%s\n' "$cur_out" | sed -n '6p')"
  echo "CURRENT ok repo=$cur_repo branch=$cur_branch feature=$cur_feature stage=$cur_stage${cur_pr:+ pr=$cur_pr}"
elif [ "$stage" = "plan" ]; then
  echo "CURRENT absent (plan creates it)"
else
  stop "current.json-missing"
fi

# ------------------------------------ 4. CONTRACT step 4: resolve the slot, verify installed
roles="$repo/.pipeline/roles.yaml"
[ -f "$roles" ] || stop "roles.yaml-missing"

slot_line="$(grep -m1 "^${stage}:" "$roles" || true)"
slot_raw="${slot_line#*:}"
slot_raw="${slot_raw%%#*}"
slot_raw="$(printf '%s' "$slot_raw" | tr -d '[]')"
slot_raw="$(trim "$slot_raw")"
[ -n "$slot_raw" ] || stop "slot-unset $stage"

names=""
while IFS= read -r p; do
  n="$(trim "$p")"
  [ -n "$n" ] || continue
  case "$n" in
    \<*\>) stop "slot-placeholder $stage=$n" ;;
  esac
  printf '%s' "$n" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]*$' || stop "slot-invalid-name $stage=$n"
  names="${names:+$names
}$n"
done <<SLOT_EOF
$(printf '%s' "$slot_raw" | tr ',' '\n')
SLOT_EOF
[ -n "$names" ] || stop "slot-unset $stage"

echo "SLOT $stage=$(printf '%s\n' "$names" | paste -sd, -)"

declared_dirs=""
if [ -n "${PIPELINE_SKILL_DIRS:-}" ]; then
  declared_dirs="$(printf '%s' "$PIPELINE_SKILL_DIRS" | tr ':' '\n' | grep -v '^$' || true)"
fi
default_dirs="$repo/.claude/skills
$repo/.agents/skills
$HOME/.agents/skills
$HOME/.claude/skills
$HOME/.pi/agent/skills
$HOME/.codex/skills"
all_dirs="${declared_dirs:+$declared_dirs
}$default_dirs"
searched="$(printf '%s\n' "$all_dirs" | paste -sd: -)"

find_skill() {
  local d
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    if [ -r "$d/$2/SKILL.md" ]; then printf '%s' "$d/$2"; return 0; fi
  done <<FIND_EOF
$1
FIND_EOF
  printf ''
}

while IFS= read -r n; do
  [ -n "$n" ] || continue
  hit=""
  if [ -n "$declared_dirs" ]; then hit="$(find_skill "$declared_dirs" "$n")"; fi
  if [ -n "$hit" ]; then
    echo "INSTALLED $n path=$hit"
    continue
  fi
  evidence="$(find_skill "$all_dirs" "$n")"
  if [ -n "$evidence" ]; then
    if [ -n "$declared_dirs" ]; then
      echo "INSTALLED $n found=$evidence UNVERIFIED (outside PIPELINE_SKILL_DIRS)"
    else
      echo "INSTALLED $n found=$evidence UNVERIFIED (set PIPELINE_SKILL_DIRS to declare this runtime's skill dirs)"
    fi
  else
    echo "INSTALLED $n UNVERIFIED searched=$searched"
  fi
  mark_unverified install-check
done <<NAMES_EOF
$names
NAMES_EOF

# ---------------------------- 5. Finalize envelope guard (feature + remote identity)
if [ "$envelope" = 0 ]; then
  echo "GUARD n/a (human-relay)"
else
  if [ "$cur_present" = 1 ]; then obs_feature="$cur_feature"; else obs_feature="<absent>"; fi
  [ "$obs_feature" = "$env_feature" ] || stale "feature" "$obs_feature" "$env_feature"

  obs_remote="$(git -C "$repo" ls-remote --get-url "$remote" 2>/dev/null; printf x)"
  obs_remote="${obs_remote%x}"; obs_remote="${obs_remote%$'\n'}"
  [ -n "$obs_remote" ] || obs_remote="<absent>"
  if [ "$obs_remote" != "<absent>" ] && [ "$obs_remote" = "$cur_repo" ]; then
    guard_remote=" remote=$obs_remote"
  else
    echo "REMOTE unverified observed=$obs_remote current.json.repo=${cur_repo:-<absent>}"
    mark_unverified remote-identity
  fi
  echo "GUARD ok seq=$env_seq commit=$env_commit$guard_remote"
fi

# -------------------------------- 5b. plan.md frontmatter check (mandatory on plan-review/impl/review)
case "$stage" in
  plan-review|impl|review|hunt)
    if [ -n "$cur_feature" ]; then
      plan_script="$(cd -P "$(dirname "$0")" && pwd -P)/check-plan.py"
      if [ ! -r "$plan_script" ]; then
        if [ "$stage" = "hunt" ]; then
          echo "PLAN advisory stage=hunt missing-checker"
        else
          stop "plan-check-missing"
        fi
      else
        plan_rc=0
        plan_commit="HEAD"
        if [ "$envelope" = 1 ]; then plan_commit="$env_commit"; fi
        plan_out="$(python3 "$plan_script" --repo "$repo" --feature "$cur_feature" \
                      --stage "$stage" --commit "$plan_commit" 2>&1)" || plan_rc=$?
        if [ -n "$plan_out" ]; then printf '%s\n' "$plan_out"; fi
        if [ "$plan_rc" = 2 ]; then
          plan_reason="$(printf '%s\n' "$plan_out" | sed -n 's/^PLAN stop //p' | sed -n 1p)"
          stop "${plan_reason:-plan-check-failed}"
        elif [ "$plan_rc" != 0 ]; then
          if [ "$stage" = "hunt" ]; then
            echo "PLAN unchecked rc=$plan_rc"
          else
            stop "plan-check-error rc=$plan_rc"
          fi
        fi
      fi
    fi
    ;;
esac

# -------------------------------- 5c. advisory upstream freshness (post-guard only)
upstream_check || true

# ------------------------------------------------------------------------ 6. final verdict
if [ -n "$unverified" ]; then
  echo "PREFLIGHT UNVERIFIED $unverified"
  exit 3
fi
echo "PREFLIGHT OK stage=$stage"
exit 0
