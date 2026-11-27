#!/usr/bin/env python3
"""Unit and end-to-end integration tests for experiments/plite.py.

Exercises every integrity guard, phase-aware resume path, locking, verdict parser,
protected config escalation, CLI argument validation, and pilot decision gate.
"""

from __future__ import annotations

import fcntl
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest

EXP_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(EXP_DIR))

import plite  # noqa: E402


def write_executable(path: pathlib.Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def init_sample_repo(repo_dir: pathlib.Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    (repo_dir / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (repo_dir / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo_dir / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8"
    )
    (repo_dir / "pyproject.toml").write_text('[project]\nname = "sample"\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_dir, text=True).strip()


class TestUnitGuards(unittest.TestCase):
    def test_resolve_identity_consistency_and_config(self) -> None:
        with self.assertRaises(plite.PliteError):
            plite.resolve_agent_identity("agy", None, None)
        with self.assertRaises(plite.PliteError):
            plite.resolve_agent_identity("agy", None, "google")
        with self.assertRaises(plite.PliteError):
            plite.resolve_agent_identity("codex", None, "openai", codex_config=pathlib.Path("/nonexistent/config.toml"))
        with self.assertRaises(plite.PliteError):
            plite.resolve_agent_identity("claude", None, "openai", claude_settings=pathlib.Path("/nonexistent/settings.json"))
        # Claude with default anthropic explicit family is consistent
        cl_def = plite.resolve_agent_identity("claude", None, "anthropic", claude_settings=pathlib.Path("/nonexistent/settings.json"))
        self.assertEqual(cl_def.family, "anthropic")
        with self.assertRaises(plite.PliteError):
            plite.resolve_agent_identity("agy", "mystery-model-9", None)
        # Explicit family contradicting recognizable model must fail closed
        with self.assertRaises(plite.PliteError):
            plite.resolve_agent_identity("agy", "claude-opus-5-5", "google")

        agy = plite.resolve_agent_identity("agy", "gemini-4-neon", None)
        self.assertEqual(agy.family, "google")
        self.assertEqual(agy.identity_source, "model_arg")

        explicit = plite.resolve_agent_identity("agy", "custom-local-9", "google")
        self.assertEqual(explicit.family, "google")
        self.assertEqual(explicit.model, "custom-local-9")

        with tempfile.TemporaryDirectory() as td:
            cfg = pathlib.Path(td) / "config.toml"
            cfg.write_text('model = "gpt-6.2-sol"\n', encoding="utf-8")
            codex = plite.resolve_agent_identity("codex", None, None, codex_config=cfg)
            self.assertEqual((codex.model, codex.family, codex.identity_source), ("gpt-6.2-sol", "openai", "config"))

            cset = pathlib.Path(td) / "settings.json"
            cset.write_text(json.dumps({"model": "claude-opus-5-5"}), encoding="utf-8")
            cl = plite.resolve_agent_identity("claude", None, None, claude_settings=cset)
            self.assertEqual((cl.model, cl.family, cl.identity_source), ("claude-opus-5-5", "anthropic", "config"))

    def test_cross_model_and_cross_resume_independence(self) -> None:
        w = plite.AgentIdentity("agy", "claude-opus-5-5", "anthropic", "model_arg")
        r = plite.AgentIdentity("claude", None, "anthropic", "cli_default")
        with self.assertRaises(plite.PliteError):
            plite.enforce_independence(w, r, [])

        w2 = plite.AgentIdentity("agy", "gemini-4-neon", "google", "model_arg")
        r2 = plite.AgentIdentity("codex", "gpt-6.2-sol", "openai", "config")
        with self.assertRaises(plite.PliteError):
            plite.enforce_independence(w2, r2, ["openai"])

    def test_parse_verdict_strict_single_block_and_types(self) -> None:
        sha = "a" * 40
        good = f'Notes\n```plite-verdict\n{{"verdict":"APPROVE","reviewed_sha":"{sha}","findings":[]}}\n```'
        self.assertEqual(plite.parse_verdict(good, sha)["verdict"], "APPROVE")

        # Duplicate same-verdict blocks must fail closed
        dup = good + "\n" + good
        with self.assertRaises(plite.PliteError):
            plite.parse_verdict(dup, sha)

        # Non-string verdict type (e.g. list) must raise PliteError, not TypeError
        bad_type = f'```plite-verdict\n{{"verdict":["APPROVE"],"reviewed_sha":"{sha}","findings":[]}}\n```'
        with self.assertRaises(plite.PliteError):
            plite.parse_verdict(bad_type, sha)

        # Missing or empty file reference must fail closed
        bad_file = (
            '```plite-verdict\n'
            f'{{"verdict":"CHANGES_REQUESTED","reviewed_sha":"{sha}","findings":[{{"tag":"semantic_bug","severity":"blocking","file":"","summary":"bug"}}]}}\n'
            '```'
        )
        with self.assertRaises(plite.PliteError):
            plite.parse_verdict(bad_file, sha)

        # Inconsistent APPROVE with blocking finding
        bad_approve = (
            '```plite-verdict\n'
            f'{{"verdict":"APPROVE","reviewed_sha":"{sha}","findings":[{{"tag":"semantic_bug","severity":"blocking","file":"a.py:1","summary":"bug"}}]}}\n'
            '```'
        )
        with self.assertRaises(plite.PliteError):
            plite.parse_verdict(bad_approve, sha)

    def test_pilot_decision_gate_logic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "runs.jsonl"

            def write_runs(recs: list[dict]) -> dict:
                p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
                return plite.evaluate_pilot_runs(p)

            def mk(i: int, **kw) -> dict:
                base = {
                    "record_type": "run_terminal",
                    "run_id": f"r{i}",
                    "outcome": "approved",
                    "regression_status": "none_observed",
                    "interventions_by_reason": {},
                    "findings_by_tag": {},
                }
                base.update(kw)
                return base

            # 10 clean autonomous observed runs -> GO
            go = write_runs([mk(i) for i in range(10)])
            self.assertEqual(go["gate"], "GO — expand pilot")
            self.assertEqual(go["autonomous_rate"], 1.0)

            # Missing regression observations -> HOLD
            unobs = write_runs([mk(i, regression_status="pending" if i == 0 else "none_observed") for i in range(10)])
            self.assertEqual(unobs["gate"], "HOLD — revise workflow")

            # Approved only via quota rescue -> autonomous rate drops -> HOLD
            rescued = write_runs([mk(i, interventions_by_reason={"quota_swap": 1} if i < 3 else {}) for i in range(10)])
            self.assertEqual(rescued["approval_rate"], 1.0)
            self.assertAlmostEqual(rescued["autonomous_rate"], 0.7)
            self.assertEqual(rescued["gate"], "HOLD — revise workflow")

            # Any integrity violation -> KILL immediately (even N < 10)
            killed = write_runs([mk(0, outcome="integrity_violation")])
            self.assertEqual(killed["gate"], "KILL — stop pilot")

            # High spec_gap rate -> HOLD
            spec_gap = write_runs([mk(i, findings_by_tag={"spec_gap": 1} if i < 4 else {}) for i in range(10)])
            self.assertEqual(spec_gap["gate"], "HOLD — revise workflow")

            # Regression rate > 10% -> HOLD
            reg_high = write_runs([mk(i, regression_status="confirmed" if i < 2 else "none_observed") for i in range(10)])
            self.assertEqual(reg_high["gate"], "HOLD — revise workflow")

            # Median interventions > 1 -> HOLD
            med_high = write_runs([mk(i, interventions_by_reason={"merge_confirm": 2}) for i in range(10)])
            self.assertEqual(med_high["gate"], "HOLD — revise workflow")

            # Malformed JSONL line is skipped, not fatal
            p.write_text(json.dumps(mk(0)) + "\n{broken json\n", encoding="utf-8")
            res = plite.evaluate_pilot_runs(p)
            self.assertEqual(res["total"], 1)
            self.assertEqual(res["malformed_lines"], 1)


class TestEndToEndRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.base_sha = init_sample_repo(self.repo)
        self.orig_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{self.bin_dir}:{self.orig_path}"

    def tearDown(self) -> None:
        os.environ["PATH"] = self.orig_path
        self.tmp.cleanup()

    def _install_strict_stubs(self, worker_body: str, reviewer_body: str) -> None:
        # Strict CLI flag checks so flag regressions are caught.
        write_executable(
            self.bin_dir / "agy",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import os, pathlib, subprocess, sys
                if "--version" in sys.argv:
                    print("agy-stub 1.5")
                    raise SystemExit(0)
                assert "-p" in sys.argv and "--mode" in sys.argv and "--model" in sys.argv, f"bad agy args: {{sys.argv}}"
                prompt = sys.stdin.read()
                {worker_body}
                """
            ),
        )
        write_executable(
            self.bin_dir / "claude",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import os, pathlib, subprocess, sys
                if "--version" in sys.argv:
                    print("claude-stub 2.1")
                    raise SystemExit(0)
                assert "-p" in sys.argv and "--permission-mode" in sys.argv, f"bad claude args: {{sys.argv}}"
                prompt = sys.stdin.read()
                {worker_body}
                """
            ),
        )
        write_executable(
            self.bin_dir / "codex",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json, os, pathlib, subprocess, sys
                if "--version" in sys.argv:
                    print("codex-stub 0.172")
                    raise SystemExit(0)
                assert sys.argv[1] == "exec" and "-s" in sys.argv and "-o" in sys.argv and sys.argv[-1] == "-", f"bad codex args: {{sys.argv}}"
                out_path, cwd = None, os.getcwd()
                for i, a in enumerate(sys.argv):
                    if a == "-o" and i + 1 < len(sys.argv):
                        out_path = pathlib.Path(sys.argv[i + 1])
                    if a == "-C" and i + 1 < len(sys.argv):
                        cwd = sys.argv[i + 1]
                prompt = sys.stdin.read()
                sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
                {reviewer_body}
                """
            ),
        )

    def _read_jsonl(self) -> list[dict]:
        p = self.state / "runs.jsonl"
        if not p.is_file():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_verify_failure_autofix_then_review_autofix(self) -> None:
        # Inv 1: breaks verify; Inv 2: fixes verify but has semantic bug caught by reviewer; Inv 3: approved.
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                p = pathlib.Path("app.py")
                if "verify_failed" in prompt:
                    p.write_text("def add(a, b):\\n    return a + b\\n\\ndef sub(a, b):\\n    return a + b\\n")
                    msg = "fix verify, wrong sub"
                elif "review_changes_requested" in prompt:
                    p.write_text("def add(a, b):\\n    return a + b\\n\\ndef sub(a, b):\\n    return a - b\\n")
                    msg = "fix sub"
                else:
                    p.write_text("def add(a, b):\\n    return a - b\\n")
                    msg = "broken add"
                subprocess.run(["git", "add", "app.py"], check=True)
                subprocess.run(["git", "commit", "-m", msg], check=True)
                """
            ),
            "                ",
        ).strip()
        reviewer_code = textwrap.indent(
            textwrap.dedent(
                """\
                app_txt = (pathlib.Path(cwd) / "app.py").read_text()
                if "def sub(a, b):\\n    return a - b" in app_txt:
                    v = {"verdict": "APPROVE", "reviewed_sha": sha, "findings": []}
                else:
                    v = {"verdict": "CHANGES_REQUESTED", "reviewed_sha": sha, "findings": [
                        {"tag": "semantic_bug", "severity": "blocking", "file": "app.py:4", "summary": "sub adds instead of subtracts"}
                    ]}
                txt = "Done.\\n```plite-verdict\\n" + json.dumps(v) + "\\n```\\n"
                out_path.write_text(txt)
                print(txt)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(worker_code, reviewer_code)

        rc = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Add sub(a, b)",
                "--acceptance", "sub(5, 3) returns 2 and existing add test passes",
                "--verify", "python3 -c 'import test_app; test_app.test_add()'",
                "--risk", "low",
                "--worker", "agy",
                "--worker-model", "gemini-4-neon",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc, 0)
        rec = self._read_jsonl()[0]
        self.assertEqual(rec["outcome"], "approved")
        self.assertEqual(rec["verify_failures"], 1)
        self.assertEqual(rec["worker_invocations"], 3)
        self.assertEqual(rec["review_rounds"], 2)
        self.assertEqual(len(rec["invocation_history"]), 5)

    def test_protected_config_modification_and_test_deletion_escalate(self) -> None:
        # Modify protected pyproject.toml -> must escalate
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                pathlib.Path("pyproject.toml").write_text('[project]\\nname = "tampered"\\n')
                subprocess.run(["git", "add", "pyproject.toml"], check=True)
                subprocess.run(["git", "commit", "-m", "edit config"], check=True)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(worker_code, "raise SystemExit(1)")

        rc = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Update code",
                "--acceptance", "code updated without touching build config",
                "--verify", "true",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc, 5)
        self.assertIn("pyproject.toml", self._read_jsonl()[0]["outcome_reason"])

        # Also test actual test-file deletion escalation
        del_code = textwrap.indent(
            textwrap.dedent(
                """\
                subprocess.run(["git", "rm", "test_app.py"], check=True)
                subprocess.run(["git", "commit", "-m", "delete test"], check=True)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(del_code, "raise SystemExit(1)")
        rc_del = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Delete test check",
                "--acceptance", "existing tests remain intact",
                "--verify", "true",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc_del, 5)
        self.assertIn("deleted test file", self._read_jsonl()[-1]["outcome_reason"])

    def test_failing_verify_that_mutates_worktree_is_integrity_violation(self) -> None:
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                pathlib.Path("app.py").write_text("def add(a, b):\\n    return a + b\\n# ok\\n")
                subprocess.run(["git", "add", "app.py"], check=True)
                subprocess.run(["git", "commit", "-m", "ok"], check=True)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(worker_code, "raise SystemExit(1)")
        rc = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Failing mutating verify",
                "--acceptance", "verify must not mutate worktree even when failing",
                "--verify", "touch dirty_on_fail.txt && exit 1",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc, 4)
        self.assertEqual(self._read_jsonl()[0]["outcome"], "integrity_violation")

    def test_reviewer_modifying_base_ref_is_integrity_violation(self) -> None:
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                pathlib.Path("app.py").write_text("def add(a, b):\\n    return a + b\\n# w\\n")
                subprocess.run(["git", "add", "app.py"], check=True)
                subprocess.run(["git", "commit", "-m", "worker"], check=True)
                """
            ),
            "                ",
        ).strip()
        reviewer_code = textwrap.indent(
            textwrap.dedent(
                """\
                subprocess.run(["git", "update-ref", "refs/heads/main", sha], cwd=cwd, check=True)
                txt = "```plite-verdict\\n" + json.dumps({"verdict": "APPROVE", "reviewed_sha": sha, "findings": []}) + "\\n```\\n"
                out_path.write_text(txt)
                print(txt)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(worker_code, reviewer_code)
        rc = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Reviewer base ref guard",
                "--acceptance", "base ref stays pinned through review",
                "--verify", "true",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc, 4)
        self.assertEqual(self._read_jsonl()[0]["outcome"], "integrity_violation")

    def test_terminal_telemetry_recovery_and_concurrent_mark_lock(self) -> None:
        run_dir = self.state / "runs" / "recover-run"
        run_dir.mkdir(parents=True)
        manifest = {
            "run_id": "recover-run",
            "repo": str(self.repo),
            "slug": "recover",
            "risk_class": "low",
            "task_type": "feature",
            "acceptance_criteria": "recovered telemetry",
            "base_ref": "main",
            "base_sha": self.base_sha,
            "approved_sha": self.base_sha,
            "candidate_sha": self.base_sha,
            "worker": {"cli": "claude", "model": "claude-opus-5-5", "family": "anthropic", "identity_source": "model_arg"},
            "reviewer": {"cli": "codex", "model": "gpt-6.2-sol", "family": "openai", "identity_source": "model_arg"},
            "contributing_families": ["anthropic"],
            "worker_invocations": 1,
            "reviewer_invocations": 1,
            "review_rounds": 1,
            "verify_failures": 0,
            "status": "approved",
            "outcome_reason": "approved",
            "telemetry_recorded": False,
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        # Resume flushes missing terminal record and returns 0
        rc = plite.main(["resume", "--run-id", "recover-run", "--no-notify", "--state-dir", str(self.state)])
        self.assertEqual(rc, 0)
        recs = self._read_jsonl()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["run_id"], "recover-run")
        # Concurrent mark while locked must fail closed
        with open(run_dir / "lock", "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            rc_mark = plite.main(
                ["mark", "--run-id", "recover-run", "--regression", "none_observed", "--state-dir", str(self.state)]
            )
            self.assertEqual(rc_mark, 2)

    def test_reconcile_recovers_all_terminal_outcomes_and_telemetry_error_preserves_integrity(self) -> None:
        # Unrecorded infra_error init failure recovered automatically by summary()
        run_dir = self.state / "runs" / "init-fail-run"
        run_dir.mkdir(parents=True)
        manifest = {
            "run_id": "init-fail-run",
            "repo": str(self.repo),
            "slug": "init-fail",
            "risk_class": "low",
            "task_type": "feature",
            "acceptance_criteria": "init failure still counted",
            "base_ref": "main",
            "base_sha": self.base_sha,
            "worker": {"cli": "claude", "model": None, "family": "anthropic", "identity_source": "cli_default"},
            "reviewer": {"cli": "codex", "model": "gpt-6.2-sol", "family": "openai", "identity_source": "model_arg"},
            "contributing_families": [],
            "worker_invocations": 0,
            "reviewer_invocations": 0,
            "review_rounds": 0,
            "verify_failures": 0,
            "status": "infra_error",
            "outcome_reason": "worktree init interrupted",
            "telemetry_recorded": False,
        }
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(plite.main(["summary", "--state-dir", str(self.state)]), 0)
        recs = self._read_jsonl()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["run_id"], "init-fail-run")
        self.assertEqual(recs[0]["outcome"], "infra_error")

        # Telemetry flush failure must NOT overwrite integrity_violation status
        bad_dir = self.state / "runs" / "iv-run"
        bad_dir.mkdir(parents=True)
        iv_manifest = dict(manifest, run_id="iv-run", status="integrity_violation", telemetry_recorded=False)
        (bad_dir / "manifest.json").write_text(json.dumps(iv_manifest), encoding="utf-8")
        # Make runs.jsonl a directory so append fails with IsADirectoryError
        bad_state = self.root / "bad_state"
        (bad_state / "runs.jsonl").mkdir(parents=True)
        ok = plite.flush_manifest_telemetry(bad_state, bad_dir, iv_manifest)
        self.assertFalse(ok)
        self.assertEqual(iv_manifest["status"], "integrity_violation")
        self.assertFalse(iv_manifest["telemetry_recorded"])

    def test_timeout_kills_surviving_descendant_process_group(self) -> None:
        # Leader spawns a child ignoring SIGTERM and exits/sleeps; timeout must SIGKILL whole group and not hang.
        script = (
            "import signal, subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']); "
            "time.sleep(60)"
        )
        rc, _out, _err, timed_out = plite.run_proc(
            [sys.executable, "-c", script], cwd=self.root, timeout_sec=1
        )
        self.assertTrue(timed_out)
        self.assertNotEqual(rc, 0)

    def test_missing_base_ref_during_integrity_check_quarantines_as_integrity_violation(self) -> None:
        # Worker deletes base ref refs/heads/main -> git error inside check_lifecycle_integrity must quarantine!
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                pathlib.Path("app.py").write_text("# changed\\n")
                subprocess.run(["git", "add", "app.py"], check=True)
                subprocess.run(["git", "commit", "-m", "change"], check=True)
                subprocess.run(["git", "update-ref", "-d", "refs/heads/main"], check=True)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(worker_code, "raise SystemExit(1)")
        rc = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Deleted base ref guard",
                "--acceptance", "deleted base ref fails closed as integrity violation",
                "--verify", "true",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc, 4)
        self.assertEqual(self._read_jsonl()[0]["outcome"], "integrity_violation")

    def test_phase_aware_reviewer_quota_resume_and_cross_resume_rejection(self) -> None:
        # Worker succeeds & verify passes; Reviewer fails with quota rc=1 -> resume skips worker and resumes review!
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                pathlib.Path("app.py").write_text("def add(a, b):\\n    return a + b\\n# done\\n")
                subprocess.run(["git", "add", "app.py"], check=True)
                subprocess.run(["git", "commit", "-m", "worker done"], check=True)
                """
            ),
            "                ",
        ).strip()
        # First reviewer (codex) fails with quota error; second reviewer (agy) approves.
        write_executable(
            self.bin_dir / "claude",
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import pathlib, subprocess, sys
                if "--version" in sys.argv: print("claude 2.1"); raise SystemExit(0)
                sys.stdin.read()
                {worker_code}
                """
            ),
        )
        write_executable(
            self.bin_dir / "codex",
            "#!/usr/bin/env python3\nimport sys\nif '--version' in sys.argv: print('codex 0.172'); raise SystemExit(0)\nprint('429 quota', file=sys.stderr)\nraise SystemExit(1)\n",
        )
        write_executable(
            self.bin_dir / "agy",
            textwrap.dedent(
                """\
                #!/usr/bin/env python3
                import json, os, subprocess, sys
                if "--version" in sys.argv: print("agy 1.5"); raise SystemExit(0)
                assert "--mode" in sys.argv and "plan" in sys.argv, f"expected read-only plan mode: {sys.argv}"
                sys.stdin.read()
                sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
                print("```plite-verdict\\n" + json.dumps({"verdict": "APPROVE", "reviewed_sha": sha, "findings": []}) + "\\n```")
                """
            ),
        )

        rc1 = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Add comment",
                "--acceptance", "comment added and add test passes",
                "--verify", "python3 -c 'import test_app; test_app.test_add()'",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc1, 3)
        run_id = self._read_jsonl()[0]["run_id"]

        # Attempting to resume with anthropic reviewer must fail closed (claude already contributed code!)
        rc_bad = plite.main(
            [
                "resume",
                "--run-id", run_id,
                "--worker", "agy",
                "--worker-model", "gemini-4-neon",
                "--reviewer", "claude",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc_bad, 2)

        # Resume with agy (google) reviewer: skips worker (worker_invocations stays 1!) and approves in round 1
        rc2 = plite.main(
            [
                "resume",
                "--run-id", run_id,
                "--reviewer", "agy",
                "--reviewer-model", "gemini-4-neon",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc2, 0)
        manifest = json.loads((self.state / "runs" / run_id / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "approved")
        self.assertEqual(manifest["worker_invocations"], 1)
        self.assertEqual(manifest["review_rounds"], 1)

    def test_integrity_violation_blocks_resume_and_branch_drift(self) -> None:
        # Worker switches off its branch -> integrity_violation, cannot resume!
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                subprocess.run(["git", "checkout", "-b", "rogue-branch"], check=True)
                pathlib.Path("app.py").write_text("# rogue\\n")
                subprocess.run(["git", "add", "app.py"], check=True)
                subprocess.run(["git", "commit", "-m", "rogue"], check=True)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(worker_code, "raise SystemExit(1)")

        rc = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Branch drift test",
                "--acceptance", "stays on assigned branch",
                "--verify", "true",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc, 4)
        rec = self._read_jsonl()[0]
        self.assertEqual(rec["outcome"], "integrity_violation")

        # Resume must refuse integrity_violation
        rc_res = plite.main(
            [
                "resume",
                "--run-id", rec["run_id"],
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc_res, 2)

    def test_verify_mutation_fails_closed_as_integrity_violation(self) -> None:
        worker_code = textwrap.indent(
            textwrap.dedent(
                """\
                pathlib.Path("app.py").write_text("def add(a, b):\\n    return a + b\\n# ok\\n")
                subprocess.run(["git", "add", "app.py"], check=True)
                subprocess.run(["git", "commit", "-m", "ok"], check=True)
                """
            ),
            "                ",
        ).strip()
        self._install_strict_stubs(worker_code, "raise SystemExit(1)")

        rc = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Verify mutation test",
                "--acceptance", "verify leaves worktree clean",
                "--verify", "touch rogue_artifact.txt",
                "--risk", "low",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc, 4)
        self.assertEqual(self._read_jsonl()[0]["outcome"], "integrity_violation")

    def test_concurrent_resume_lock_blocks_second_caller(self) -> None:
        run_dir = self.state / "runs" / "fake-locked-run"
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(json.dumps({"status": "infra_error"}), encoding="utf-8")
        lock_file = run_dir / "lock"
        with open(lock_file, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            rc = plite.main(
                ["resume", "--run-id", "fake-locked-run", "--no-notify", "--state-dir", str(self.state)]
            )
            self.assertEqual(rc, 2)

    def test_high_risk_and_invalid_budget_rejected(self) -> None:
        rc_high = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Trading order rewrite",
                "--acceptance", "orders execute safely",
                "--verify", "true",
                "--risk", "high",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc_high, 2)

        rc_zero = plite.main(
            [
                "run",
                "--repo", str(self.repo),
                "--task", "Zero budget test",
                "--acceptance", "valid positive budget required",
                "--verify", "true",
                "--risk", "low",
                "--max-rounds", "0",
                "--worker", "claude",
                "--reviewer", "codex",
                "--reviewer-model", "gpt-6.2-sol",
                "--no-notify",
                "--state-dir", str(self.state),
            ]
        )
        self.assertEqual(rc_zero, 2)


if __name__ == "__main__":
    unittest.main()
