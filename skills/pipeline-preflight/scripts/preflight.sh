#!/usr/bin/env bash
# pipeline-preflight — the deterministic executor for CONTRACT shim-loop steps 1, 3, 4
# plus the coordinated-mode pre-write stale-dispatch guard.
#
# These steps run HERE under `set -euo pipefail`, never as LLM prose. Prose "read roles.yaml
# and verify the skill is installed" is re-derived by a different model on every run: it can
# invent a slot, skip the fetch, or narrate a guard it never executed. This script cannot —
# every check either prints its one line or STOPs with a machine-readable reason.
#
# NOT a pipeline stage and NOT a `roles.yaml` slot: a stage skill runs it as its step 0.
# It is the executor, not the spec — CONTRACT's prose steps 1–4 remain the spec AND the
# fallback (script absent, exit 4, or the checks named by exit 3 ⇒ the caller does that part
# as written).
#
# Usage:
#   preflight.sh --stage <prd|arch|task|impl|review|hunt> [--repo <path>] \
#                [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]
#
#   The five k=v fields are the coordinator's dispatch envelope (CONTRACT §Dispatch envelope):
#   pass ALL five or NONE. No envelope = human-relay = the stale-dispatch guard does not apply.
#   Presence is decided by the ARGUMENT, not by its value: `feature=` counts as passed-and-empty,
#   which is an incomplete envelope (STOP), never a silent downgrade to human-relay.
#
# Exit codes:
#   0   PREFLIGHT OK           — steps 1/3/4 (+ the guard, when an envelope is present) all passed
#   2   PREFLIGHT STOP <why>   — contract violation (includes STALE_DISPATCH); the caller STOPs
#   3   PREFLIGHT UNVERIFIED <check>[,<check>]
#                              — everything ELSE passed; the caller performs the NAMED check(s)
#                                itself and STOPs on failure. Checks: `install-check`,
#                                `remote-identity`.
#   4   PREFLIGHT SKIPPED <why>
#                              — the script could not run (python3 missing): NOTHING was executed
#                                and nothing was mutated; the caller runs steps 1–4 as written,
#                                exactly as if this script were absent.
#   64  usage error (message on stderr)
#
# Output grammar — one line per check on stdout, greppable, values unquoted:
#   PULL ok head=<sha>                     | PULL fail  (+ git's stderr)
#   UPSTREAM ok head=<sha>[ cached]                       # installed == pipeline main (or ahead of it)
#   UPSTREAM newer head=<sha> installed=<sha> run=pipeline-update[ cached]
#   UPSTREAM unverified <no-install-stamp|network>        # advisory only — never affects exit code
#   ENV file=<name> keys=<N>               # a COUNT only — never a key name, never a value
#   ENV none
#   CURRENT ok repo=<v> branch=<v> feature=<v> stage=<v>[ pr=<v>] | CURRENT absent (prd creates it)
#   SLOT <stage>=<name>[,<name>…]
#   INSTALLED <name> path=<dir>/<name>                  # verified: a PIPELINE_SKILL_DIRS hit
#   INSTALLED <name> found=<dir>/<name> UNVERIFIED (…)  # evidence only — this runtime undeclared
#   INSTALLED <name> UNVERIFIED searched=<d1>:<d2>:…    # nothing anywhere
#   FETCH fail <remote>/<branch>           (+ git's stderr)
#   REMOTE unverified observed=<v> current.json.repo=<v>
#   GUARD ok seq=<n> commit=<sha>[ remote=<url>]      | GUARD n/a (human-relay)
#   STALE_DISPATCH <field> observed=<v> expected=<v>
#   PREFLIGHT OK stage=<s> | PREFLIGHT STOP <reason> | PREFLIGHT UNVERIFIED <checks>
#   PREFLIGHT SKIPPED <reason>
#
# Writes nothing inside any repo or skill dir. Its only write is the upstream-check throttle stamp
# ${XDG_CACHE_HOME:-$HOME/.cache}/pipeline/upstream-head (once per 24h; see `upstream_check`). The
# only checkout mutations are `git pull --rebase` (CONTRACT step 1) and the guard's `git fetch` —
# both are the contract's own first act. It never resolves a conflict: a failed pull is a STOP for
# the human, not a merge decision. A failed fetch is likewise a STOP: comparing a cached remote-tracking
# ref after a fetch that never reached the network would turn a stale dispatch into a false GUARD ok.
#
# Deps: git, python3 (stdlib json only), coreutils. Self-contained — sources nothing.
# Self-test: bash "$(dirname "$0")/preflight-test.sh"
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: preflight.sh --stage <prd|arch|task|impl|review|hunt> [--repo <path>]
                    [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]

The five k=v envelope fields are all-or-nothing (a partial envelope is a STOP, not a default).
An argument that is PRESENT but empty (`feature=`) counts as present — and incomplete.
EOF
  exit 64
}

stage=""
opt_repo=""
env_repo=""; env_branch=""; env_feature=""; env_seq=""; env_commit=""
# Presence is tracked per key, independently of the value: an all-empty envelope
# (`repo= branch= feature= expected_seq= expected_commit=`) is an INCOMPLETE envelope, not
# the absence of one — treating it as absent would skip the mandatory guard.
has_repo=0; has_branch=0; has_feature=0; has_seq=0; has_commit=0

while [ $# -gt 0 ]; do
  case "$1" in
    --stage)           [ $# -ge 2 ] || usage; stage="$2"; shift 2 ;;
    --stage=*)         stage="${1#--stage=}"; shift ;;
    --repo)            [ $# -ge 2 ] || usage; opt_repo="$2"; shift 2 ;;
    --repo=*)          opt_repo="${1#--repo=}"; shift ;;
    -h|--help)         usage ;;
    repo=*)            env_repo="${1#repo=}";                 has_repo=1;    shift ;;
    branch=*)          env_branch="${1#branch=}";             has_branch=1;  shift ;;
    feature=*)         env_feature="${1#feature=}";           has_feature=1; shift ;;
    expected_seq=*)    env_seq="${1#expected_seq=}";          has_seq=1;     shift ;;
    expected_commit=*) env_commit="${1#expected_commit=}";    has_commit=1;  shift ;;
    # Unknown args are a usage error, never ignored: a typo'd envelope key would otherwise
    # silently degrade a coordinated dispatch into an unguarded human-relay run.
    *) printf 'preflight.sh: unknown argument: %s\n' "$1" >&2; usage ;;
  esac
done

case "$stage" in
  prd|arch|task|impl|review|hunt) ;;
  *) printf 'preflight.sh: --stage must be one of: prd arch task impl review hunt\n' >&2; usage ;;
esac

# Envelope completeness. All five or none — a PARTIAL envelope must never be treated as
# "no envelope", which would skip the mandatory guard on a coordinated dispatch.
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

# Target repo. --repo wins; otherwise the toplevel of $PWD. Normalising to the toplevel means
# a caller standing in a subdirectory still gets the .pipeline/ paths right.
if [ -n "$opt_repo" ]; then
  repo="$(cd "$opt_repo" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null || true)"
else
  repo="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [ -z "$repo" ]; then
  echo "PREFLIGHT STOP not-a-git-repo"
  exit 2
fi

# Dependency check BEFORE any git mutation: without python3 this script cannot parse
# current.json / control.json, so it must degrade to "not run at all" — exit 4, NOT exit 3.
# Exit 3 means "one named check is unverified, everything else passed"; here nothing passed.
if ! command -v python3 >/dev/null 2>&1; then
  echo "PREFLIGHT SKIPPED python3-missing"
  exit 4
fi

# The UNVERIFIED set: an ordered, de-duplicated comma list of the checks this run could NOT
# make. Non-empty at the end ⇒ exit 3, and the caller performs exactly those checks itself.
unverified=""
mark_unverified() {  # $1 = check name
  case ",$unverified," in *",$1,"*) return 0 ;; esac
  unverified="${unverified:+$unverified,}$1"
}

stop() {   # $1 = reason ⇒ the caller's STOP, exit 2
  echo "PREFLIGHT STOP $1"
  exit 2
}

stale() {  # $1 = field, $2 = observed, $3 = expected — CONTRACT §Pre-write stale-dispatch guard
  echo "STALE_DISPATCH $1 observed=$2 expected=$3"
  echo "PREFLIGHT STOP stale-dispatch"
  exit 2
}

trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

# --------------------------------------------------------------- 1. CONTRACT step 1: pull
pull_err=""
if pull_err="$(git -C "$repo" pull --rebase --quiet 2>&1 >/dev/null)"; then
  echo "PULL ok head=$(git -C "$repo" rev-parse HEAD)"
else
  # Never attempt a resolution: a rebase conflict is a human decision, and continuing would
  # run every later check against a half-rebased tree.
  echo "PULL fail"
  # `[ … ] && printf` would be the last status under set -e and abort before the STOP line.
  if [ -n "$pull_err" ]; then printf '%s\n' "$pull_err"; fi
  stop "pull-failed"
fi

# ------------------------------------------ 1b. advisory upstream freshness (once per 24h)
# The stage cannot know it is running an OLD skill set; this tells the operator to run
# `pipeline-update` BETWEEN stages. Advisory by construction: every path returns 0, no path calls
# `stop`, `exit`, or prints `PREFLIGHT …`, and the caller must invoke it as `upstream_check || true`.
# Installed version: the clone's HEAD when the skills live in a pipeline clone (a runtime loading
# them straight from the clone), else the install stamp `pipeline-update` writes
# (`<skills-dir>/.pipeline-update.head`). No stamp ⇒ nothing was ever verified ⇒ say so, don't guess.
# Throttle: ONE network call per 24h, guarded by a cache stamp that is written even when the fetch
# FAILS — a GitHub outage then costs one 8s wait per day, not one per stage run.
UPSTREAM_URL="${PIPELINE_UPSTREAM_URL:-https://github.com/jackypanster/pipeline.git}"
UPSTREAM_REMOTE_RE='(^|[@/])github\.com[:/]jackypanster/pipeline(\.git)?/?$'   # same as update.sh
upstream_check() {
  local self_dir skills_dir top installed mode2 cache stamp now c_epoch c_sha c_rest cached remote suffix
  # Self-locate with symlinks resolved: …/pipeline-preflight/scripts → the skills dir holding the
  # pipeline-* copies (a symlinked attachment resolves to its real target, like the install check).
  self_dir="$(cd -P "$(dirname "$0")" && pwd -P)"
  skills_dir="$(cd -P "$self_dir/../.." && pwd -P)"
  mode2=0
  installed=""
  top="$(git -C "$skills_dir" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$top" ] && git -C "$top" remote get-url origin 2>/dev/null | grep -Eq "$UPSTREAM_REMOTE_RE"; then
    mode2=1
    installed="$(git -C "$top" rev-parse HEAD 2>/dev/null || true)"
  else
    stamp=""
    if [ -r "$skills_dir/.pipeline-update.head" ]; then
      stamp="$(tr -d '[:space:]' < "$skills_dir/.pipeline-update.head" 2>/dev/null || true)"
    fi
    if printf '%s' "$stamp" | grep -Eq '^[0-9a-f]{40}$'; then installed="$stamp"; fi
  fi
  if [ -z "$installed" ]; then
    echo "UPSTREAM unverified no-install-stamp"
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
    # ALWAYS write the throttle stamp — a failed fetch is throttled too. Unwritable cache dir ⇒
    # skip caching silently: a stale check is advisory, never a reason to fail or to nag louder.
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
  elif [ "$mode2" = 1 ] \
       && git -C "$top" cat-file -e "$remote" 2>/dev/null \
       && git -C "$top" merge-base --is-ancestor "$remote" HEAD 2>/dev/null; then
    echo "UPSTREAM ok head=$remote$suffix"          # local clone is AHEAD of main — not stale
  else
    echo "UPSTREAM newer head=$remote installed=$installed run=pipeline-update$suffix"
  fi
  return 0
}

upstream_check || true    # advisory only — can never STOP or change the exit code

# -------------------------------------------- 2. CONTRACT step 2: dotenv DETECTION only
# Reports which file exists and HOW MANY keys it defines — a COUNT, never a name and never a
# value. Names were dropped deliberately: a multi-line quoted value whose continuation line
# looks like `SOMETHING=…` would be printed as a "key", leaking secret material into a chat
# transcript / journal. The count may therefore be off by one on such a file; a count leaks
# nothing, so that is the safe direction to be wrong in. Nothing is exported either — shell
# state cannot survive back to the agent anyway; loading stays the caller's step 2.
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
KEYS = ("repo", "branch", "feature", "stage")   # "pr" is optional (CONTRACT step 3)
for k in KEYS:
    if k not in d:
        print("MISSING " + k); raise SystemExit(0)
    v = d[k]
    # null / number / list / "" are all unusable downstream: a non-empty STRING or nothing.
    # Surrounding whitespace is rejected too, never trimmed: `repo` is compared BYTE-FOR-BYTE
    # with the remote URL and `feature` names a directory, so a stray space is a wrong value,
    # and silently stripping it would let "/r " verify against "/r" (review round 4).
    if not isinstance(v, str) or not v.strip() or v != v.strip():
        print("BADFIELD " + k); raise SystemExit(0)
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
elif [ "$stage" = "prd" ]; then
  echo "CURRENT absent (prd creates it)"
else
  stop "current.json-missing"
fi

# ------------------------------------ 4. CONTRACT step 4: resolve the slot, verify installed
roles="$repo/.pipeline/roles.yaml"
[ -f "$roles" ] || stop "roles.yaml-missing"

slot_line="$(grep -m1 "^${stage}:" "$roles" || true)"
slot_raw="${slot_line#*:}"
slot_raw="${slot_raw%%#*}"                        # drop roles.yaml's trailing `# …` comment
slot_raw="$(printf '%s' "$slot_raw" | tr -d '[]')"  # `[a, b]` list form → bare comma list
slot_raw="$(trim "$slot_raw")"
[ -n "$slot_raw" ] || stop "slot-unset $stage"

# Names as a newline-separated list (not a bash array: an empty array under `set -u` is an
# unbound-variable error on bash 3.2, which is still /bin/bash on macOS).
names=""
while IFS= read -r p; do
  n="$(trim "$p")"
  [ -n "$n" ] || continue
  # `<autonomous-coding-skill>` is roles.yaml's REQUIRED-to-replace placeholder. Resolving it
  # as if it were a skill name is exactly the silent fallback CONTRACT step 4 forbids.
  case "$n" in
    \<*\>) stop "slot-placeholder $stage=$n" ;;
  esac
  # A slot value is a skill DIRECTORY NAME, never a path. `../outside` would reach a SKILL.md
  # outside every declared PIPELINE_SKILL_DIRS and be reported as a verified install. The
  # charset also excludes `.` and `..` (both fail the leading [A-Za-z0-9]).
  printf '%s' "$n" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._-]*$' || stop "slot-invalid-name $stage=$n"
  names="${names:+$names
}$n"
done <<SLOT_EOF
$(printf '%s' "$slot_raw" | tr ',' '\n')
SLOT_EOF
[ -n "$names" ] || stop "slot-unset $stage"

echo "SLOT $stage=$(printf '%s\n' "$names" | paste -sd, -)"

# A readable SKILL.md proves a directory exists on disk — it does NOT prove THIS runtime loads
# skills from there. Only the operator knows that, and declares it in $PIPELINE_SKILL_DIRS
# (colon-separated). A hit in a DECLARED dir is a verified install; a hit anywhere else is
# evidence only ⇒ `install-check` joins the UNVERIFIED set and the stage re-verifies it.
# $PIPELINE_UPSTREAM_URL overrides the repo the advisory `UPSTREAM …` check ls-remotes (default:
# this repo's GitHub URL) — for tests and mirrors.
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

find_skill() {  # $1 = newline-separated dirs, $2 = skill name → prints the hit dir, or nothing
  local d
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    # -r follows symlinks, so a runtime that attaches skills by symlink resolves too.
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

# ---------------------------- 5. CONTRACT §Pre-write stale-dispatch guard (envelope only)

if [ "$envelope" = 0 ]; then
  echo "GUARD n/a (human-relay)"
else
  # repo — observed = where the envelope points ON THIS MACHINE, expected = where we run.
  tgt_phys="$(cd "$repo" && pwd -P)"
  env_phys="$(cd "$env_repo" 2>/dev/null && pwd -P || true)"
  [ -n "$env_phys" ] || env_phys="<unreadable>"
  [ "$env_phys" = "$tgt_phys" ] || stale "repo" "$env_phys" "$tgt_phys"

  remote="$(git -C "$repo" config "branch.$env_branch.remote" 2>/dev/null || true)"
  [ -n "$remote" ] || remote="origin"

  # remote identity — CONTRACT guard step 2's first field: BYTE EQUALITY, or UNVERIFIED. There
  # is no third answer, and in particular no STALE_DISPATCH: the STALE_DISPATCH-able identity
  # check is `repo` above (envelope path vs this checkout's physical toplevel).
  #
  # Git's URL grammar is deliberately NOT re-implemented here. scp-like `host:path`,
  # `scheme://`, `file://`, relative paths, `insteadOf` rewrites and a remote carrying SEVERAL
  # urls follow rules only Git owns, and every hand-rolled normaliser mis-reads some of them —
  # `host:2222/x` is not a port, `example.com/acme/demo.git` is not a network address,
  # `config --get` returns the LAST url while fetch uses the FIRST. A mis-read yields a
  # confident WRONG verdict, which is strictly worse than handing the check to the stage.
  #
  # observed = `ls-remote --get-url`: the URL Git itself would use for this remote — insteadOf
  # applied, first url picked exactly as fetch picks it, and no network is touched.
  # expected = `current.json.repo` verbatim. Equal ⇒ verified; anything else ⇒ UNVERIFIED.
  guard_remote=""
  # Read the URL byte-exact: `$()` would eat EVERY trailing newline, so a URL that itself ends
  # in one would collapse onto its trimmed twin. Keep the bytes, then drop exactly the ONE
  # record terminator ls-remote appends (review round 5).
  obs_remote="$(git -C "$repo" ls-remote --get-url "$remote" 2>/dev/null; printf x)"
  obs_remote="${obs_remote%x}"; obs_remote="${obs_remote%$'\n'}"
  [ -n "$obs_remote" ] || obs_remote="<absent>"
  if [ "$obs_remote" != "<absent>" ] && [ "$obs_remote" = "$cur_repo" ]; then
    guard_remote=" remote=$obs_remote"
  else
    echo "REMOTE unverified observed=$obs_remote current.json.repo=${cur_repo:-<absent>}"
    mark_unverified remote-identity
  fi

  # branch
  cur_br="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  [ -n "$cur_br" ] || cur_br="<absent>"
  [ "$cur_br" = "$env_branch" ] || stale "branch" "$cur_br" "$env_branch"

  # feature
  if [ "$cur_present" = 1 ]; then obs_feature="$cur_feature"; else obs_feature="<absent>"; fi
  [ "$obs_feature" = "$env_feature" ] || stale "feature" "$obs_feature" "$env_feature"

  # expected_commit — a short sha is rejected outright: prefix matching is precisely the
  # ambiguity an exact-match, fail-closed guard exists to forbid.
  if ! printf '%s' "$env_commit" | grep -Eq '^[0-9a-f]{40}$'; then
    stale "expected_commit" "<short-sha-rejected>" "$env_commit"
  fi
  # The fetch is the guard's only source of remote truth. If it fails we STOP: every ref still on
  # disk — the remote-tracking ref AND the FETCH_HEAD left by step 1's pull — is a CACHE from an
  # earlier command, and comparing one would turn "could not observe the remote" into a false
  # GUARD ok on an already-stale dispatch.
  fetch_err=""
  if ! fetch_err="$(git -C "$repo" fetch --quiet "$remote" "$env_branch" 2>&1 >/dev/null)"; then
    echo "FETCH fail $remote/$env_branch"
    if [ -n "$fetch_err" ]; then printf '%s\n' "$fetch_err"; fi
    stop "fetch-failed"
  fi
  # FETCH_HEAD, not `refs/remotes/<remote>/<branch>`: the fetch above is not guaranteed to have
  # updated that ref (a narrowed `remote.<r>.fetch` refspec, or a local branch tracking a
  # DIFFERENTLY NAMED remote branch, leaves it at whatever an older run cached — which is
  # exactly the stale value the guard exists to refuse). FETCH_HEAD is written BY that fetch.
  remote_sha="$(git -C "$repo" rev-parse FETCH_HEAD 2>/dev/null || true)"
  [ -n "$remote_sha" ] || remote_sha="<absent>"
  [ "$remote_sha" = "$env_commit" ] || stale "expected_commit" "$remote_sha" "$env_commit"
  head_sha="$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)"
  [ -n "$head_sha" ] || head_sha="<absent>"
  [ "$head_sha" = "$env_commit" ] || stale "expected_commit" "$head_sha" "$env_commit"

  # expected_seq — the journal tail is authoritative (CONTRACT §Run journal).
  journal="$repo/.pipeline/$env_feature/journal.md"
  tail_line=""
  if [ -f "$journal" ]; then
    tail_line="$(grep -n '^## seq=' "$journal" | tail -1 | cut -d: -f1 || true)"
  fi
  if [ -n "$tail_line" ]; then
    obs_seq="$(sed -n "${tail_line}p" "$journal" | sed -E 's/^## seq=([0-9]+).*/\1/' || true)"
    [ -n "$obs_seq" ] || obs_seq="<absent>"
  else
    obs_seq="<absent>"
  fi
  [ "$obs_seq" = "$env_seq" ] || stale "expected_seq" "$obs_seq" "$env_seq"

  # next — the TAIL ENTRY's own handoff must name THIS stage's command. Scoped to the last
  # `## seq=` header on purpose: the last handoff in the whole file may belong to an OLDER entry,
  # and an append-only journal always has several. A tail entry with no handoff is a mismatch,
  # not a licence to reuse the previous entry's.
  #
  # The markers are matched as WHOLE LINES (CONTRACT §Run journal's grammar), never as
  # substrings: the first line that IS `--- handoff ---`, whose next non-empty line must BE
  # `>>> NEXT`, and the command is the next non-empty line after that. A body line that merely
  # MENTIONS `>>> NEXT` is prose — reading it as the marker let a decoy command line underneath
  # it satisfy a stage the real handoff hands off to somebody else.
  obs_next="<absent-handoff-in-tail>"
  next_tok=""
  if [ -n "$tail_line" ]; then
    # awk opens the journal ITSELF — no upstream `tail … |`. awk's early `exit` would close a
    # pipe its writer is still filling, and on a handoff body larger than the pipe buffer
    # (~64 KiB) that SIGPIPEs the writer: under `set -o pipefail` the whole script dies 141.
    cand="$(awk -v start="$tail_line" '
      NR < start { next }
      { line = $0; sub(/[ \t\r]+$/, "", line) }
      state == 0 { if (line == "--- handoff ---") state = 1; next }
      state == 1 { if (line == "") next
                   if (line != ">>> NEXT") exit
                   state = 2; next }
      state == 2 { if (line == "") next
                   print line; exit }
    ' "$journal")"
    if [ -n "$cand" ]; then
      obs_next="$cand"
      # Exact FIRST-TOKEN match after stripping the shapes a command legitimately arrives in:
      # leading whitespace, a slash-command `/`, a `$` prompt, and CONTRACT's `Run ` prefix.
      # Substring matching is what let `Run pipeline-review after pipeline-task finishes`
      # satisfy stage `task`.
      next_tok="$(printf '%s' "$cand" | sed -E \
        -e 's#^[[:space:]]+##' -e 's#^[/$]##' -e 's#^Run[[:space:]]+##' -e 's#[[:space:]].*$##')"
    fi
  fi
  if [ "$next_tok" != "pipeline-$stage" ]; then
    stale "next" "${obs_next:0:80}" "pipeline-$stage"
  fi

  # control.* — the COMPLETE authorization tuple, read at the DISPATCHED commit.
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
print("OK")
'
  if control_raw="$(git -C "$repo" show \
        "$env_commit:.pipeline/$env_feature/control.json" 2>/dev/null)"; then
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

  echo "GUARD ok seq=$env_seq commit=$env_commit$guard_remote"
fi

# ------------------------------------------------------------------------ 6. final verdict
if [ -n "$unverified" ]; then
  echo "PREFLIGHT UNVERIFIED $unverified"
  exit 3
fi
echo "PREFLIGHT OK stage=$stage"
exit 0
