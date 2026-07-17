#!/usr/bin/env python3
"""watch-pane.py — frozen single-sample authority+state watcher for a Herdr pane.

Extracted (semantics verbatim) from skills/pipeline-coordinate/SKILL.md §Transport verbs,
where the reviewed inline bash+perl loop lived. The semantics are FROZEN; change only via
meta-PR against the pipeline repo (see handoff-watch-pane-py.md).

The reviewed predicate is a fail-closed, single-sample authority+state read: state WITHOUT
authority is the fallback's always-idle lie. Bounded sampling prevents a wedged
`herdr agent explain` from hanging the coordinator loop.

Exit-code contract (frozen):
    0  pane went working -> idle
    2  authoritative blocked
    3  max samples exhausted
    4  authority lost / unexpected state / sample failure (fail closed)
    5  never started processing
    64 usage / bad env (does not collide with the semantic codes above)
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
from typing import List, Tuple

USAGE = "usage: watch-pane.py <pane_id>"
DIAG_LIMIT = 200


def _truncate(text, limit=DIAG_LIMIT):
    # type: (str, int) -> str
    """Collapse to one line and cap length so the diagnostic is always a single line."""
    text = (text or "").replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        text = text[:limit]
    return text


def _fail(code, i, reason, last):
    # type: (int, int, str, str) -> None
    """Print exactly one diagnostic line and exit. Silent on exit 0 (call sys.exit(0) directly)."""
    sys.stderr.write(
        "watch-pane: exit=%d i=%d reason=%s last=%s\n"
        % (code, i, reason, _truncate(last))
    )
    sys.exit(code)


def _kill_group(pgid):
    # type: (int) -> None
    """SIGKILL the whole process group `pgid`. No-op if it is already gone (ESRCH).

    Uses the known PGID directly: `start_new_session=True` made the leader's PID == PGID for
    the whole life of the group. Do NOT resolve via os.getpgid(leader_pid) — once the leader
    has exited, getpgid raises ESRCH even though a same-group descendant still lives, and that
    descendant would be orphaned (the load-bearing regression of finding #1).
    """
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _reap(proc, timeout=2.0):
    # type: (subprocess.Popen, float) -> None
    """Bounded reap. Never blocks longer than `timeout` — never rely on Popen.__exit__'s
    unbounded wait after a kill (finding #1: no path may re-enter an unbounded wait)."""
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


def _read_env_int(name, default, minimum, maximum):
    # type: (str, int, int, int) -> int
    raw = os.environ.get(name)
    if raw is None or raw == "":
        val = default
    else:
        try:
            val = int(raw)
        except ValueError:
            _fail(64, 0, "bad_env", "%s=%r is not an integer" % (name, raw))
            return default  # unreachable: _fail exits
    if val < minimum or val > maximum:
        _fail(64, 0, "bad_env", "%s=%d is outside the allowed range [%d, %d]"
              % (name, val, minimum, maximum))
        return default  # unreachable: _fail exits
    return val


def _parse_sample(raw):
    # type: (str) -> Tuple[int, str, str]
    """Decode one `herdr agent explain --json` payload -> (authority, state, last).

    Un-parseable / non-object JSON -> (0, "unknown", <diagnostic>).
    """
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0, "unknown", "unparseable JSON: " + raw.strip()
    if not isinstance(payload, dict):
        return 0, "unknown", "non-object JSON: " + raw.strip()
    sdsr = payload.get("screen_detection_skip_reason")
    matched_rule = payload.get("matched_rule")
    fallback_reason = payload.get("fallback_reason")
    authority = 1 if (
        (sdsr == "full_lifecycle_hook_authority" or matched_rule is not None)
        and fallback_reason is None
    ) else 0
    state_val = payload.get("state")
    if state_val is None:
        state = "unknown"
    else:
        if not isinstance(state_val, str):
            state_val = str(state_val)
        # Frozen bash-tokenization compatibility: the reference `set -- $(sample)` word-splits
        # the emitted "<authority> <state>" line on bash's DEFAULT IFS (space/tab/newline only)
        # and the loop consumes only the FIRST token. CR/VT/FF/NBSP and all other Unicode
        # whitespace are ORDINARY characters that stay inside the token, so e.g. "working\rdetail"
        # is ONE token -> fails the idle|working check -> exit 4, exactly as bash. str.split()
        # is NOT used (it splits every Unicode whitespace char). An empty/IFS-only state yields
        # an empty token -> exit 4 (same as bash `$2` unset).
        parts = [t for t in re.split(r"[ \t\n]+", state_val) if t]
        state = parts[0] if parts else ""
    return authority, state, raw.strip()


def _sample(pane_id, sample_ms):
    # type: (str, int) -> Tuple[int, str, str]
    """ONE bounded `herdr agent explain <pane_id> --json` -> (authority, state, last).

    NEVER raises: TimeoutExpired, nonzero rc, missing binary (FileNotFoundError), or
    un-parseable JSON all collapse to (0, "unknown", <diagnostic>) — the fail-closed
    predicate. The transport rc is checked BEFORE parsing (piping the bounded command
    straight into a JSON parser would let the parser consume JSON that arrived before a
    hang and erase the timeout).
    """
    cmd = ["herdr", "agent", "explain", pane_id, "--json"]
    timeout_s = sample_ms / 1000.0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
    except FileNotFoundError:
        return 0, "unknown", "herdr binary not found on PATH"
    except OSError as exc:
        return 0, "unknown", "spawn failed: %s" % exc

    # start_new_session=True guarantees PGID == proc.pid for the whole life of the group;
    # capture it ONCE here (see _kill_group for why os.getpgid is never re-resolved later).
    pgid = proc.pid
    timed_out = False
    out = ""
    try:
        try:
            out, _ = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
        if timed_out:
            result = (0, "unknown", "herdr agent explain timed out after %dms" % sample_ms)
        else:
            rc = proc.returncode  # communicate() already reaped the leader here
            if rc != 0:
                result = (0, "unknown", "herdr agent explain exited rc=%d" % rc)
            else:
                result = _parse_sample(out)
        return result
    except Exception as exc:  # never raise out of sample — fail closed
        return 0, "unknown", "sample internal error: %s" % exc
    finally:
        # Unconditional process-group kill on EVERY sample exit path (normal rc==0, rc!=0,
        # timeout, parse failure, internal error) — mirrors the reviewed perl `kill "KILL",-$p`
        # that ran after every waitpid. The known PGID is used directly so a descendant that
        # outlives the exited leader is still reaped; then a bounded reap (never Popen.__exit__).
        _kill_group(pgid)
        _reap(proc)


def _main(argv):
    # type: (List[str]) -> None
    if len(argv) != 1 or argv[0].startswith("-"):
        _fail(64, 0, "usage", USAGE)
    pane_id = argv[0]

    sample_ms = _read_env_int("HERDR_WATCH_SAMPLE_MS", 5000, minimum=1, maximum=86400000)
    interval_s = _read_env_int("HERDR_WATCH_INTERVAL_S", 20, minimum=0, maximum=86400)
    start_samples = _read_env_int("HERDR_WATCH_START_SAMPLES", 6, minimum=1, maximum=1000000)
    max_samples = _read_env_int("HERDR_WATCH_MAX_SAMPLES", 135, minimum=1, maximum=1000000)

    started = False
    last = ""
    for i in range(1, max_samples + 1):
        authority, state, last = _sample(pane_id, sample_ms)
        # (a) authority first — lost/never-had authority fails closed
        if authority != 1:
            _fail(4, i, "authority_lost_or_sample_failed", last)
        # (b) authoritative blocked — read the pane to see the blocker
        if state == "blocked":
            _fail(2, i, "blocked", last)
        # (c) any state outside idle|working fails closed (a fast permission/quota prompt
        #     can skip the sampled working state)
        if state not in ("idle", "working"):
            _fail(4, i, "unexpected_state", last)
        if not started:
            # (d) phase 1: wait for the pane to START (working)
            if state == "working":
                started = True
            if i >= start_samples and state == "idle":
                _fail(5, i, "never_started", last)
        else:
            # (e) phase 2: started -> idle
            if state == "idle":
                sys.exit(0)
        # (f) interval
        time.sleep(interval_s)
    # loop exhausted
    _fail(3, max_samples, "max_samples_exhausted", last)


if __name__ == "__main__":
    _main(sys.argv[1:])
