#!/usr/bin/env python3
"""Self-test for watch-pane.py. Stdlib only.

Runnable as `python3 scripts/watch-pane-test.py` from any cwd. Total runtime < 60s.
Exit 0 iff every scenario matches its expected (frozen) exit code. Prints one PASS/FAIL
line per scenario.

Mechanism: a temp dir holds a fake executable `herdr` (/bin/sh script) prepended to PATH
for the child. The fake reads a per-scenario directory of numbered response files plus a
counter file: call N emits file min(N, max) and increments the counter. A response file
may hold a JSON body, or one of the tokens SLEEP / GARBAGE / RC1.
"""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH_PANE = os.path.join(HERE, "watch-pane.py")

# The fake `herdr`. Reads $HERDR_FAKE_DIR/counter (default 0), emits file min(call, max),
# then increments the counter. Tokens SLEEP / GARBAGE / RC1 override the body.
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

    def run(self, label, files, expected, args, env_overrides=None, expect_fast=False):
        env_overrides = env_overrides or {}
        scn = self.scenario_dir(label, files)
        env = dict(os.environ)
        env.update(DEFAULT_ENV)
        env.update(env_overrides)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        env["HERDR_FAKE_DIR"] = scn
        cmd = [sys.executable, WATCH_PANE] + args
        t0 = time.monotonic()
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
        elapsed = time.monotonic() - t0
        ok = proc.returncode == expected
        if expect_fast and elapsed >= 2.5:
            ok = False
        status = "PASS" if ok else "FAIL"
        detail = "exit=%d(exp %d) %.2fs" % (proc.returncode, expected, elapsed)
        if expect_fast:
            detail += " <2.5s"
        print("%-4s %-24s %s" % (status, label, detail))
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
            # 1a: authoritative working (rule) then idle -> 0
            ("1a-rule-working-idle", {"1": WORKING_RULE, "2": IDLE_RULE}, 0, ["p1"], {}, False),
            # 1b: hook-authority variant -> 0
            ("1b-hook-working-idle", {"1": WORKING_HOOK, "2": IDLE_HOOK}, 0, ["p1"], {}, False),
            # 2: authoritative blocked on first sample -> 2
            ("2-blocked", {"1": BLOCKED_RULE}, 2, ["p1"], {}, False),
            # 3: authoritative working on every sample -> 3
            ("3-always-working", {"1": WORKING_RULE}, 3, ["p1"], {}, False),
            # 4: fallback idle (the always-idle lie) -> 4
            ("4-fallback-idle", {"1": FALLBACK_IDLE}, 4, ["p1"], {}, False),
            # 5: authoritative idle on every sample -> 5 (fires at i=2)
            ("5-always-idle", {"1": IDLE_RULE}, 5, ["p1"], {}, False),
            # 6: SLEEP with sample_ms=300 -> 4 and completes < 2.5s (process-group kill bounds the hang)
            ("6-sleep-timeout", {"1": "SLEEP"}, 4, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "300"}, True),
            # 7a: GARBAGE -> 4
            ("7a-garbage", {"1": "GARBAGE"}, 4, ["p1"], {}, False),
            # 7b: RC1 -> 4
            ("7b-rc1", {"1": "RC1"}, 4, ["p1"], {}, False),
            # 8a: no argv -> 64
            ("8a-no-argv", {}, 64, [], {}, False),
            # 8b: HERDR_WATCH_SAMPLE_MS=abc -> 64
            ("8b-bad-env", {"1": IDLE_RULE}, 64, ["p1"], {"HERDR_WATCH_SAMPLE_MS": "abc"}, False),
        ]
        for label, files, expected, args, overrides, fast in cases:
            total += 1
            if r.run(label, files, expected, args, overrides, expect_fast=fast):
                passed += 1
    finally:
        r.cleanup()
    print("---\n%d/%d scenarios passed" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
