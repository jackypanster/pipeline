#!/usr/bin/env python3
"""plite — minimal single-machine 2-agent x 3-step experiment runner.

Experimental companion to jackypanster/pipeline for low-risk local tasks
(87.199 admission boundary). Preserves existing pipeline contracts untouched
while collecting comparable telemetry to test whether local subprocess
orchestration + 2-model separation can replace heavy multi-stage ceremony on
low-risk changes.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import fnmatch
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Iterator

EXPERIMENT_VERSION = "0.2.0"
DEFAULT_STATE_DIR = pathlib.Path.home() / ".local" / "state" / "plite"

VALID_CLIS = ("agy", "claude", "codex")
VALID_TAGS = ("semantic_bug", "spec_gap", "test_weakening", "nit")
VALID_SEVERITIES = ("blocking", "non_blocking")
VALID_VERDICTS = ("APPROVE", "CHANGES_REQUESTED")
VALID_REGRESSION_STATUSES = ("unknown", "pending", "none_observed", "confirmed")

DEFAULT_PROTECTED_GLOBS = (
    # Python
    "pyproject.toml",
    "pytest.ini",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "ruff.toml",
    ".flake8",
    "mypy.ini",
    "conftest.py",
    "*/conftest.py",
    # JS / TS
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "tsconfig.json",
    "vitest.config.*",
    "jest.config.*",
    # Rust / Go / Build / CI
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Makefile",
    "makefile",
    "justfile",
    ".github/workflows/*",
    ".gitlab-ci.yml",
)

TEST_PATH_PATTERNS = (
    "*_test.*",
    "*.test.*",
    "*.spec.*",
    "test_*",
    "tests/*",
    "*/tests/*",
    "__tests__/*",
    "*/__tests__/*",
)

MODEL_FAMILY_PREFIXES = (
    (("claude", "anthropic", "opus", "sonnet", "haiku", "fable"), "anthropic"),
    (("gpt", "o1", "o3", "o4", "codex", "openai"), "openai"),
    (("gemini", "google", "neon", "argon"), "google"),
    (("deepseek",), "deepseek"),
    (("glm", "zhipu"), "zhipu"),
    (("qwen",), "qwen"),
    (("grok", "xai"), "xai"),
)


class PliteError(Exception):
    """Expected fail-closed operational or validation error."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str, max_len: int = 32) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned[:max_len].strip("-") or "task"


def atomic_write_json(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def append_jsonl(path: pathlib.Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def acquire_run_lock(run_dir: pathlib.Path, run_id: str) -> Iterator[None]:
    """Exclusive non-blocking lock covering full read-modify-write run transactions."""
    lock_path = run_dir / "lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PliteError(f"run '{run_id}' is already locked by another process") from exc
        try:
            yield
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def run_proc(
    cmd: list[str] | str,
    *,
    cwd: pathlib.Path,
    stdin_text: str | None = None,
    timeout_sec: int = 600,
    shell: bool = False,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str, bool]:
    """Run subprocess in its own process group; kill whole group on timeout."""
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=shell,
        env=env,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(input=stdin_text, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_group(proc)
        stdout, stderr = _drain_after_terminate(proc)
    except BaseException:
        _terminate_process_group(proc)
        _drain_after_terminate(proc)
        raise
    return proc.returncode if proc.returncode is not None else -1, stdout, stderr, timed_out


def _drain_after_terminate(proc: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        return proc.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return "", ""


def _terminate_process_group(proc: subprocess.Popen[str]) -> None:
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            proc.poll()
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def git(repo_or_wt: pathlib.Path, *args: str) -> str:
    rc, out, err, timed_out = run_proc(["git", *args], cwd=repo_or_wt, timeout_sec=60)
    if timed_out or rc != 0:
        raise PliteError(f"git {' '.join(args)} failed (rc={rc}): {err.strip() or out.strip()}")
    return out.strip()


def git_raw(repo_or_wt: pathlib.Path, *args: str) -> str:
    rc, out, err, timed_out = run_proc(["git", *args], cwd=repo_or_wt, timeout_sec=60)
    if timed_out or rc != 0:
        raise PliteError(f"git {' '.join(args)} failed (rc={rc}): {err.strip() or out.strip()}")
    return out


def git_is_ancestor(repo_or_wt: pathlib.Path, ancestor_sha: str, descendant_sha: str) -> bool:
    rc, _out, _err, timed_out = run_proc(
        ["git", "merge-base", "--is-ancestor", ancestor_sha, descendant_sha],
        cwd=repo_or_wt,
        timeout_sec=30,
    )
    if timed_out:
        raise PliteError("git merge-base --is-ancestor timed out")
    return rc == 0


def infer_family_from_model(model: str) -> str | None:
    lower = model.strip().lower()
    tokens = re.split(r"[/:\s_-]+", lower)
    for prefixes, family in MODEL_FAMILY_PREFIXES:
        for tok in tokens:
            if any(tok == p or tok.startswith(p) for p in prefixes):
                return family
    return None


def read_codex_config_model(config_path: pathlib.Path | None = None) -> str | None:
    path = config_path or (pathlib.Path.home() / ".codex" / "config.toml")
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'(?m)^\s*model\s*=\s*"([^"]+)"', text)
    return m.group(1).strip() if m else None


def read_claude_settings_model(settings_path: pathlib.Path | None = None) -> str | None:
    path = settings_path or (pathlib.Path.home() / ".claude" / "settings.json")
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    model = data.get("model")
    return model.strip() if isinstance(model, str) and model.strip() else None


@dataclasses.dataclass(frozen=True)
class AgentIdentity:
    cli: str
    model: str | None
    family: str
    identity_source: str  # explicit | model_arg | config | cli_default

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def resolve_agent_identity(
    cli: str,
    model: str | None,
    explicit_family: str | None,
    *,
    codex_config: pathlib.Path | None = None,
    claude_settings: pathlib.Path | None = None,
) -> AgentIdentity:
    if cli not in VALID_CLIS:
        raise PliteError(f"unsupported CLI '{cli}'; supported: {', '.join(VALID_CLIS)}")

    if explicit_family:
        fam = explicit_family.strip().lower()
        if not fam or not re.fullmatch(r"[a-z0-9_-]+", fam):
            raise PliteError(f"invalid explicit family '{explicit_family}'")
        # Check consistency against recognizable model name (explicit arg or CLI config)
        eff_model = model
        if not eff_model:
            if cli == "codex":
                eff_model = read_codex_config_model(codex_config)
            elif cli == "claude":
                eff_model = read_claude_settings_model(claude_settings)
        if eff_model:
            inferred = infer_family_from_model(eff_model)
            if inferred and inferred != fam:
                raise PliteError(
                    f"explicit family '{fam}' contradicts recognizable model '{eff_model}' (family='{inferred}')"
                )
        elif cli == "claude":
            if fam != "anthropic":
                raise PliteError(
                    f"explicit family '{fam}' for 'claude' requires an explicit --model "
                    "(default claude CLI is anthropic-only)"
                )
        else:
            raise PliteError(
                f"for '{cli}', --worker-family/--reviewer-family requires a resolved --model "
                "(or configured model) so the invoked model matches the declared family"
            )
        return AgentIdentity(cli=cli, model=eff_model, family=fam, identity_source="explicit")

    if model:
        fam = infer_family_from_model(model)
        if not fam:
            raise PliteError(
                f"unknown model family for {cli} model='{model}'; "
                f"pass --worker-family/--reviewer-family explicitly"
            )
        return AgentIdentity(cli=cli, model=model, family=fam, identity_source="model_arg")

    if cli == "codex":
        cfg_model = read_codex_config_model(codex_config)
        if cfg_model:
            fam = infer_family_from_model(cfg_model)
            if not fam:
                raise PliteError(
                    f"codex config model '{cfg_model}' has unknown family; "
                    f"pass --worker-family/--reviewer-family explicitly"
                )
            return AgentIdentity(cli=cli, model=cfg_model, family=fam, identity_source="config")
        raise PliteError(
            "cannot resolve codex model identity (no --model and no model in ~/.codex/config.toml); "
            "pass --model or --worker-family/--reviewer-family"
        )

    if cli == "claude":
        cfg_model = read_claude_settings_model(claude_settings)
        if cfg_model:
            fam = infer_family_from_model(cfg_model)
            if not fam:
                raise PliteError(
                    f"claude settings model '{cfg_model}' has unknown family; "
                    f"pass --worker-family/--reviewer-family explicitly"
                )
            return AgentIdentity(cli=cli, model=cfg_model, family=fam, identity_source="config")
        return AgentIdentity(cli=cli, model=None, family="anthropic", identity_source="cli_default")

    raise PliteError(
        "cannot infer model family for 'agy' without --worker-model/--reviewer-model "
        "(agy hosts multiple model families)"
    )


def enforce_independence(
    worker: AgentIdentity,
    reviewer: AgentIdentity,
    contributing_families: list[str],
) -> None:
    if worker.family == reviewer.family:
        raise PliteError(
            f"cross-model guard failed: worker ({worker.cli}/{worker.family}) "
            f"and reviewer ({reviewer.cli}/{reviewer.family}) share model family '{worker.family}'"
        )
    if reviewer.family in contributing_families:
        raise PliteError(
            f"cross-resume independence guard failed: reviewer family '{reviewer.family}' "
            f"already contributed implementation on this run ({contributing_families})"
        )


def detect_cli_version(cli: str) -> str:
    exe = shutil.which(cli)
    if not exe:
        raise PliteError(f"required CLI '{cli}' not found on PATH")
    rc, out, err, timed_out = run_proc([exe, "--version"], cwd=pathlib.Path.cwd(), timeout_sec=15)
    if timed_out or rc != 0:
        return "unknown"
    line = (out.strip() or err.strip()).splitlines()
    return line[0][:120] if line else "unknown"


def is_test_path(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    base = os.path.basename(norm)
    for pat in TEST_PATH_PATTERNS:
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def is_protected_path(rel_path: str, extra_globs: list[str]) -> bool:
    norm = rel_path.replace("\\", "/")
    base = os.path.basename(norm)
    for pat in (*DEFAULT_PROTECTED_GLOBS, *extra_globs):
        if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(base, pat):
            return True
    return False


def inspect_changed_files(
    worktree: pathlib.Path,
    base_sha: str,
    candidate_sha: str,
    *,
    allow_protected_edit: bool,
    extra_protected_globs: list[str],
) -> dict[str, list[str]]:
    # NUL-delimited output avoids Git filename quoting/escaping issues.
    raw = git_raw(worktree, "diff", "--name-status", "--no-renames", "-z", f"{base_sha}..{candidate_sha}")
    tokens = [t for t in raw.split("\0") if t != ""]
    all_files: list[str] = []
    deleted_tests: list[str] = []
    modified_existing_tests: list[str] = []
    added_tests: list[str] = []
    protected_touched: list[str] = []

    idx = 0
    while idx + 1 < len(tokens):
        status = tokens[idx].strip()
        rel_path = tokens[idx + 1]
        idx += 2
        all_files.append(f"{status}\t{rel_path}")
        if is_protected_path(rel_path, extra_protected_globs):
            protected_touched.append(f"{status}:{rel_path}")
        if is_test_path(rel_path):
            if status == "D":
                deleted_tests.append(rel_path)
            elif status == "A":
                added_tests.append(rel_path)
            else:
                modified_existing_tests.append(rel_path)

    if not allow_protected_edit and (deleted_tests or protected_touched):
        reasons = []
        if deleted_tests:
            reasons.append(f"deleted test file(s): {', '.join(deleted_tests)}")
        if protected_touched:
            reasons.append(f"verification/build config touched: {', '.join(protected_touched)}")
        raise PliteError(
            "ESCALATE test/verification protection triggered — "
            + "; ".join(reasons)
            + " (re-run with --allow-protected-edit only if explicitly intended, or escalate to full pipeline)"
        )

    return {
        "all_files": all_files,
        "deleted_tests": deleted_tests,
        "modified_existing_tests": modified_existing_tests,
        "added_tests": added_tests,
        "protected_touched": protected_touched,
    }


def parse_verdict(raw_output: str, expected_sha: str) -> dict[str, Any]:
    """Failure-closed parser requiring EXACTLY ONE fenced plite-verdict JSON block."""
    pattern = re.compile(r"```plite-verdict\s*\n(.*?)\n```", re.DOTALL)
    matches = pattern.findall(raw_output)
    if len(matches) == 0:
        raise PliteError("reviewer output missing required ```plite-verdict JSON block")
    if len(matches) > 1:
        raise PliteError(f"ambiguous reviewer output: expected exactly 1 plite-verdict block, found {len(matches)}")

    try:
        obj = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise PliteError(f"malformed plite-verdict JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise PliteError("plite-verdict JSON must be an object")

    verdict = obj.get("verdict")
    if not isinstance(verdict, str) or verdict not in VALID_VERDICTS:
        raise PliteError(f"invalid verdict '{verdict}'; expected one of {VALID_VERDICTS}")

    reviewed_sha = obj.get("reviewed_sha")
    if not isinstance(reviewed_sha, str) or reviewed_sha.strip() != expected_sha:
        raise PliteError(
            f"verdict reviewed_sha mismatch: got '{reviewed_sha}', expected exact '{expected_sha}'"
        )

    findings = obj.get("findings")
    if not isinstance(findings, list):
        raise PliteError("plite-verdict 'findings' must be a list")

    blocking_count = 0
    normalized_findings: list[dict[str, str]] = []
    for idx, item in enumerate(findings):
        if not isinstance(item, dict):
            raise PliteError(f"finding[{idx}] must be an object")
        tag = item.get("tag")
        severity = item.get("severity")
        summary = item.get("summary")
        file_ref = item.get("file")
        if not isinstance(tag, str) or tag not in VALID_TAGS:
            raise PliteError(f"finding[{idx}] invalid tag '{tag}'; allowed: {VALID_TAGS}")
        if not isinstance(severity, str) or severity not in VALID_SEVERITIES:
            raise PliteError(f"finding[{idx}] invalid severity '{severity}'; allowed: {VALID_SEVERITIES}")
        if not isinstance(file_ref, str) or not file_ref.strip():
            raise PliteError(f"finding[{idx}] missing non-empty string 'file'")
        if not isinstance(summary, str) or not summary.strip():
            raise PliteError(f"finding[{idx}] missing non-empty summary")
        if severity == "blocking":
            blocking_count += 1
        normalized_findings.append(
            {
                "tag": tag,
                "severity": severity,
                "file": file_ref.strip(),
                "summary": summary.strip(),
            }
        )

    if verdict == "APPROVE" and blocking_count > 0:
        raise PliteError(f"inconsistent verdict: APPROVE with {blocking_count} blocking finding(s)")
    if verdict == "CHANGES_REQUESTED" and blocking_count == 0:
        raise PliteError("inconsistent verdict: CHANGES_REQUESTED with 0 blocking findings")

    return {
        "verdict": verdict,
        "reviewed_sha": expected_sha,
        "findings": normalized_findings,
    }


def invoke_agent_cli(
    identity: AgentIdentity,
    *,
    role: str,  # "worker" | "reviewer"
    worktree: pathlib.Path,
    prompt: str,
    timeout_sec: int,
    out_file: pathlib.Path,
) -> tuple[int, str, str, bool]:
    """Invoke a local agent CLI non-interactively, passing prompt on stdin."""
    exe = shutil.which(identity.cli)
    if not exe:
        raise PliteError(f"CLI '{identity.cli}' not found on PATH")

    if identity.cli == "codex":
        sandbox = "workspace-write" if role == "worker" else "read-only"
        cmd = [exe, "exec", "-C", str(worktree), "-s", sandbox, "-o", str(out_file)]
        if identity.model:
            cmd.extend(["-m", identity.model])
        cmd.append("-")
        rc, stdout, stderr, timed_out = run_proc(
            cmd, cwd=worktree, stdin_text=prompt, timeout_sec=timeout_sec
        )
        final_out = out_file.read_text(encoding="utf-8") if out_file.is_file() else stdout
        return rc, final_out, stderr, timed_out

    if identity.cli == "claude":
        perm_mode = "auto" if role == "worker" else "plan"
        cmd = [exe, "-p", "--permission-mode", perm_mode]
        if identity.model:
            cmd.extend(["--model", identity.model])
        rc, stdout, stderr, timed_out = run_proc(
            cmd, cwd=worktree, stdin_text=prompt, timeout_sec=timeout_sec
        )
        out_file.write_text(stdout, encoding="utf-8")
        return rc, stdout, stderr, timed_out

    if identity.cli == "agy":
        mode = "auto" if role == "worker" else "plan"
        cmd = [exe, "-p", "--mode", mode]
        if identity.model:
            cmd.extend(["--model", identity.model])
        rc, stdout, stderr, timed_out = run_proc(
            cmd, cwd=worktree, stdin_text=prompt, timeout_sec=timeout_sec
        )
        out_file.write_text(stdout, encoding="utf-8")
        return rc, stdout, stderr, timed_out

    raise PliteError(f"no adapter for CLI '{identity.cli}'")


def send_notification(title: str, message: str, *, notify_cmd: str | None, disabled: bool) -> None:
    """Best-effort notification; never raises or masks delivery outcome."""
    if disabled:
        return
    try:
        if notify_cmd:
            env = os.environ.copy()
            env["PLITE_NOTIFY_TITLE"] = title
            env["PLITE_NOTIFY_MESSAGE"] = message
            run_proc(notify_cmd, cwd=pathlib.Path.cwd(), timeout_sec=15, shell=True, env=env)
            return
        if sys.platform == "darwin" and shutil.which("osascript"):
            safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
            safe_msg = message.replace("\\", "\\\\").replace('"', '\\"')[:240]
            script = f'display notification "{safe_msg}" with title "{safe_title}"'
            run_proc(["osascript", "-e", script], cwd=pathlib.Path.cwd(), timeout_sec=10)
    except Exception:
        pass


def build_worker_prompt(
    manifest: dict[str, Any],
    *,
    round_num: int,
    feedback_kind: str | None,
    feedback_body: str | None,
) -> str:
    protected_rule = (
        "2. Protected config/verification edits are EXPLICITLY ALLOWED for this run (--allow-protected-edit); "
        "still do NOT weaken existing test assertions."
        if manifest.get("allow_protected_edit")
        else "2. Do NOT delete existing tests or modify build/verification/CI config files."
    )
    lines = [
        "You are the Worker agent in a 2-agent local coding loop.",
        f"Repository worktree: {manifest['worktree_path']}",
        f"Base branch/SHA: {manifest['base_ref']} ({manifest['base_sha']})",
        f"Working branch: {manifest['branch']}",
        "",
        "## Task",
        manifest["task_text"],
        "",
        "## Immutable Acceptance Criteria",
        manifest["acceptance_criteria"],
        "",
        "## External Verification Command (runner executes this independently)",
        manifest["verify_cmd"],
        "",
        "## Hard Rules",
        "1. Work ONLY inside this worktree on the current branch. Do NOT switch branches, touch the base branch, push, or open PRs.",
        protected_rule,
        "3. Before finishing, run checks, stage all your changes, and commit them cleanly so `git status --porcelain` is EMPTY.",
        "4. Leave zero untracked files behind.",
    ]
    if feedback_kind and feedback_body:
        lines.extend(
            [
                "",
                f"## Round {round_num} Feedback to Fix ({feedback_kind})",
                feedback_body,
                "",
                "Fix every issue above and create a new clean commit on this branch.",
            ]
        )
    return "\n".join(lines) + "\n"


def build_review_packet(
    manifest: dict[str, Any],
    *,
    round_num: int,
    candidate_sha: str,
    diff_stat: str,
    diff_file: pathlib.Path,
    file_audit: dict[str, list[str]],
    verify_stdout: str,
    verify_stderr: str,
) -> str:
    modified_tests = file_audit["modified_existing_tests"]
    added_tests = file_audit["added_tests"]
    verify_tail = (verify_stdout + "\n" + verify_stderr).strip()[-3000:]

    lines = [
        f"# plite Independent Review Packet (Round {round_num})",
        "",
        f"- Run ID: `{manifest['run_id']}`",
        f"- Base SHA: `{manifest['base_sha']}`",
        f"- Candidate SHA: `{candidate_sha}`",
        f"- Contributing worker families: `{manifest['contributing_families']}`",
        "",
        "## Task",
        manifest["task_text"],
        "",
        "## Immutable Acceptance Criteria",
        manifest["acceptance_criteria"],
        "",
        "## Diff Summary",
        "```",
        diff_stat or "(empty)",
        "```",
        f"Full diff saved at: `{diff_file}`",
        "",
        "## Test & Verification Audit",
        f"- Added test files: {added_tests or 'none'}",
        f"- EXISTING test files modified (SCRUTINIZE FOR WEAKENED ASSERTIONS): {modified_tests or 'none'}",
        f"- External verify command (PASSED exit 0 on exact candidate SHA): `{manifest['verify_cmd']}`",
        "```",
        verify_tail or "(no output)",
        "```",
    ]
    return "\n".join(lines) + "\n"


def build_reviewer_prompt(packet_file: pathlib.Path, diff_file: pathlib.Path, candidate_sha: str) -> str:
    return (
        "You are the independent cross-model Reviewer.\n"
        "You MUST NOT modify any files or git state (read-only review).\n"
        f"Read the review packet at `{packet_file}` and the full diff at `{diff_file}`.\n"
        "Also inspect any referenced source/test files in the repo as needed.\n"
        "\n"
        "Review axes:\n"
        "1. Does the candidate satisfy every item in Immutable Acceptance Criteria?\n"
        "2. Any semantic bugs, edge-case failures, regressions, or scope creep?\n"
        "3. If existing test files were modified, were any assertions weakened, skipped, or bypassed?\n"
        "4. Are new tests genuine and non-tautological?\n"
        "\n"
        "End your final response with EXACTLY ONE fenced `plite-verdict` JSON block:\n"
        "```plite-verdict\n"
        "{\n"
        '  "verdict": "APPROVE" | "CHANGES_REQUESTED",\n'
        f'  "reviewed_sha": "{candidate_sha}",\n'
        '  "findings": [\n'
        "    {\n"
        '      "tag": "semantic_bug" | "spec_gap" | "test_weakening" | "nit",\n'
        '      "severity": "blocking" | "non_blocking",\n'
        '      "file": "relative/path:line",\n'
        '      "summary": "concise actionable description"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "```\n"
        "Rules: APPROVE must have 0 blocking findings. CHANGES_REQUESTED must have >=1 blocking finding.\n"
    )


def record_terminal_telemetry(state_dir: pathlib.Path, manifest: dict[str, Any]) -> None:
    findings_by_tag: dict[str, int] = {t: 0 for t in VALID_TAGS}
    for rnd in manifest.get("rounds", []):
        for f in rnd.get("findings", []):
            tag = f.get("tag")
            if tag in findings_by_tag:
                findings_by_tag[tag] += 1

    rec = {
        "record_type": "run_terminal",
        "ts": utc_now(),
        "run_id": manifest["run_id"],
        "experiment_version": EXPERIMENT_VERSION,
        "repo": manifest["repo"],
        "task_slug": manifest["slug"],
        "risk_class": manifest["risk_class"],
        "task_type": manifest["task_type"],
        "acceptance_criteria": manifest["acceptance_criteria"],
        "base_ref": manifest["base_ref"],
        "base_sha": manifest["base_sha"],
        "candidate_sha": manifest.get("candidate_sha"),
        "approved_sha": manifest.get("approved_sha"),
        "merged_sha": manifest.get("merged_sha"),
        "cli_versions": manifest.get("cli_versions", {}),
        "resolved_models": {
            "worker": manifest["worker"],
            "reviewer": manifest["reviewer"],
        },
        "invocation_history": manifest.get("invocation_history", []),
        "contributing_families": manifest["contributing_families"],
        "worker_invocations": manifest["worker_invocations"],
        "reviewer_invocations": manifest["reviewer_invocations"],
        "review_rounds": manifest["review_rounds"],
        "verify_failures": manifest["verify_failures"],
        "existing_tests_modified": manifest.get("existing_tests_modified", []),
        "findings_by_tag": findings_by_tag,
        "wall_time_sec": round(manifest.get("wall_time_sec", 0.0), 2),
        "step_timings": manifest.get("step_timings", {}),
        "human_active_time_sec": manifest.get("human_active_time_sec"),
        "interventions_by_reason": manifest.get("interventions_by_reason", {}),
        "outcome": manifest["status"],
        "outcome_reason": manifest.get("outcome_reason", ""),
        "regression_status": manifest.get("regression_status", "pending"),
    }
    append_jsonl(state_dir / "runs.jsonl", rec)


TERMINAL_STATUSES = ("approved", "blocked", "escalated", "infra_error", "integrity_violation")


def flush_manifest_telemetry(state_dir: pathlib.Path, run_dir: pathlib.Path, manifest: dict[str, Any]) -> bool:
    """Persist terminal telemetry without allowing I/O failure to overwrite manifest status."""
    try:
        record_terminal_telemetry(state_dir, manifest)
        manifest["telemetry_recorded"] = True
        save_manifest(run_dir, manifest)
        return True
    except Exception as exc:
        print(
            f"PLITE WARNING: failed to flush telemetry for run_id={manifest.get('run_id')}: {exc}",
            file=sys.stderr,
        )
        return False


def reconcile_unrecorded_manifests(state_dir: pathlib.Path) -> int:
    """Flush any terminal manifest whose JSONL append was interrupted before completion."""
    runs_root = state_dir / "runs"
    if not runs_root.is_dir():
        return 0
    flushed = 0
    for manifest_path in sorted(runs_root.glob("*/manifest.json")):
        run_dir = manifest_path.parent
        run_id = run_dir.name
        try:
            with acquire_run_lock(run_dir, run_id):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("status") in TERMINAL_STATUSES and manifest.get("telemetry_recorded") is False:
                    if flush_manifest_telemetry(state_dir, run_dir, manifest):
                        flushed += 1
        except Exception:
            continue
    return flushed


def save_manifest(run_dir: pathlib.Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    atomic_write_json(run_dir / "manifest.json", manifest)


def format_state_dir_flag(state_dir: pathlib.Path) -> str:
    if state_dir.resolve() == DEFAULT_STATE_DIR.resolve():
        return ""
    return f" --state-dir {shlex.quote(str(state_dir))}"


def execute_loop_locked(
    state_dir: pathlib.Path,
    run_dir: pathlib.Path,
    manifest: dict[str, Any],
    worker: AgentIdentity,
    reviewer: AgentIdentity,
    *,
    timeout_sec: int,
    notify_cmd: str | None,
    no_notify: bool,
) -> int:
    """Core 2-agent x 3-step loop (caller holds exclusive run lock)."""
    worktree = pathlib.Path(manifest["worktree_path"])
    repo = pathlib.Path(manifest["repo"])
    rounds_dir = run_dir / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    loop_start = time.monotonic()
    sd_flag = format_state_dir_flag(state_dir)

    def finish(status: str, reason: str, exit_code: int, notify_title: str) -> int:
        manifest["status"] = status
        manifest["outcome_reason"] = reason
        manifest["telemetry_recorded"] = False
        manifest["wall_time_sec"] = round(
            manifest.get("wall_time_sec", 0.0) + (time.monotonic() - loop_start), 2
        )
        save_manifest(run_dir, manifest)
        flush_manifest_telemetry(state_dir, run_dir, manifest)
        send_notification(notify_title, reason, notify_cmd=notify_cmd, disabled=no_notify)
        print(f"[{status.upper()}] run_id={manifest['run_id']} reason={reason}")
        if status == "approved":
            print(f"  approved_sha={manifest['approved_sha']}")
            print(f"  branch={manifest['branch']} worktree={worktree}")
            print(
                f"  inspect: git -C {shlex.quote(str(worktree))} diff "
                f"{manifest['base_sha']}..{manifest['approved_sha']}"
            )
            print(
                f"  after merge, backfill: plite.py mark --run-id {shlex.quote(manifest['run_id'])}"
                f"{sd_flag} --merged-sha <sha> --regression none_observed"
            )
        elif status == "integrity_violation":
            print(f"  worktree quarantined (NOT resumable): {worktree}")
        else:
            print(f"  worktree preserved: {worktree}")
            print(
                f"  resume: plite.py resume --run-id {shlex.quote(manifest['run_id'])}"
                f"{sd_flag} --worker <agy|claude|codex> --reviewer {shlex.quote(reviewer.cli)}"
            )
        return exit_code

    def check_lifecycle_integrity(
        stage: str, *, expected_head: str | None = None, require_clean: bool = False
    ) -> str | None:
        try:
            if git(repo, "rev-parse", "--verify", f"{manifest['base_ref']}^{{commit}}") != manifest["base_sha"]:
                return f"integrity violation ({stage}): base ref '{manifest['base_ref']}' was modified"
            cur_branch = git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
            if cur_branch != manifest["branch"]:
                return (
                    f"integrity violation ({stage}): worktree branch changed to "
                    f"'{cur_branch}' (expected '{manifest['branch']}')"
                )
            cur_head = git(worktree, "rev-parse", "HEAD")
            if not git_is_ancestor(worktree, manifest["base_sha"], cur_head):
                return (
                    f"integrity violation ({stage}): HEAD {cur_head} does not descend from "
                    f"pinned base {manifest['base_sha']}"
                )
            if expected_head is not None and cur_head != expected_head:
                return f"integrity violation ({stage}): HEAD changed to {cur_head} (expected {expected_head})"
            if require_clean and git(worktree, "status", "--porcelain"):
                return f"integrity violation ({stage}): worktree mutated (dirty state detected)"
        except PliteError as exc:
            return f"integrity violation ({stage}): git verification failed ({exc})"
        return None

    try:
        # Base-drift guard before entering loop
        current_base_sha = git(repo, "rev-parse", "--verify", f"{manifest['base_ref']}^{{commit}}")
        if current_base_sha != manifest["base_sha"]:
            return finish(
                "blocked",
                f"base ref '{manifest['base_ref']}' drifted from pinned {manifest['base_sha']} to {current_base_sha}",
                2,
                "plite BLOCKED: base drift",
            )

        feedback_kind: str | None = manifest.get("pending_feedback_kind")
        feedback_body: str | None = manifest.get("pending_feedback_body")
        phase: str = manifest.get("phase", "worker")

        while True:
            if manifest["review_rounds"] >= manifest["max_rounds"]:
                return finish(
                    "blocked",
                    f"reached max review rounds ({manifest['max_rounds']}) without approval",
                    2,
                    f"plite BLOCKED ({manifest['slug']})",
                )
            if manifest["reviewer_invocations"] >= manifest["max_invocations"]:
                return finish(
                    "blocked",
                    f"reached max reviewer invocations ({manifest['max_invocations']})",
                    2,
                    f"plite BLOCKED ({manifest['slug']})",
                )

            # ---- PHASE 1: WORKER ----
            if phase == "worker":
                if manifest["worker_invocations"] >= manifest["max_invocations"]:
                    return finish(
                        "blocked",
                        f"reached max worker invocations ({manifest['max_invocations']})",
                        2,
                        f"plite BLOCKED ({manifest['slug']})",
                    )

                enforce_independence(worker, reviewer, manifest["contributing_families"])

                inv_num = manifest["worker_invocations"] + 1
                round_num = manifest["review_rounds"] + 1
                manifest["worker"] = worker.to_dict()
                manifest["reviewer"] = reviewer.to_dict()
                manifest["phase"] = "worker"
                manifest["status"] = "worker_running"
                manifest["worker_invocations"] = inv_num
                if worker.family not in manifest["contributing_families"]:
                    manifest["contributing_families"].append(worker.family)
                save_manifest(run_dir, manifest)

                worker_prompt = build_worker_prompt(
                    manifest,
                    round_num=round_num,
                    feedback_kind=feedback_kind,
                    feedback_body=feedback_body,
                )
                worker_out_file = rounds_dir / f"inv-{inv_num:02d}-worker.log"
                t0 = time.monotonic()
                rc, w_out, w_err, timed_out = invoke_agent_cli(
                    worker,
                    role="worker",
                    worktree=worktree,
                    prompt=worker_prompt,
                    timeout_sec=timeout_sec,
                    out_file=worker_out_file,
                )
                dur = round(time.monotonic() - t0, 2)
                manifest["step_timings"]["worker_sec"] = round(
                    manifest["step_timings"].get("worker_sec", 0.0) + dur, 2
                )
                manifest.setdefault("invocation_history", []).append(
                    {
                        "role": "worker",
                        "cli": worker.cli,
                        "model": worker.model,
                        "family": worker.family,
                        "identity_source": worker.identity_source,
                        "ts": utc_now(),
                        "duration_sec": dur,
                        "rc": rc,
                        "timed_out": timed_out,
                    }
                )

                # Integrity checks run BEFORE failure/timeout handling so tampered runs never remain resumable
                violation = check_lifecycle_integrity("post-worker")
                if violation:
                    return finish(
                        "integrity_violation",
                        violation,
                        4,
                        f"plite INTEGRITY VIOLATION ({manifest['slug']})",
                    )

                if timed_out:
                    return finish(
                        "infra_error",
                        f"worker ({worker.cli}) timed out after {timeout_sec}s on invocation {inv_num}",
                        3,
                        f"plite ERROR ({manifest['slug']})",
                    )
                if rc != 0:
                    err_tail = (w_err.strip() or w_out.strip())[-240:]
                    return finish(
                        "infra_error",
                        f"worker ({worker.cli}) exited rc={rc} on invocation {inv_num}: {err_tail}",
                        3,
                        f"plite ERROR ({manifest['slug']})",
                    )

                # Clean worktree check (tracked & untracked)
                porcelain = git(worktree, "status", "--porcelain")
                if porcelain:
                    feedback_kind = "dirty_worktree"
                    feedback_body = (
                        "Worktree is not clean after your turn. Commit all intended changes and remove any "
                        f"untracked scratch files.\n\ngit status --porcelain:\n{porcelain}"
                    )
                    manifest["pending_feedback_kind"] = feedback_kind
                    manifest["pending_feedback_body"] = feedback_body
                    manifest["verify_failures"] += 1
                    save_manifest(run_dir, manifest)
                    continue

                candidate_sha = git(worktree, "rev-parse", "HEAD")

                last_reviewed = manifest.get("last_reviewed_sha")
                if candidate_sha == manifest["base_sha"] or (last_reviewed and candidate_sha == last_reviewed):
                    feedback_kind = "no_new_commit"
                    feedback_body = (
                        f"No new commit was created on branch '{manifest['branch']}' "
                        f"(HEAD is still {candidate_sha}). Implement the required changes and commit them."
                    )
                    manifest["pending_feedback_kind"] = feedback_kind
                    manifest["pending_feedback_body"] = feedback_body
                    manifest["verify_failures"] += 1
                    save_manifest(run_dir, manifest)
                    continue

                manifest["candidate_sha"] = candidate_sha
                manifest["verified_sha"] = None
                phase = "verify"
                manifest["phase"] = phase
                save_manifest(run_dir, manifest)

            # ---- PHASE 2: EXTERNAL VERIFY ----
            if phase == "verify":
                candidate_sha = git(worktree, "rev-parse", "HEAD")
                cur_branch = git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
                if cur_branch != manifest["branch"] or not git_is_ancestor(worktree, manifest["base_sha"], candidate_sha):
                    return finish(
                        "integrity_violation",
                        f"integrity violation before verify: branch={cur_branch} head={candidate_sha}",
                        4,
                        f"plite INTEGRITY VIOLATION ({manifest['slug']})",
                    )
                if git(worktree, "status", "--porcelain"):
                    return finish(
                        "integrity_violation",
                        "integrity violation: worktree became dirty before verify",
                        4,
                        f"plite INTEGRITY VIOLATION ({manifest['slug']})",
                    )

                try:
                    file_audit = inspect_changed_files(
                        worktree,
                        manifest["base_sha"],
                        candidate_sha,
                        allow_protected_edit=manifest["allow_protected_edit"],
                        extra_protected_globs=manifest["extra_protected_globs"],
                    )
                except PliteError as exc:
                    return finish("escalated", str(exc), 5, f"plite ESCALATED ({manifest['slug']})")

                manifest["candidate_sha"] = candidate_sha
                manifest["existing_tests_modified"] = file_audit["modified_existing_tests"]
                manifest["status"] = "verifying"
                save_manifest(run_dir, manifest)

                inv_tag = manifest["worker_invocations"]
                verify_env = os.environ.copy()
                verify_env["PYTHONDONTWRITEBYTECODE"] = "1"
                t1 = time.monotonic()
                v_rc, v_out, v_err, v_timed_out = run_proc(
                    manifest["verify_cmd"],
                    cwd=worktree,
                    timeout_sec=timeout_sec,
                    shell=True,
                    env=verify_env,
                )
                manifest["step_timings"]["verify_sec"] = round(
                    manifest["step_timings"].get("verify_sec", 0.0) + (time.monotonic() - t1), 2
                )
                verify_log = rounds_dir / f"inv-{inv_tag:02d}-verify.log"
                verify_log.write_text(
                    f"rc={v_rc} timed_out={v_timed_out}\n--- STDOUT ---\n{v_out}\n--- STDERR ---\n{v_err}\n",
                    encoding="utf-8",
                )

                # Integrity check runs BEFORE failure/timeout handling so mutating verify commands quarantine immediately
                violation = check_lifecycle_integrity(
                    "post-verify", expected_head=candidate_sha, require_clean=True
                )
                if violation:
                    return finish(
                        "integrity_violation",
                        violation,
                        4,
                        f"plite INTEGRITY VIOLATION ({manifest['slug']})",
                    )

                if v_timed_out:
                    return finish(
                        "infra_error",
                        f"verify command timed out after {timeout_sec}s on {candidate_sha}",
                        3,
                        f"plite VERIFY TIMEOUT ({manifest['slug']})",
                    )
                if v_rc != 0:
                    manifest["verify_failures"] += 1
                    feedback_kind = "verify_failed"
                    feedback_body = (
                        f"External verify command `{manifest['verify_cmd']}` failed (rc={v_rc}) on {candidate_sha}:\n\n"
                        f"STDOUT:\n{v_out[-2500:]}\n\nSTDERR:\n{v_err[-2500:]}"
                    )
                    manifest["pending_feedback_kind"] = feedback_kind
                    manifest["pending_feedback_body"] = feedback_body
                    phase = "worker"
                    manifest["phase"] = phase
                    save_manifest(run_dir, manifest)
                    continue

                manifest["verified_sha"] = candidate_sha
                manifest["last_verify_stdout"] = v_out[-3000:]
                manifest["last_verify_stderr"] = v_err[-3000:]
                phase = "review"
                manifest["phase"] = phase
                save_manifest(run_dir, manifest)

            # ---- PHASE 3: INDEPENDENT REVIEW ----
            if phase == "review":
                candidate_sha = git(worktree, "rev-parse", "HEAD")
                if manifest.get("verified_sha") != candidate_sha or git(worktree, "status", "--porcelain"):
                    return finish(
                        "integrity_violation",
                        f"integrity violation entering review: verified_sha={manifest.get('verified_sha')} head={candidate_sha}",
                        4,
                        f"plite INTEGRITY VIOLATION ({manifest['slug']})",
                    )
                enforce_independence(worker, reviewer, manifest["contributing_families"])

                file_audit = inspect_changed_files(
                    worktree,
                    manifest["base_sha"],
                    candidate_sha,
                    allow_protected_edit=manifest["allow_protected_edit"],
                    extra_protected_globs=manifest["extra_protected_globs"],
                )
                round_num = manifest["review_rounds"] + 1
                rev_inv = manifest["reviewer_invocations"] + 1

                diff_stat = git(worktree, "diff", "--stat", f"{manifest['base_sha']}..{candidate_sha}")
                full_diff = git_raw(worktree, "diff", f"{manifest['base_sha']}..{candidate_sha}")
                diff_file = rounds_dir / f"round-{round_num:02d}.diff"
                diff_file.write_text(full_diff, encoding="utf-8")

                packet_file = rounds_dir / f"round-{round_num:02d}-review-input.md"
                packet_file.write_text(
                    build_review_packet(
                        manifest,
                        round_num=round_num,
                        candidate_sha=candidate_sha,
                        diff_stat=diff_stat,
                        diff_file=diff_file,
                        file_audit=file_audit,
                        verify_stdout=manifest.get("last_verify_stdout", ""),
                        verify_stderr=manifest.get("last_verify_stderr", ""),
                    ),
                    encoding="utf-8",
                )

                manifest["reviewer"] = reviewer.to_dict()
                manifest["status"] = "review_running"
                manifest["reviewer_invocations"] = rev_inv
                save_manifest(run_dir, manifest)

                rev_prompt = build_reviewer_prompt(packet_file, diff_file, candidate_sha)
                rev_out_file = rounds_dir / f"round-{round_num:02d}-rev-{rev_inv:02d}.log"
                t2 = time.monotonic()
                r_rc, r_out, r_err, r_timed_out = invoke_agent_cli(
                    reviewer,
                    role="reviewer",
                    worktree=worktree,
                    prompt=rev_prompt,
                    timeout_sec=timeout_sec,
                    out_file=rev_out_file,
                )
                dur_r = round(time.monotonic() - t2, 2)
                manifest["step_timings"]["reviewer_sec"] = round(
                    manifest["step_timings"].get("reviewer_sec", 0.0) + dur_r, 2
                )
                manifest.setdefault("invocation_history", []).append(
                    {
                        "role": "reviewer",
                        "cli": reviewer.cli,
                        "model": reviewer.model,
                        "family": reviewer.family,
                        "identity_source": reviewer.identity_source,
                        "ts": utc_now(),
                        "duration_sec": dur_r,
                        "rc": r_rc,
                        "timed_out": r_timed_out,
                    }
                )

                # Integrity check runs BEFORE exit/verdict handling (covers base_ref, branch, HEAD, cleanliness)
                violation = check_lifecycle_integrity(
                    f"post-reviewer:{reviewer.cli}", expected_head=candidate_sha, require_clean=True
                )
                if violation:
                    return finish(
                        "integrity_violation",
                        violation,
                        4,
                        f"plite INTEGRITY VIOLATION ({manifest['slug']})",
                    )

                if r_timed_out:
                    return finish(
                        "infra_error",
                        f"reviewer ({reviewer.cli}) timed out after {timeout_sec}s on round {round_num}",
                        3,
                        f"plite REVIEW TIMEOUT ({manifest['slug']})",
                    )
                if r_rc != 0:
                    err_tail = (r_err.strip() or r_out.strip())[-240:]
                    return finish(
                        "infra_error",
                        f"reviewer ({reviewer.cli}) exited rc={r_rc} on round {round_num}: {err_tail}",
                        3,
                        f"plite REVIEW ERROR ({manifest['slug']})",
                    )

                try:
                    verdict_obj = parse_verdict(r_out, candidate_sha)
                except PliteError as exc:
                    return finish(
                        "infra_error",
                        f"invalid reviewer verdict on round {round_num}: {exc}",
                        3,
                        f"plite VERDICT ERROR ({manifest['slug']})",
                    )

                manifest["review_rounds"] = round_num
                manifest["last_reviewed_sha"] = candidate_sha
                manifest["rounds"].append(
                    {
                        "round": round_num,
                        "candidate_sha": candidate_sha,
                        "verdict": verdict_obj["verdict"],
                        "findings": verdict_obj["findings"],
                        "reviewer": reviewer.to_dict(),
                        "ts": utc_now(),
                    }
                )

                if verdict_obj["verdict"] == "APPROVE":
                    manifest["approved_sha"] = candidate_sha
                    manifest["pending_feedback_kind"] = None
                    manifest["pending_feedback_body"] = None
                    manifest["phase"] = "done"
                    return finish(
                        "approved",
                        f"approved at {candidate_sha[:12]} in {round_num} review round(s)",
                        0,
                        f"plite APPROVED ({manifest['slug']})",
                    )

                # CHANGES_REQUESTED -> return to worker phase
                formatted_findings = "\n".join(
                    f"- [{f['tag']}|{f['severity']}] {f['file']}: {f['summary']}"
                    for f in verdict_obj["findings"]
                )
                feedback_kind = "review_changes_requested"
                feedback_body = (
                    f"Independent reviewer ({reviewer.cli}/{reviewer.family}) requested changes on {candidate_sha}:\n\n"
                    f"{formatted_findings}"
                )
                manifest["pending_feedback_kind"] = feedback_kind
                manifest["pending_feedback_body"] = feedback_body
                phase = "worker"
                manifest["phase"] = phase
                save_manifest(run_dir, manifest)

    except BaseException as exc:
        # Guarantee durable failure telemetry even on unexpected exceptions or SIGINT.
        reason = f"unexpected {type(exc).__name__}: {exc}"
        try:
            finish("infra_error", reason, 3, f"plite CRASH ({manifest['slug']})")
        except Exception:
            pass
        if isinstance(exc, KeyboardInterrupt):
            return 130
        if isinstance(exc, PliteError):
            return 2
        raise


def validate_positive_ints(args: argparse.Namespace) -> None:
    for name in ("max_rounds", "max_invocations", "timeout_sec"):
        val = getattr(args, name, None)
        if val is not None and val < 1:
            raise PliteError(f"--{name.replace('_', '-')} must be >= 1 (got {val})")


def cmd_run(args: argparse.Namespace) -> int:
    validate_positive_ints(args)
    if args.risk != "low":
        print(
            "ESCALATE: --risk high is outside plite's admission boundary (87.199). "
            "Use the full pipeline (prd -> arch -> task -> impl -> review with frozen spec).",
            file=sys.stderr,
        )
        return 2

    repo = pathlib.Path(args.repo).expanduser().resolve()
    if not (repo / ".git").exists():
        raise PliteError(f"not a git repository: {repo}")

    if args.task_file:
        task_path = pathlib.Path(args.task_file).expanduser().resolve()
        if not task_path.is_file():
            raise PliteError(f"task file not found: {task_path}")
        task_text = task_path.read_text(encoding="utf-8").strip()
    else:
        task_text = args.task.strip()
    if not task_text:
        raise PliteError("task description must be non-empty")

    acceptance = args.acceptance.strip()
    if len(acceptance) < 8 or acceptance.lower() in ("tbd", "todo", "none", "n/a"):
        raise PliteError("--acceptance must contain concrete objective criteria (not a placeholder)")

    worker = resolve_agent_identity(args.worker, args.worker_model, args.worker_family)
    reviewer = resolve_agent_identity(args.reviewer, args.reviewer_model, args.reviewer_family)
    enforce_independence(worker, reviewer, [])

    base_sha = git(repo, "rev-parse", "--verify", f"{args.base}^{{commit}}")
    slug = slugify(args.slug or task_text)
    short_id = uuid.uuid4().hex[:8]
    run_id = f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}-{slug}-{short_id}"

    state_dir = pathlib.Path(args.state_dir).expanduser().resolve()
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    worktree_path = run_dir / "worktree"
    branch = f"plite/{slug}-{short_id}"

    manifest: dict[str, Any] = {
        "run_id": run_id,
        "experiment_version": EXPERIMENT_VERSION,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "repo": str(repo),
        "slug": slug,
        "risk_class": args.risk,
        "task_type": args.task_type,
        "task_text": task_text,
        "acceptance_criteria": acceptance,
        "verify_cmd": args.verify,
        "base_ref": args.base,
        "base_sha": base_sha,
        "branch": branch,
        "worktree_path": str(worktree_path),
        "worker": worker.to_dict(),
        "reviewer": reviewer.to_dict(),
        "cli_versions": {},
        "invocation_history": [],
        "contributing_families": [],
        "max_rounds": args.max_rounds,
        "max_invocations": args.max_invocations,
        "allow_protected_edit": bool(args.allow_protected_edit),
        "extra_protected_globs": list(args.protected_glob or []),
        "worker_invocations": 0,
        "reviewer_invocations": 0,
        "review_rounds": 0,
        "verify_failures": 0,
        "candidate_sha": None,
        "verified_sha": None,
        "last_reviewed_sha": None,
        "approved_sha": None,
        "merged_sha": None,
        "existing_tests_modified": [],
        "rounds": [],
        "wall_time_sec": 0.0,
        "step_timings": {},
        "human_active_time_sec": None,
        "interventions_by_reason": {},
        "phase": "worker",
        "status": "initializing",
        "outcome_reason": "",
        "telemetry_recorded": False,
        "regression_status": "pending",
        "pending_feedback_kind": None,
        "pending_feedback_body": None,
    }

    with acquire_run_lock(run_dir, run_id):
        save_manifest(run_dir, manifest)
        try:
            manifest["cli_versions"] = {
                worker.cli: detect_cli_version(worker.cli),
                reviewer.cli: detect_cli_version(reviewer.cli),
            }
            git(repo, "worktree", "add", "-b", branch, str(worktree_path), base_sha)
        except BaseException as exc:
            manifest["status"] = "infra_error"
            manifest["outcome_reason"] = f"initialization failed ({type(exc).__name__}): {exc}"
            manifest["telemetry_recorded"] = False
            save_manifest(run_dir, manifest)
            flush_manifest_telemetry(state_dir, run_dir, manifest)
            if isinstance(exc, KeyboardInterrupt):
                return 130
            raise PliteError(manifest["outcome_reason"]) from exc

        manifest["status"] = "ready"
        save_manifest(run_dir, manifest)
        return execute_loop_locked(
            state_dir,
            run_dir,
            manifest,
            worker,
            reviewer,
            timeout_sec=args.timeout_sec,
            notify_cmd=args.notify_cmd,
            no_notify=args.no_notify,
        )


def cmd_resume(args: argparse.Namespace) -> int:
    validate_positive_ints(args)
    state_dir = pathlib.Path(args.state_dir).expanduser().resolve()
    run_dir = state_dir / "runs" / args.run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PliteError(f"run manifest not found: {manifest_path}")

    with acquire_run_lock(run_dir, args.run_id):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PliteError(f"corrupt manifest.json for '{args.run_id}': {exc}") from exc

        status = manifest.get("status")
        # Crash recovery: flush missed terminal telemetry for ANY terminal status.
        if status in TERMINAL_STATUSES and manifest.get("telemetry_recorded") is False:
            flush_manifest_telemetry(state_dir, run_dir, manifest)

        if status == "approved":
            print(f"[ALREADY APPROVED] run_id={args.run_id} approved_sha={manifest.get('approved_sha')}")
            return 0
        if status == "integrity_violation":
            raise PliteError(
                f"run '{args.run_id}' terminated with integrity_violation ({manifest.get('outcome_reason')}); "
                "refusing to resume — inspect worktree and start a fresh run"
            )

        worktree = pathlib.Path(manifest["worktree_path"])
        if not worktree.is_dir():
            raise PliteError(f"worktree missing for run '{args.run_id}': {worktree}")

        prev_w = manifest["worker"]
        prev_r = manifest["reviewer"]
        w_cli = args.worker or prev_w["cli"]
        r_cli = args.reviewer or prev_r["cli"]
        w_model = args.worker_model if args.worker_model is not None else (prev_w["model"] if w_cli == prev_w["cli"] else None)
        r_model = args.reviewer_model if args.reviewer_model is not None else (prev_r["model"] if r_cli == prev_r["cli"] else None)
        w_fam = args.worker_family if args.worker_family is not None else (prev_w["family"] if (w_cli == prev_w["cli"] and w_model == prev_w["model"]) else None)
        r_fam = args.reviewer_family if args.reviewer_family is not None else (prev_r["family"] if (r_cli == prev_r["cli"] and r_model == prev_r["model"]) else None)

        worker = resolve_agent_identity(w_cli, w_model, w_fam)
        reviewer = resolve_agent_identity(r_cli, r_model, r_fam)
        enforce_independence(worker, reviewer, manifest["contributing_families"])

        manifest.setdefault("cli_versions", {})[worker.cli] = detect_cli_version(worker.cli)
        manifest["cli_versions"][reviewer.cli] = detect_cli_version(reviewer.cli)

        reason = args.resume_reason or "quota_swap"
        manifest.setdefault("interventions_by_reason", {})
        manifest["interventions_by_reason"][reason] = manifest["interventions_by_reason"].get(reason, 0) + 1
        save_manifest(run_dir, manifest)

        return execute_loop_locked(
            state_dir,
            run_dir,
            manifest,
            worker,
            reviewer,
            timeout_sec=args.timeout_sec,
            notify_cmd=args.notify_cmd,
            no_notify=args.no_notify,
        )


def cmd_mark(args: argparse.Namespace) -> int:
    state_dir = pathlib.Path(args.state_dir).expanduser().resolve()
    run_dir = state_dir / "runs" / args.run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PliteError(f"run manifest not found: {manifest_path}")

    with acquire_run_lock(run_dir, args.run_id):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if args.merged_sha:
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", args.merged_sha.strip()):
                raise PliteError(f"invalid --merged-sha '{args.merged_sha}' (expected hex commit SHA)")
            repo = pathlib.Path(manifest["repo"])
            resolved = git(repo, "rev-parse", "--verify", f"{args.merged_sha.strip()}^{{commit}}")
            manifest["merged_sha"] = resolved
        if args.regression:
            manifest["regression_status"] = args.regression
        if args.human_time is not None:
            if args.human_time < 0:
                raise PliteError("--human-time must be >= 0")
            manifest["human_active_time_sec"] = args.human_time
        for reason in args.intervention or []:
            manifest.setdefault("interventions_by_reason", {})
            manifest["interventions_by_reason"][reason] = manifest["interventions_by_reason"].get(reason, 0) + 1

        save_manifest(run_dir, manifest)
        append_jsonl(
            state_dir / "runs.jsonl",
            {
                "record_type": "annotation",
                "ts": utc_now(),
                "run_id": args.run_id,
                "regression_status": manifest.get("regression_status"),
                "merged_sha": manifest.get("merged_sha"),
                "human_active_time_sec": manifest.get("human_active_time_sec"),
                "interventions_by_reason": manifest.get("interventions_by_reason", {}),
            },
        )
        print(
            f"Marked run_id={args.run_id}: regression={manifest['regression_status']} "
            f"merged_sha={manifest.get('merged_sha')}"
        )
    return 0


def evaluate_pilot_runs(jsonl_path: pathlib.Path) -> dict[str, Any]:
    runs: dict[str, dict[str, Any]] = {}
    malformed_lines = 0
    if jsonl_path.is_file():
        for raw_line in jsonl_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                rec = json.loads(raw_line)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue
            if not isinstance(rec, dict):
                malformed_lines += 1
                continue
            rid = rec.get("run_id")
            if not rid:
                continue
            if rec.get("record_type") == "annotation":
                if rid in runs:
                    for k in ("regression_status", "merged_sha", "human_active_time_sec", "interventions_by_reason"):
                        if rec.get(k) is not None:
                            runs[rid][k] = rec[k]
            else:
                runs[rid] = rec

    total = len(runs)
    if total == 0:
        return {"total": 0, "malformed_lines": malformed_lines, "gate": "COLLECTING", "gate_reasons": ["no runs"]}

    approved = sum(1 for r in runs.values() if r.get("outcome") == "approved")
    blocked = sum(1 for r in runs.values() if r.get("outcome") == "blocked")
    infra = sum(1 for r in runs.values() if r.get("outcome") == "infra_error")
    escalated = sum(1 for r in runs.values() if r.get("outcome") == "escalated")
    integrity_violations = sum(1 for r in runs.values() if r.get("outcome") == "integrity_violation")

    # Rescue interventions exclude routine final merge confirmation.
    autonomous_approved = 0
    all_interventions: list[int] = []
    runs_with_spec_gap = 0
    for r in runs.values():
        ibr = r.get("interventions_by_reason") or {}
        total_int = sum(int(v) for v in ibr.values())
        rescue_int = sum(int(v) for k, v in ibr.items() if k != "merge_confirm")
        all_interventions.append(total_int)
        if r.get("outcome") == "approved" and rescue_int == 0:
            autonomous_approved += 1
        if (r.get("findings_by_tag") or {}).get("spec_gap", 0) > 0:
            runs_with_spec_gap += 1

    all_interventions.sort()
    mid = total // 2
    median_int = all_interventions[mid] if total % 2 == 1 else (all_interventions[mid - 1] + all_interventions[mid]) / 2

    approved_runs = [r for r in runs.values() if r.get("outcome") == "approved"]
    observed_approved = sum(
        1 for r in approved_runs if r.get("regression_status") in ("none_observed", "confirmed")
    )
    confirmed_reg = sum(1 for r in runs.values() if r.get("regression_status") == "confirmed")

    approval_rate = approved / total
    autonomous_rate = autonomous_approved / total
    spec_gap_rate = runs_with_spec_gap / total

    gate_reasons: list[str] = []
    if integrity_violations > 0:
        gate = "KILL — stop pilot"
        gate_reasons.append(f"integrity_violations={integrity_violations} (>0)")
    elif total < 10:
        gate = "COLLECTING"
        gate_reasons.append(f"only {total} eligible runs (need >= 10)")
    else:
        hold_reasons: list[str] = []
        if observed_approved < approved:
            hold_reasons.append(f"missing regression observations ({observed_approved}/{approved} approved observed)")
        if observed_approved > 0 and (confirmed_reg / observed_approved) > 0.10:
            hold_reasons.append(f"regression rate {confirmed_reg}/{observed_approved} > 10%")
        if autonomous_rate < 0.80:
            hold_reasons.append(f"autonomous convergence {autonomous_rate:.0%} < 80%")
        if median_int > 1:
            hold_reasons.append(f"median interventions {median_int} > 1")
        if spec_gap_rate > 0.30:
            hold_reasons.append(f"spec_gap rate {spec_gap_rate:.0%} > 30%")
        if hold_reasons:
            gate = "HOLD — revise workflow"
            gate_reasons.extend(hold_reasons)
        else:
            gate = "GO — expand pilot"
            gate_reasons.append("all pilot expansion criteria met")

    return {
        "total": total,
        "malformed_lines": malformed_lines,
        "approved": approved,
        "autonomous_approved": autonomous_approved,
        "blocked": blocked,
        "infra_error": infra,
        "escalated": escalated,
        "integrity_violations": integrity_violations,
        "approval_rate": approval_rate,
        "autonomous_rate": autonomous_rate,
        "spec_gap_rate": spec_gap_rate,
        "median_interventions": median_int,
        "observed_approved": observed_approved,
        "confirmed_reg": confirmed_reg,
        "gate": gate,
        "gate_reasons": gate_reasons,
    }


def cmd_summary(args: argparse.Namespace) -> int:
    state_dir = pathlib.Path(args.state_dir).expanduser().resolve()
    reconcile_unrecorded_manifests(state_dir)
    stats = evaluate_pilot_runs(state_dir / "runs.jsonl")
    if stats["total"] == 0:
        print("No runs recorded yet.")
        return 0

    print(f"plite Pilot Summary (N={stats['total']})")
    if stats["malformed_lines"]:
        print(f"  warning: skipped {stats['malformed_lines']} malformed JSONL line(s)")
    print(
        f"  outcomes: approved={stats['approved']} ({stats['approval_rate']:.0%}), "
        f"autonomous_approved={stats['autonomous_approved']} ({stats['autonomous_rate']:.0%}), "
        f"blocked={stats['blocked']}, infra_error={stats['infra_error']}, "
        f"escalated={stats['escalated']}, integrity_violations={stats['integrity_violations']}"
    )
    print(f"  median_interventions={stats['median_interventions']} spec_gap_rate={stats['spec_gap_rate']:.0%}")
    print(
        f"  regressions: confirmed={stats['confirmed_reg']} / observed_approved={stats['observed_approved']} "
        f"(unobserved_approved={stats['approved'] - stats['observed_approved']})"
    )
    print(f"  gate: {stats['gate']} ({'; '.join(stats['gate_reasons'])})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="plite.py", description="Minimal local 2-agent x 3-step experiment runner.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Start a new 2-agent run in an isolated git worktree.")
    r.add_argument("--repo", required=True, help="Path to target git repository")
    task_grp = r.add_mutually_exclusive_group(required=True)
    task_grp.add_argument("--task", help="Inline task description")
    task_grp.add_argument("--task-file", help="Path to markdown/text file containing task description")
    r.add_argument("--acceptance", required=True, help="Immutable objective acceptance criteria")
    r.add_argument("--verify", required=True, help="External verification shell command")
    r.add_argument("--risk", required=True, choices=("low", "high"), help="Explicit 87.199 risk classification")
    r.add_argument("--worker", required=True, choices=VALID_CLIS)
    r.add_argument("--reviewer", required=True, choices=VALID_CLIS)
    r.add_argument("--worker-model", default=None)
    r.add_argument("--reviewer-model", default=None)
    r.add_argument("--worker-family", default=None)
    r.add_argument("--reviewer-family", default=None)
    r.add_argument("--base", default="main", help="Base branch/ref (default: main)")
    r.add_argument("--slug", default=None)
    r.add_argument("--task-type", default="feature", choices=("bugfix", "feature", "refactor", "docs", "chore"))
    r.add_argument("--max-rounds", type=int, default=3)
    r.add_argument("--max-invocations", type=int, default=6)
    r.add_argument("--timeout-sec", type=int, default=600)
    r.add_argument("--allow-protected-edit", action="store_true")
    r.add_argument("--protected-glob", action="append", default=[])
    r.add_argument("--notify-cmd", default=None)
    r.add_argument("--no-notify", action="store_true")
    r.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))

    rs = sub.add_parser("resume", help="Resume an interrupted/blocked run by run_id (phase-aware).")
    rs.add_argument("--run-id", required=True)
    rs.add_argument("--worker", choices=VALID_CLIS, default=None)
    rs.add_argument("--reviewer", choices=VALID_CLIS, default=None)
    rs.add_argument("--worker-model", default=None)
    rs.add_argument("--reviewer-model", default=None)
    rs.add_argument("--worker-family", default=None)
    rs.add_argument("--reviewer-family", default=None)
    rs.add_argument("--resume-reason", default="quota_swap")
    rs.add_argument("--timeout-sec", type=int, default=600)
    rs.add_argument("--notify-cmd", default=None)
    rs.add_argument("--no-notify", action="store_true")
    rs.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))

    m = sub.add_parser("mark", help="Backfill merge SHA, regression observation, or human interventions.")
    m.add_argument("--run-id", required=True)
    m.add_argument("--regression", choices=VALID_REGRESSION_STATUSES, default=None)
    m.add_argument("--merged-sha", default=None)
    m.add_argument("--human-time", type=float, default=None)
    m.add_argument("--intervention", action="append", default=[])
    m.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))

    s = sub.add_parser("summary", help="Print pilot scorecard from runs.jsonl.")
    s.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            return cmd_run(args)
        if args.command == "resume":
            return cmd_resume(args)
        if args.command == "mark":
            return cmd_mark(args)
        if args.command == "summary":
            return cmd_summary(args)
        return 2
    except PliteError as exc:
        print(f"PLITE ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
