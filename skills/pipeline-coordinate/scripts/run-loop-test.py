#!/usr/bin/env python3
"""Hermetic offline test suite for run-loop.py (stdlib + real temp git repos + stub CLIs).

Independent of operator's global git init.defaultBranch (explicitly initializes `main`).
Run: python3 skills/pipeline-coordinate/scripts/run-loop-test.py
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RUNNER = Path(__file__).resolve().parent / "run-loop.py"
FEATURE = "demo-feat"


def _write_exe(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _git(repo, *args, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r.stdout.strip()


def _mk_fixture(root, name, *, verify_cmds=None, forbid_paths=None, stage="plan-review",
                tail_trans="plan-review→impl · completed", pause_after_plan=False,
                plan_rejects=0, review_rejects=0, planner_model="frontier-a",
                pin_approved_blob=True, git_env=None):
    base = root / name
    remote = base / "remote.git"
    work = base / "work"
    base.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote)], check=True, env=git_env)
    subprocess.run(["git", "clone", "--quiet", str(remote), str(work)], check=True, env=git_env)
    _git(work, "checkout", "-B", "main", env=git_env)
    _git(work, "config", "user.name", "test", env=git_env)
    _git(work, "config", "user.email", "test@example.invalid", env=git_env)
    _git(work, "config", "commit.gpgsign", "false", env=git_env)

    (work / "README.md").write_text("# Demo\n", encoding="utf-8")
    (work / "secrets").mkdir()
    (work / "secrets" / "key.txt").write_text("do-not-touch\n", encoding="utf-8")
    pdir = work / ".pipeline" / FEATURE
    pdir.mkdir(parents=True)
    vlist = verify_cmds if verify_cmds is not None else ["test -f delivered.txt"]
    flist = forbid_paths if forbid_paths is not None else ["secrets/"]
    forbid_block = "forbid-paths: []\n" if not flist else "forbid-paths:\n" + "".join(f'  - "{p}"\n' for p in flist)
    plan_md = (
        "---\n"
        f"feature: {FEATURE}\n"
        f"planner_model: {planner_model}\n"
        "verify:\n"
        + "".join(f'  - "{c}"\n' for c in vlist)
        + forbid_block
        + "---\n"
        "## Baseline Evidence\n- README.md:1\n"
        "## Specifications\n1. Create delivered.txt\n"
        "## Acceptance Matrix (T1-T10)\n- T1: delivered.txt exists\n"
    )
    (pdir / "plan.md").write_text(plan_md, encoding="utf-8")
    (pdir / "journal.md").write_text(
        f"## seq=1 · 2026-11-27T00:00:00Z · {tail_trans} · by=seed\n"
        "done: seed\noutput: .pipeline/demo-feat/plan.md\n"
        "--- handoff ---\n>>> NEXT\nRun pipeline-impl\n<<< END\n",
        encoding="utf-8",
    )
    if pause_after_plan:
        (pdir / "control.json").write_text(
            json.dumps({"schema_version": 1, "mode": "coordinated", "merge_gate": "human-direct", "pause_after_plan": True}),
            encoding="utf-8",
        )
    cur_obj = {
        "repo": str(remote),
        "branch": "main",
        "feature": FEATURE,
        "stage": stage,
        "plan_rejects": plan_rejects,
        "review_rejects": review_rejects,
        "planner_model": planner_model,
    }
    (work / ".pipeline" / "current.json").write_text(json.dumps(cur_obj, indent=2) + "\n", encoding="utf-8")
    _git(work, "add", "-A", env=git_env)
    _git(work, "commit", "--quiet", "-m", "seed", env=git_env)
    if pin_approved_blob:
        blob = _git(work, "rev-parse", f"HEAD:.pipeline/{FEATURE}/plan.md", env=git_env)
        cur_obj["approved_plan_blob"] = blob
        (work / ".pipeline" / "current.json").write_text(json.dumps(cur_obj, indent=2) + "\n", encoding="utf-8")
        _git(work, "add", ".pipeline/current.json", env=git_env)
        _git(work, "commit", "--quiet", "-m", "pin approved_plan_blob", env=git_env)
    _git(work, "push", "--quiet", "-u", "origin", "main", env=git_env)
    return work


def _append_plan_handoff(work, seq_num, note="revised plan", git_env=None):
    j = work / ".pipeline" / FEATURE / "journal.md"
    with open(j, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n## seq={seq_num} · 2026-11-27T00:01:00Z · plan→plan-review · completed · by=planner:frontier-a\n"
            f"done: {note}\noutput: .pipeline/{FEATURE}/plan.md\n"
            "--- handoff ---\n>>> NEXT\nRun pipeline-plan-review\n<<< END\n"
        )
    _git(work, "add", str(j), env=git_env)
    _git(work, "commit", "--quiet", "-m", f"planner handoff {seq_num}", env=git_env)


def _mk_fresh_repo(root, name, *, git_env=None):
    base = root / name
    remote = base / "remote.git"
    work = base / "work"
    base.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote)], check=True, env=git_env)
    subprocess.run(["git", "clone", "--quiet", str(remote), str(work)], check=True, env=git_env)
    _git(work, "checkout", "-B", "main", env=git_env)
    _git(work, "config", "user.name", "test", env=git_env)
    _git(work, "config", "user.email", "test@example.invalid", env=git_env)
    _git(work, "config", "commit.gpgsign", "false", env=git_env)
    (work / "README.md").write_text("# Fresh Repo\n", encoding="utf-8")
    _git(work, "add", "-A", env=git_env)
    _git(work, "commit", "--quiet", "-m", "initial", env=git_env)
    _git(work, "push", "--quiet", "-u", "origin", "main", env=git_env)
    return work


def _mk_config(root, name, impl_cmd, rev_cmd, *, planner_cmd=None, planner_model="frontier-a",
               impl_model="cheap-c", rev_model="frontier-b",
               planner_timeout=30, impl_timeout=30, rev_timeout=30):
    cfg = root / f"runners-{name}.json"
    obj = {
        "implementer": {"model_id": impl_model, "argv": impl_cmd, "timeout_s": impl_timeout},
        "reviewer": {"model_id": rev_model, "argv": rev_cmd, "prompt_via": "stdin", "timeout_s": rev_timeout},
    }
    if planner_cmd is not None:
        obj["planner"] = {"model_id": planner_model, "argv": planner_cmd, "timeout_s": planner_timeout}
    cfg.write_text(json.dumps(obj), encoding="utf-8")
    return cfg


def _run(*args, env=None):
    return subprocess.run([sys.executable, str(RUNNER), *args], capture_output=True, text=True, env=env)


def run_all():
    passed = 0
    failed = 0

    def check(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            print(f"PASS {name}")
            passed += 1
        else:
            print(f"FAIL {name}: {detail}")
            failed += 1

    with tempfile.TemporaryDirectory(prefix="run-loop-test-") as td:
        root = Path(td).resolve()
        # Prove fixtures work even when global git defaultBranch is 'master'!
        fake_home = root / "home-master"
        fake_home.mkdir()
        (fake_home / ".gitconfig").write_text("[init]\n\tdefaultBranch = master\n", encoding="utf-8")
        git_env = os.environ.copy()
        git_env["HOME"] = str(fake_home)
        git_env["GIT_CONFIG_NOSYSTEM"] = "1"

        stubs = root / "stubs"
        stubs.mkdir()

        rev_queue = stubs / "rev-queue.py"
        _write_exe(rev_queue, """#!/usr/bin/env python3
import sys
from pathlib import Path
_ = sys.stdin.read()
q = Path(sys.argv[1])
lines = q.read_text().splitlines()
verdict = lines.pop(0)
q.write_text("\\n".join(lines) + ("\\n" if lines else ""))
print(verdict)
print("Evidence checked.")
""")

        impl_ok = stubs / "impl-ok.py"
        _write_exe(impl_ok, """#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
counter = Path(sys.argv[1])
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
Path("delivered.txt").write_text(f"round {n}\\n")
subprocess.run(["git", "add", "delivered.txt"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", f"impl round {n}"], check=True)
print("## Assumptions\\n- none")
""")

        impl_forbid = stubs / "impl-forbid.py"
        _write_exe(impl_forbid, """#!/usr/bin/env python3
import subprocess
from pathlib import Path
Path("delivered.txt").write_text("ok\\n")
Path("secrets/key.txt").write_text("hacked\\n")
subprocess.run(["git", "add", "delivered.txt", "secrets/key.txt"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", "touch secret"], check=True)
""")

        impl_pipeline_edit = stubs / "impl-pipeline-edit.py"
        _write_exe(impl_pipeline_edit, """#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
counter = Path(sys.argv[1])
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
Path("delivered.txt").write_text(f"ok {n}\\n")
Path(".pipeline/current.json").write_text(f'{{"tampered": {n}}}\\n')
subprocess.run(["git", "add", "delivered.txt", ".pipeline/current.json"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", f"tamper pipeline metadata {n}"], check=True)
""")

        impl_rename_forbid = stubs / "impl-rename-forbid.py"
        _write_exe(impl_rename_forbid, """#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
counter = Path(sys.argv[1])
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
Path("delivered.txt").write_text(f"ok {n}\\n")
if Path("secrets/key.txt").exists():
    subprocess.run(["git", "mv", "secrets/key.txt", "public.txt"], check=True)
subprocess.run(["git", "add", "delivered.txt"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", f"rename secret out {n}"], check=True)
""")

        impl_detached = stubs / "impl-detached.py"
        _write_exe(impl_detached, """#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
counter = Path(sys.argv[1])
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
subprocess.run(["git", "checkout", "--quiet", "--detach", "HEAD"], check=True)
Path("delivered.txt").write_text(f"detached {n}\\n")
subprocess.run(["git", "add", "delivered.txt"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", f"detached commit {n}"], check=True)
""")

        impl_bad_verify = stubs / "impl-bad-verify.py"
        _write_exe(impl_bad_verify, """#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
counter = Path(sys.argv[1])
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
Path("wrong.txt").write_text(f"try {n}\\n")
subprocess.run(["git", "add", "wrong.txt"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", f"wrong {n}"], check=True)
""")

        impl_hang = stubs / "impl-hang.py"
        _write_exe(impl_hang, """#!/usr/bin/env python3
import subprocess, sys, time
from pathlib import Path
pidfile = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pidfile.write_text(str(child.pid))
time.sleep(60)
""")

        # 1. review-plan ACCEPT WITH NITS proceeds to impl (exit 0) and pins approved_plan_blob
        w1 = _mk_fixture(root, "t1", tail_trans="plan→plan-review · completed", pin_approved_blob=False, git_env=git_env)
        q1 = root / "q1.txt"
        q1.write_text("VERDICT: ACCEPT WITH NITS | CAUSE: none\n")
        c1 = _mk_config(root, "t1", [str(impl_ok), str(root / "cnt1"), "{prompt}"], [str(rev_queue), str(q1)])
        r1 = _run("review-plan", "--repo", str(w1), "--config", str(c1), env=git_env)
        cur1 = json.loads((w1 / ".pipeline" / "current.json").read_text())
        j1 = (w1 / ".pipeline" / FEATURE / "journal.md").read_text()
        check("1-plan-accept-with-nits-pins-blob",
              r1.returncode == 0 and "plan-review→impl · completed" in j1 and len(cur1.get("approved_plan_blob", "")) == 40,
              f"rc={r1.returncode} cur={cur1} err={r1.stderr}")

        # 2. pause_after_plan -> approve-plan (stage=impl consistent with human->impl) -> phase2 end-to-end
        w2 = _mk_fixture(root, "t2", tail_trans="plan→plan-review · completed", pause_after_plan=True, pin_approved_blob=False, git_env=git_env)
        q2 = root / "q2.txt"
        q2.write_text("VERDICT: ACCEPT | CAUSE: none\nVERDICT: ACCEPT | CAUSE: none\n")
        c2 = _mk_config(root, "t2", [str(impl_ok), str(root / "cnt2"), "{prompt}"], [str(rev_queue), str(q2)])
        r2a = _run("review-plan", "--repo", str(w2), "--config", str(c2), env=git_env)
        r2b = _run("approve-plan", "--repo", str(w2), env=git_env)
        cur2_mid = json.loads((w2 / ".pipeline" / "current.json").read_text())
        j2_mid = (w2 / ".pipeline" / FEATURE / "journal.md").read_text()
        r2c = _run("phase2", "--repo", str(w2), "--config", str(c2), env=git_env)
        check("2-pause-approve-phase2-e2e",
              (r2a.returncode, r2b.returncode, r2c.returncode) == (3, 0, 0)
              and cur2_mid["stage"] == "impl" and "human→impl · completed" in j2_mid,
              f"rcs={(r2a.returncode, r2b.returncode, r2c.returncode)} mid={cur2_mid}")

        # 3. review-plan rejects duplicate call without new planner handoff, then 3rd rejection hits breaker
        w3 = _mk_fixture(root, "t3", tail_trans="plan→plan-review · completed", pin_approved_blob=False, git_env=git_env)
        q3 = root / "q3.txt"
        q3.write_text("VERDICT: REJECT | CAUSE: plan_fact\nVERDICT: REJECT | CAUSE: scope\nVERDICT: REJECT | CAUSE: plan_design\n")
        c3 = _mk_config(root, "t3", [str(impl_ok), str(root / "cnt3"), "{prompt}"], [str(rev_queue), str(q3)])
        r3a = _run("review-plan", "--repo", str(w3), "--config", str(c3), env=git_env)
        r3dup = _run("review-plan", "--repo", str(w3), "--config", str(c3), env=git_env)  # must refuse!
        _append_plan_handoff(w3, 3, git_env=git_env)
        r3b = _run("review-plan", "--repo", str(w3), "--config", str(c3), env=git_env)
        _append_plan_handoff(w3, 5, git_env=git_env)
        r3c = _run("review-plan", "--repo", str(w3), "--config", str(c3), env=git_env)
        cur3 = json.loads((w3 / ".pipeline" / "current.json").read_text())
        check("3-plan-dup-refused-and-3rd-breaker",
              (r3a.returncode, r3dup.returncode, r3b.returncode, r3c.returncode) == (10, 4, 10, 2)
              and cur3["stage"] == "blocked" and cur3["plan_rejects"] == 3,
              f"rcs={(r3a.returncode, r3dup.returncode, r3b.returncode, r3c.returncode)} cur={cur3}")

        # 4. model separation: planner==reviewer, impl==reviewer, and conflicting --planner-model
        w4 = _mk_fixture(root, "t4", tail_trans="plan→plan-review · completed", git_env=git_env)
        c4a = _mk_config(root, "t4a", [str(impl_ok), str(root / "cnt4"), "{prompt}"], [str(rev_queue), str(q1)], rev_model="frontier-a")
        r4a = _run("review-plan", "--repo", str(w4), "--config", str(c4a), env=git_env)
        c4b = _mk_config(root, "t4b", [str(impl_ok), str(root / "cnt4"), "{prompt}"], [str(rev_queue), str(q1)], impl_model="same-m", rev_model="same-m")
        r4b = _run("review-plan", "--repo", str(w4), "--config", str(c4b), env=git_env)
        r4c = _run("review-plan", "--repo", str(w4), "--config", str(c1), "--planner-model", "other-model", env=git_env)
        check("4-model-separation-strict",
              r4a.returncode == 4 and r4b.returncode == 4 and r4c.returncode == 4
              and "model-separation" in r4a.stderr and "model-separation" in r4b.stderr and "conflicting planner_model" in r4c.stderr,
              f"r4a={r4a.returncode} r4b={r4b.returncode} r4c={r4c.returncode}/{r4c.stderr}")

        # 5. phase2 happy path: 1 rework then ACCEPT — proves BOTH Step 3 verify AND Step 4 verify re-run (4x total), Reviewed-Head in report, zero merge
        vcnt5 = root / "vcnt5.log"
        w5 = _mk_fixture(root, "t5", verify_cmds=[f"test -f delivered.txt && echo x >> {vcnt5}"], git_env=git_env)
        q5 = root / "q5.txt"
        q5.write_text("VERDICT: REJECT | CAUSE: weak_test\nVERDICT: ACCEPT | CAUSE: none\n")
        c5 = _mk_config(root, "t5", [str(impl_ok), str(root / "cnt5"), "{prompt}"], [str(rev_queue), str(q5)])
        r5 = _run("phase2", "--repo", str(w5), "--config", str(c5), "--push", env=git_env)
        cur5 = json.loads((w5 / ".pipeline" / "current.json").read_text())
        j5 = (w5 / ".pipeline" / FEATURE / "journal.md").read_text()
        rep5 = (w5 / ".pipeline" / FEATURE / "impl-report.md").read_text()
        cr5 = (w5 / ".pipeline" / FEATURE / "reviews" / "code-review-02.md").read_text()
        vruns5 = len(vcnt5.read_text().splitlines()) if vcnt5.exists() else 0
        merged_on_trunk = (w5 / "delivered.txt").exists()
        check("5-phase2-rework-accept-step4-verify-and-reviewed-head",
              r5.returncode == 0 and cur5["review_rejects"] == 1 and cur5["stage"] == "review"
              and vruns5 == 4 and "Reviewed-Head: feat/demo-feat@" in cr5
              and "Headless Step 4 ACCEPT" in j5 and not merged_on_trunk and "impl round 2" in rep5,
              f"rc={r5.returncode} vruns={vruns5} cur={cur5} cr5={cr5[:120]!r} err={r5.stderr}")

        # 6. phase2 3rd rejection hits circuit breaker (exit 2)
        w6 = _mk_fixture(root, "t6", git_env=git_env)
        q6 = root / "q6.txt"
        q6.write_text("VERDICT: REJECT | CAUSE: impl_bug\nVERDICT: REJECT | CAUSE: weak_test\nVERDICT: REJECT | CAUSE: scope\n")
        c6 = _mk_config(root, "t6", [str(impl_ok), str(root / "cnt6"), "{prompt}"], [str(rev_queue), str(q6)])
        r6 = _run("phase2", "--repo", str(w6), "--config", str(c6), env=git_env)
        cur6 = json.loads((w6 / ".pipeline" / "current.json").read_text())
        check("6-phase2-3rd-reject-breaker", r6.returncode == 2 and cur6["review_rejects"] == 3 and cur6["stage"] == "blocked",
              f"rc={r6.returncode} cur={cur6} err={r6.stderr}")

        # 7. phase2 short-circuits immediately on plan_fact (exit 3, review_rejects stays 0)
        w7 = _mk_fixture(root, "t7", git_env=git_env)
        q7 = root / "q7.txt"
        q7.write_text("VERDICT: REJECT | CAUSE: plan_fact\n")
        c7 = _mk_config(root, "t7", [str(impl_ok), str(root / "cnt7"), "{prompt}"], [str(rev_queue), str(q7)])
        r7 = _run("phase2", "--repo", str(w7), "--config", str(c7), env=git_env)
        cur7 = json.loads((w7 / ".pipeline" / "current.json").read_text())
        check("7-phase2-plan-fact-short-circuit", r7.returncode == 3 and cur7["review_rejects"] == 0 and cur7["stage"] == "blocked",
              f"rc={r7.returncode} cur={cur7} err={r7.stderr}")

        # 8. strict physical line-1 verdict rejects leading blank line & ACCEPT WITH NITS in Step 4
        w8 = _mk_fixture(root, "t8", git_env=git_env)
        q8 = root / "q8.txt"
        q8.write_text("\nVERDICT: ACCEPT | CAUSE: none\n")
        c8 = _mk_config(root, "t8", [str(impl_ok), str(root / "cnt8"), "{prompt}"], [str(rev_queue), str(q8)])
        r8 = _run("phase2", "--repo", str(w8), "--config", str(c8), env=git_env)
        check("8-strict-line1-verdict", r8.returncode == 4 and "malformed physical line-1" in r8.stderr,
              f"rc={r8.returncode} err={r8.stderr}")

        # 9. built-in .pipeline/ forbid catches metadata tampering even when forbid-paths: []
        w9 = _mk_fixture(root, "t9", forbid_paths=[], git_env=git_env)
        q9 = root / "q9.txt"
        q9.write_text("VERDICT: ACCEPT | CAUSE: none\n")
        c9 = _mk_config(root, "t9", [str(impl_pipeline_edit), str(root / "cnt9"), "{prompt}"], [str(rev_queue), str(q9)])
        r9 = _run("phase2", "--repo", str(w9), "--config", str(c9), env=git_env)
        check("9-builtin-pipeline-forbid-catches-tamper", r9.returncode == 2 and "VERDICT: ACCEPT" in q9.read_text(),
              f"rc={r9.returncode} out={r9.stdout} q9={q9.read_text()!r}")

        # 10. rename out of forbidden dir caught via NUL-delimited -M diff before Step 4
        w10 = _mk_fixture(root, "t10", git_env=git_env)
        q10 = root / "q10.txt"
        q10.write_text("VERDICT: ACCEPT | CAUSE: none\n")
        c10 = _mk_config(root, "t10", [str(impl_rename_forbid), str(root / "cnt10"), "{prompt}"], [str(rev_queue), str(q10)])
        r10 = _run("phase2", "--repo", str(w10), "--config", str(c10), env=git_env)
        check("10-forbid-rename-source-caught", r10.returncode == 2 and "secrets/key.txt" in r10.stdout and "VERDICT: ACCEPT" in q10.read_text(),
              f"rc={r10.returncode} out={r10.stdout}")

        # 11. verify pre-gate failure loops impl and halts at 2 without calling Reviewer
        w11 = _mk_fixture(root, "t11", git_env=git_env)
        q11 = root / "q11.txt"
        q11.write_text("VERDICT: ACCEPT | CAUSE: none\n")
        c11 = _mk_config(root, "t11", [str(impl_bad_verify), str(root / "cnt11"), "{prompt}"], [str(rev_queue), str(q11)])
        r11 = _run("phase2", "--repo", str(w11), "--config", str(c11), env=git_env)
        check("11-verify-pregate-blocks-without-reviewer", r11.returncode == 2 and "VERDICT: ACCEPT" in q11.read_text(),
              f"rc={r11.returncode} out={r11.stdout}")

        # 12. detached HEAD by implementer rejected before Step 4
        w12 = _mk_fixture(root, "t12", git_env=git_env)
        q12 = root / "q12.txt"
        q12.write_text("VERDICT: ACCEPT | CAUSE: none\n")
        c12 = _mk_config(root, "t12", [str(impl_detached), str(root / "cnt12"), "{prompt}"], [str(rev_queue), str(q12)])
        r12 = _run("phase2", "--repo", str(w12), "--config", str(c12), env=git_env)
        check("12-detached-head-rejected", r12.returncode == 2 and "VERDICT: ACCEPT" in q12.read_text(),
              f"rc={r12.returncode} out={r12.stdout}")

        # 13. verify mutating working tree caught before Step 4
        w13 = _mk_fixture(root, "t13", verify_cmds=["test -f delivered.txt && echo dirty > stray.tmp"], git_env=git_env)
        q13 = root / "q13.txt"
        q13.write_text("VERDICT: ACCEPT | CAUSE: none\n")
        c13 = _mk_config(root, "t13", [str(impl_ok), str(root / "cnt13"), "{prompt}"], [str(rev_queue), str(q13)])
        r13 = _run("phase2", "--repo", str(w13), "--config", str(c13), env=git_env)
        check("13-post-verify-dirty-tree-caught", r13.returncode == 2 and "VERDICT: ACCEPT" in q13.read_text(),
              f"rc={r13.returncode} out={r13.stdout}")

        # 14. implementer timeout STOPs immediately (exit 4, hard rule 2) AND kills descendant process group
        w14 = _mk_fixture(root, "t14", git_env=git_env)
        pidfile = root / "child.pid"
        c14 = _mk_config(root, "t14", [str(impl_hang), str(pidfile), "{prompt}"], [str(rev_queue), str(q1)], impl_timeout=2.5)
        r14 = _run("phase2", "--repo", str(w14), "--config", str(c14), env=git_env)
        spawned = pidfile.exists()
        child_dead = False
        if spawned:
            cpid = int(pidfile.read_text().strip())
            time.sleep(0.1)
            try:
                os.kill(cpid, 0)
            except ProcessLookupError:
                child_dead = True
        check("14-implementer-timeout-stops-exit4-and-kills-pg", r14.returncode == 4 and spawned and child_dead,
              f"rc={r14.returncode} spawned={spawned} child_dead={child_dead} err={r14.stderr}")

        # 15. plan.md modified after approval (blob mismatch) refused before running
        w15 = _mk_fixture(root, "t15", git_env=git_env)
        p15 = w15 / ".pipeline" / FEATURE / "plan.md"
        p15.write_text(p15.read_text() + "\n# sneaky edit\n", encoding="utf-8")
        _git(w15, "add", str(p15), env=git_env)
        _git(w15, "commit", "--quiet", "-m", "sneaky plan edit", env=git_env)
        r15 = _run("phase2", "--repo", str(w15), "--config", str(c5), env=git_env)
        check("15-plan-blob-mismatch-refused", r15.returncode == 4 and "blob" in r15.stderr,
              f"rc={r15.returncode} err={r15.stderr}")

        # 16. crash recovery from impl->review · completed blocks self-review AND missing impl-report,
        # and jumps straight to Step 4 without re-running Step 3 when both records match and reviewer is distinct
        w16 = _mk_fixture(root, "t16", git_env=git_env)
        q16 = root / "q16.txt"
        q16.write_text("bad-non-verdict\nVERDICT: ACCEPT | CAUSE: none\n")
        cnt16 = root / "cnt16"
        c16 = _mk_config(root, "t16", [str(impl_ok), str(cnt16), "{prompt}"], [str(rev_queue), str(q16)])
        c16_self = _mk_config(root, "t16-self", [str(impl_ok), str(cnt16), "{prompt}"], [str(rev_queue), str(q16)], impl_model="other-d", rev_model="cheap-c")
        r16a = _run("phase2", "--repo", str(w16), "--config", str(c16), env=git_env)  # Step 3 succeeds, Step 4 fails closed (exit 4)
        r16_self = _run("phase2", "--repo", str(w16), "--config", str(c16_self), env=git_env)  # must refuse self-review!
        # Also verify missing impl-report.md fails closed on resume (restore afterward)
        rep16_path = w16 / ".pipeline" / FEATURE / "impl-report.md"
        rep16_saved = rep16_path.read_text()
        rep16_path.unlink()
        _git(w16, "add", "-u", env=git_env)
        _git(w16, "commit", "--quiet", "-m", "drop report temporarily", env=git_env)
        r16_norep = _run("phase2", "--repo", str(w16), "--config", str(c16), env=git_env)
        rep16_path.write_text(rep16_saved)
        _git(w16, "add", str(rep16_path), env=git_env)
        _git(w16, "commit", "--quiet", "-m", "restore report", env=git_env)
        r16b = _run("phase2", "--repo", str(w16), "--config", str(c16), env=git_env)  # Resumes at Step 4 directly!
        check("16-resume-step4-guards-and-recovers",
              r16a.returncode == 4 and r16_self.returncode == 4 and "model-separation" in r16_self.stderr
              and r16_norep.returncode == 4 and "missing" in r16_norep.stderr
              and r16b.returncode == 0 and cnt16.read_text().strip() == "1",
              f"r16a={r16a.returncode} r16_self={r16_self.returncode} r16_norep={r16_norep.returncode}/{r16_norep.stderr} r16b={r16b.returncode}")

        # 17. CLI usage error and malformed runners.json both exit 64 (never 2 or 4)
        bad_cfg = root / "bad.json"
        bad_cfg.write_text("{not-json", encoding="utf-8")
        r17a = _run("phase2", "--unknown-flag")
        r17b = _run("phase2", "--repo", str(w1), "--config", str(bad_cfg))
        check("17-usage-and-bad-config-exit-64", r17a.returncode == 64 and r17b.returncode == 64,
              f"r17a={r17a.returncode} r17b={r17b.returncode}")

        # 18. omitted ## Assumptions deterministically REJECTS (CAUSE: scope) even if reviewer would ACCEPT
        impl_no_assump = stubs / "impl-no-assump.py"
        _write_exe(impl_no_assump, """#!/usr/bin/env python3
import subprocess, sys
from pathlib import Path
counter = Path(sys.argv[1])
n = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(n))
Path("delivered.txt").write_text(f"no assumptions {n}\\n")
subprocess.run(["git", "add", "delivered.txt"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", f"impl no assump {n}"], check=True)
print("Done implementing.")
""")
        w18 = _mk_fixture(root, "t18", git_env=git_env)
        q18 = root / "q18.txt"
        q18.write_text("VERDICT: ACCEPT | CAUSE: none\n")
        c18 = _mk_config(root, "t18", [str(impl_no_assump), str(root / "cnt18"), "{prompt}"], [str(rev_queue), str(q18)])
        r18 = _run("phase2", "--repo", str(w18), "--config", str(c18), env=git_env)
        cur18 = json.loads((w18 / ".pipeline" / "current.json").read_text())
        cr18 = (w18 / ".pipeline" / FEATURE / "reviews" / "code-review-01.md").read_text()
        check("18-omitted-assumptions-deterministic-reject",
              r18.returncode == 2 and cur18["review_rejects"] == 3
              and "VERDICT: REJECT | CAUSE: scope" in cr18 and "VERDICT: ACCEPT" in q18.read_text(),
              f"rc={r18.returncode} cur18={cur18} cr18={cr18[:100]!r} q18={q18.read_text()!r}")

        # 19. Step 4 independent verify re-run failure triggers deterministic REJECT | CAUSE: impl_bug and loops back to Step 3
        vflaky = stubs / "verify-flaky.py"
        _write_exe(vflaky, """#!/usr/bin/env python3
import sys
from pathlib import Path
c = Path(sys.argv[1])
n = int(c.read_text()) + 1 if c.exists() else 1
c.write_text(str(n))
# Pass on call 1 (Step 3 round 1), fail on call 2 (Step 4 round 1), pass on calls 3 & 4 (round 2)
sys.exit(1 if n == 2 else 0)
""")
        w19 = _mk_fixture(root, "t19", verify_cmds=[f"test -f delivered.txt && {vflaky} {root / 'v19.cnt'}"], git_env=git_env)
        q19 = root / "q19.txt"
        q19.write_text("VERDICT: ACCEPT | CAUSE: none\n")
        c19 = _mk_config(root, "t19", [str(impl_ok), str(root / "cnt19"), "{prompt}"], [str(rev_queue), str(q19)])
        r19 = _run("phase2", "--repo", str(w19), "--config", str(c19), env=git_env)
        cur19 = json.loads((w19 / ".pipeline" / "current.json").read_text())
        cr19_1 = (w19 / ".pipeline" / FEATURE / "reviews" / "code-review-01.md").read_text()
        impl_runs19 = (root / "cnt19").read_text().strip() if (root / "cnt19").exists() else "0"
        check("19-step4-verify-fail-routes-impl-bug-and-recovers",
              r19.returncode == 0 and cur19["review_rejects"] == 1 and impl_runs19 == "2"
              and "VERDICT: REJECT | CAUSE: impl_bug" in cr19_1,
              f"rc={r19.returncode} impl_runs={impl_runs19} cur19={cur19} cr19_1={cr19_1[:100]!r} err={r19.stderr}")

        # 20. Reviewer mutating worktree during ACCEPT fails closed (exit 4) with clean trunk and NO report left behind
        rev_mutate = stubs / "rev-mutate.py"
        _write_exe(rev_mutate, """#!/usr/bin/env python3
import sys
from pathlib import Path
_ = sys.stdin.read()
Path("stray_reviewer_edit.tmp").write_text("oops\\n")
print("VERDICT: ACCEPT | CAUSE: none")
""")
        w20 = _mk_fixture(root, "t20", git_env=git_env)
        c20 = _mk_config(root, "t20", [str(impl_ok), str(root / "cnt20"), "{prompt}"], [str(rev_mutate)])
        r20 = _run("phase2", "--repo", str(w20), "--config", str(c20), env=git_env)
        trunk_dirty20 = _git(w20, "status", "--porcelain", env=git_env)
        rev20_exists = (w20 / ".pipeline" / FEATURE / "reviews" / "code-review-01.md").exists()
        check("20-final-check-fail-leaves-trunk-clean-no-accept-file",
              r20.returncode == 4 and not trunk_dirty20 and not rev20_exists,
              f"rc={r20.returncode} dirty={trunk_dirty20!r} rev_exists={rev20_exists} err={r20.stderr}")

        # 21. Git `ident` checkout filter expands `$Id$` in working tree, but Step 2 + Phase II both consume immutable committed blob!
        w21 = _mk_fixture(root, "t21", tail_trans="plan→plan-review · completed", pin_approved_blob=False, git_env=git_env)
        (w21 / ".gitattributes").write_text(f".pipeline/{FEATURE}/plan.md ident\n", encoding="utf-8")
        # In committed blob, marker is literal "$Id$"; checkout expands it to "$Id: <sha> $".
        # verify asserts the literal unexpanded marker passed from the immutable blob!
        ident_plan = (
            "---\n"
            f"feature: {FEATURE}\n"
            "planner_model: frontier-a\n"
            "verify:\n"
            "  - \"test -f delivered.txt && test '$Id$' = '$''Id''$'\"\n"
            "forbid-paths: []\n"
            "---\n"
            "## Baseline Evidence\n- README.md:1\n"
            "## Specifications\n1. ident test\n"
            "## Acceptance Matrix (T1-T10)\n- T1: delivered.txt\n"
        )
        p21 = w21 / ".pipeline" / FEATURE / "plan.md"
        p21.write_text(ident_plan, encoding="utf-8")
        _git(w21, "add", ".gitattributes", str(p21), env=git_env)
        _git(w21, "commit", "--quiet", "-m", "add ident filter on plan.md", env=git_env)
        # Re-checkout plan.md so Git expands `$Id$` -> `$Id: <blob_sha> $` in the working tree!
        p21.unlink()
        _git(w21, "checkout", "--", f".pipeline/{FEATURE}/plan.md", env=git_env)
        checkout_expanded = "$Id: " in p21.read_text(encoding="utf-8")
        _git(w21, "push", "--quiet", "origin", "main", env=git_env)
        q21 = root / "q21.txt"
        q21.write_text("VERDICT: ACCEPT | CAUSE: none\nVERDICT: ACCEPT | CAUSE: none\n")
        c21 = _mk_config(root, "t21", [str(impl_ok), str(root / "cnt21"), "{prompt}"], [str(rev_queue), str(q21)])
        r21a = _run("review-plan", "--repo", str(w21), "--config", str(c21), env=git_env)
        r21b = _run("phase2", "--repo", str(w21), "--config", str(c21), env=git_env)
        check("21-ident-checkout-filter-uses-committed-blob",
              checkout_expanded and r21a.returncode == 0 and r21b.returncode == 0,
              f"expanded={checkout_expanded} r21a={r21a.returncode}/{r21a.stderr} r21b={r21b.returncode}/{r21b.stderr}")

        # 22. Valid non-UTF-8 (Latin-1) diff does NOT crash Step 4 and surfaces explicit non-UTF-8 notice to reviewer
        impl_latin1 = stubs / "impl-latin1.py"
        _write_exe(impl_latin1, """#!/usr/bin/env python3
import subprocess
from pathlib import Path
Path("delivered.txt").write_bytes(b"caf\\xe9\\n")
subprocess.run(["git", "add", "delivered.txt"], check=True)
subprocess.run(["git", "commit", "--quiet", "-m", "add latin1 file"], check=True)
print("## Assumptions\\n- none")
""")
        rev_capture = stubs / "rev-capture.py"
        _write_exe(rev_capture, """#!/usr/bin/env python3
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(sys.stdin.read(), encoding="utf-8")
print("VERDICT: ACCEPT | CAUSE: none")
print("Checked non-UTF-8 diff.")
""")
        w22 = _mk_fixture(root, "t22", git_env=git_env)
        cap22 = root / "rev22-prompt.txt"
        c22 = _mk_config(root, "t22", [str(impl_latin1), "{prompt}"], [str(rev_capture), str(cap22)])
        r22 = _run("phase2", "--repo", str(w22), "--config", str(c22), env=git_env)
        prompt22 = cap22.read_text(encoding="utf-8") if cap22.exists() else ""
        check("22-non-utf8-latin1-diff-handled-cleanly",
              r22.returncode == 0 and "non-UTF-8 bytes" in prompt22 and "caf\ufffd" in prompt22,
              f"rc={r22.returncode} err={r22.stderr} has_note={'non-UTF-8 bytes' in prompt22}")

        # 23. Committed CRLF plan.md blob parses cleanly in Step 2 (review-plan) and Phase II while pinning raw blob SHA
        w23 = _mk_fixture(root, "t23", tail_trans="plan→plan-review · completed", pin_approved_blob=False, git_env=git_env)
        p23 = w23 / ".pipeline" / FEATURE / "plan.md"
        crlf_plan = (
            "---\r\n"
            f"feature: {FEATURE}\r\n"
            "planner_model: frontier-a\r\n"
            "verify:\r\n"
            "  - \"test -f delivered.txt\"\r\n"
            "forbid-paths: []\r\n"
            "---\r\n"
            "## Baseline Evidence\r\n- README.md:1\r\n"
            "## Specifications\r\n1. CRLF plan test\r\n"
            "## Acceptance Matrix (T1-T10)\r\n- T1: delivered.txt\r\n"
        )
        p23.write_bytes(crlf_plan.encode("utf-8"))
        _git(w23, "-c", "core.autocrlf=false", "add", str(p23), env=git_env)
        _git(w23, "-c", "core.autocrlf=false", "commit", "--quiet", "-m", "commit crlf plan", env=git_env)
        _git(w23, "push", "--quiet", "origin", "main", env=git_env)
        crlf_sha23 = _git(w23, "rev-parse", f"HEAD:.pipeline/{FEATURE}/plan.md", env=git_env)
        raw_blob23 = subprocess.check_output(["git", "-C", str(w23), "cat-file", "blob", crlf_sha23], env=git_env)
        q23 = root / "q23.txt"
        q23.write_text("VERDICT: ACCEPT | CAUSE: none\nVERDICT: ACCEPT | CAUSE: none\n")
        c23 = _mk_config(root, "t23", [str(impl_ok), str(root / "cnt23"), "{prompt}"], [str(rev_queue), str(q23)])
        r23a = _run("review-plan", "--repo", str(w23), "--config", str(c23), env=git_env)
        cur23 = json.loads((w23 / ".pipeline" / "current.json").read_text())
        r23b = _run("phase2", "--repo", str(w23), "--config", str(c23), env=git_env)
        has_crlf23 = b"\r\n" in raw_blob23
        check("23-crlf-committed-plan-blob-review-and-phase2",
              has_crlf23 and r23a.returncode == 0
              and cur23.get("approved_plan_blob") == crlf_sha23 and r23b.returncode == 0,
              f"has_crlf={has_crlf23} r23a={r23a.returncode}/{r23a.stderr} "
              f"pin_match={cur23.get('approved_plan_blob') == crlf_sha23} r23b={r23b.returncode}/{r23b.stderr}")

        # --- Full 4-Step Orchestrator & Background Execution Tests (24-31) ---
        planner_ok = stubs / "planner-ok.py"
        _write_exe(planner_ok, """#!/usr/bin/env python3
import sys
from pathlib import Path
cnt_file = Path(sys.argv[1])
feat = sys.argv[2]
n = int(cnt_file.read_text()) + 1 if cnt_file.exists() else 1
cnt_file.write_text(str(n))
p = Path(".pipeline") / feat / "plan.md"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(
    f"---\\nfeature: {feat}\\nplanner_model: frontier-a\\nverify:\\n  - \\"test -f delivered.txt\\"\\nforbid-paths: []\\n---\\n"
    f"## Baseline Evidence\\n- README.md:1\\n## Specifications\\n1. revision {n}\\n## Acceptance Matrix (T1-T10)\\n- T1: delivered.txt\\n",
    encoding="utf-8",
)
print(f"authored plan revision {n}")
""")

        # 24. Full 4-step E2E from fresh repo (no .pipeline/): Step 1->Step 2 REJECT->Step 1->Step 2 ACCEPT->Step 3->Step 4 REJECT->Step 3->Step 4 ACCEPT (0 planner tokens in Phase II!)
        w24 = _mk_fresh_repo(root, "t24", git_env=git_env)
        q24 = root / "q24.txt"
        q24.write_text(
            "VERDICT: REJECT | CAUSE: plan_design\n"
            "VERDICT: ACCEPT | CAUSE: none\n"
            "VERDICT: REJECT | CAUSE: impl_bug\n"
            "VERDICT: ACCEPT | CAUSE: none\n"
        )
        c24 = _mk_config(
            root, "t24",
            [str(impl_ok), str(root / "impl24"), "{prompt}"],
            [str(rev_queue), str(q24)],
            planner_cmd=[str(planner_ok), str(root / "plan24"), FEATURE, "{prompt}"],
        )
        r24 = _run("run", "--repo", str(w24), "--feature", FEATURE, "--idea", "Build demo feature", "--config", str(c24), env=git_env)
        cur24 = json.loads((w24 / ".pipeline" / "current.json").read_text())
        plan_runs24 = (root / "plan24").read_text().strip() if (root / "plan24").exists() else "0"
        impl_runs24 = (root / "impl24").read_text().strip() if (root / "impl24").exists() else "0"
        st24 = _run("status", "--repo", str(w24), "--feature", FEATURE, "--json", env=git_env)
        st24_obj = json.loads(st24.stdout) if st24.returncode == 0 else {}
        check("24-full-4step-e2e-auto-loops-with-zero-planner-tokens-in-phase2",
              r24.returncode == 0 and plan_runs24 == "2" and impl_runs24 == "2"
              and cur24.get("plan_rejects") == 1 and cur24.get("review_rejects") == 1
              and st24_obj.get("status") == "accept" and st24_obj.get("exit_code") == 0,
              f"rc={r24.returncode} plan_runs={plan_runs24} impl_runs={impl_runs24} cur24={cur24} st={st24_obj} err={r24.stderr}")

        # 25. Full 4-step --background detaches cleanly, status --json reaches accept, logs show PHASE2 ACCEPT
        w25 = _mk_fresh_repo(root, "t25", git_env=git_env)
        q25 = root / "q25.txt"
        q25.write_text("VERDICT: ACCEPT | CAUSE: none\nVERDICT: ACCEPT | CAUSE: none\n")
        c25 = _mk_config(
            root, "t25",
            [str(impl_ok), str(root / "impl25"), "{prompt}"],
            [str(rev_queue), str(q25)],
            planner_cmd=[str(planner_ok), str(root / "plan25"), FEATURE, "{prompt}"],
        )
        r25 = _run("run", "--repo", str(w25), "--feature", FEATURE, "--idea", "Background demo", "--config", str(c25), "--background", env=git_env)
        st25_obj = {}
        for _ in range(100):
            st25 = _run("status", "--repo", str(w25), "--feature", FEATURE, "--json", env=git_env)
            if st25.returncode == 0:
                st25_obj = json.loads(st25.stdout)
                if not st25_obj.get("alive") and st25_obj.get("status") not in ("starting", "running"):
                    break
            time.sleep(0.1)
        lg25 = _run("logs", "--repo", str(w25), "--feature", FEATURE, "--tail", "50", env=git_env)
        check("25-full-4step-background-status-logs",
              r25.returncode == 0 and "RUN_STARTED" in r25.stdout
              and st25_obj.get("status") == "accept" and st25_obj.get("exit_code") == 0
              and lg25.returncode == 0 and "PHASE2 ACCEPT" in lg25.stdout,
              f"r25={r25.returncode}/{r25.stdout}/{r25.stderr} st25={st25_obj} lg={lg25.stdout[-200:]!r}")

        # 26. Planner NEEDS_HUMAN pauses (exit 3), then `resume --answer` completes all 4 steps
        planner_ask = stubs / "planner-ask.py"
        _write_exe(planner_ask, """#!/usr/bin/env python3
import sys
from pathlib import Path
feat = sys.argv[1]
prompt = sys.argv[2]
if "--- HUMAN ANSWERS ---" not in prompt:
    print("NEEDS_HUMAN: Need output format decision")
    print("1. Should output be JSON or plain text?")
    sys.exit(0)
p = Path(".pipeline") / feat / "plan.md"
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(
    f"---\\nfeature: {feat}\\nplanner_model: frontier-a\\nverify:\\n  - \\"test -f delivered.txt\\"\\nforbid-paths: []\\n---\\n"
    f"## Baseline Evidence\\n- README.md:1\\n## Specifications\\n1. Use JSON per human answer\\n## Acceptance Matrix (T1-T10)\\n- T1: delivered.txt\\n",
    encoding="utf-8",
)
print("authored plan after human answer")
""")
        w26 = _mk_fresh_repo(root, "t26", git_env=git_env)
        q26 = root / "q26.txt"
        q26.write_text("VERDICT: ACCEPT | CAUSE: none\nVERDICT: ACCEPT | CAUSE: none\n")
        c26 = _mk_config(
            root, "t26",
            [str(impl_ok), str(root / "impl26"), "{prompt}"],
            [str(rev_queue), str(q26)],
            planner_cmd=[str(planner_ask), FEATURE, "{prompt}"],
        )
        r26a = _run("run", "--repo", str(w26), "--feature", FEATURE, "--idea", "Ambiguous idea", "--config", str(c26), env=git_env)
        qfile26 = w26 / ".pipeline" / FEATURE / "human-questions.md"
        r26b = _run("resume", "--repo", str(w26), "--feature", FEATURE, "--answer", "1. JSON", "--config", str(c26), env=git_env)
        afile26 = w26 / ".pipeline" / FEATURE / "human-answers.md"
        check("26-needs-human-pause-and-resume-with-answer",
              r26a.returncode == 3 and "RUN NEEDS_HUMAN" in r26a.stdout and qfile26.is_file()
              and r26b.returncode == 0 and afile26.is_file() and "1. JSON" in afile26.read_text(),
              f"r26a={r26a.returncode}/{r26a.stdout}/{r26a.stderr} r26b={r26b.returncode}/{r26b.stderr}")

        # 27. Step 2 3rd rejection breaker stops full `run` (exit 2) with 0 impl runs, and `resume` refuses to bypass breaker
        w27 = _mk_fresh_repo(root, "t27", git_env=git_env)
        q27 = root / "q27.txt"
        q27.write_text(
            "VERDICT: REJECT | CAUSE: plan_design\n"
            "VERDICT: REJECT | CAUSE: plan_design\n"
            "VERDICT: REJECT | CAUSE: plan_design\n"
        )
        c27 = _mk_config(
            root, "t27",
            [str(impl_ok), str(root / "impl27"), "{prompt}"],
            [str(rev_queue), str(q27)],
            planner_cmd=[str(planner_ok), str(root / "plan27"), FEATURE, "{prompt}"],
        )
        r27 = _run("run", "--repo", str(w27), "--feature", FEATURE, "--idea", "Bad plan loop", "--config", str(c27), env=git_env)
        r27_res = _run("resume", "--repo", str(w27), "--feature", FEATURE, "--config", str(c27), env=git_env)
        plan_runs27 = (root / "plan27").read_text().strip() if (root / "plan27").exists() else "0"
        impl_runs27 = (root / "impl27").read_text().strip() if (root / "impl27").exists() else "0"
        check("27-step1-plan-3rd-reject-breaker-stops-and-blocks-resume",
              r27.returncode == 2 and plan_runs27 == "3" and impl_runs27 == "0" and r27_res.returncode == 2,
              f"r27={r27.returncode} plan_runs={plan_runs27} impl_runs={impl_runs27} r27_res={r27_res.returncode}/{r27_res.stderr}")

        # 28. Planner write-set violation (touching README.md) fails closed (exit 4) without committing
        planner_rogue = stubs / "planner-rogue.py"
        _write_exe(planner_rogue, """#!/usr/bin/env python3
from pathlib import Path
Path("README.md").write_text("# hacked by planner\\n", encoding="utf-8")
print("edited readme")
""")
        w28 = _mk_fresh_repo(root, "t28", git_env=git_env)
        head28_before = _git(w28, "rev-parse", "HEAD", env=git_env)
        c28 = _mk_config(
            root, "t28",
            [str(impl_ok), str(root / "impl28"), "{prompt}"],
            [str(rev_queue), str(q25)],
            planner_cmd=[str(planner_rogue), "{prompt}"],
        )
        r28 = _run("run", "--repo", str(w28), "--feature", FEATURE, "--idea", "Rogue planner", "--config", str(c28), env=git_env)
        head28_after = _git(w28, "rev-parse", "HEAD", env=git_env)
        st28 = json.loads(_run("status", "--repo", str(w28), "--feature", FEATURE, "--json", env=git_env).stdout)
        check("28-step1-write-set-violation-fails-closed",
              r28.returncode == 4 and head28_before == head28_after
              and "write-set" in r28.stderr and st28.get("status") == "error" and "README.md" in st28.get("reason", ""),
              f"rc={r28.returncode} err={r28.stderr} st28={st28}")

        # 29. `stop` terminates a live background worker cleanly
        planner_slow = stubs / "planner-slow.py"
        _write_exe(planner_slow, """#!/usr/bin/env python3
import time
time.sleep(30)
""")
        w29 = _mk_fresh_repo(root, "t29", git_env=git_env)
        c29 = _mk_config(
            root, "t29",
            [str(impl_ok), str(root / "impl29"), "{prompt}"],
            [str(rev_queue), str(q25)],
            planner_cmd=[str(planner_slow), "{prompt}"],
        )
        r29_start = _run("run", "--repo", str(w29), "--feature", FEATURE, "--idea", "Slow run", "--config", str(c29), "--background", env=git_env)
        time.sleep(0.3)
        st29_live = json.loads(_run("status", "--repo", str(w29), "--feature", FEATURE, "--json", env=git_env).stdout)
        r29_stop = _run("stop", "--repo", str(w29), "--feature", FEATURE, env=git_env)
        st29_after = json.loads(_run("status", "--repo", str(w29), "--feature", FEATURE, "--json", env=git_env).stdout)
        check("29-background-stop-terminates-running-worker",
              r29_start.returncode == 0 and st29_live.get("alive") is True
              and r29_stop.returncode == 0 and "was_alive=true" in r29_stop.stdout
              and st29_after.get("alive") is False and st29_after.get("status") == "stopped_by_user",
              f"start={r29_start.returncode} live={st29_live} stop={r29_stop.returncode}/{r29_stop.stdout} after={st29_after}")

        # 30. `init-config` creates valid distinct-model runners.json and refuses clobber without --force
        cfg30 = root / "generated-runners.json"
        r30a = _run("init-config", "--output", str(cfg30), env=git_env)
        r30b = _run("init-config", "--output", str(cfg30), env=git_env)
        obj30 = json.loads(cfg30.read_text()) if cfg30.is_file() else {}
        models30 = {k: obj30.get(k, {}).get("model_id", "") for k in ("planner", "implementer", "reviewer")}
        check("30-init-config-generates-valid-runners-json",
              r30a.returncode == 0 and "INIT_CONFIG ok" in r30a.stdout and r30b.returncode == 64
              and all(models30.values())
              and models30["reviewer"] != models30["planner"]
              and models30["reviewer"] != models30["implementer"],
              f"r30a={r30a.returncode}/{r30a.stdout}/{r30a.stderr} r30b={r30b.returncode} models={models30}")

        # 31. Starting a different feature while another feature is active fails synchronously BEFORE --background spawn
        w31 = _mk_fixture(root, "t31", stage="plan", tail_trans="plan→plan-review · completed", git_env=git_env)
        c31 = _mk_config(
            root, "t31",
            [str(impl_ok), str(root / "impl31"), "{prompt}"],
            [str(rev_queue), str(q25)],
            planner_cmd=[str(planner_ok), str(root / "plan31"), "other-feat", "{prompt}"],
        )
        r31 = _run("run", "--repo", str(w31), "--feature", "other-feat", "--idea", "Conflict", "--config", str(c31), "--background", env=git_env)
        check("31-background-blocks-active-feature-conflict-before-spawn",
              r31.returncode == 4 and "currently active" in r31.stderr,
              f"rc={r31.returncode} out={r31.stdout!r} err={r31.stderr!r}")

    print(f"---\n{passed}/{passed + failed} cases passed ({failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
