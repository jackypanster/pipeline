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
does not hold the leader's pipe open). The fake herdr RECORDS that descendant's PID ($!)
into a per-scenario file under this run's own temp dir and waits for a readiness signal
before emitting its body. The runner's dead-descendant assertion and its cleanup operate on
EXACTLY those recorded PIDs (every child from multi-sample A included) — there is no
process-table-wide pattern (each run kills only its own recorded PIDs), so two concurrent
suite runs never cross-kill.
This exercises the frozen process-group kill: once the leader has exited, the descendant can
only be reaped by watch-pane's unconditional `os.killpg(pgid, SIGKILL)` (finding #1).
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

# Descendant one-liner. argv[1] = readiness file: the descendant writes its own PID there
# (proving it reached the sleep), then sleeps until killed. Its stdout/stderr are redirected
# to /dev/null by the fake herdr so it never holds the leader's pipe.
DESCENDANT = (
    "import os,sys,time;"
    "r=open(sys.argv[1],'w');r.write(str(os.getpid()));r.close();"
    "time.sleep(3600)"
)

# The fake `herdr`. Reads $HERDR_FAKE_DIR/counter (default 0), emits file min(call, max),
# then increments the counter. Tokens SLEEP / GARBAGE / RC1 override the body. When
# $HERDR_FAKE_SPAWN_CHILD is set it first spawns a same-group descendant (finding #1) and
# records that descendant's PID into $HERDR_FAKE_CHILD_PIDS.
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
# thing that reaps it once the leader has exited. RECORD its PID in this run's own temp file
# (the runner asserts/cleans exactly these PIDs — never a process-table-wide pattern).
if [ -n "$HERDR_FAKE_SPAWN_CHILD" ]; then
  : > "$HERDR_FAKE_CHILD_READY"
  "$HERDR_FAKE_PYTHON" -c "$HERDR_FAKE_DESCENDANT" "$HERDR_FAKE_CHILD_READY" >/dev/null 2>&1 &
  printf '%s\n' "$!" >> "$HERDR_FAKE_CHILD_PIDS"
  k=0
  while [ ! -s "$HERDR_FAKE_CHILD_READY" ] && [ "$k" -lt 200 ]; do k=$((k+1)); sleep 0.01; done
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
    after its leader exits, so we cannot waitpid it; read its state via `ps`."""
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


def _cleanup_pids(pids):
    # type: (list) -> None
    """SIGKILL exactly the PIDs this runner recorded (best-effort; never a global pattern)."""
    for pid in pids:
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
        pids_path = ready_path = None
        if child:
            pids_path = os.path.join(scn, "child_pids")
            ready_path = os.path.join(scn, "child_ready")
            open(pids_path, "w").close()
            open(ready_path, "w").close()
            env["HERDR_FAKE_SPAWN_CHILD"] = "1"
            env["HERDR_FAKE_CHILD_PIDS"] = pids_path
            env["HERDR_FAKE_CHILD_READY"] = ready_path
        cmd = [sys.executable, WATCH_PANE] + args
        t0 = time.monotonic()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        elapsed = time.monotonic() - t0
        ok = proc.returncode == expected
        if expect_fast and elapsed >= 2.5:
            ok = False
        child_note = ""
        if child:
            pids = _read_pids(pids_path)
            dead = _descendants_all_dead(pids)
            if dead is None:
                child_note = " [no descendant PIDs recorded]"
                ok = False
            elif dead:
                child_note = " [%d descendants dead]" % len(pids)
            else:
                child_note = " [descendant ALIVE]"
                ok = False
            _cleanup_pids(pids)  # kill only the PIDs this run created
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
            # --- finding #1 (round 1): unconditional process-group kill on every sample path ---
            # A: authoritative working then idle, each sample spawns a same-group descendant
            #    that outlives the exited leader. watch-pane must return 0 AND every recorded
            #    descendant PID (multi-sample) must be dead.
            ("A-child-work-idle", {"1": WORKING_RULE, "2": IDLE_RULE}, 0, ["p1"], {}, False, True),
            # B: leader exits rc 1 before the sample timeout leaving a descendant (the ESRCH
            #    path); watch-pane exits 4 and the descendant must be dead.
            ("B-child-rc1", {"1": "RC1"}, 4, ["p1"], {}, False, True),
            # --- finding #2 (round 1): bash whitespace-tokenization of state (space IS IFS) ---
            ("2b-state-tokens", {"1": WORKING_DETAIL, "2": IDLE_DETAIL}, 0, ["p1"], {}, False, False),
            ("2c-state-empty", {"1": STATE_EMPTY}, 4, ["p1"], {}, False, False),
            # --- finding #3 (round 1): env range validation (exit 64, no traceback) ---
            ("8c-interval-neg", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_INTERVAL_S": "-1"}, False, False),
            ("8d-sample-ms-zero", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "0"}, False, False),
            # --- finding #1 (round 2): bash default-IFS is space/tab/newline ONLY ---
            # CR is an ordinary char -> one token -> exit 4 at sample 1 (fails open on 2baca68).
            ("2d-state-cr", {"1": WORKING_CR_DETAIL, "2": IDLE_CR_DETAIL}, 4, ["p1"], {}, False, False),
            # NBSP is an ordinary char -> one token -> exit 4 (fails open on 2baca68).
            ("2e-state-nbsp", {"1": WORKING_NBSP_X, "2": IDLE_NBSP_X}, 4, ["p1"], {}, False, False),
            # --- finding #2 (round 2): safe upper bounds (overflow -> exit 64, no traceback) ---
            ("8e-sample-ms-huge", {"1": BLOCKED_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": HUGE_INT}, False, False),
            ("8f-interval-huge", {"1": WORKING_RULE}, 64, ["p1"], {"HERDR_WATCH_INTERVAL_S": OVERFLOW_INTERVAL}, False, False),
            ("8g-interval-86400-ok", {"1": BLOCKED_RULE}, 2, ["p1"], {"HERDR_WATCH_INTERVAL_S": "86400"}, False, False),
            ("8h-interval-86401-bad", {"1": BLOCKED_RULE}, 64, ["p1"], {"HERDR_WATCH_INTERVAL_S": "86401"}, False, False),
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
