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
# fallback (script absent, or exit 3 ⇒ the caller does that part as written).
#
# Usage:
#   preflight.sh --stage <prd|arch|task|impl|review|hunt> [--repo <path>] \
#                [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]
#
#   The five k=v fields are the coordinator's dispatch envelope (CONTRACT §Dispatch envelope):
#   pass ALL five or NONE. No envelope = human-relay = the stale-dispatch guard does not apply.
#
# Exit codes:
#   0   PREFLIGHT OK          — steps 1/3/4 (+ the guard, when an envelope is present) all passed
#   2   PREFLIGHT STOP <why>  — contract violation (includes STALE_DISPATCH); the caller STOPs
#   3   PREFLIGHT UNVERIFIED  — everything passed EXCEPT the install check (or python3 is missing);
#                               the caller verifies that part itself via the prose step
#   64  usage error (message on stderr)
#
# Output grammar — one line per check on stdout, greppable, values unquoted:
#   PULL ok head=<sha>                     | PULL fail  (+ git's stderr)
#   ENV file=<name> keys=<K1,K2>           # KEY NAMES ONLY — never a value, and nothing is exported
#   ENV none
#   CURRENT ok repo=<v> branch=<v> feature=<v> stage=<v>   | CURRENT absent (prd creates it)
#   SLOT <stage>=<name>[,<name>…]
#   INSTALLED <name> path=<dir>/<name>     | INSTALLED <name> UNVERIFIED searched=<d1>:<d2>:…
#   GUARD ok seq=<n> commit=<sha>          | GUARD n/a (human-relay)
#   STALE_DISPATCH <field> observed=<v> expected=<v>
#   PREFLIGHT OK stage=<s> | PREFLIGHT STOP <reason> | PREFLIGHT UNVERIFIED <reason>
#
# Writes ZERO files anywhere. The only checkout mutations are `git pull --rebase` (CONTRACT
# step 1) and the guard's `git fetch` — both are the contract's own first act. It never
# resolves a conflict: a failed pull is a STOP for the human, not a merge decision.
#
# Deps: git, python3 (stdlib json only), coreutils. Self-contained — sources nothing.
# Self-test: bash "$(dirname "$0")/preflight-test.sh"
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: preflight.sh --stage <prd|arch|task|impl|review|hunt> [--repo <path>]
                    [repo=<abs> branch=<b> feature=<slug> expected_seq=<n> expected_commit=<sha40>]

The five k=v envelope fields are all-or-nothing (a partial envelope is a STOP, not a default).
EOF
  exit 64
}

stage=""
opt_repo=""
env_repo=""; env_branch=""; env_feature=""; env_seq=""; env_commit=""

while [ $# -gt 0 ]; do
  case "$1" in
    --stage)           [ $# -ge 2 ] || usage; stage="$2"; shift 2 ;;
    --stage=*)         stage="${1#--stage=}"; shift ;;
    --repo)            [ $# -ge 2 ] || usage; opt_repo="$2"; shift 2 ;;
    --repo=*)          opt_repo="${1#--repo=}"; shift ;;
    -h|--help)         usage ;;
    repo=*)            env_repo="${1#repo=}"; shift ;;
    branch=*)          env_branch="${1#branch=}"; shift ;;
    feature=*)         env_feature="${1#feature=}"; shift ;;
    expected_seq=*)    env_seq="${1#expected_seq=}"; shift ;;
    expected_commit=*) env_commit="${1#expected_commit=}"; shift ;;
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
present=0
for kv in "repo=$env_repo" "branch=$env_branch" "feature=$env_feature" \
          "expected_seq=$env_seq" "expected_commit=$env_commit"; do
  k="${kv%%=*}"; v="${kv#*=}"
  if [ -n "$v" ]; then present=1; else missing="${missing:+$missing,}$k"; fi
done
if [ "$present" = 1 ]; then
  if [ -n "$missing" ]; then
    echo "PREFLIGHT STOP envelope-incomplete missing=$missing"
    exit 2
  fi
  envelope=1
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
# current.json / control.json, so it must degrade to the prose path having changed nothing.
if ! command -v python3 >/dev/null 2>&1; then
  echo "PREFLIGHT UNVERIFIED python3-missing"
  exit 3
fi

unverified=0

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

# -------------------------------------------- 2. CONTRACT step 2: dotenv DETECTION only
# Reports which file exists and which KEY NAMES it defines. It never prints a value and never
# exports anything (shell state cannot survive back to the agent anyway) — loading stays the
# caller's step 2. Printing a value here would leak a token into a chat transcript / journal.
env_found=0
for f in .env .env.local .envrc; do
  [ -f "$repo/$f" ] || continue
  env_found=1
  keys="$(sed -nE 's/^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=.*/\2/p' \
          "$repo/$f" | paste -sd, - || true)"
  echo "ENV file=$f keys=$keys"
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
for k in ("repo", "branch", "feature", "stage"):   # "pr" is optional (CONTRACT step 3)
    if k not in d:
        print("MISSING " + k); raise SystemExit(0)
print("OK")
for k in ("repo", "branch", "feature", "stage"):
    v = d[k]
    print(v if isinstance(v, str) else json.dumps(v))
'

current="$repo/.pipeline/current.json"
cur_present=0
cur_feature=""
if [ -f "$current" ]; then
  cur_present=1
  cur_out="$(python3 -c "$PY_CURRENT" "$current")"
  cur_head="$(printf '%s\n' "$cur_out" | sed -n '1p')"
  case "$cur_head" in
    INVALID)     stop "current.json-invalid" ;;
    "MISSING "*) stop "current.json-missing-field ${cur_head#MISSING }" ;;
  esac
  cur_repo="$(printf '%s\n' "$cur_out" | sed -n '2p')"
  cur_branch="$(printf '%s\n' "$cur_out" | sed -n '3p')"
  cur_feature="$(printf '%s\n' "$cur_out" | sed -n '4p')"
  cur_stage="$(printf '%s\n' "$cur_out" | sed -n '5p')"
  echo "CURRENT ok repo=$cur_repo branch=$cur_branch feature=$cur_feature stage=$cur_stage"
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
  names="${names:+$names
}$n"
done <<SLOT_EOF
$(printf '%s' "$slot_raw" | tr ',' '\n')
SLOT_EOF
[ -n "$names" ] || stop "slot-unset $stage"

echo "SLOT $stage=$(printf '%s\n' "$names" | paste -sd, -)"

# Search order: caller override, then the target repo, then this machine's runtimes.
search_dirs="$repo/.claude/skills
$repo/.agents/skills
$HOME/.agents/skills
$HOME/.claude/skills
$HOME/.pi/agent/skills
$HOME/.codex/skills"
if [ -n "${PIPELINE_SKILL_DIRS:-}" ]; then
  search_dirs="$(printf '%s' "$PIPELINE_SKILL_DIRS" | tr ':' '\n' | grep -v '^$' || true)
$search_dirs"
fi
searched="$(printf '%s\n' "$search_dirs" | paste -sd: -)"

while IFS= read -r n; do
  [ -n "$n" ] || continue
  hit=""
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    # -r follows symlinks, so a runtime that attaches skills by symlink resolves too.
    if [ -r "$d/$n/SKILL.md" ]; then hit="$d/$n"; break; fi
  done <<DIRS_EOF
$search_dirs
DIRS_EOF
  if [ -n "$hit" ]; then
    echo "INSTALLED $n path=$hit"
  else
    # Never claim installed without a SKILL.md hit: this runtime may load skills from a place
    # this script does not know. UNVERIFIED ⇒ exit 3 ⇒ the caller verifies it the prose way.
    echo "INSTALLED $n UNVERIFIED searched=$searched"
    unverified=1
  fi
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
  remote="$(git -C "$repo" config "branch.$env_branch.remote" 2>/dev/null || true)"
  [ -n "$remote" ] || remote="origin"
  git -C "$repo" fetch --quiet "$remote" "$env_branch" 2>/dev/null || true
  remote_sha="$(git -C "$repo" rev-parse "$remote/$env_branch" 2>/dev/null || true)"
  [ -n "$remote_sha" ] || remote_sha="<absent>"
  [ "$remote_sha" = "$env_commit" ] || stale "expected_commit" "$remote_sha" "$env_commit"
  head_sha="$(git -C "$repo" rev-parse HEAD 2>/dev/null || true)"
  [ -n "$head_sha" ] || head_sha="<absent>"
  [ "$head_sha" = "$env_commit" ] || stale "expected_commit" "$head_sha" "$env_commit"

  # expected_seq — the journal tail is authoritative (CONTRACT §Run journal).
  journal="$repo/.pipeline/$env_feature/journal.md"
  if [ -f "$journal" ]; then
    obs_seq="$(grep -E '^## seq=[0-9]+' "$journal" | tail -1 \
               | sed -E 's/^## seq=([0-9]+).*/\1/' || true)"
    [ -n "$obs_seq" ] || obs_seq="<absent>"
  else
    obs_seq="<absent>"
  fi
  [ "$obs_seq" = "$env_seq" ] || stale "expected_seq" "$obs_seq" "$env_seq"

  # next — the tail's `>>> NEXT` block must name THIS stage's command.
  obs_next="<absent>"
  if [ -f "$journal" ]; then
    nline="$(grep -n '>>> NEXT' "$journal" | tail -1 | cut -d: -f1 || true)"
    if [ -n "$nline" ]; then
      cand="$(tail -n +"$((nline + 1))" "$journal" | grep -m1 -v '^[[:space:]]*$' || true)"
      if [ -n "$cand" ]; then obs_next="$cand"; fi
    fi
  fi
  if ! printf '%s' "$obs_next" \
       | grep -Eq "(^|[^A-Za-z0-9_-])pipeline-${stage}([^A-Za-z0-9_-]|\$)"; then
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

  echo "GUARD ok seq=$env_seq commit=$env_commit"
fi

# ------------------------------------------------------------------------ 6. final verdict
if [ "$unverified" = 1 ]; then
  echo "PREFLIGHT UNVERIFIED install-check"
  exit 3
fi
echo "PREFLIGHT OK stage=$stage"
exit 0
