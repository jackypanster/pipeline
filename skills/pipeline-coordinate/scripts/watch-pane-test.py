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
descendant (a `python -c` one-liner) that holds an exclusive flock on a lockfile, with its
stdout/stderr redirected to /dev/null (so it does not hold the leader's pipe open). The
leader waits for a readiness file before emitting its body. This exercises the frozen
process-group kill: once the leader has exited, the descendant can only be reaped by
watch-pane's unconditional `os.killpg(pgid, SIGKILL)` (finding #1).
"""

import fcntl
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

# Sentinel var so lingering descendants from a regression run can be swept with
# `pkill -f _wpdesc=1` (best-effort hygiene; the normal fixed run leaves none).
DESCENDANT = (
    "_wpdesc=1;"
    "import fcntl,os,sys,time;"
    "f=open(sys.argv[1],'a');"
    "fcntl.flock(f,fcntl.LOCK_EX);"
    "r=open(sys.argv[2],'w');r.write(str(os.getpid()));r.close();"
    "time.sleep(3600)"
)

# The fake `herdr`. Reads $HERDR_FAKE_DIR/counter (default 0), emits file min(call, max),
# then increments the counter. Tokens SLEEP / GARBAGE / RC1 override the body. When
# $HERDR_FAKE_SPAWN_CHILD is set it first spawns a same-group descendant (finding #1).
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
# Optional same-group descendant holding an exclusive flock (finding #1 regression). Its
# stdout/stderr are redirected to /dev/null so it does NOT hold the leader's pipe open; it
# stays in the leader's process group (non-interactive sh, no job control), so watch-pane's
# process-group kill is the only thing that reaps it once the leader has exited.
if [ -n "$HERDR_FAKE_SPAWN_CHILD" ]; then
  "$HERDR_FAKE_PYTHON" -c "$HERDR_FAKE_DESCENDANT" "$HERDR_FAKE_LOCKFILE" "$HERDR_FAKE_READYFILE" >/dev/null 2>&1 &
  k=0
  while [ ! -f "$HERDR_FAKE_READYFILE" ] && [ "$k" -lt 200 ]; do k=$((k+1)); sleep 0.01; done
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
# Finding #2: authoritative state strings carrying a trailing whitespace token. The bash
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


def _descendant_dead(lock_path, ready_path, timeout=2.0):
    """True if the descendant is gone (lock acquirable); False if still alive; None if the
    readiness file never appeared (descendant never reached lock-acquire — invalid scenario)."""
    if not os.path.exists(ready_path):
        return None
    f = open(lock_path, "r+")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.02)
    finally:
        try:
            f.close()
        except OSError:
            pass


def _cleanup_ready_pid(ready_path):
    """Best-effort SIGKILL of the PID recorded in the readiness file (regression-run hygiene)."""
    try:
        with open(ready_path) as fh:
            pid = int(fh.read().strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


class Runner:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="watch-pane-test-")
        self.bindir = self._install_fake()

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

    def run(self, label, files, expected, args, env_overrides=None, expect_fast=False, child=False):
        env_overrides = env_overrides or {}
        scn = self.scenario_dir(label, files)
        env = dict(os.environ)
        env.update(DEFAULT_ENV)
        env.update(env_overrides)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        env["HERDR_FAKE_DIR"] = scn
        env["HERDR_FAKE_PYTHON"] = sys.executable
        env["HERDR_FAKE_DESCENDANT"] = DESCENDANT
        lock_path = ready_path = None
        if child:
            lock_path = os.path.join(scn, "child.lock")
            ready_path = os.path.join(scn, "child.ready")
            open(lock_path, "w").close()  # pre-create empty lockfile
            if os.path.exists(ready_path):
                os.remove(ready_path)
            env["HERDR_FAKE_SPAWN_CHILD"] = "1"
            env["HERDR_FAKE_LOCKFILE"] = lock_path
            env["HERDR_FAKE_READYFILE"] = ready_path
        cmd = [sys.executable, WATCH_PANE] + args
        t0 = time.monotonic()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        elapsed = time.monotonic() - t0
        ok = proc.returncode == expected
        if expect_fast and elapsed >= 2.5:
            ok = False
        child_note = ""
        if child:
            dead = _descendant_dead(lock_path, ready_path)
            if dead is None:
                child_note = " [descendant readiness MISSING]"
                ok = False
            elif dead is True:
                child_note = " [descendant dead]"
            else:
                child_note = " [descendant ALIVE]"
                ok = False
            _cleanup_ready_pid(ready_path)
        status = "PASS" if ok else "FAIL"
        detail = "exit=%d(exp %d) %.2fs%s" % (proc.returncode, expected, elapsed, child_note)
        if expect_fast:
            detail += " <2.5s"
        print("%-4s %-26s %s" % (status, label, detail))
        if not ok:
            err = (proc.stderr.strip() or "<empty>")[:300]
            print("     stderr: %s" % err)
        return ok

    def cleanup(self):
        # best-effort sweep of any lingering descendants (regression run only)
        try:
            subprocess.run(["pkill", "-9", "-f", "_wpdesc=1"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, FileNotFoundError):
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)


def main():
    r = Runner()
    passed = 0
    total = 0
    try:
        cases = [
            # --- original frozen-exit-code coverage ---
            ("1a-rule-working-idle", {"1": WORKING_RULE, "2": IDLE_RULE}, 0, ["p1"], {}, False, False),
            ("1b-hook-working-idle", {"1": WORKING_HOOK, "2": IDLE_HOOK}, 0, ["p1"], {}, False, False),
            ("2-blocked", {"1": BLOCKED_RULE}, 2, ["p1"], {}, False, False),
            ("3-always-working", {"1": WORKING_RULE}, 3, ["p1"], {}, False, False),
            ("4-fallback-idle", {"1": FALLBACK_IDLE}, 4, ["p1"], {}, False, False),
            ("5-always-idle", {"1": IDLE_RULE}, 5, ["p1"], {}, False, False),
            ("6-sleep-timeout", {"1": "SLEEP"}, 4, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "300"}, True, False),
            ("7a-garbage", {"1": "GARBAGE"}, 4, ["p1"], {}, False, False),
            ("7b-rc1", {"1": "RC1"}, 4, ["p1"], {}, False, False),
            ("8a-no-argv", {}, 64, [], {}, False, False),
            ("8b-bad-env", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "abc"}, False, False),
            # --- finding #1: unconditional process-group kill on every sample path ---
            # A: authoritative working then idle, each sample spawns a same-group descendant
            #    that outlives the exited leader. watch-pane must return 0 AND the descendant
            #    must be dead (the lock acquirable).
            ("A-child-work-idle", {"1": WORKING_RULE, "2": IDLE_RULE}, 0, ["p1"], {}, False, True),
            # B: leader exits rc 1 before the sample timeout leaving a descendant (the ESRCH
            #    path); watch-pane exits 4 and the descendant must be dead.
            ("B-child-rc1", {"1": "RC1"}, 4, ["p1"], {}, False, True),
            # --- finding #2: bash `set -- $(sample)` whitespace-tokenization of state ---
            ("2b-state-tokens", {"1": WORKING_DETAIL, "2": IDLE_DETAIL}, 0, ["p1"], {}, False, False),
            ("2c-state-empty", {"1": STATE_EMPTY}, 4, ["p1"], {}, False, False),
            # --- finding #3: env range validation (exit 64, no traceback) ---
            ("8c-interval-neg", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_INTERVAL_S": "-1"}, False, False),
            ("8d-sample-ms-zero", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "0"}, False, False),
        ]
        for label, files, expected, args, overrides, fast, child in cases:
            total += 1
            if r.run(label, files, expected, args, overrides, expect_fast=fast, child=child):
                passed += 1
    finally:
        r.cleanup()
    print("---\n%d/%d scenarios passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
