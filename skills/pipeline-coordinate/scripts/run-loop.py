#!/usr/bin/env python3
"""run-loop.py — stateless headless CLI runner for the v2 3-Agent 4-Step pipeline.

Subcommands:
  review-plan   Run ONE headless Step 2 (plan-review) verdict against .pipeline/<feature>/plan.md.
  approve-plan  Record explicit human approval after pause_after_plan=true (human->impl).
  phase2        Run Phase II (Step 3 implementer CLI <-> deterministic verify <-> Step 4 reviewer CLI)
                with ZERO planner tokens until ACCEPT, 3rd rejection breaker, or escalation.

Hard invariants enforced mechanically:
  - Model independence: reviewer.model_id must be non-empty and distinct from both planner_model
    (required, non-conflicting across plan.md/current.json/--planner-model) and implementer.model_id.
  - Never uses shell=True for agent CLIs (argv is a JSON array of strings).
  - Bounded subprocess timeouts with full process-group kill (start_new_session=True + os.killpg).
  - Repository-wide exclusive lock (<git-common-dir>/pipeline-run-loop.lock) prevents concurrent state races.
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
  0   review-plan: ACCEPT / ACCEPT WITH NITS (proceed to Step 3)
      approve-plan: human->impl recorded
      phase2: headless ACCEPT (SHA-bound handoff to interactive pipeline-review; NOT merged)
  2   Circuit breaker hit (plan_rejects >= 3, review_rejects >= 3, or verify/forbid-paths cap)
  3   Escalation to human/planner (plan_fact|plan_design|env short-circuit, or pause_after_plan)
  4   Fail-closed runtime/contract error (implementer crash/timeout, model collision, dirty trunk,
      stale plan/journal, malformed verdict, lock busy, git/CLI failure)
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
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_REJECTS = 3
MAX_IMPL_FAILURES_PER_ROUND = 2
DIFF_INLINE_LIMIT = 200_000
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

PLAN_VERDICT_RE = re.compile(
    r"^VERDICT: (ACCEPT|ACCEPT WITH NITS|REJECT) \| CAUSE: (none|plan_fact|plan_design|scope|env)$"
)
CODE_VERDICT_RE = re.compile(
    r"^VERDICT: (ACCEPT|REJECT) \| CAUSE: (none|impl_bug|weak_test|scope|plan_fact|plan_design|env)$"
)
SHORT_CIRCUIT_CAUSES = ("plan_fact", "plan_design", "env")
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


def _die(code, msg):
    sys.stderr.write(f"run-loop: {msg}\n")
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


def _run_bounded(argv, cwd, timeout_s, stdin_text=None):
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
    try:
        out, err = proc.communicate(input=stdin_text, timeout=timeout_s)
        return proc.returncode, out or "", err or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return -9, out or "", err or "", True


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
    timeout_s = _validate_positive_finite(
        rcfg.get("timeout_s", 1800 if role_name == "implementer" else 600),
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
    Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@contextlib.contextmanager
def _repo_lock(repo):
    common_dir = Path(_git(repo, ["rev-parse", "--path-format=absolute", "--git-common-dir"]).stdout.strip())
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
    rels = [os.path.relpath(str(p), str(repo)) for p in paths]
    _git(repo, ["add", "--"] + rels)
    _git(repo, ["commit", "--only", "--quiet", "-m", message, "--"] + rels)
    new_head = _git(repo, ["rev-parse", "HEAD"]).stdout.strip()
    if do_push:
        _git(repo, ["push", "--quiet", "origin", f"{new_head}:refs/heads/{branch}"])
    return new_head


def _plan_blob_sha(repo, feature):
    return _git(repo, ["rev-parse", f"HEAD:.pipeline/{feature}/plan.md"]).stdout.strip()


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

        journal_path = repo / ".pipeline" / feature / "journal.md"
        _, tail_trans, _, _ = _read_journal_tail_strict(journal_path)
        if tail_trans != "plan→plan-review · completed":
            _die(4, f"journal tail '{tail_trans}' does not authorize Step 2 (need 'plan→plan-review · completed')")

        plan_path = repo / ".pipeline" / feature / "plan.md"
        check_plan = _load_check_plan()
        meta, errs = check_plan.extract_plan_meta(str(plan_path), expected_feature=feature)
        if errs:
            _die(4, f"plan.md invalid: {', '.join(errs)}")

        planner_model = _resolve_planner_model(args.planner_model, cur.get("planner_model"), meta.get("planner_model"))
        _check_model_separation(rev_cfg["model_id"], planner_model=planner_model, implementer_model=impl_model)

        rejects = int(cur.get("plan_rejects", 0) or 0)
        if rejects >= MAX_REJECTS:
            _die(2, f"plan_rejects already at {rejects} (>= {MAX_REJECTS}); human intervention required")

        plan_blob = _plan_blob_sha(repo, feature)
        prompt = (
            f"You are Step 2 (pipeline-plan-review) reviewing .pipeline/{feature}/plan.md (blob {plan_blob}) in {repo}.\n"
            "Spot-check >=3 citations from Baseline Evidence against real repo files, verify <=10 specs, "
            "T1-T10 observable acceptance matrix, verify commands, and forbid-paths.\n"
            "Physical line 1 of your reply MUST be EXACTLY:\n"
            "VERDICT: <ACCEPT|ACCEPT WITH NITS|REJECT> | CAUSE: <none|plan_fact|plan_design|scope|env>\n"
            "(ACCEPT / ACCEPT WITH NITS require CAUSE: none; REJECT requires a non-none CAUSE.)\n\n"
            f"--- BEGIN plan.md ---\n{plan_path.read_text(encoding='utf-8')}\n--- END plan.md ---\n"
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
                    ["Await human plan approval (run `run-loop.py approve-plan` to proceed)."],
                )
                _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review {verdict} (paused)", args.push, trunk)
                print(f"PLAN_REVIEW {verdict} PAUSED blob={plan_blob} review={rel_rev}")
                return 3
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "plan-review", "impl", "completed", by_tag,
                f"plan review {verdict} on blob {plan_blob[:10]}", rel_rev,
                [f"Run pipeline-impl for {feature} (zero planner tokens from here)."],
            )
            _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review {verdict}", args.push, trunk)
            print(f"PLAN_REVIEW {verdict} blob={plan_blob} review={rel_rev}")
            return 0

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
            _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review blocked ({rejects}/{MAX_REJECTS})", args.push, trunk)
            print(f"PLAN_REVIEW BLOCKED rejects={rejects} cause={cause} review={rel_rev}")
            return 2

        cur["stage"] = "plan-review"
        _write_json(cur_path, cur)
        _append_journal(
            journal_path, "plan-review", "plan", "failed", by_tag,
            f"plan rejected ({rejects}/{MAX_REJECTS}, CAUSE: {cause})", rel_rev,
            [f"Run pipeline-plan to address {rel_rev}."],
        )
        _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): plan-review reject {rejects}/{MAX_REJECTS}", args.push, trunk)
        print(f"PLAN_REVIEW REJECT rejects={rejects} cause={cause} review={rel_rev}")
        return 10


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

        journal_path = repo / ".pipeline" / feature / "journal.md"
        _, tail_trans, _, _ = _read_journal_tail_strict(journal_path)
        if tail_trans != "plan-review→human · blocked":
            _die(4, f"approve-plan requires tail 'plan-review→human · blocked' (found '{tail_trans}')")
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
        _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): human approved plan", args.push, trunk)
        print(f"PLAN_APPROVED feature={feature} blob={cur_blob}")
        return 0


def _extract_assumptions(impl_stdout):
    """Return the implementer's explicit `## Assumptions` body, or None if omitted.

    Never synthesize a placeholder `## Assumptions` section — missing assumptions MUST remain
    visible so Step 4 enforces CONTRACT §Anti-cheat (`missing => REJECT | CAUSE: scope`).
    """
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

        plan_path = repo / ".pipeline" / feature / "plan.md"
        check_plan = _load_check_plan()
        meta, errs = check_plan.extract_plan_meta(str(plan_path), expected_feature=feature)
        if errs:
            _die(4, f"plan.md invalid: {', '.join(errs)}")

        planner_model = _resolve_planner_model(args.planner_model, cur.get("planner_model"), meta.get("planner_model"))
        _check_model_separation(rev_cfg["model_id"], planner_model=planner_model, implementer_model=impl_cfg["model_id"])

        approved_blob = cur.get("approved_plan_blob", "")
        cur_blob = _plan_blob_sha(repo, feature)
        if not approved_blob or approved_blob != cur_blob:
            _die(4, f"plan.md not pinned to an approved Step 2 blob (approved={approved_blob or '<absent>'}, current={cur_blob})")

        journal_path = repo / ".pipeline" / feature / "journal.md"
        _, tail_trans, tail_by, tail_output = _read_journal_tail_strict(journal_path)
        if tail_trans not in PHASE2_ALLOWED_TAILS:
            _die(4, f"journal tail '{tail_trans}' does not authorize phase2 (need one of {PHASE2_ALLOWED_TAILS})")

        review_rejects = int(cur.get("review_rejects", 0) or 0)
        if review_rejects >= MAX_REJECTS:
            _die(2, f"review_rejects already at {review_rejects} (>= {MAX_REJECTS}); human intervention required")

        wt_dir, feat_branch = _ensure_worktree(repo, common_dir, trunk, feature, fetch_remote=args.push)
        verify_cmds = meta["verify"]
        forbid_paths = meta["forbid_paths"]
        plan_text = plan_path.read_text(encoding="utf-8")
        reviews_dir = repo / ".pipeline" / feature / "reviews"

        feedback = ""
        if reviews_dir.is_dir():
            existing_cr = sorted(reviews_dir.glob("code-review-*.md"))
            if existing_cr and tail_trans == "review→impl · failed":
                feedback = f"Prior code review feedback ({existing_cr[-1].name}):\n{existing_cr[-1].read_text(encoding='utf-8')}\n"

        # If resuming from `impl->review · completed` (e.g. reviewer crashed earlier), verify recorded
        # implementer identity against reviewer (prevent self-review) and pin the exact recorded SHA!
        resume_step4 = (tail_trans == "impl→review · completed")
        impl_failures = 0

        while review_rejects < MAX_REJECTS:
            if not resume_step4:
                # ---- Step 3: Implementer ----
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
                # Hard rule 2: implementer crash or timeout = STOP immediately (never auto-retry death).
                if timed_out or rc != 0:
                    cur["stage"] = "blocked"
                    _write_json(cur_path, cur)
                    reason = f"timeout={timed_out} rc={rc}"
                    _append_journal(
                        journal_path, "impl", "human", "blocked", f"implementer:{impl_cfg['model_id']}",
                        f"implementer died ({reason}) — stopped per hard rule 2", feat_branch,
                        [f"Human intervention required: implementer died ({reason}) in {wt_dir}."],
                    )
                    trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): impl died ({reason})", args.push, trunk)
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
                        trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): impl blocked", args.push, trunk)
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
                        trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): impl forbid-paths blocked", args.push, trunk)
                        print(f"PHASE2 BLOCKED stage=forbid-paths hits={','.join(forbid_hits)}")
                        return 2
                    feedback = f"FORBIDDEN PATHS TOUCHED: {', '.join(forbid_hits)}. Revert them and commit cleanly.\n"
                    continue

                # ---- Step 3 Deterministic verify pre-gate (BEFORE Step 4) ----
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
                        trunk_head = _commit_trunk(repo, trunk_head, [cur_path, journal_path], f"pipeline({feature}): verify pre-gate blocked", args.push, trunk)
                        print(f"PHASE2 BLOCKED stage=verify cmd={fcmd} rc={frc}")
                        return 2
                    feedback = f"Deterministic verify failed (rc={frc}, timeout={fto}) for `{fcmd}`:\n{ftail}\nFix and commit cleanly.\n"
                    continue

                impl_failures = 0
                # Publish verified feature SHA BEFORE publishing trunk impl->review handoff!
                if args.push:
                    _ensure_remote_feature_sha(wt_dir, feat_branch, after_sha)

                commits_log = _git(wt_dir, ["log", "--oneline", f"{base_sha}..{after_sha}"]).stdout.strip()
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
                trunk_head = _commit_trunk(repo, trunk_head, [impl_report, cur_path, journal_path], f"pipeline({feature}): impl @{after_sha[:10]}", args.push, trunk)
            else:
                # Resuming directly into Step 4: verify recorded implementer != reviewer AND exact recorded SHA!
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
                if args.push:
                    _ensure_remote_feature_sha(wt_dir, feat_branch, after_sha)

            # ---- Step 4 Deterministic Gates (BEFORE invoking Reviewer LLM) ----
            # 1) Independent verify re-run on after_sha -> failure is deterministic REJECT | CAUSE: impl_bug
            # 2) impl-report.md must carry explicit `## Assumptions` heading -> missing is deterministic REJECT | CAUSE: scope
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
                # ---- Step 4: Reviewer ----
                diff_stat = _git(wt_dir, ["diff", "--stat", f"{base_sha}..{after_sha}"]).stdout
                full_diff = _git(wt_dir, ["diff", f"{base_sha}..{after_sha}"]).stdout
                if len(full_diff) > DIFF_INLINE_LIMIT:
                    full_diff = (
                        full_diff[:DIFF_INLINE_LIMIT]
                        + f"\n... [TRUNCATED at {DIFF_INLINE_LIMIT} bytes; inspect full diff with `git -C {wt_dir} diff {base_sha}..{after_sha}`] ...\n"
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
                # Verify worktree integrity and remote SHA BEFORE writing any file onto trunk!
                final_sha = _git(wt_dir, ["rev-parse", "HEAD"]).stdout.strip()
                final_br = _git(wt_dir, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
                if final_sha != after_sha or final_br != feat_branch or _git(wt_dir, ["status", "--porcelain"]).stdout.strip():
                    _die(4, f"worktree mutated during Step 4 review (expected {feat_branch}@{after_sha}, got {final_br}@{final_sha})")
                if args.push:
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
                trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review ACCEPT @{after_sha[:10]}", args.push, trunk)
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
                trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review escalate {cause}", args.push, trunk)
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
                trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review blocked ({review_rejects}/{MAX_REJECTS})", args.push, trunk)
                print(f"PHASE2 BLOCKED rejects={review_rejects} cause={cause} review={rel_rev}")
                return 2

            cur["stage"] = "review"
            _write_json(cur_path, cur)
            _append_journal(
                journal_path, "review", "impl", "failed", by_tag,
                f"code review rejected ({review_rejects}/{MAX_REJECTS}, CAUSE: {cause})", rel_rev,
                [f"Run pipeline-impl to address {rel_rev}."],
            )
            trunk_head = _commit_trunk(repo, trunk_head, [rev_file, cur_path, journal_path], f"pipeline({feature}): review reject {review_rejects}/{MAX_REJECTS}", args.push, trunk)
            feedback = f"Code review rejection {review_rejects}/{MAX_REJECTS} (CAUSE: {cause}) from {rel_rev}:\n{rev_body.strip()}\n"

        return 2


def main(argv):
    parser = _Exit64Parser(description="Stateless headless CLI runner for pipeline v2 (3-Agent 4-Step).")
    sub = parser.add_subparsers(dest="cmd", required=True)
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
        if args.cmd == "approve-plan":
            return cmd_approve_plan(args)
        cfg = _read_json(args.config, exit_code=64)
        if not isinstance(cfg, dict):
            _die(64, "runners.json root must be a JSON object")
        if args.cmd == "review-plan":
            return cmd_review_plan(args, cfg)
        return cmd_phase2(args, cfg)
    except RuntimeError as exc:
        _die(4, str(exc))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
