#!/usr/bin/env python3
"""run-loop.py — stateless headless CLI runner for the v2 3-Agent 4-Step pipeline.

Subcommands:
  init-config   Detect local CLIs and write ~/.config/pipeline/runners.json template.
  run           Run the FULL 4-step pipeline end-to-end (Step 1 Planner <-> Step 2 Reviewer ->
                Step 3 Implementer <-> verify <-> Step 4 Reviewer) in foreground or --background,
                looping automatically until ACCEPT, 3rd-rejection breaker, or human escalation.
  resume        Resume a paused/NEEDS_HUMAN/interrupted feature (with optional --answer).
  status        Show live background/foreground state, PID liveness, stage, and reject counts.
  logs          Print recent lines of a feature's background run log.
  stop          Gracefully stop a running background pipeline process group.
  review-plan   Run ONE headless Step 2 (plan-review) verdict against .pipeline/<feature>/plan.md.
  approve-plan  Record explicit human approval after pause_after_plan=true (human->impl).
  phase2        Run Phase II (Step 3 implementer CLI <-> deterministic verify <-> Step 4 reviewer CLI)
                with ZERO planner tokens until ACCEPT, 3rd rejection breaker, or escalation.

Hard invariants enforced mechanically:
  - Model independence: reviewer.model_id must be non-empty and distinct from both planner_model
    (required, non-conflicting across plan.md/current.json/runners.json/--planner-model) and
    implementer.model_id.
  - Never uses shell=True for agent CLIs (argv is a JSON array of strings).
  - Bounded subprocess timeouts with full process-group kill (start_new_session=True + os.killpg).
  - Repository-wide exclusive lock (<git-common-dir>/pipeline-run-loop.lock) prevents state races.
  - Step 1 write-set gate: planner may ONLY write .pipeline/<feature>/plan.md (and optional
    CONTEXT.md / docs/adr/*) on trunk without moving HEAD; runner validates via check-plan.py
    and commits atomically.
  - Trunk holds .pipeline/ metadata; feat/<feature> runs in an isolated git worktree with zero
    .pipeline/ files allowed in its diff (checked via NUL-delimited git diff -z -M covering renames).
  - Deterministic forbid-paths + plan.md verify commands run BEFORE Step 4 reviewer is invoked,
    and post-verify working-tree/HEAD/branch integrity is verified before review or push.
  - Implementer crash or timeout STOPs immediately (exit 4, implementer death = STOP).
  - Strict physical line-1 VERDICT | CAUSE parsing; malformed output fails closed (exit 4).
  - Short-circuits Step 4 REJECT on plan_fact | plan_design | env immediately to human (exit 3).
  - Circuit breaker at 3rd rejection (rejects >= 3 => blocked, exit 2).
  - ZERO merge capability: never merges PRs or branches; hands off SHA-bound ACCEPT to interactive
    pipeline-review for human-direct GO-gate confirmation.

Frozen exit codes:
  0   run/phase2: headless ACCEPT (SHA-bound handoff to interactive pipeline-review; NOT merged)
      run --background: background worker started
      review-plan: ACCEPT / ACCEPT WITH NITS; approve-plan/init-config/status/logs/stop: OK
  2   Circuit breaker hit (plan_rejects >= 3, review_rejects >= 3, or verify/forbid-paths cap)
  3   Escalation to human (NEEDS_HUMAN, plan_fact|plan_design|env short-circuit, or pause_after_plan)
  4   Fail-closed runtime/contract error (agent crash/timeout, model collision, dirty trunk,
      stale plan/journal, write-set violation, malformed verdict, lock busy, git/CLI failure)
  10  review-plan REJECT with plan_rejects < 3 (planner revises plan.md and retries)
  64  Usage or invalid config error
"""
import argparse
import contextlib
import datetime
import fcntl
import fnmatch
import importlib.util
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_REJECTS = 3
MAX_IMPL_FAILURES_PER_ROUND = 2
MAX_PLAN_FORMAT_RETRIES = 1
DIFF_INLINE_LIMIT = 200_000
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_CONFIG_PATH = "~/.config/pipeline/runners.json"

PLAN_VERDICT_RE = re.compile(
    r"^VERDICT: (ACCEPT|ACCEPT WITH NITS|REJECT) \| CAUSE: (none|plan_fact|plan_design|scope|env)$"
)
CODE_VERDICT_RE = re.compile(
    r"^VERDICT: (ACCEPT|REJECT) \| CAUSE: (none|impl_bug|weak_test|scope|plan_fact|plan_design|env)$"
)
SHORT_CIRCUIT_CAUSES = ("plan_fact", "plan_design", "env")
PHASE1_PLAN_TAILS = (
    None,
    "plan-review→plan · failed",
    "human→plan · completed",
    "hunt→plan · completed",
)
PHASE2_ALLOWED_TAILS = (
    "plan-review→impl · completed",
    "human→impl · completed",
    "review→impl · failed",
    "hunt→impl · completed",
    "impl→review · completed",
)
JOURNAL_HEADER_RE = re.compile(
    r"^## seq=([0-9]+) · [0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z · "
    r"([a-z-]+→[a-z-]+ · (?:completed|failed|blocked)) · by=(.+)$"
)


class _Exit64Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"run-loop: {message}\n")
        sys.exit(64)


def _load_check_plan():
    path = Path(__file__).resolve().parents[2] / "pipeline-preflight" / "scripts" / "check-plan.py"
    spec = importlib.util.spec_from_file_location("pipeline_check_plan", str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load check-plan.py from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(msg):
    sys.stdout.write(f"[{_utc_now()}] {msg}\n")
    sys.stdout.flush()


_LAST_DIE_MSG = ""


def _die(code, msg):
    global _LAST_DIE_MSG
    _LAST_DIE_MSG = msg
    sys.stderr.write(f"run-loop: {msg}\n")
    sys.stderr.flush()
    sys.exit(code)


def _git(repo, args, check=True, text=True):
    try:
        r = subprocess.run(
            ["git", "-C", str(repo)] + list(args),
            capture_output=True,
            text=text,
        )
    except OSError as exc:
        raise RuntimeError(f"failed to launch git: {exc}") from exc
    if check and r.returncode != 0:
        err = r.stderr if text else r.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git {' '.join(args)} failed (rc={r.returncode}): {err.strip()}")
    return r


def _git_common_dir(repo):
    return Path(_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout.strip())


_ACTIVE_STATE_DIR = None
_ACTIVE_CHILD_PROC = None


def _pg_is_alive(pgid):
    if not isinstance(pgid, int) or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _kill_pg_and_wait(pgid, grace_s=1.5):
    """Terminate an owned process group (even after its group leader exits) and wait until gone."""
    if not isinstance(pgid, int) or pgid <= 0 or pgid == os.getpid() or pgid == os.getpgrp():
        return

    def _alive():
        return _pg_is_alive(pgid) or _pid_is_alive(pgid)

    if not _alive():
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        try:
            os.kill(pgid, signal.SIGTERM)
        except OSError:
            pass
    deadline = time.time() + grace_s
    while time.time() < deadline and _alive():
        time.sleep(0.05)
    if _alive():
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pgid, signal.SIGKILL)
            except OSError:
                pass
        kill_deadline = time.time() + 2.0
        while time.time() < kill_deadline and _alive():
            time.sleep(0.05)


def _record_active_child(pid):
    if not _ACTIVE_STATE_DIR:
        return
    p = Path(_ACTIVE_STATE_DIR) / "active-child.pid"
    try:
        if pid is None:
            if p.exists():
                p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(f".pid.tmp.{os.getpid()}")
            tmp.write_text(f"{pid}\n", encoding="utf-8")
            os.replace(str(tmp), str(p))
    except OSError:
        pass


def _install_stop_handler(state_dir):
    global _ACTIVE_STATE_DIR
    _ACTIVE_STATE_DIR = Path(state_dir) if state_dir else None

    def _on_signal(signum, _frame):
        proc = _ACTIVE_CHILD_PROC
        if proc is not None:
            # Always terminate the entire owned process group, even if the group leader already exited.
            _kill_pg_and_wait(proc.pid, grace_s=1.0)
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
        _record_active_child(None)
        _update_run_state(
            _ACTIVE_STATE_DIR,
            status="stopped_by_user",
            reason=f"terminated by signal {signum}",
            exit_code=128 + signum,
        )
        sys.exit(128 + signum)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)


def _run_bounded(argv, cwd, timeout_s, stdin_text=None):
    global _ACTIVE_CHILD_PROC
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise RuntimeError(f"failed to launch {argv[0]!r}: {exc}") from exc
    _ACTIVE_CHILD_PROC = proc
    _record_active_child(proc.pid)
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout_s)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        _kill_pg_and_wait(proc.pid, grace_s=0.2)
        try:
            out, err = proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return -9, out or "", err or "", True
    finally:
        if _pg_is_alive(proc.pid):
            _kill_pg_and_wait(proc.pid, grace_s=0.5)
        _ACTIVE_CHILD_PROC = None
        _record_active_child(None)


def _validate_positive_finite(val, label):
    if not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val) or val <= 0:
        _die(64, f"{label} must be a finite positive number")
    return float(val)


def _validate_role_cfg(cfg, role_name):
    if role_name not in cfg or not isinstance(cfg[role_name], dict):
        _die(64, f"config missing object '{role_name}'")
    rcfg = cfg[role_name]
    model_id = rcfg.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        _die(64, f"config '{role_name}.model_id' must be a non-empty string")
    argv = rcfg.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
        _die(64, f"config '{role_name}.argv' must be a non-empty list of strings")
    prompt_via = rcfg.get("prompt_via", "arg")
    if prompt_via not in ("arg", "stdin"):
        _die(64, f"config '{role_name}.prompt_via' must be 'arg' or 'stdin'")
    has_placeholder = any(("{prompt}" in a or "{prompt_file}" in a) for a in argv)
    if prompt_via == "arg" and not has_placeholder:
        _die(64, f"config '{role_name}.argv' needs '{{prompt}}', '{{prompt_file}}', or prompt_via='stdin'")
    default_timeout = {"planner": 900, "implementer": 1800, "reviewer": 600}.get(role_name, 600)
    timeout_s = _validate_positive_finite(
        rcfg.get("timeout_s", default_timeout),
        f"config '{role_name}.timeout_s'",
    )
    return {
        "model_id": model_id.strip(),
        "argv": argv,
        "prompt_via": prompt_via,
        "timeout_s": timeout_s,
    }


def _invoke_agent(role_cfg, prompt_text, cwd):
    with tempfile.TemporaryDirectory(prefix="pipeline-prompt-") as tmpdir:
        pfile = Path(tmpdir) / "prompt.md"
        pfile.write_text(prompt_text, encoding="utf-8")
        argv = [
            a.replace("{prompt}", prompt_text).replace("{prompt_file}", str(pfile))
            for a in role_cfg["argv"]
        ]
        stdin_text = prompt_text if role_cfg["prompt_via"] == "stdin" else None
        return _run_bounded(argv, cwd=cwd, timeout_s=role_cfg["timeout_s"], stdin_text=stdin_text)


def _norm_model(m):
    return (m or "").strip().lower()


def _resolve_planner_model(cli_model, cur_model, plan_model):
    candidates = {}
    for src, val in (("plan.md", plan_model), ("current.json", cur_model), ("--planner-model", cli_model)):
        if isinstance(val, str) and val.strip():
            candidates[src] = val.strip()
    if not candidates:
        _die(4, "model-separation failed: planner_model is missing in plan.md, current.json, and --planner-model")
    normed = {_norm_model(v) for v in candidates.values()}
    if len(normed) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in candidates.items())
        _die(4, f"model-separation failed: conflicting planner_model values ({detail})")
    return next(iter(candidates.values()))


def _check_model_separation(reviewer_model, planner_model, implementer_model=None):
    rm = _norm_model(reviewer_model)
    pm = _norm_model(planner_model)
    if not rm or not pm:
        _die(4, "model-separation failed: reviewer and planner model_id must both be non-empty")
    if rm == pm:
        _die(4, f"model-separation failed: reviewer ({reviewer_model}) == planner ({planner_model})")
    if implementer_model is not None:
        im = _norm_model(implementer_model)
        if not im:
            _die(4, "model-separation failed: implementer model_id is empty")
        if rm == im:
            _die(4, f"model-separation failed: reviewer ({reviewer_model}) == implementer ({implementer_model})")


def _read_json(path, exit_code=4):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        _die(exit_code, f"cannot parse JSON {path}: {exc}")


def _write_json(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_json_atomic(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


@contextlib.contextmanager
def _repo_lock(repo):
    common_dir = _git_common_dir(repo)
    lock_path = common_dir / "pipeline-run-loop.lock"
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        _die(4, f"lock-busy: another run-loop holds {lock_path}")
    try:
        yield common_dir
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _probe_lock_free(repo):
    common_dir = _git_common_dir(repo)
    lock_path = common_dir / "pipeline-run-loop.lock"
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        fh.close()


def _pid_is_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _run_dir_for(repo, feature):
    return _git_common_dir(repo) / "pipeline-runs" / feature


def _update_run_state(state_dir, **fields):
    if not state_dir:
        return
    sp = Path(state_dir) / "state.json"
    cur = {}
    if sp.is_file():
        try:
            cur = json.loads(sp.read_text(encoding="utf-8"))
            if not isinstance(cur, dict):
                cur = {}
        except Exception:
            cur = {}
    cur.update(fields)
    cur["updated_at"] = _utc_now()
    _write_json_atomic(sp, cur)


def _read_journal_tail_strict(journal_path):
    """Parse the physically-last `## seq=` entry strictly. Returns (seq, trans, by_tag, output_line)."""
    if not journal_path.is_file():
        return 0, None, "", ""
    lines = journal_path.read_text(encoding="utf-8", errors="replace").splitlines()
    last_idx = None
    for idx, line in enumerate(lines):
        if line.startswith("## seq="):
            last_idx = idx
    if last_idx is None:
        return 0, None, "", ""
    m = JOURNAL_HEADER_RE.match(lines[last_idx])
    if not m:
        _die(4, f"malformed journal tail header: {lines[last_idx][:120]}")
    out_line = ""
    for line in lines[last_idx + 1:]:
        if line.startswith("output:"):
            out_line = line.partition(":")[2].strip()
            break
    return int(m.group(1)), m.group(2), m.group(3).strip(), out_line


def _append_journal(journal_path, from_stage, to_stage, status, by_tag, done_line, output_line, next_lines):
    seq, _, _, _ = _read_journal_tail_strict(journal_path)
    ts = _utc_now()
    entry = (
        f"\n## seq={seq + 1} · {ts} · {from_stage}→{to_stage} · {status} · by={by_tag}\n"
        f"done:   {done_line}\n"
        f"output: {output_line}\n"
        f"--- handoff ---\n"
        f">>> NEXT\n"
        + "\n".join(next_lines)
        + "\n<<< END\n"
    )
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with open(journal_path, "a", encoding="utf-8") as fh:
        fh.write(entry)
    return seq + 1


def _next_review_file(reviews_dir, prefix):
    reviews_dir.mkdir(parents=True, exist_ok=True)
    highest = 0
    pat = re.compile(rf"^{re.escape(prefix)}-([0-9]+)\.md$")
    for child in reviews_dir.iterdir():
        m = pat.match(child.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return reviews_dir / f"{prefix}-{highest + 1:02d}.md"


def parse_verdict(text, mode):
    """Exact physical line-1 match for VERDICT | CAUSE. Returns (verdict, cause) or (None, reason)."""
    lines = (text or "").splitlines()
    if not lines:
        return None, "empty reviewer output"
    first = lines[0].rstrip("\r")
    rx = PLAN_VERDICT_RE if mode == "plan" else CODE_VERDICT_RE
    m = rx.match(first)
    if not m:
        return None, f"malformed physical line-1 verdict: {first[:120]!r}"
    verdict, cause = m.group(1), m.group(2)
    if verdict in ("ACCEPT", "ACCEPT WITH NITS") and cause != "none":
        return None, f"invalid pairing: {verdict} requires CAUSE: none (got {cause})"
    if verdict == "REJECT" and cause == "none":
        return None, "invalid pairing: REJECT requires non-none CAUSE"
    return verdict, cause


def _strip_dot_slash(path_str):
    norm = path_str.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    return norm


def _matches_forbid(rel_path, patterns):
    norm = _strip_dot_slash(rel_path)
    if norm == ".pipeline" or norm.startswith(".pipeline/"):
        return ".pipeline/"
    for raw in patterns:
        pat = _strip_dot_slash(raw)
        if not pat:
            continue
        if any(ch in pat for ch in "*?["):
            if fnmatch.fnmatch(norm, pat):
                return raw
        else:
            stripped = pat.rstrip("/")
            if norm == stripped or norm.startswith(stripped + "/"):
                return raw
    return None


def _changed_paths_nul(wt_dir, base_sha, head_sha):
    """Return every touched path (including both source and dest of renames/copies) via -z."""
    raw = _git(wt_dir, ["diff", "--name-status", "-z", "-M", f"{base_sha}..{head_sha}"], text=False).stdout
    tokens = [t.decode("utf-8", "surrogateescape") for t in raw.split(b"\x00")]
    paths = []
    i = 0
    while i < len(tokens):
        st = tokens[i]
        if not st:
            break
        code = st[0]
        if code in ("R", "C"):
            if i + 2 < len(tokens):
                paths.append(tokens[i + 1])
                paths.append(tokens[i + 2])
            i += 3
        else:
            if i + 1 < len(tokens):
                paths.append(tokens[i + 1])
            i += 2
    return paths


def _porcelain_paths_nul(repo):
    """Return every dirty/untracked path in repo via `git status --porcelain -z -uall`."""
    raw = _git(repo, ["status", "--porcelain", "-z", "-uall"], text=False).stdout
    tokens = [t.decode("utf-8", "surrogateescape") for t in raw.split(b"\x00")]
    paths = []
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        if not entry:
            break
        xy = entry[:2]
        p = entry[3:]
        paths.append(_strip_dot_slash(p))
        if xy[0] in ("R", "C") and i + 1 < len(tokens):
            paths.append(_strip_dot_slash(tokens[i + 1]))
            i += 2
        else:
            i += 1
    return paths


def _is_step1_allowed_path(rel_path, feature):
    norm = _strip_dot_slash(rel_path)
    if norm in (f".pipeline/{feature}/plan.md", f".pipeline/{feature}/CONTEXT.md"):
        return True
    adr_prefix = f".pipeline/{feature}/docs/adr/"
    if norm.startswith(adr_prefix) and norm.endswith(".md") and ".." not in norm.split("/"):
        return True
    return False


def _resolve_feature(args_feature, cur):
    cur_feat = cur.get("feature", "")
    if not isinstance(cur_feat, str) or not SLUG_RE.fullmatch(cur_feat):
        _die(4, f"invalid feature slug in current.json: {cur_feat!r}")
    if args_feature:
        if not SLUG_RE.fullmatch(args_feature):
            _die(64, f"invalid --feature slug: {args_feature!r}")
        if args_feature != cur_feat:
            _die(4, f"--feature ({args_feature}) does not match current.json.feature ({cur_feat})")
    return cur_feat


def _require_clean_trunk(repo, expected_branch, check_remote=False):
    br = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if br != expected_branch:
        _die(4, f"trunk checkout must be on '{expected_branch}' (found '{br}')")
    status = _git(repo, ["status", "--porcelain"]).stdout.strip()
    if status:
        _die(4, f"dirty trunk checkout; commit or clean before running run-loop:\n{status}")
    head_sha = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    if check_remote:
        _git(repo, ["fetch", "--quiet", "origin", expected_branch])
        remote_sha = _git(repo, ["rev-parse", "FETCH_HEAD"]).stdout.strip()
        if head_sha != remote_sha:
            _die(4, f"local trunk {head_sha[:10]} differs from origin/{expected_branch} {remote_sha[:10]}; sync first")
    return head_sha


def _commit_trunk(repo, expected_head, paths, message, do_push=False, branch="main"):
    cur_head = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    if cur_head != expected_head:
        _die(4, f"trunk HEAD moved unexpectedly ({expected_head[:10]} -> {cur_head[:10]}); aborting commit")
    rels = []
    for p in paths:
        rp = os.path.relpath(str(p), str(repo))
        if rp not in rels:
            rels.append(rp)
    _git(repo, ["add", "--"] + rels)
    _git(repo, ["commit", "--only", "--quiet", "-m", message, "--"] + rels)
    new_head = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    if do_push:
        _git(repo, ["push", "--quiet", "origin", f"{new_head}:refs/heads/{branch}"])
    return new_head


def _plan_blob_sha(repo, feature):
    return _git(repo, ["rev-parse", f"HEAD:.pipeline/{feature}/plan.md"]).stdout.strip()


def _plan_blob_text(repo, blob_sha):
    raw = _git(repo, ["cat-file", "blob", blob_sha], text=False).stdout
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _die(4, f"plan.md blob {blob_sha[:10]} is not valid UTF-8: {exc}")


def _git_lossy_text(repo, args):
    """Run git command returning (decoded_text, had_non_utf8) without crashing on Latin-1/binary diffs."""
    raw = _git(repo, args, text=False).stdout
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace"), True


def _read_pause_after_plan(repo, feature):
    ctrl_path = Path(repo) / ".pipeline" / feature / "control.json"
    if not ctrl_path.is_file():
        return False
    ctrl = _read_json(ctrl_path)
    return bool(ctrl.get("pause_after_plan", False))


def _ensure_worktree(repo, common_dir, trunk_branch, feature, fetch_remote=False):
    wt_root = common_dir / "pipeline-worktrees"
    wt_root.mkdir(parents=True, exist_ok=True)
    wt_dir = wt_root / feature
    feat_branch = f"feat/{feature}"
    if wt_dir.exists():
        br = _git(wt_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        if br != feat_branch:
            _die(4, f"worktree {wt_dir} is on '{br}', expected '{feat_branch}'")
        st = _git(wt_dir, ["status", "--porcelain"]).stdout.strip()
        if st:
            _die(4, f"worktree {wt_dir} is dirty:\n{st}")
        return wt_dir, feat_branch
    has_local = _git(repo, ["show-ref", "--verify", "--quiet", f"refs/heads/{feat_branch}"], check=False).returncode == 0
    if has_local:
        _git(repo, ["worktree", "add", "--quiet", str(wt_dir), feat_branch])
        return wt_dir, feat_branch
    if fetch_remote:
        _git(repo, ["fetch", "--quiet", "origin", feat_branch], check=False)
    has_remote = _git(repo, ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{feat_branch}"], check=False).returncode == 0
    if has_remote:
        _git(repo, ["worktree", "add", "--quiet", "-b", feat_branch, str(wt_dir), f"origin/{feat_branch}"])
    else:
        _git(repo, ["worktree", "add", "--quiet", "-b", feat_branch, str(wt_dir), trunk_branch])
    return wt_dir, feat_branch


def _step2_review_plan_locked(repo, feature, cur, cur_path, trunk, trunk_head, rev_cfg, impl_model, planner_model_hint, do_push):
    """Execute ONE Step 2 (plan-review) verdict under an already-held repo lock.

    Returns (rc, new_trunk_head, info_dict).
    """
    journal_path = repo / ".pipeline" / feature / "journal.md"
    _, tail_trans, _, _ = _read_journal_tail_strict(journal_path)
    if tail_trans != "plan→plan-review · completed":
        _die(4, f"journal tail '{tail_trans}' does not authorize Step 2 (need 'plan→plan-review · completed')")

    plan_blob = _plan_blob_sha(repo, feature)
    plan_text = _plan_blob_text(repo, plan_blob)
    check_plan = _load_check_plan()
    meta, errs = check_plan.extract_plan_meta_from_text(plan_text, expected_feature=feature)
    if errs:
        _die(4, f"plan.md invalid: {', '.join(errs)}")

    planner_model = _resolve_planner_model(planner_model_hint, cur.get("planner_model"), meta.get("planner_model"))
    _check_model_separation(rev_cfg["model_id"], planner_model=planner_model, implementer_model=impl_model)

    rejects = int(cur.get("plan_rejects", 0) or 0)
    if rejects >= MAX_REJECTS:
        _die(2, f"plan_rejects already at {rejects} (>= {MAX_REJECTS}); human intervention required")

    prompt = (
        f"You are Step 2 (pipeline-plan-review) reviewing .pipeline/{feature}/plan.md (blob {plan_blob}) in {repo}.\n"
        "Spot-check >=3 citations from Baseline Evidence against real repo files, verify <=10 specs, "
        "T1-T10 observable acceptance matrix, verify commands, and forbid-paths.\n"
        "Physical line 1 of your reply MUST be EXACTLY:\n"
        "VERDICT: <ACCEPT|ACCEPT WITH NITS|REJECT> | CAUSE: <none|plan_fact|plan_design|scope|env>\n"
        "(ACCEPT / ACCEPT WITH NITS require CAUSE: none; REJECT requires a non-none CAUSE.)\n\n"
        f"--- BEGIN plan.md (immutable blob {plan_blob}) ---\n{plan_text}\n--- END plan.md ---\n"
    )
    rc, out, err, timed_out = _invoke_agent(rev_cfg, prompt, cwd=repo)
    if timed_out or rc != 0:
        _die(4, f"reviewer CLI failed (rc={rc}, timed_out={timed_out}): {err.strip()[:300]}")

    verdict, cause = parse_verdict(out, mode="plan")
    if verdict is None:
        _die(4, f"step 2 fail-closed: {cause}")

    rev_file = _next_review_file(repo / ".pipeline" / feature / "reviews", "plan-review")
    rev_file.write_text(out.strip() + "\n", encoding="utf-8")
    rel_rev = os.path.relpath(str(rev_file), str(repo))
    by_tag = f"reviewer:{rev_cfg['model_id']}"

    if verdict in ("ACCEPT", "ACCEPT WITH NITS"):
        cur["approved_plan_blob"] = plan_blob
        cur["planner_model"] = planner_model
        cur["stage"] = "plan-review"
        pause = _read_pause_after_plan(repo, feature)
        if pause:
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "plan-review", "human", "blocked", by_tag,
                f"{verdict} on blob {plan_blob[:10]} (pause_after_plan=true)", rel_rev,
                ["Await human plan approval (run `run-loop.py approve-plan` or `resume` to proceed)."],
            )
            new_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review {verdict} (paused)", do_push, trunk)
            print(f"PLAN_REVIEW {verdict} PAUSED blob={plan_blob} review={rel_rev}")
            return 3, new_head, {"verdict": verdict, "cause": cause, "blob": plan_blob, "review": rel_rev, "paused": True}
        _write_json(cur_path, cur)
        _append_journal(
            journal_path, "plan-review", "impl", "completed", by_tag,
            f"plan review {verdict} on blob {plan_blob[:10]}", rel_rev,
            [f"Run pipeline-impl for {feature} (zero planner tokens from here)."],
        )
        new_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review {verdict}", do_push, trunk)
        print(f"PLAN_REVIEW {verdict} blob={plan_blob} review={rel_rev}")
        return 0, new_head, {"verdict": verdict, "cause": cause, "blob": plan_blob, "review": rel_rev, "paused": False}

    rejects += 1
    cur["plan_rejects"] = rejects
    cur.pop("approved_plan_blob", None)
    if rejects >= MAX_REJECTS:
        cur["stage"] = "blocked"
        _write_json(cur_path, cur)
        _append_journal(
            journal_path, "plan-review", "human", "blocked", by_tag,
            f"plan rejected ({rejects}/{MAX_REJECTS}, CAUSE: {cause}) — breaker hit", rel_rev,
            [f"Human intervention required: 3rd plan rejection ({rel_rev})."],
        )
        new_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review blocked ({rejects}/{MAX_REJECTS})", do_push, trunk)
        print(f"PLAN_REVIEW BLOCKED rejects={rejects} cause={cause} review={rel_rev}")
        return 2, new_head, {"verdict": verdict, "cause": cause, "rejects": rejects, "review": rel_rev}

    cur["stage"] = "plan-review"
    _write_json(cur_path, cur)
    _append_journal(
        journal_path, "plan-review", "plan", "failed", by_tag,
        f"plan rejected ({rejects}/{MAX_REJECTS}, CAUSE: {cause})", rel_rev,
        [f"Run pipeline-plan to address {rel_rev}."],
    )
    new_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review reject {rejects}/{MAX_REJECTS}", do_push, trunk)
    print(f"PLAN_REVIEW REJECT rejects={rejects} cause={cause} review={rel_rev}")
    return 10, new_head, {"verdict": verdict, "cause": cause, "rejects": rejects, "review": rel_rev}


def cmd_review_plan(args, cfg):
    repo = Path(args.repo).resolve()
    rev_cfg = _validate_role_cfg(cfg, "reviewer")
    impl_model = None
    if "implementer" in cfg:
        impl_model = _validate_role_cfg(cfg, "implementer")["model_id"]

    with _repo_lock(repo):
        cur_path = repo / ".pipeline" / "current.json"
        if not cur_path.is_file():
            _die(4, f"missing {cur_path}")
        cur = _read_json(cur_path)
        feature = _resolve_feature(args.feature, cur)
        trunk = cur.get("branch", "main")
        trunk_head = _require_clean_trunk(repo, trunk, check_remote=args.push)
        rc, _, _ = _step2_review_plan_locked(
            repo, feature, cur, cur_path, trunk, trunk_head,
            rev_cfg, impl_model, args.planner_model, args.push,
        )
        return rc


def _approve_plan_locked(repo, feature, cur, cur_path, trunk, trunk_head, do_push):
    journal_path = repo / ".pipeline" / feature / "journal.md"
    _, tail_trans, _, _ = _read_journal_tail_strict(journal_path)
    if tail_trans != "plan-review→human · blocked":
        _die(4, f"approve-plan requires tail 'plan-review→human · blocked' (found '{tail_trans}')")
    if int(cur.get("plan_rejects", 0) or 0) >= MAX_REJECTS:
        _die(2, f"plan_rejects already at {cur.get('plan_rejects')} (>= {MAX_REJECTS}); human/hunt intervention required")
    if not _read_pause_after_plan(repo, feature):
        _die(4, "approve-plan only applies when control.json sets pause_after_plan=true")
    approved_blob = cur.get("approved_plan_blob", "")
    cur_blob = _plan_blob_sha(repo, feature)
    if not approved_blob or approved_blob != cur_blob:
        _die(4, f"plan.md blob mismatch (approved={approved_blob or '<absent>'}, current={cur_blob}); re-run Step 2")

    cur["stage"] = "impl"
    _write_json(cur_path, cur)
    _append_journal(
        journal_path, "human", "impl", "completed", "human:operator",
        f"operator approved plan blob {cur_blob[:10]}", f".pipeline/{feature}/plan.md",
        [f"Run pipeline-impl for {feature}."],
    )
    new_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): human approved plan", do_push, trunk)
    print(f"PLAN_APPROVED feature={feature} blob={cur_blob}")
    return new_head


def cmd_approve_plan(args):
    repo = Path(args.repo).resolve()
    with _repo_lock(repo):
        cur_path = repo / ".pipeline" / "current.json"
        if not cur_path.is_file():
            _die(4, f"missing {cur_path}")
        cur = _read_json(cur_path)
        feature = _resolve_feature(args.feature, cur)
        trunk = cur.get("branch", "main")
        trunk_head = _require_clean_trunk(repo, trunk, check_remote=args.push)
        _approve_plan_locked(repo, feature, cur, cur_path, trunk, trunk_head, args.push)
        return 0


def _extract_assumptions(impl_stdout):
    """Return the implementer's explicit `## Assumptions` body, or None if omitted."""
    m = re.search(r"(?m)^## Assumptions\s*\n(.*?)(?=^## |\Z)", impl_stdout or "", re.S)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return None


def _parse_recorded_impl_meta(impl_report_path, tail_by, tail_output, feat_branch):
    """Extract recorded implementer model_id and 40-hex head SHA from journal + impl-report.md."""
    rec_model = ""
    if tail_by.startswith("implementer:") and tail_by[len("implementer:"):].strip():
        rec_model = tail_by[len("implementer:"):].strip()
    rec_sha = ""
    m_out = re.fullmatch(rf"{re.escape(feat_branch)}@([0-9a-f]{{40}})", tail_output or "")
    if m_out:
        rec_sha = m_out.group(1)

    if not impl_report_path.is_file():
        _die(4, f"cannot resume Step 4: missing {impl_report_path}")
    txt = impl_report_path.read_text(encoding="utf-8", errors="replace")
    rep_model, rep_sha = "", ""
    mm = re.search(r"(?m)^- implementer:\s*`([^`]+)`\s*$", txt)
    if mm:
        rep_model = mm.group(1).strip()
    ms = re.search(r"(?m)^- head:\s*`([0-9a-f]{40})`\s*$", txt)
    if ms:
        rep_sha = ms.group(1).strip()

    if not rec_sha or not rep_sha or rep_sha != rec_sha:
        _die(4, f"cannot resume Step 4: missing or conflicting recorded head SHA (journal={rec_sha or '<absent>'}, report={rep_sha or '<absent>'})")
    if not rec_model or not rep_model or _norm_model(rep_model) != _norm_model(rec_model):
        _die(4, f"cannot resume Step 4: missing or conflicting recorded implementer model (journal={rec_model or '<absent>'}, report={rep_model or '<absent>'})")
    return rec_model, rec_sha


def _run_verify_pass(wt_dir, feat_branch, expected_sha, verify_cmds, verify_timeout):
    """Run all plan.md verify commands and assert clean tree + exact branch + exact SHA afterward."""
    summary = []
    for cmd in verify_cmds:
        vrc, vout, verr, vto = _run_bounded(["/bin/sh", "-c", cmd], cwd=wt_dir, timeout_s=verify_timeout)
        if vto or vrc != 0:
            return None, (cmd, vrc, vto, (vout + "\n" + verr).strip()[-4000:])
        summary.append(f"PASS (exit 0): {cmd}")
    post_dirty = _git(wt_dir, ["status", "--porcelain"]).stdout.strip()
    post_sha = _git(wt_dir, ["rev-parse", "HEAD"]).stdout.strip()
    post_br = _git(wt_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if post_dirty or post_sha != expected_sha or post_br != feat_branch:
        return None, (
            "<post-verify-cleanliness>",
            1,
            False,
            f"verify mutated worktree: dirty={bool(post_dirty)} sha_match={post_sha == expected_sha} br={post_br}\n{post_dirty[:1000]}",
        )
    return summary, None


def _ensure_remote_feature_sha(wt_dir, feat_branch, expected_sha):
    """Ensure origin/<feat_branch> points to expected_sha (push exact SHA, then verify ls-remote)."""
    _git(wt_dir, ["push", "--quiet", "origin", f"{expected_sha}:refs/heads/{feat_branch}"])
    ls_out = _git(wt_dir, ["ls-remote", "origin", f"refs/heads/{feat_branch}"]).stdout.strip()
    remote_sha = ls_out.split()[0] if ls_out else ""
    if remote_sha != expected_sha:
        _die(4, f"remote {feat_branch} SHA mismatch after push (remote={remote_sha or '<absent>'}, expected={expected_sha})")


def _run_phase2_locked(repo, common_dir, feature, cur, cur_path, trunk, trunk_head, impl_cfg, rev_cfg, planner_model_hint, verify_timeout, do_push, state_cb=None):
    """Run Phase II (Step 3 <-> verify <-> Step 4) under an already-held repo lock."""
    def _emit(**kw):
        if state_cb is not None:
            state_cb(**kw)

    approved_blob = cur.get("approved_plan_blob", "")
    cur_blob = _plan_blob_sha(repo, feature)
    if not approved_blob or approved_blob != cur_blob:
        _die(4, f"plan.md not pinned to an approved Step 2 blob (approved={approved_blob or '<absent>'}, current={cur_blob})")

    plan_text = _plan_blob_text(repo, approved_blob)
    check_plan = _load_check_plan()
    meta, errs = check_plan.extract_plan_meta_from_text(plan_text, expected_feature=feature)
    if errs:
        _die(4, f"plan.md invalid: {', '.join(errs)}")

    planner_model = _resolve_planner_model(planner_model_hint, cur.get("planner_model"), meta.get("planner_model"))
    _check_model_separation(rev_cfg["model_id"], planner_model=planner_model, implementer_model=impl_cfg["model_id"])

    journal_path = repo / ".pipeline" / feature / "journal.md"
    _, tail_trans, tail_by, tail_output = _read_journal_tail_strict(journal_path)
    if tail_trans not in PHASE2_ALLOWED_TAILS:
        _die(4, f"journal tail '{tail_trans}' does not authorize phase2 (need one of {PHASE2_ALLOWED_TAILS})")

    review_rejects = int(cur.get("review_rejects", 0) or 0)
    if review_rejects >= MAX_REJECTS:
        _die(2, f"review_rejects already at {review_rejects} (>= {MAX_REJECTS}); human intervention required")

    wt_dir, feat_branch = _ensure_worktree(repo, common_dir, trunk, feature, fetch_remote=do_push)
    verify_cmds = meta["verify"]
    forbid_paths = meta["forbid_paths"]
    reviews_dir = repo / ".pipeline" / feature / "reviews"

    feedback = ""
    if reviews_dir.is_dir():
        existing_cr = sorted(reviews_dir.glob("code-review-*.md"))
        if existing_cr and tail_trans == "review→impl · failed":
            feedback = f"Prior code review feedback ({existing_cr[-1].name}):\n{existing_cr[-1].read_text(encoding='utf-8')}\n"

    resume_step4 = (tail_trans == "impl→review · completed")
    impl_failures = 0

    while review_rejects < MAX_REJECTS:
        if not resume_step4:
            _emit(status="running", phase="phase2", step="impl", review_rejects=review_rejects, worktree=str(wt_dir), branch=feat_branch)
            before_sha = _git(wt_dir, ["rev-parse", "HEAD"]).stdout.strip()
            impl_prompt = (
                f"You are Step 3 (pipeline-impl) for feature '{feature}' on branch '{feat_branch}' "
                f"in worktree {wt_dir}.\n"
                "Rules:\n"
                "1. Implement the specs AND real unit/integration tests covering EVERY T1-T10 row.\n"
                "2. NEVER touch .pipeline/ or any forbid-paths. Stay on branch " + feat_branch + ".\n"
                f"3. Run EVERY verify command until exit 0: {json.dumps(verify_cmds)}\n"
                "4. Commit all changes cleanly on this branch (clean working tree). Do NOT push or merge.\n"
                "5. Print a `## Assumptions` section in your final summary (or `none`).\n\n"
                f"--- BEGIN plan.md ---\n{plan_text}\n--- END plan.md ---\n"
            )
            if feedback:
                impl_prompt += f"\n--- FEEDBACK TO ADDRESS ---\n{feedback}\n"

            rc, out, err, timed_out = _invoke_agent(impl_cfg, impl_prompt, cwd=wt_dir)
            if timed_out or rc != 0:
                cur["stage"] = "blocked"
                _write_json(cur_path, cur)
                reason = f"timeout={timed_out} rc={rc}"
                _append_journal(
                    journal_path, "impl", "human", "blocked", f"implementer:{impl_cfg['model_id']}",
                    f"implementer died ({reason}) — stopped per hard rule 2", feat_branch,
                    [f"Human intervention required: implementer died ({reason}) in {wt_dir}."],
                )
                trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): impl died ({reason})", do_push, trunk)
                _emit(status="error", phase="phase2", step="impl", reason=f"implementer died ({reason})", exit_code=4)
                _die(4, f"implementer died ({reason}); stderr: {err.strip()[:300]}")

            cur_wt_br = _git(wt_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
            after_sha = _git(wt_dir, ["rev-parse", "HEAD"]).stdout.strip()
            dirty = _git(wt_dir, ["status", "--porcelain"]).stdout.strip()

            if cur_wt_br != feat_branch or dirty or after_sha == before_sha:
                impl_failures += 1
                reason = (
                    f"branch={cur_wt_br} expected_branch={feat_branch} "
                    f"dirty={bool(dirty)} head_advanced={after_sha != before_sha}"
                )
                if impl_failures >= MAX_IMPL_FAILURES_PER_ROUND:
                    cur["stage"] = "blocked"
                    _write_json(cur_path, cur)
                    _append_journal(
                        journal_path, "impl", "human", "blocked", f"implementer:{impl_cfg['model_id']}",
                        f"implementer failed clean-commit check {impl_failures}x ({reason})", feat_branch,
                        [f"Human intervention required: {reason} in {wt_dir}."],
                    )
                    trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): impl blocked", do_push, trunk)
                    _emit(status="blocked", phase="phase2", step="impl", reason=reason, exit_code=2)
                    print(f"PHASE2 BLOCKED stage=impl reason={reason}")
                    return 2
                feedback = f"Previous impl did not leave a clean commit on {feat_branch} ({reason}). Fix and commit cleanly.\n"
                continue

            base_sha = _git(wt_dir, ["merge-base", trunk, feat_branch]).stdout.strip()
            changed_files = _changed_paths_nul(wt_dir, base_sha, after_sha)
            forbid_hits = []
            for f in changed_files:
                hit = _matches_forbid(f, forbid_paths)
                if hit:
                    forbid_hits.append(f"{f} (matches {hit})")
            if forbid_hits:
                impl_failures += 1
                if impl_failures >= MAX_IMPL_FAILURES_PER_ROUND:
                    cur["stage"] = "blocked"
                    _write_json(cur_path, cur)
                    _append_journal(
                        journal_path, "impl", "human", "blocked", f"implementer:{impl_cfg['model_id']}",
                        f"forbid-paths violation: {', '.join(forbid_hits)}", feat_branch,
                        [f"Human intervention required: forbidden paths touched ({', '.join(forbid_hits)})."],
                    )
                    trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): impl forbid-paths blocked", do_push, trunk)
                    _emit(status="blocked", phase="phase2", step="forbid-paths", reason=", ".join(forbid_hits), exit_code=2)
                    print(f"PHASE2 BLOCKED stage=forbid-paths hits={','.join(forbid_hits)}")
                    return 2
                feedback = f"FORBIDDEN PATHS TOUCHED: {', '.join(forbid_hits)}. Revert them and commit cleanly.\n"
                continue

            _emit(status="running", phase="phase2", step="verify", head_sha=after_sha, review_rejects=review_rejects)
            verify_summary, verify_failed = _run_verify_pass(wt_dir, feat_branch, after_sha, verify_cmds, verify_timeout)
            if verify_failed is not None:
                impl_failures += 1
                fcmd, frc, fto, ftail = verify_failed
                if impl_failures >= MAX_IMPL_FAILURES_PER_ROUND:
                    cur["stage"] = "blocked"
                    _write_json(cur_path, cur)
                    _append_journal(
                        journal_path, "impl", "human", "blocked", f"implementer:{impl_cfg['model_id']}",
                        f"verify pre-gate failed {impl_failures}x on `{fcmd}` (rc={frc}, timeout={fto})", feat_branch,
                        [f"Human intervention required: verify failed on `{fcmd}` in {wt_dir}."],
                    )
                    trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): verify pre-gate blocked", do_push, trunk)
                    _emit(status="blocked", phase="phase2", step="verify", reason=f"verify failed on {fcmd} rc={frc}", exit_code=2)
                    print(f"PHASE2 BLOCKED stage=verify cmd={fcmd} rc={frc}")
                    return 2
                feedback = f"Deterministic verify failed (rc={frc}, timeout={fto}) for `{fcmd}`:\n{ftail}\nFix and commit cleanly.\n"
                continue

            impl_failures = 0
            if do_push:
                _ensure_remote_feature_sha(wt_dir, feat_branch, after_sha)

            commits_log, _ = _git_lossy_text(wt_dir, ["log", "--oneline", f"{base_sha}..{after_sha}"])
            commits_log = commits_log.strip()
            assumptions_body = _extract_assumptions(out)
            if assumptions_body is not None:
                assumptions_block = f"## Assumptions\n\n{assumptions_body}\n\n"
            else:
                assumptions_block = "<!-- MISSING: implementer omitted the Assumptions section -->\n\n"
            impl_report = repo / ".pipeline" / feature / "impl-report.md"
            impl_report.write_text(
                f"# Implementation Report ({feature})\n\n"
                f"- branch: `{feat_branch}`\n"
                f"- base: `{base_sha}`\n"
                f"- head: `{after_sha}`\n"
                f"- implementer: `{impl_cfg['model_id']}`\n\n"
                f"## Commits\n\n```text\n{commits_log}\n```\n\n"
                + assumptions_block
                + "## Tests & Verify\n\n"
                + "\n".join(f"- {s}" for s in verify_summary)
                + "\n- Not run: none (all verify commands from plan.md exited 0)\n",
                encoding="utf-8",
            )
            cur["stage"] = "impl"
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "impl", "review", "completed", f"implementer:{impl_cfg['model_id']}",
                f"impl green on {feat_branch}@{after_sha[:10]} ({len(verify_summary)} verify cmds passed)",
                f"{feat_branch}@{after_sha}",
                [f"Run pipeline-review on {feat_branch}@{after_sha}."],
            )
            trunk_head = _commit_trunk(repo, trunk_head, [impl_report, cur_path, journal_path], f"pipeline({feature}): impl @{after_sha[:10]}", do_push, trunk)
        else:
            resume_step4 = False
            impl_report = repo / ".pipeline" / feature / "impl-report.md"
            rec_impl_model, rec_sha = _parse_recorded_impl_meta(impl_report, tail_by, tail_output, feat_branch)
            _check_model_separation(rev_cfg["model_id"], planner_model=planner_model, implementer_model=rec_impl_model)
            cur_wt_br = _git(wt_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
            after_sha = _git(wt_dir, ["rev-parse", "HEAD"]).stdout.strip()
            if cur_wt_br != feat_branch or after_sha != rec_sha or _git(wt_dir, ["status", "--porcelain"]).stdout.strip():
                _die(4, f"cannot resume Step 4: worktree {wt_dir} ({cur_wt_br}@{after_sha}) does not cleanly match recorded {feat_branch}@{rec_sha}")
            base_sha = _git(wt_dir, ["merge-base", trunk, feat_branch]).stdout.strip()
            changed_files = _changed_paths_nul(wt_dir, base_sha, after_sha)
            for f in changed_files:
                hit = _matches_forbid(f, forbid_paths)
                if hit:
                    _die(4, f"cannot resume Step 4: forbidden path {f} matches {hit}")
            if do_push:
                _ensure_remote_feature_sha(wt_dir, feat_branch, after_sha)

        _emit(status="running", phase="phase2", step="review", head_sha=after_sha, review_rejects=review_rejects)
        impl_report_text = impl_report.read_text(encoding="utf-8") if impl_report.is_file() else ""
        step4_verify, step4_vfail = _run_verify_pass(wt_dir, feat_branch, after_sha, verify_cmds, verify_timeout)
        det_reject = None
        if step4_vfail is not None:
            fcmd, frc, fto, ftail = step4_vfail
            det_reject = (
                "impl_bug",
                f"VERDICT: REJECT | CAUSE: impl_bug\n"
                f"Reviewed-Head: {feat_branch}@{after_sha}\n"
                f"Reviewer: deterministic-step4-gate\n\n"
                f"Step 4 independent verify re-run failed for `{fcmd}` (rc={frc}, timeout={fto}):\n{ftail}\n",
            )
        elif not re.search(r"(?m)^## Assumptions\s*$", impl_report_text):
            det_reject = (
                "scope",
                f"VERDICT: REJECT | CAUSE: scope\n"
                f"Reviewed-Head: {feat_branch}@{after_sha}\n"
                f"Reviewer: deterministic-step4-gate\n\n"
                "Missing `## Assumptions` section in impl-report.md (CONTRACT §Anti-cheat #4).\n",
            )

        if det_reject is not None:
            verdict, cause = "REJECT", det_reject[0]
            rev_body = det_reject[1]
            by_tag = f"reviewer:{rev_cfg['model_id']}(deterministic-gate)"
        else:
            diff_stat, stat_non_utf8 = _git_lossy_text(wt_dir, ["diff", "--stat", f"{base_sha}..{after_sha}"])
            full_diff, diff_non_utf8 = _git_lossy_text(wt_dir, ["diff", f"{base_sha}..{after_sha}"])
            if len(full_diff) > DIFF_INLINE_LIMIT:
                full_diff = (
                    full_diff[:DIFF_INLINE_LIMIT]
                    + f"\n... [TRUNCATED at {DIFF_INLINE_LIMIT} bytes; inspect full diff with `git -C {wt_dir} diff {base_sha}..{after_sha}`] ...\n"
                )
            if stat_non_utf8 or diff_non_utf8:
                full_diff = (
                    "[NOTE: diff contains non-UTF-8 bytes; undecodable bytes replaced with U+FFFD — "
                    f"inspect raw bytes with `git -C {wt_dir} diff {base_sha}..{after_sha}` if needed]\n"
                    + full_diff
                )
            review_prompt = (
                f"You are Step 4 (pipeline-review) for feature '{feature}' reviewing {feat_branch}@{after_sha} (base {base_sha}).\n"
                "Mandatory checks:\n"
                "1. Audit impl-report.md `## Assumptions` against plan.md (contradiction => REJECT).\n"
                "2. Verify every T1-T10 row maps to a real test in the diff.\n"
                "3. Mutation / assertion-strength audit: if writable, break 1-2 core logic branches and confirm tests fail (revert immediately); "
                "if read-only, record per-T-row logic-inversion evidence proving each test would fail on broken logic. Hollow/mocked tests => REJECT | CAUSE: weak_test.\n"
                "4. Check for real bugs, regressions, scope creep, or plan/env flaws (CAUSE: plan_fact|plan_design|env).\n"
                "Physical line 1 of your reply MUST be EXACTLY:\n"
                "VERDICT: <ACCEPT|REJECT> | CAUSE: <none|impl_bug|weak_test|scope|plan_fact|plan_design|env>\n"
                "(Strict binary: no ACCEPT WITH NITS. ACCEPT requires CAUSE: none; REJECT requires non-none CAUSE.)\n\n"
                f"--- STEP 4 INDEPENDENT VERIFY RE-RUN (ALL GREEN AT {after_sha}) ---\n" + "\n".join(step4_verify) + "\n\n"
                f"--- impl-report.md ---\n{impl_report_text}\n\n"
                f"--- plan.md ---\n{plan_text}\n\n"
                f"--- git diff --stat {base_sha}..{after_sha} ---\n{diff_stat}\n"
                f"--- git diff {base_sha}..{after_sha} ---\n{full_diff}\n"
            )
            rrc, rout, rerr, rto = _invoke_agent(rev_cfg, review_prompt, cwd=wt_dir)
            if rto or rrc != 0:
                _die(4, f"reviewer CLI failed (rc={rrc}, timed_out={rto}): {rerr.strip()[:300]}")

            verdict, cause = parse_verdict(rout, mode="code")
            if verdict is None:
                _die(4, f"step 4 fail-closed: {cause}")

            rev_lines = rout.strip().splitlines()
            rev_body = "\n".join(
                [rev_lines[0], f"Reviewed-Head: {feat_branch}@{after_sha}", f"Reviewer: {rev_cfg['model_id']}"]
                + rev_lines[1:]
            ) + "\n"
            by_tag = f"reviewer:{rev_cfg['model_id']}"

        if verdict == "ACCEPT":
            final_sha = _git(wt_dir, ["rev-parse", "HEAD"]).stdout.strip()
            final_br = _git(wt_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
            if final_sha != after_sha or final_br != feat_branch or _git(wt_dir, ["status", "--porcelain"]).stdout.strip():
                _die(4, f"worktree mutated during Step 4 review (expected {feat_branch}@{after_sha}, got {final_br}@{final_sha})")
            if do_push:
                _ensure_remote_feature_sha(wt_dir, feat_branch, after_sha)
            rev_file = _next_review_file(reviews_dir, "code-review")
            rev_file.write_text(rev_body, encoding="utf-8")
            rel_rev = os.path.relpath(str(rev_file), str(repo))
            cur["stage"] = "review"
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "review", "review", "completed", by_tag,
                f"headless Step 4 ACCEPT on {feat_branch}@{after_sha}", rel_rev,
                [f"Headless Step 4 ACCEPT for {feat_branch}@{after_sha} ({rel_rev}) — open interactive pipeline-review to verify SHA and arm human-direct GO-gate."],
            )
            trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review ACCEPT @{after_sha[:10]}", do_push, trunk)
            _emit(
                status="accept", phase="phase2", step="review",
                branch=feat_branch, head_sha=after_sha, review=rel_rev, worktree=str(wt_dir),
                review_rejects=review_rejects, exit_code=0,
                next_action=f"Open interactive pipeline-review to confirm {feat_branch}@{after_sha} and merge via GO-gate.",
            )
            print(f"PHASE2 ACCEPT branch={feat_branch} head={after_sha} review={rel_rev} worktree={wt_dir}")
            return 0

        rev_file = _next_review_file(reviews_dir, "code-review")
        rev_file.write_text(rev_body, encoding="utf-8")
        rel_rev = os.path.relpath(str(rev_file), str(repo))

        if cause in SHORT_CIRCUIT_CAUSES:
            cur["stage"] = "blocked"
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "review", "human", "blocked", by_tag,
                f"short-circuit escalation (CAUSE: {cause}) on {feat_branch}@{after_sha[:10]}", rel_rev,
                [f"Human/Planner intervention required: Step 4 reported CAUSE: {cause} ({rel_rev})."],
            )
            trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review escalate {cause}", do_push, trunk)
            _emit(
                status="escalated", phase="phase2", step="review",
                cause=cause, review=rel_rev, review_rejects=review_rejects, exit_code=3,
                next_action=f"Inspect {rel_rev} (CAUSE: {cause}) and resolve before resuming.",
            )
            print(f"PHASE2 ESCALATE cause={cause} review={rel_rev}")
            return 3

        review_rejects += 1
        cur["review_rejects"] = review_rejects
        if review_rejects >= MAX_REJECTS:
            cur["stage"] = "blocked"
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "review", "human", "blocked", by_tag,
                f"code review rejected ({review_rejects}/{MAX_REJECTS}, CAUSE: {cause}) — breaker hit", rel_rev,
                [f"Human intervention required: 3rd code review rejection ({rel_rev})."],
            )
            trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review blocked ({review_rejects}/{MAX_REJECTS})", do_push, trunk)
            _emit(
                status="blocked", phase="phase2", step="review",
                cause=cause, review=rel_rev, review_rejects=review_rejects, exit_code=2,
                next_action=f"3rd code review rejection ({rel_rev}); run pipeline-hunt or inspect manually.",
            )
            print(f"PHASE2 BLOCKED rejects={review_rejects} cause={cause} review={rel_rev}")
            return 2

        cur["stage"] = "review"
        _write_json(cur_path, cur)
        _append_journal(
            journal_path, "review", "impl", "failed", by_tag,
            f"code review rejected ({review_rejects}/{MAX_REJECTS}, CAUSE: {cause})", rel_rev,
            [f"Run pipeline-impl to address {rel_rev}."],
        )
        trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review reject {review_rejects}/{MAX_REJECTS}", do_push, trunk)
        feedback = f"Code review rejection {review_rejects}/{MAX_REJECTS} (CAUSE: {cause}) from {rel_rev}:\n{rev_body.strip()}\n"

    return 2


def cmd_phase2(args, cfg):
    repo = Path(args.repo).resolve()
    impl_cfg = _validate_role_cfg(cfg, "implementer")
    rev_cfg = _validate_role_cfg(cfg, "reviewer")
    verify_timeout = _validate_positive_finite(args.verify_timeout, "--verify-timeout")

    with _repo_lock(repo) as common_dir:
        cur_path = repo / ".pipeline" / "current.json"
        if not cur_path.is_file():
            _die(4, f"missing {cur_path}")
        cur = _read_json(cur_path)
        feature = _resolve_feature(args.feature, cur)
        trunk = cur.get("branch", "main")
        trunk_head = _require_clean_trunk(repo, trunk, check_remote=args.push)
        return _run_phase2_locked(
            repo, common_dir, feature, cur, cur_path, trunk, trunk_head,
            impl_cfg, rev_cfg, args.planner_model, verify_timeout, args.push,
        )


def _read_idea_arg(idea, idea_file):
    if idea and idea_file:
        _die(64, "pass at most one of --idea or --idea-file")
    if idea_file:
        p = Path(idea_file)
        if not p.is_file():
            _die(64, f"idea file not found: {p}")
        return p.read_text(encoding="utf-8").strip()
    return (idea or "").strip()


def _resolve_trunk_for_run(repo, feature, cur_path):
    """Determine (trunk_branch, existing_cur_dict_or_None) for `run`, failing closed on active conflicts."""
    cur_br = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if cur_br == "HEAD":
        _die(4, "trunk checkout is on detached HEAD; check out trunk branch first")
    if cur_br.startswith("feat/"):
        _die(4, f"trunk checkout is on feature branch '{cur_br}'; check out trunk branch first")
    if not cur_path.is_file():
        return cur_br, None
    cur = _read_json(cur_path)
    if not isinstance(cur, dict):
        _die(4, f"invalid current.json object at {cur_path}")
    existing_feat = cur.get("feature", "")
    existing_stage = cur.get("stage", "")
    if existing_feat == feature:
        return cur.get("branch") or cur_br, cur
    if existing_stage == "done":
        return cur.get("branch") or cur_br, None
    _die(4, f"another feature '{existing_feat}' is currently active (stage={existing_stage}); finish or close it before starting '{feature}'")


def _run_step1_planner_locked(repo, feature, cur, cur_path, trunk, trunk_head, plan_cfg, idea_text, do_push, state_dir):
    """Execute Step 1 (Planner CLI) under repo lock, validate write-set & plan.md, commit atomically.

    Returns (outcome, new_trunk_head, info) where outcome is 'planned' or 'needs_human'.
    """
    journal_path = repo / ".pipeline" / feature / "journal.md"
    reviews_dir = repo / ".pipeline" / feature / "reviews"
    plan_path = repo / ".pipeline" / feature / "plan.md"
    questions_path = repo / ".pipeline" / feature / "human-questions.md"
    answers_path = repo / ".pipeline" / feature / "human-answers.md"
    check_plan = _load_check_plan()

    prior_review = ""
    if reviews_dir.is_dir():
        pr_files = sorted(reviews_dir.glob("plan-review-*.md"))
        if pr_files:
            prior_review = f"Latest Step 2 plan review ({pr_files[-1].name}):\n{pr_files[-1].read_text(encoding='utf-8')}\n"

    human_questions = ""
    if questions_path.is_file():
        human_questions = questions_path.read_text(encoding="utf-8").strip()

    human_answers = ""
    if answers_path.is_file():
        human_answers = answers_path.read_text(encoding="utf-8").strip()

    existing_plan = plan_path.read_text(encoding="utf-8") if plan_path.is_file() else ""
    existing_plan_bytes = plan_path.read_bytes() if plan_path.is_file() else None
    format_feedback = ""

    for attempt in range(MAX_PLAN_FORMAT_RETRIES + 1):
        prompt = (
            f"You are Step 1 (pipeline-plan) for feature '{feature}' in repository {repo}.\n"
            f"Your planner_model identifier is `{plan_cfg['model_id']}`.\n"
            "Rules:\n"
            f"1. Survey the real codebase first and write `.pipeline/{feature}/plan.md`.\n"
            "2. `.pipeline/<feature>/plan.md` MUST start with valid YAML frontmatter:\n"
            "   ---\n"
            f"   feature: {feature}\n"
            f"   planner_model: {plan_cfg['model_id']}\n"
            "   verify:\n"
            "     - \"<exact deterministic test/lint/build command>\"\n"
            "   forbid-paths: []\n"
            "   ---\n"
            "   (Replace `forbid-paths: []` with a YAML list when paths are forbidden; never append "
            "trailing inline comments to YAML list lines.)\n"
            "   Body MUST include `## Baseline Evidence` (>=3 verified `file:line` citations), "
            "`## Specifications` (<=10 specs), and `## Acceptance Matrix (T1-T10)` (observable rules).\n"
            f"3. STRICT WRITE-SET: ONLY write `.pipeline/{feature}/plan.md` (and optionally "
            f"`.pipeline/{feature}/CONTEXT.md` or `.pipeline/{feature}/docs/adr/*.md`). NEVER edit product code/tests, "
            "`current.json`, or `journal.md`. NEVER run `git commit` or `git push` — the runner commits atomically.\n"
            "4. HUMAN ESCAPE HATCH: If there is an unresolvable product/business ambiguity that cannot be decided "
            "from the codebase, make NO file edits and print `NEEDS_HUMAN: <short summary>` on physical line 1 "
            "followed by numbered concise questions.\n"
        )
        if idea_text:
            prompt += f"\n--- FEATURE REQUIREMENT ---\n{idea_text}\n"
        if human_questions:
            prompt += f"\n--- PRIOR HUMAN QUESTIONS ---\n{human_questions}\n"
        if human_answers:
            prompt += f"\n--- HUMAN ANSWERS ---\n{human_answers}\n"
        if prior_review:
            prompt += f"\n--- PRIOR STEP 2 PLAN REVIEW FEEDBACK TO ADDRESS ---\n{prior_review}\n"
        if existing_plan:
            prompt += f"\n--- EXISTING plan.md ---\n{existing_plan}\n"
        if format_feedback:
            prompt += f"\n--- FRONTMATTER / SCHEMA ERRORS TO FIX ---\n{format_feedback}\n"

        _log(f"Step 1 (planner:{plan_cfg['model_id']}) attempt {attempt + 1} for {feature}")
        rc, out, err, timed_out = _invoke_agent(plan_cfg, prompt, cwd=repo)
        if timed_out or rc != 0:
            _die(4, f"planner CLI failed (rc={rc}, timed_out={timed_out}): {err.strip()[:300]}")

        after_head = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
        if after_head != trunk_head:
            _die(4, f"planner moved trunk HEAD ({trunk_head[:10]} -> {after_head[:10]}); planner must not commit")

        dirty_paths = _porcelain_paths_nul(repo)
        lines = (out or "").splitlines()
        first_line = lines[0].rstrip("\r") if lines else ""

        if first_line.startswith("NEEDS_HUMAN:"):
            if dirty_paths:
                _die(4, f"planner returned NEEDS_HUMAN but left dirty files: {', '.join(dirty_paths)}")
            q_file = repo / ".pipeline" / feature / "human-questions.md"
            q_file.parent.mkdir(parents=True, exist_ok=True)
            q_file.write_text(out.strip() + "\n", encoding="utf-8")
            rel_q = os.path.relpath(str(q_file), str(repo))
            cur["stage"] = "blocked"
            cur["planner_model"] = plan_cfg["model_id"]
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "plan", "human", "blocked", f"planner:{plan_cfg['model_id']}",
                first_line[:140], rel_q,
                [f"Answer questions in {rel_q} and run `run-loop.py resume --repo {repo} --feature {feature} --answer ...`."],
            )
            new_head = _commit_trunk(repo, trunk_head, [q_file, cur_path, journal_path], f"pipeline({feature}): planner needs human input", do_push, trunk)
            _update_run_state(
                state_dir, status="needs_human", phase="phase1", step="plan",
                questions_file=rel_q, reason=first_line, exit_code=3,
                next_action=f"Review {rel_q} and resume with --answer.",
            )
            print(f"RUN NEEDS_HUMAN feature={feature} questions={rel_q}")
            return "needs_human", new_head, {"questions_file": rel_q, "summary": first_line}

        bad_paths = [p for p in dirty_paths if not _is_step1_allowed_path(p, feature)]
        if bad_paths:
            _die(4, f"planner violated Step 1 write-set (touched: {', '.join(bad_paths)})")
        if not plan_path.is_file():
            _die(4, f"planner exited 0 without writing .pipeline/{feature}/plan.md")
        if prior_review and existing_plan_bytes is not None and plan_path.read_bytes() == existing_plan_bytes:
            if attempt < MAX_PLAN_FORMAT_RETRIES:
                format_feedback = "plan.md is unchanged since the prior Step 2 rejection; revise it to address review feedback."
                continue
            _die(4, f"planner did not modify .pipeline/{feature}/plan.md after Step 2 rejection")

        plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
        meta, meta_errs = check_plan.extract_plan_meta_from_text(plan_text, expected_feature=feature)
        if meta and meta.get("planner_model") and _norm_model(meta["planner_model"]) != _norm_model(plan_cfg["model_id"]):
            meta_errs.append(f"planner_model mismatch (plan.md={meta['planner_model']} != config={plan_cfg['model_id']})")
        if meta_errs:
            if attempt < MAX_PLAN_FORMAT_RETRIES:
                format_feedback = "check-plan.py rejected plan.md with: " + ", ".join(meta_errs)
                existing_plan = plan_text
                continue
            _die(4, f"planner produced invalid plan.md after retry: {', '.join(meta_errs)}")

        cur["stage"] = "plan"
        cur["planner_model"] = plan_cfg["model_id"]
        cur.pop("approved_plan_blob", None)
        _write_json(cur_path, cur)
        _append_journal(
            journal_path, "plan", "plan-review", "completed", f"planner:{plan_cfg['model_id']}",
            f"authored/revised .pipeline/{feature}/plan.md ({len(meta['verify'])} verify cmds)",
            f".pipeline/{feature}/plan.md",
            [f"Run pipeline-plan-review for {feature}."],
        )
        commit_paths = [repo / p for p in dirty_paths]
        if plan_path not in commit_paths:
            commit_paths.append(plan_path)
        commit_paths.extend([cur_path, journal_path])
        new_head = _commit_trunk(repo, trunk_head, commit_paths, f"pipeline({feature}): plan ready for review", do_push, trunk)
        _log(f"Step 1 committed plan.md at trunk {new_head[:10]}")
        return "planned", new_head, {"plan_path": str(plan_path)}

    _die(4, "planner failed unexpectedly")


def _spawn_background(subcmd, args, repo, feature, config_path, idea_text=None, answer_text=None):
    """Validate prerequisites synchronously, then detach a background worker process."""
    run_dir = _run_dir_for(repo, feature)
    run_dir.mkdir(parents=True, exist_ok=True)
    pid_file = run_dir / "pid"
    if pid_file.is_file():
        try:
            old_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except Exception:
            old_pid = 0
        if _pid_is_alive(old_pid):
            _die(4, f"background run already active for '{feature}' (pid={old_pid}); inspect with `status` or stop with `stop`")

    if not _probe_lock_free(repo):
        _die(4, f"lock-busy: another run-loop holds {_git_common_dir(repo) / 'pipeline-run-loop.lock'}")

    child_argv = [
        sys.executable,
        str(Path(__file__).resolve()),
        subcmd,
        "--repo", str(repo),
        "--feature", feature,
        "--config", str(Path(config_path).expanduser().resolve()),
        "--verify-timeout", str(args.verify_timeout),
        "--_state-dir", str(run_dir),
    ]
    if args.push:
        child_argv.append("--push")
    if idea_text:
        idea_saved = run_dir / "idea.md"
        idea_saved.write_text(idea_text + "\n", encoding="utf-8")
        child_argv.extend(["--idea-file", str(idea_saved)])
    if answer_text:
        ans_saved = run_dir / "resume-answer.md"
        ans_saved.write_text(answer_text + "\n", encoding="utf-8")
        child_argv.extend(["--answer-file", str(ans_saved)])

    log_path = run_dir / "run.log"
    state_path = run_dir / "state.json"
    _update_run_state(
        run_dir,
        status="starting",
        feature=feature,
        repo=str(repo),
        started_at=_utc_now(),
        log_path=str(log_path),
        exit_code=None,
        reason="",
    )
    with open(log_path, "a", encoding="utf-8") as log_fh:
        log_fh.write(f"\n=== [{_utc_now()}] spawning background {subcmd} for {feature} ===\n")
        log_fh.flush()
        proc = subprocess.Popen(
            child_argv,
            cwd=str(repo),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
    _update_run_state(run_dir, pid=proc.pid)
    print(f"RUN_STARTED feature={feature} pid={proc.pid} state={state_path} log={log_path}")
    return 0


def _run_full_pipeline(args, cfg, repo, feature, idea_text, state_dir):
    plan_cfg = _validate_role_cfg(cfg, "planner")
    impl_cfg = _validate_role_cfg(cfg, "implementer")
    rev_cfg = _validate_role_cfg(cfg, "reviewer")
    verify_timeout = _validate_positive_finite(args.verify_timeout, "--verify-timeout")
    _check_model_separation(rev_cfg["model_id"], planner_model=plan_cfg["model_id"], implementer_model=impl_cfg["model_id"])

    _install_stop_handler(state_dir)
    _update_run_state(
        state_dir,
        status="running",
        feature=feature,
        repo=str(repo),
        pid=os.getpid(),
        planner_model=plan_cfg["model_id"],
        implementer_model=impl_cfg["model_id"],
        reviewer_model=rev_cfg["model_id"],
    )

    with _repo_lock(repo) as common_dir:
        cur_path = repo / ".pipeline" / "current.json"
        trunk, existing_cur = _resolve_trunk_for_run(repo, feature, cur_path)
        trunk_head = _require_clean_trunk(repo, trunk, check_remote=args.push)

        if existing_cur is None:
            remote_r = _git(repo, ["config", "--get", "remote.origin.url"], check=False)
            repo_id = remote_r.stdout.strip() if remote_r.returncode == 0 and remote_r.stdout.strip() else str(repo)
            cur = {
                "repo": repo_id,
                "branch": trunk,
                "feature": feature,
                "stage": "plan",
                "plan_rejects": 0,
                "review_rejects": 0,
                "planner_model": plan_cfg["model_id"],
            }
        else:
            cur = existing_cur
            if cur.get("planner_model") and _norm_model(cur["planner_model"]) != _norm_model(plan_cfg["model_id"]):
                _die(4, f"model-separation/config mismatch: current.json planner_model={cur['planner_model']} != runners.json planner.model_id={plan_cfg['model_id']}")

        journal_path = repo / ".pipeline" / feature / "journal.md"
        saved_idea = Path(state_dir) / "idea.md" if state_dir else None
        if idea_text and saved_idea is not None:
            saved_idea.parent.mkdir(parents=True, exist_ok=True)
            saved_idea.write_text(idea_text + "\n", encoding="utf-8")
        elif not idea_text and saved_idea is not None and saved_idea.is_file():
            idea_text = saved_idea.read_text(encoding="utf-8").strip()

        # ---- Phase I Loop: Step 1 Planner <-> Step 2 Reviewer ----
        while True:
            _, tail_trans, _, tail_out = _read_journal_tail_strict(journal_path)
            if tail_trans in PHASE2_ALLOWED_TAILS:
                break
            if tail_trans in ("review→review · completed", "review→done · completed"):
                _update_run_state(state_dir, status="accept", phase="done", step="review", exit_code=0, reason=tail_trans)
                print(f"RUN ALREADY_COMPLETE feature={feature} tail='{tail_trans}' output={tail_out}")
                return 0
            if tail_trans and tail_trans.endswith("· blocked"):
                pr_cnt = int(cur.get("plan_rejects", 0) or 0)
                rr_cnt = int(cur.get("review_rejects", 0) or 0)
                if pr_cnt >= MAX_REJECTS or rr_cnt >= MAX_REJECTS:
                    _update_run_state(state_dir, status="blocked", reason=tail_trans, plan_rejects=pr_cnt, review_rejects=rr_cnt, exit_code=2)
                    _die(2, f"feature '{feature}' is blocked at breaker ({tail_trans}); human/hunt intervention required")
                _update_run_state(state_dir, status="escalated", reason=tail_trans, exit_code=3)
                _die(3, f"feature '{feature}' is blocked ({tail_trans}); resolve and run `resume`")

            if tail_trans in PHASE1_PLAN_TAILS:
                plan_file = repo / ".pipeline" / feature / "plan.md"
                if tail_trans is None and not plan_file.is_file() and not idea_text:
                    _die(64, "--idea or --idea-file is required when starting a new feature with no plan.md")
                _update_run_state(state_dir, status="running", phase="phase1", step="plan", plan_rejects=int(cur.get("plan_rejects", 0) or 0))
                outcome, trunk_head, _ = _run_step1_planner_locked(
                    repo, feature, cur, cur_path, trunk, trunk_head,
                    plan_cfg, idea_text, args.push, state_dir,
                )
                if outcome == "needs_human":
                    return 3
                continue

            if tail_trans == "plan→plan-review · completed":
                _update_run_state(state_dir, status="running", phase="phase1", step="plan-review", plan_rejects=int(cur.get("plan_rejects", 0) or 0))
                _log(f"Step 2 (reviewer:{rev_cfg['model_id']}) reviewing plan.md for {feature}")
                rc2, trunk_head, info2 = _step2_review_plan_locked(
                    repo, feature, cur, cur_path, trunk, trunk_head,
                    rev_cfg, impl_cfg["model_id"], plan_cfg["model_id"], args.push,
                )
                if rc2 == 0:
                    _log(f"Step 2 passed ({info2['verdict']}); entering Phase II (0 planner tokens from here)")
                    break
                if rc2 == 10:
                    _log(f"Step 2 rejected ({info2['rejects']}/{MAX_REJECTS}, CAUSE: {info2['cause']}); looping back to Step 1 planner")
                    continue
                if rc2 == 3:
                    _update_run_state(
                        state_dir, status="paused_after_plan", phase="phase1", step="plan-review",
                        review=info2.get("review", ""), exit_code=3,
                        next_action=f"Review plan and run `resume --repo {repo} --feature {feature}`.",
                    )
                    return 3
                _update_run_state(
                    state_dir, status="blocked", phase="phase1", step="plan-review",
                    plan_rejects=info2.get("rejects", MAX_REJECTS), cause=info2.get("cause", ""),
                    review=info2.get("review", ""), exit_code=2,
                    next_action=f"3rd plan rejection ({info2.get('review', '')}); human intervention required.",
                )
                return 2

            _die(4, f"unexpected journal tail '{tail_trans}' for `run`")

        # ---- Phase II Loop: Step 3 Implementer <-> verify <-> Step 4 Reviewer (0 planner tokens) ----
        def _state_cb(**kw):
            _update_run_state(state_dir, **kw)

        return _run_phase2_locked(
            repo, common_dir, feature, cur, cur_path, trunk, trunk_head,
            impl_cfg, rev_cfg, plan_cfg["model_id"], verify_timeout, args.push,
            state_cb=_state_cb,
        )


def cmd_run(args, cfg):
    repo = Path(args.repo).resolve()
    feature = args.feature
    if not SLUG_RE.fullmatch(feature):
        _die(64, f"invalid --feature slug: {feature!r}")
    idea_text = _read_idea_arg(args.idea, args.idea_file)

    # Validate roles + separation + trunk cleanly before any detach!
    plan_cfg = _validate_role_cfg(cfg, "planner")
    impl_cfg = _validate_role_cfg(cfg, "implementer")
    rev_cfg = _validate_role_cfg(cfg, "reviewer")
    _validate_positive_finite(args.verify_timeout, "--verify-timeout")
    _check_model_separation(rev_cfg["model_id"], planner_model=plan_cfg["model_id"], implementer_model=impl_cfg["model_id"])

    if args.background:
        cur_path = repo / ".pipeline" / "current.json"
        trunk, existing_cur = _resolve_trunk_for_run(repo, feature, cur_path)
        _require_clean_trunk(repo, trunk, check_remote=args.push)
        if existing_cur is not None and existing_cur.get("planner_model"):
            if _norm_model(existing_cur["planner_model"]) != _norm_model(plan_cfg["model_id"]):
                _die(4, f"model-separation/config mismatch: current.json planner_model={existing_cur['planner_model']} != runners.json planner.model_id={plan_cfg['model_id']}")
        journal_path = repo / ".pipeline" / feature / "journal.md"
        _, tail_trans, _, _ = _read_journal_tail_strict(journal_path)
        plan_file = repo / ".pipeline" / feature / "plan.md"
        if tail_trans is None and not plan_file.is_file() and not idea_text:
            _die(64, "--idea or --idea-file is required when starting a new feature with no plan.md")
        return _spawn_background("run", args, repo, feature, args.config, idea_text=idea_text)

    state_dir = Path(args._state_dir) if args._state_dir else _run_dir_for(repo, feature)
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        rc = _run_full_pipeline(args, cfg, repo, feature, idea_text, state_dir)
        _update_run_state(state_dir, exit_code=rc)
        return rc
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 4
        if code not in (0, 2, 3):
            _update_run_state(state_dir, status="error", reason=_LAST_DIE_MSG, exit_code=code)
        raise
    except Exception as exc:
        _update_run_state(state_dir, status="error", reason=str(exc), exit_code=4)
        raise


def _is_pause_after_plan_wait(repo, feature, cur, tail_trans):
    """True ONLY when blocked by pause_after_plan=true with an approved pin (never a 3rd-reject breaker)."""
    if tail_trans != "plan-review→human · blocked":
        return False
    if int(cur.get("plan_rejects", 0) or 0) >= MAX_REJECTS:
        return False
    return _read_pause_after_plan(repo, feature) and bool(cur.get("approved_plan_blob"))


def _check_resume_blocked(repo, feature, cur, tail_trans, answer_text, state_dir=None):
    """Fail closed before spawning/approving if feature is blocked by breaker or unresolved escalation."""
    if not (tail_trans and tail_trans.endswith("· blocked")):
        return
    pr_cnt = int(cur.get("plan_rejects", 0) or 0)
    rr_cnt = int(cur.get("review_rejects", 0) or 0)
    if pr_cnt >= MAX_REJECTS or rr_cnt >= MAX_REJECTS:
        _update_run_state(
            state_dir, status="blocked", reason=tail_trans,
            plan_rejects=pr_cnt, review_rejects=rr_cnt, exit_code=2,
        )
        _die(2, f"feature '{feature}' is blocked at breaker ({tail_trans}); human/hunt intervention required")
    if answer_text and tail_trans == "plan→human · blocked":
        return
    if _is_pause_after_plan_wait(repo, feature, cur, tail_trans):
        return
    _update_run_state(state_dir, status="escalated", reason=tail_trans, exit_code=3)
    _die(3, f"feature '{feature}' is blocked ({tail_trans}); pass --answer or resolve before resuming")


def cmd_resume(args, cfg):
    repo = Path(args.repo).resolve()
    feature = args.feature
    if not SLUG_RE.fullmatch(feature):
        _die(64, f"invalid --feature slug: {feature!r}")
    answer_text = _read_idea_arg(args.answer, args.answer_file)

    plan_cfg = _validate_role_cfg(cfg, "planner")
    impl_cfg = _validate_role_cfg(cfg, "implementer")
    rev_cfg = _validate_role_cfg(cfg, "reviewer")
    _validate_positive_finite(args.verify_timeout, "--verify-timeout")
    _check_model_separation(rev_cfg["model_id"], planner_model=plan_cfg["model_id"], implementer_model=impl_cfg["model_id"])

    if args.background:
        cur_path = repo / ".pipeline" / "current.json"
        if not cur_path.is_file():
            _die(4, f"cannot resume: missing {cur_path}")
        cur = _read_json(cur_path)
        _resolve_feature(feature, cur)
        trunk = cur.get("branch", "main")
        _require_clean_trunk(repo, trunk, check_remote=args.push)
        journal_path = repo / ".pipeline" / feature / "journal.md"
        _, tail_trans, _, _ = _read_journal_tail_strict(journal_path)
        if answer_text and tail_trans != "plan→human · blocked":
            _die(4, f"--answer only applies when tail is 'plan→human · blocked' (found '{tail_trans}')")
        _check_resume_blocked(repo, feature, cur, tail_trans, answer_text, state_dir=_run_dir_for(repo, feature))
        return _spawn_background("resume", args, repo, feature, args.config, answer_text=answer_text)

    state_dir = Path(args._state_dir) if args._state_dir else _run_dir_for(repo, feature)
    state_dir.mkdir(parents=True, exist_ok=True)
    _install_stop_handler(state_dir)

    with _repo_lock(repo):
        cur_path = repo / ".pipeline" / "current.json"
        if not cur_path.is_file():
            _die(4, f"cannot resume: missing {cur_path}")
        cur = _read_json(cur_path)
        _resolve_feature(feature, cur)
        trunk = cur.get("branch", "main")
        trunk_head = _require_clean_trunk(repo, trunk, check_remote=args.push)
        journal_path = repo / ".pipeline" / feature / "journal.md"
        _, tail_trans, _, _ = _read_journal_tail_strict(journal_path)

        if answer_text and tail_trans != "plan→human · blocked":
            _die(4, f"--answer only applies when tail is 'plan→human · blocked' (found '{tail_trans}')")
        _check_resume_blocked(repo, feature, cur, tail_trans, answer_text, state_dir=state_dir)

        if answer_text:
            ans_path = repo / ".pipeline" / feature / "human-answers.md"
            ans_path.parent.mkdir(parents=True, exist_ok=True)
            with open(ans_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n## Answer ({_utc_now()})\n\n{answer_text}\n")
            rel_ans = os.path.relpath(str(ans_path), str(repo))
            cur["stage"] = "plan"
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "human", "plan", "completed", "human:operator",
                "operator answered planner questions", rel_ans,
                [f"Run pipeline-plan for {feature} with {rel_ans}."],
            )
            _commit_trunk(repo, trunk_head, [ans_path, cur_path, journal_path], f"pipeline({feature}): human answered planner questions", args.push, trunk)
        elif _is_pause_after_plan_wait(repo, feature, cur, tail_trans):
            _approve_plan_locked(repo, feature, cur, cur_path, trunk, trunk_head, args.push)

    try:
        rc = _run_full_pipeline(args, cfg, repo, feature, "", state_dir)
        _update_run_state(state_dir, exit_code=rc)
        return rc
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 4
        if code not in (0, 2, 3):
            _update_run_state(state_dir, status="error", reason=_LAST_DIE_MSG, exit_code=code)
        raise


def _resolve_status_feature(repo, args_feature):
    if args_feature:
        if not SLUG_RE.fullmatch(args_feature):
            _die(64, f"invalid --feature slug: {args_feature!r}")
        return args_feature
    cur_path = repo / ".pipeline" / "current.json"
    if cur_path.is_file():
        cur = _read_json(cur_path)
        feat = cur.get("feature", "") if isinstance(cur, dict) else ""
        if isinstance(feat, str) and SLUG_RE.fullmatch(feat):
            return feat
    _die(64, "pass --feature <slug> (or have .pipeline/current.json present)")


def cmd_status(args):
    repo = Path(args.repo).resolve()
    feature = _resolve_status_feature(repo, args.feature)
    run_dir = _run_dir_for(repo, feature)
    state_path = run_dir / "state.json"
    pid_path = run_dir / "pid"
    log_path = run_dir / "run.log"

    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    pid = None
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except Exception:
            pid = None
    elif isinstance(state.get("pid"), int):
        pid = state["pid"]
    alive = _pid_is_alive(pid)

    cur_path = repo / ".pipeline" / "current.json"
    cur = {}
    if cur_path.is_file():
        try:
            cur = json.loads(cur_path.read_text(encoding="utf-8"))
        except Exception:
            cur = {}
    journal_path = repo / ".pipeline" / feature / "journal.md"
    seq, tail_trans, tail_by, tail_out = (0, None, "", "")
    if journal_path.is_file():
        seq, tail_trans, tail_by, tail_out = _read_journal_tail_strict(journal_path)

    status_val = state.get("status", "unknown")
    if status_val in ("starting", "running") and not alive:
        status_val = "exited_unexpectedly"

    out_obj = {
        "feature": feature,
        "repo": str(repo),
        "status": status_val,
        "pid": pid,
        "alive": alive,
        "phase": state.get("phase", ""),
        "step": state.get("step", ""),
        "stage": cur.get("stage", "") if cur.get("feature") == feature else "",
        "plan_rejects": cur.get("plan_rejects", 0) if cur.get("feature") == feature else 0,
        "review_rejects": cur.get("review_rejects", 0) if cur.get("feature") == feature else 0,
        "journal_seq": seq,
        "journal_tail": tail_trans or "",
        "journal_output": tail_out,
        "exit_code": state.get("exit_code"),
        "reason": state.get("reason", ""),
        "next_action": state.get("next_action", ""),
        "log_path": str(log_path) if log_path.is_file() else "",
    }
    if args.json:
        print(json.dumps(out_obj, indent=2, ensure_ascii=False))
    else:
        print(
            f"STATUS feature={feature} status={out_obj['status']} alive={alive} pid={pid or '-'} "
            f"phase={out_obj['phase'] or '-'} step={out_obj['step'] or '-'} "
            f"plan_rejects={out_obj['plan_rejects']} review_rejects={out_obj['review_rejects']} "
            f"exit={out_obj['exit_code'] if out_obj['exit_code'] is not None else '-'}"
        )
        if out_obj["journal_tail"]:
            print(f"JOURNAL seq={seq} tail={out_obj['journal_tail']} output={tail_out}")
        if out_obj["reason"]:
            print(f"REASON {out_obj['reason']}")
        if out_obj["next_action"]:
            print(f"NEXT {out_obj['next_action']}")
        if out_obj["log_path"]:
            print(f"LOG {out_obj['log_path']}")
    return 0


def cmd_logs(args):
    repo = Path(args.repo).resolve()
    feature = _resolve_status_feature(repo, args.feature)
    log_path = _run_dir_for(repo, feature) / "run.log"
    if not log_path.is_file():
        _die(4, f"no run log found at {log_path}")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail_n = max(1, int(args.tail))
    for line in lines[-tail_n:]:
        print(line)
    return 0


def _read_pid_file(path):
    if not path.is_file():
        return None
    try:
        v = int(path.read_text(encoding="utf-8").strip())
        return v if v > 0 else None
    except Exception:
        return None


def cmd_stop(args):
    repo = Path(args.repo).resolve()
    feature = _resolve_status_feature(repo, args.feature)
    run_dir = _run_dir_for(repo, feature)
    pid_path = run_dir / "pid"
    child_pid_path = run_dir / "active-child.pid"
    if not pid_path.is_file():
        _die(4, f"no pid file found for '{feature}' at {pid_path}")
    pid = _read_pid_file(pid_path)
    if pid is None:
        _die(4, f"invalid pid file at {pid_path}")
    if pid == os.getpid():
        _die(4, "refusing to stop current process")

    child_pid = _read_pid_file(child_pid_path)
    worker_alive = _pg_is_alive(pid) or _pid_is_alive(pid)
    child_alive = (child_pid is not None) and (_pg_is_alive(child_pid) or _pid_is_alive(child_pid))
    if not worker_alive and not child_alive:
        if child_pid_path.exists():
            try:
                child_pid_path.unlink()
            except OSError:
                pass
        _update_run_state(run_dir, status="stopped_by_user", reason="already exited")
        print(f"RUN_STOPPED feature={feature} pid={pid} was_alive=false")
        return 0

    # Signal worker (its SIGTERM handler also terminates the whole child process group), then
    # explicitly verify every owned process group is completely dead before returning RUN_STOPPED.
    if worker_alive:
        _kill_pg_and_wait(pid, grace_s=2.0)
    latest_child = _read_pid_file(child_pid_path) or child_pid
    for cpg in {p for p in (latest_child, child_pid) if p}:
        if _pg_is_alive(cpg) or _pid_is_alive(cpg):
            _kill_pg_and_wait(cpg, grace_s=1.5)
        if _pg_is_alive(cpg) or _pid_is_alive(cpg):
            _die(4, f"failed to terminate active child process group {cpg}")
    if _pg_is_alive(pid) or _pid_is_alive(pid):
        _die(4, f"failed to terminate background worker process group {pid}")
    if child_pid_path.exists():
        try:
            child_pid_path.unlink()
        except OSError:
            pass
    _update_run_state(run_dir, status="stopped_by_user", reason="stopped via run-loop stop")
    print(f"RUN_STOPPED feature={feature} pid={pid} was_alive=true")
    return 0


def _detect_codex_bin():
    releases = Path.home() / ".codex" / "packages" / "app-server-daemon" / "releases"
    if releases.is_dir():
        cands = sorted(releases.glob("*/codex"), reverse=True)
        for c in cands:
            if c.is_file() and os.access(str(c), os.X_OK):
                return str(c)
    return shutil.which("codex") or "codex"


def _read_local_json_field(path, key, default):
    try:
        d = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        v = d.get(key) if isinstance(d, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    except Exception:
        pass
    return default


def cmd_init_config(args):
    out_path = Path(args.output).expanduser().resolve()
    if out_path.exists() and not args.force:
        _die(64, f"config already exists at {out_path} (pass --force to overwrite)")

    agy_bin = shutil.which("agy")
    claude_bin = shutil.which("claude")
    pi_bin = shutil.which("pi")
    codex_bin = _detect_codex_bin()

    if agy_bin:
        agy_model = _read_local_json_field("~/.gemini/antigravity-cli/settings.json", "model", "Gemini 4 Neon (High)")
        planner_cfg = {
            "model_id": agy_model,
            "argv": [agy_bin, "-p", "--mode", "auto", "--model", agy_model, "--dangerously-skip-permissions", "{prompt}"],
            "prompt_via": "arg",
            "timeout_s": 900,
        }
    elif claude_bin:
        planner_cfg = {
            "model_id": "opus",
            "argv": [claude_bin, "-p", "--model", "opus", "--permission-mode", "acceptEdits", "{prompt}"],
            "prompt_via": "arg",
            "timeout_s": 900,
        }
    else:
        planner_cfg = {
            "model_id": "planner-frontier-a",
            "argv": ["<planner-cli>", "-p", "--model", "planner-frontier-a", "{prompt}"],
            "prompt_via": "arg",
            "timeout_s": 900,
        }

    if pi_bin:
        pi_model = _read_local_json_field("~/.pi/agent/settings.json", "defaultModel", "group/pi-main")
        impl_cfg = {
            "model_id": pi_model,
            "argv": [pi_bin, "-p", "--no-session", "--no-mcp", "--model", pi_model, "--", "{prompt}"],
            "prompt_via": "arg",
            "timeout_s": 1800,
        }
    else:
        impl_cfg = {
            "model_id": "implementer-cli-c",
            "argv": ["<impl-cli>", "-p", "--model", "implementer-cli-c", "{prompt}"],
            "prompt_via": "arg",
            "timeout_s": 1800,
        }

    rev_model = "gpt-6.2-astra"
    reviewer_cfg = {
        "model_id": rev_model,
        "argv": [codex_bin, "exec", "-s", "read-only", "-m", rev_model, "-"],
        "prompt_via": "stdin",
        "timeout_s": 600,
    }

    cfg = {
        "planner": planner_cfg,
        "implementer": impl_cfg,
        "reviewer": reviewer_cfg,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(out_path, cfg)
    print(
        f"INIT_CONFIG ok path={out_path} "
        f"planner={planner_cfg['model_id']}({planner_cfg['argv'][0]}) "
        f"implementer={impl_cfg['model_id']}({impl_cfg['argv'][0]}) "
        f"reviewer={reviewer_cfg['model_id']}({reviewer_cfg['argv'][0]})"
    )
    return 0


def main(argv):
    parser = _Exit64Parser(description="Stateless headless CLI runner for pipeline v2 (3-Agent 4-Step).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp_init = sub.add_parser("init-config", help="Generate ~/.config/pipeline/runners.json from detected CLIs")
    sp_init.add_argument("--output", default=DEFAULT_CONFIG_PATH, help="Output config path")
    sp_init.add_argument("--force", action="store_true", help="Overwrite existing config")

    sp_run = sub.add_parser("run", help="Run full 4-step pipeline end-to-end (foreground or --background)")
    sp_run.add_argument("--repo", required=True, help="Path to target git repo (checked out on trunk)")
    sp_run.add_argument("--feature", required=True, help="Feature slug")
    sp_run.add_argument("--idea", default="", help="Feature idea / requirement text")
    sp_run.add_argument("--idea-file", default="", help="Path to file containing feature idea")
    sp_run.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to runners.json")
    sp_run.add_argument("--push", action="store_true", help="Push trunk/feature branch after commits (never force)")
    sp_run.add_argument("--verify-timeout", type=float, default=600.0, help="Per-verify-command timeout seconds")
    sp_run.add_argument("--background", action="store_true", help="Run detached in background and print PID/log paths")
    sp_run.add_argument("--_state-dir", default="", help=argparse.SUPPRESS)

    sp_res = sub.add_parser("resume", help="Resume a paused/NEEDS_HUMAN/interrupted feature")
    sp_res.add_argument("--repo", required=True, help="Path to target git repo (checked out on trunk)")
    sp_res.add_argument("--feature", required=True, help="Feature slug")
    sp_res.add_argument("--answer", default="", help="Human answer for NEEDS_HUMAN pause")
    sp_res.add_argument("--answer-file", default="", help="Path to file with human answer")
    sp_res.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to runners.json")
    sp_res.add_argument("--push", action="store_true", help="Push trunk/feature branch after commits")
    sp_res.add_argument("--verify-timeout", type=float, default=600.0, help="Per-verify-command timeout seconds")
    sp_res.add_argument("--background", action="store_true", help="Run detached in background")
    sp_res.add_argument("--_state-dir", default="", help=argparse.SUPPRESS)

    sp_st = sub.add_parser("status", help="Show live pipeline run status")
    sp_st.add_argument("--repo", required=True, help="Path to target git repo")
    sp_st.add_argument("--feature", default="", help="Feature slug (defaults to .pipeline/current.json)")
    sp_st.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    sp_lg = sub.add_parser("logs", help="Show background run log tail")
    sp_lg.add_argument("--repo", required=True, help="Path to target git repo")
    sp_lg.add_argument("--feature", default="", help="Feature slug (defaults to .pipeline/current.json)")
    sp_lg.add_argument("--tail", type=int, default=80, help="Number of trailing lines to show")

    sp_sp = sub.add_parser("stop", help="Stop a running background pipeline")
    sp_sp.add_argument("--repo", required=True, help="Path to target git repo")
    sp_sp.add_argument("--feature", default="", help="Feature slug (defaults to .pipeline/current.json)")

    for name in ("review-plan", "approve-plan", "phase2"):
        sp = sub.add_parser(name)
        sp.add_argument("--repo", required=True, help="Path to target git repo (checked out on trunk)")
        sp.add_argument("--feature", default="", help="Feature slug (must match .pipeline/current.json.feature)")
        sp.add_argument("--push", action="store_true", help="Push trunk/feature branch after commits (never force)")
        if name in ("review-plan", "phase2"):
            sp.add_argument("--config", required=True, help="Path to runners.json")
            sp.add_argument("--planner-model", default="", help="Optional planner model_id (must not conflict)")
        if name == "phase2":
            sp.add_argument("--verify-timeout", type=float, default=600.0, help="Per-verify-command timeout seconds")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "init-config":
            return cmd_init_config(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "logs":
            return cmd_logs(args)
        if args.cmd == "stop":
            return cmd_stop(args)
        if args.cmd == "approve-plan":
            return cmd_approve_plan(args)

        cfg_path = Path(args.config).expanduser().resolve()
        cfg = _read_json(cfg_path, exit_code=64)
        if not isinstance(cfg, dict):
            _die(64, "runners.json root must be a JSON object")
        if args.cmd == "run":
            return cmd_run(args, cfg)
        if args.cmd == "resume":
            return cmd_resume(args, cfg)
        if args.cmd == "review-plan":
            return cmd_review_plan(args, cfg)
        return cmd_phase2(args, cfg)
    except RuntimeError as exc:
        _die(4, str(exc))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
