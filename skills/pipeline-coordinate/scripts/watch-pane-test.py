#!/usr/bin/env python3
"""Self-test for watch-pane.py. Stdlib only.

Runnable as `python3 scripts/watch-pane-test.py` from any cwd. Total runtime < 60s.
Exit 0 iff every scenario matches its expected (frozen) exit code. Prints one PASS/FAIL
line per scenario.

Mechanism: a temp dir holds a fake executable `herdr` (/bin/sh script) prepended to PATH
for the child. The fake reads a per-scenario directory of numbered response files plus a
counter file: call N emits file min(N, max) and increments the counter. A response file
may hold a JSON body, or one of the tokens SLEEP / GARBAGE / RC1.

When HERDR_FAKE_SPAWN_CHILD is set, each fake-herdr call ALSO spawns a same-process-group
descendant (a `python -c` one-liner) with its stdout/stderr redirected to /dev/null (so it
does not hold the leader's pipe open). This exercises the frozen process-group kill: once
the leader has exited, the descendant can only be reaped by watch-pane's unconditional
`os.killpg(pgid, SIGKILL)` (finding #1).

Harness lifecycle contract (round 3):
  1. Spawn = record. Each descendant writes its OWN pid (once alive) into a per-scenario
     registry; the fake herdr records the spawn pid (`$!`) into a separate spawn list.
  2. Record = verify. Before the dead-descendant assertion, the readiness list must EQUAL
     the spawn list AND the expected per-scenario child count. Short/missing/mismatched =>
     FAIL loudly (a child replaced by immediate exit must NOT pass — the r3 repro).
  3. Signal = own. Before ANY kill, revalidate that the pid's `ps -o command=` carries this
     run's unique token (its temp-dir path); never signal a pid that is dead or unverifiable
     (a reused pid belonging to an unrelated process is never signaled).
  4. Cleanup = guaranteed. Every registry is tracked on the Runner and cleaned in a `finally`
     that runs BEFORE temp-dir deletion on every path — normal, scenario exception, and
     SIGINT mid-run (no leaked descendants; the registry is not deleted while children live).
"""

import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH_PANE = os.path.join(HERE, "watch-pane.py")

# Descendant one-liner. argv[1] = registry file: the descendant APPENDS its own pid once
# alive (contract item 1), then sleeps until killed. Its stdout/stderr are redirected to
# /dev/null by the fake herdr so it never holds the leader's pipe. Its command line carries
# the registry path (under this run's temp dir), which is the ownership token (item 3).
DESCENDANT = (
    "import os,sys,time;"
    "r=open(sys.argv[1],'a');print(os.getpid(),file=r);r.close();"
    "time.sleep(3600)"
)

# The fake `herdr`. Reads $HERDR_FAKE_DIR/counter (default 0), emits file min(call, max),
# then increments the counter. Tokens SLEEP / GARBAGE / RC1 override the body. When
# $HERDR_FAKE_SPAWN_CHILD is set it spawns a same-group descendant (finding #1), records the
# spawn pid (`$!`) into $HERDR_FAKE_CHILD_SPAWNED, and waits for the child to record its OWN
# pid in $HERDR_FAKE_CHILD_REGISTRY (contract item 1) before emitting.
FAKE_HERDR = r'''#!/bin/sh
# Fake herdr agent explain — deterministic, stateful across calls via a counter file.
DIR="$HERDR_FAKE_DIR"
C="$DIR/counter"
if [ -f "$C" ]; then c=$(cat "$C" 2>/dev/null); else c=0; fi
case "$c" in ''|*[!0-9]*) c=0;; esac
c=$((c + 1))
printf '%s' "$c" > "$C"
max=0
for f in "$DIR"/[0-9]*; do
  [ -f "$f" ] || continue
  n=$(basename "$f")
  case "$n" in *[!0-9]*) continue;; esac
  [ "$n" -gt "$max" ] && max=$n
done
[ "$max" -eq 0 ] && max=1
if [ "$c" -gt "$max" ]; then idx=$max; else idx=$c; fi
resp="$DIR/$idx"
[ -f "$resp" ] || resp="$DIR/$max"
if [ ! -f "$resp" ]; then printf '%s' ''; exit 0; fi
body=$(cat "$resp")
# Optional same-group descendant (finding #1 regression). Its stdout/stderr are redirected
# to /dev/null so it does NOT hold the leader's pipe open; it stays in the leader's process
# group (non-interactive sh, no job control), so watch-pane's process-group kill is the only
# thing that reaps it once the leader has exited.
if [ -n "$HERDR_FAKE_SPAWN_CHILD" ]; then
  "$HERDR_FAKE_PYTHON" -c "$HERDR_FAKE_DESCENDANT" "$HERDR_FAKE_CHILD_REGISTRY" >/dev/null 2>&1 &
  dpid=$!
  printf '%s\n' "$dpid" >> "$HERDR_FAKE_CHILD_SPAWNED"
  # Contract item 1: wait for the child to record its OWN pid in the registry (grep -x the
  # exact pid line). A child that exits before recording leaves the registry short — the
  # runner's readiness-equality check (item 2) then FAILs loudly. On timeout we proceed
  # (emit) so watch-pane still runs; the runner detects the missing readiness.
  k=0
  while ! grep -qx "$dpid" "$HERDR_FAKE_CHILD_REGISTRY" 2>/dev/null && [ "$k" -lt 200 ]; do
    k=$((k+1)); sleep 0.01
  done
fi
case "$body" in
  SLEEP) sleep 3; printf '%s' '{"state":"working","matched_rule":{"id":"r"},"screen_detection_skip_reason":null,"fallback_reason":null}';;
  GARBAGE) printf '%s' 'zzz-not-json';;
  RC1) exit 1;;
  *) printf '%s' "$body";;
esac
'''

# Default per-scenario env (handoff §Deliverable 2).
DEFAULT_ENV = {
    "HERDR_WATCH_SAMPLE_MS": "500",
    "HERDR_WATCH_INTERVAL_S": "0",
    "HERDR_WATCH_START_SAMPLES": "2",
    "HERDR_WATCH_MAX_SAMPLES": "4",
}

# JSON bodies mirroring the real agent-explain fields the predicate reads.
WORKING_RULE = (
    '{"state":"working","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
WORKING_HOOK = (
    '{"state":"working","matched_rule":null,'
    '"screen_detection_skip_reason":"full_lifecycle_hook_authority","fallback_reason":null}'
)
IDLE_RULE = (
    '{"state":"idle","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
IDLE_HOOK = (
    '{"state":"idle","matched_rule":null,'
    '"screen_detection_skip_reason":"full_lifecycle_hook_authority","fallback_reason":null}'
)
BLOCKED_RULE = (
    '{"state":"blocked","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
FALLBACK_IDLE = (
    '{"state":"idle","matched_rule":null,"screen_detection_skip_reason":null,'
    '"fallback_reason":"default_known_agent_idle_fallback"}'
)
# Finding #2: authoritative state strings carrying a trailing space token. The bash
# `set -- $(sample)` consumes only the first token, so these classify as working/idle.
WORKING_DETAIL = (
    '{"state":"working detail","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
IDLE_DETAIL = (
    '{"state":"idle detail","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
STATE_EMPTY = (
    '{"state":"","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
# Finding #1 (round 2): bash default-IFS splits ONLY space/tab/newline. CR and NBSP are
# ORDINARY chars and stay inside the token, so these are ONE token -> exit 4. The JSON uses
# the escapes \r and \u00a0 so json.loads yields a state containing a real CR / NBSP byte.
WORKING_CR_DETAIL = (
    '{"state":"working\\rdetail","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
IDLE_CR_DETAIL = (
    '{"state":"idle\\rdetail","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
WORKING_NBSP_X = (
    '{"state":"working\\u00a0x","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)
IDLE_NBSP_X = (
    '{"state":"idle\\u00a0x","matched_rule":{"id":"r"},'
    '"screen_detection_skip_reason":null,"fallback_reason":null}'
)

HUGE_INT = "1" * 400           # 400-digit positive integer (overflow on float conversion)
OVERFLOW_INTERVAL = "9" * 20   # 99999999999999999999 >> 86400


def _read_pids(path):
    # type: (str) -> list
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return []
    pids = []
    for line in lines:
        s = line.strip()
        if s.isdigit():
            pids.append(int(s))
    return pids


def _pid_dead(pid):
    # type: (int) -> bool
    """True if `pid` is gone (reaped) or a zombie. The descendant is reparented to PID 1
    after its leader exits, so we cannot waitpid it; read its state via `ps`. On any ps
    failure, be conservative (treat as alive) so a dead-descendant assertion never passes
    on undeterminable state."""
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False  # cannot determine — be conservative (treat as alive)
    state = out.stdout.strip()
    if not state:
        return True  # no such process — reaped/gone
    return state[0] == "Z"  # zombie = exited, awaiting reap = dead for our purposes


def _owned_alive(pid, token):
    # type: (int, str) -> bool
    """Contract item 3: True ONLY if `pid` is alive AND its command line carries this run's
    unique `token` (the run temp-dir path, which appears in the descendant's argv). A pid
    that is dead, or that was reused by an unrelated process (command lacks the token), or
    that is undeterminable, returns False — it must NEVER be signaled."""
    try:
        out = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=2.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False  # unverifiable — never signal
    cmd = out.stdout
    return bool(cmd) and token in cmd


def _descendants_all_dead(pids, timeout=2.0):
    if not pids:
        return None  # no PIDs recorded -> descendant path never ran -> invalid scenario
    deadline = time.monotonic() + timeout
    while True:
        if all(_pid_dead(p) for p in pids):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _cleanup_pids_owned(pids, token):
    # type: (list, str) -> None
    """Contract items 3-4: SIGKILL only pids that are alive AND owned by this run (command
    carries `token`). Never signal a pid already dead or unverifiable. Never a
    process-table-wide pattern — only the pids this run recorded."""
    for pid in sorted(set(pids)):
        if not _owned_alive(pid, token):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass


class Runner:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="watch-pane-test-")
        self.token = self.tmp  # unique per-run ownership token (in every descendant's argv)
        self.bindir = self._install_fake()
        self._registries = []  # list of (spawned_path, registry_path); tracked for cleanup

    def _install_fake(self):
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        path = os.path.join(bindir, "herdr")
        with open(path, "w") as f:
            f.write(FAKE_HERDR)
        mode = os.stat(path).st_mode
        os.chmod(path, mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        return bindir

    def scenario_dir(self, label, files):
        d = os.path.join(self.tmp, "scn-" + label)
        os.makedirs(d, exist_ok=True)
        cfile = os.path.join(d, "counter")
        if os.path.exists(cfile):
            os.remove(cfile)
        for fname, body in files.items():
            with open(os.path.join(d, fname), "w") as f:
                f.write(body)
        return d

    def run(self, label, files, expected, args, env_overrides=None, expect_fast=False,
            child=False, child_count=0, stderr_contains=None):
        env_overrides = env_overrides or {}
        scn = self.scenario_dir(label, files)
        env = dict(os.environ)
        env.update(DEFAULT_ENV)
        env.update(env_overrides)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        env["HERDR_FAKE_DIR"] = scn
        env["HERDR_FAKE_PYTHON"] = sys.executable
        env["HERDR_FAKE_DESCENDANT"] = DESCENDANT
        spawned_path = registry_path = None
        if child:
            spawned_path = os.path.join(scn, "child_spawned")
            registry_path = os.path.join(scn, "child_registry")
            open(spawned_path, "w").close()
            open(registry_path, "w").close()
            env["HERDR_FAKE_SPAWN_CHILD"] = "1"
            env["HERDR_FAKE_CHILD_SPAWNED"] = spawned_path
            env["HERDR_FAKE_CHILD_REGISTRY"] = registry_path
            # track BEFORE run so an interrupt/exception still cleans up (item 4)
            self._registries.append((spawned_path, registry_path))
        cmd = [sys.executable, WATCH_PANE] + args
        t0 = time.monotonic()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        elapsed = time.monotonic() - t0
        ok = proc.returncode == expected
        if expect_fast and elapsed >= 2.5:
            ok = False
        if stderr_contains is not None and stderr_contains not in proc.stderr:
            ok = False
        child_note = ""
        if child:
            spawned = _read_pids(spawned_path)
            ready = _read_pids(registry_path)
            # Contract item 2: readiness list must EQUAL the spawn-recorded list AND the
            # expected per-scenario child count. No proceed-on-timeout — a child that exited
            # before recording its pid (e.g. sabotage) makes this FAIL loudly.
            ready_ok = (len(spawned) == child_count
                        and len(ready) == child_count
                        and set(ready) == set(spawned))
            if not ready_ok:
                child_note = (" [readiness MISMATCH spawned=%d ready=%d expected=%d]"
                              % (len(spawned), len(ready), child_count))
                ok = False
            else:
                dead = _descendants_all_dead(ready)
                if dead is True:
                    child_note = " [%d descendants dead]" % len(ready)
                else:
                    child_note = " [descendant ALIVE]"
                    ok = False
            # kill only owned+alive tracked pids (items 3-4)
            _cleanup_pids_owned(set(spawned) | set(ready), self.token)
        status = "PASS" if ok else "FAIL"
        detail = "exit=%d(exp %d) %.2fs%s" % (proc.returncode, expected, elapsed, child_note)
        if expect_fast:
            detail += " <2.5s"
        if stderr_contains is not None:
            detail += " stderr~%r" % stderr_contains
        print("%-4s %-26s %s" % (status, label, detail))
        if not ok:
            err = (proc.stderr.strip() or "<empty>")[:300]
            print("     stderr: %s" % err)
        return ok

    def cleanup(self):
        # Contract item 4: kill every tracked descendant (owned + alive) BEFORE removing the
        # temp dir, on every path — normal, scenario exception, or SIGINT mid-run. The
        # registry is not deleted while children live. Per-registry try so one failure does
        # not skip the rest; the outer finally still deletes the temp dir.
        try:
            for spawned_path, registry_path in self._registries:
                try:
                    pids = set(_read_pids(spawned_path)) | set(_read_pids(registry_path))
                    _cleanup_pids_owned(sorted(pids), self.token)
                except Exception:
                    pass
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


def main():
    r = Runner()
    passed = 0
    total = 0
    interrupted = False
    try:
        # row = (label, files, expected, args, env_overrides, expect_fast, child, child_count,
        #        stderr_contains)
        cases = [
            # --- original frozen-exit-code coverage ---
            ("1a-rule-working-idle", {"1": WORKING_RULE, "2": IDLE_RULE}, 0, ["p1"], {}, False, False, 0, None),
            ("1b-hook-working-idle", {"1": WORKING_HOOK, "2": IDLE_HOOK}, 0, ["p1"], {}, False, False, 0, None),
            ("2-blocked", {"1": BLOCKED_RULE}, 2, ["p1"], {}, False, False, 0, None),
            ("3-always-working", {"1": WORKING_RULE}, 3, ["p1"], {}, False, False, 0, None),
            ("4-fallback-idle", {"1": FALLBACK_IDLE}, 4, ["p1"], {}, False, False, 0, None),
            ("5-always-idle", {"1": IDLE_RULE}, 5, ["p1"], {}, False, False, 0, None),
            ("6-sleep-timeout", {"1": "SLEEP"}, 4, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "300"}, True, False, 0, None),
            ("7a-garbage", {"1": "GARBAGE"}, 4, ["p1"], {}, False, False, 0, None),
            ("7b-rc1", {"1": "RC1"}, 4, ["p1"], {}, False, False, 0, None),
            ("8a-no-argv", {}, 64, [], {}, False, False, 0, None),
            ("8b-bad-env", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "abc"}, False, False, 0, None),
            # --- finding #1 (round 1): unconditional process-group kill on every sample path ---
            # A: working then idle, each sample spawns a same-group descendant. watch-pane must
            #    return 0 AND every recorded descendant (multi-sample) must be dead.
            ("A-child-work-idle", {"1": WORKING_RULE, "2": IDLE_RULE}, 0, ["p1"], {}, False, True, 2, None),
            # B: leader exits rc 1 before the sample timeout leaving a descendant (ESRCH path);
            #    watch-pane exits 4, the failure diagnostic must name rc=1 (not a timeout).
            ("B-child-rc1", {"1": "RC1"}, 4, ["p1"], {}, False, True, 1, "rc=1"),
            # --- finding #2 (round 1): bash whitespace-tokenization of state (space IS IFS) ---
            ("2b-state-tokens", {"1": WORKING_DETAIL, "2": IDLE_DETAIL}, 0, ["p1"], {}, False, False, 0, None),
            ("2c-state-empty", {"1": STATE_EMPTY}, 4, ["p1"], {}, False, False, 0, None),
            # --- finding #3 (round 1): env range validation (exit 64, no traceback) ---
            ("8c-interval-neg", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_INTERVAL_S": "-1"}, False, False, 0, None),
            ("8d-sample-ms-zero", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "0"}, False, False, 0, None),
            # --- finding #1 (round 2): bash default-IFS is space/tab/newline ONLY ---
            ("2d-state-cr", {"1": WORKING_CR_DETAIL, "2": IDLE_CR_DETAIL}, 4, ["p1"], {}, False, False, 0, None),
            ("2e-state-nbsp", {"1": WORKING_NBSP_X, "2": IDLE_NBSP_X}, 4, ["p1"], {}, False, False, 0, None),
            # --- finding #2 (round 2): safe upper bounds (overflow -> exit 64, no traceback) ---
            ("8e-sample-ms-huge", {"1": BLOCKED_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": HUGE_INT}, False, False, 0, None),
            ("8f-interval-huge", {"1": WORKING_RULE}, 64, ["p1"], {"HERDR_WATCH_INTERVAL_S": OVERFLOW_INTERVAL}, False, False, 0, None),
            ("8g-interval-86400-ok", {"1": BLOCKED_RULE}, 2, ["p1"], {"HERDR_WATCH_INTERVAL_S": "86400"}, False, False, 0, None),
            ("8h-interval-86401-bad", {"1": BLOCKED_RULE}, 64, ["p1"], {"HERDR_WATCH_INTERVAL_S": "86401"}, False, False, 0, None),
        ]
        for label, files, expected, args, overrides, fast, child, child_count, stderr_contains in cases:
            total += 1
            if r.run(label, files, expected, args, overrides, expect_fast=fast,
                     child=child, child_count=child_count, stderr_contains=stderr_contains):
                passed += 1
    except KeyboardInterrupt:
        interrupted = True
    finally:
        r.cleanup()
    if interrupted:
        print("\n[interrupt: all tracked descendants cleaned, temp dir removed]")
        return 130
    print("---\n%d/%d scenarios passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
