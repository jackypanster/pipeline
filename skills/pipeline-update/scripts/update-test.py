#!/usr/bin/env python3
"""Recovery regression tests using real Git, Bash and disposable installs (no network).

Run with python3 update-test.py [path/to/update.sh]. The optional script path tests an
old version against the same assertions. Python 3.9+, standard library only.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[3]
SCRIPT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).with_name('update.sh')
URL = 'https://github.com/jackypanster/pipeline.git'


def run(args, cwd, env, check=True):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError(f'{args!r} in {cwd}: exit {result.returncode}\n{result.stderr}')
    return result


def snapshot(root):
    """Compare names, symlink targets and bytes; never follow symlinks."""
    result = {}
    for entry in sorted(root.rglob('*')):
        name = str(entry.relative_to(root))
        if entry.is_symlink():
            result[name] = ('link', os.readlink(entry))
        elif entry.is_file():
            result[name] = ('file', entry.read_bytes())
        else:
            result[name] = ('dir',)
    return result


def main():
    with tempfile.TemporaryDirectory(prefix='pipeline update test ') as tmp:
        root = Path(tmp)
        env = dict(os.environ)
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        upstream = root / 'upstream'
        upstream.mkdir()
        shutil.copytree(SOURCE / 'skills', upstream / 'skills')
        run(['git', 'init', '-b', 'main'], upstream, env)
        run(['git', 'config', 'user.name', 'Pipeline test'], upstream, env)
        run(['git', 'config', 'user.email', 'test@example.invalid'], upstream, env)
        run(['git', 'add', 'skills'], upstream, env)
        run(['git', 'commit', '-m', 'fixture base'], upstream, env)
        base = run(['git', 'rev-parse', 'HEAD'], upstream, env).stdout.strip()
        marker = upstream / 'skills/pipeline-review/upstream-marker.txt'
        marker.write_text('updated upstream content\n')
        run(['git', 'add', 'skills'], upstream, env)
        run(['git', 'commit', '-m', 'fixture update'], upstream, env)
        bare = root / 'upstream.git'
        run(['git', 'clone', '--bare', str(upstream), str(bare)], root, env)
        env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0=f'url.{bare.as_uri()}.insteadOf',
                   GIT_CONFIG_VALUE_0=URL)
        failures = []

        def scenario(name, mode, pending=None, current=False, empty=False, source_pending=False):
            case = root / name
            case.mkdir()
            install = case / 'installed skills'
            if empty:
                install.mkdir()
            else:
                shutil.copytree(upstream / 'skills', install)
                if not current:
                    (install / 'pipeline-review/upstream-marker.txt').unlink()
            clone = case / 'consumer'
            if mode == 2:
                run(['git', 'clone', URL, str(clone)], case, env)
                run(['git', 'reset', '--hard', base], clone, env)
            destination = clone / 'skills' if source_pending else install
            if pending == 'backup':
                recovery = destination / '.pipeline-update.txn.interrupted'
                recovery.mkdir()
                backup = recovery / '.bak.pipeline-review'
                backup.mkdir()
                (backup / 'SKILL.md').write_text('unique old recovery content\n')
                (destination / '.pipeline-update.txn.second').mkdir()
            elif pending == 'dangling':
                (destination / '.pipeline-update.txn.dangling').symlink_to('missing-target')
            elif pending == 'symlink':
                recovery = case / 'recovery outside install'
                recovery.mkdir()
                (recovery / 'evidence').write_text('keep linked evidence\n')
                (destination / '.pipeline-update.txn.link').symlink_to(recovery, target_is_directory=True)
            before = snapshot(install)
            source_before = snapshot(clone / 'skills') if mode == 2 else None
            options = dict(env, PIPELINE_CANON_SKILLS=str(install))
            if mode == 2:
                # get-url expands insteadOf, which would select Mode 1. Keep the real
                # pinned remote identity for Mode 2, but forbid HTTPS transport: a
                # recovery refusal must happen BEFORE fetch. No network or fake Git.
                options.update(GIT_CONFIG_COUNT='0', GIT_ALLOW_PROTOCOL='file')
            target = clone / 'skills' if mode == 2 else install
            # Run outside both the source checkout and the destination.
            for attempt in range(2 if pending else 1):
                result = run(['bash', str(SCRIPT), str(target)], case, options, check=False)
                try:
                    if pending:
                        assert result.returncode == 1, f'expected refusal, got {result.returncode}'
                        assert snapshot(install) == before, 'install or recovery evidence changed'
                        paths = list(destination.glob('.pipeline-update.txn.*'))
                        assert paths and all(str(p) in result.stderr for p in paths), 'pending path not reported'
                        assert 'no install changes made this run' in result.stderr
                        assert 'already latest' not in result.stdout
                        if mode == 2:
                            assert snapshot(clone / 'skills') == source_before, 'source skill tree changed'
                            assert run(['git', 'rev-parse', 'HEAD'], clone, env).stdout.strip() == base
                            assert not (clone / '.git/pipeline-update.lock').exists(), 'clone lock leaked'
                    elif mode == 2:
                        assert result.returncode != 0 and "transport 'https' not allowed" in result.stderr
                        assert 'unresolved recovery transaction' not in result.stderr
                        assert snapshot(install) == before
                        assert run(['git', 'rev-parse', 'HEAD'], clone, env).stdout.strip() == base
                        assert not (clone / '.git/pipeline-update.lock').exists(), 'clone lock leaked'
                    else:
                        assert result.returncode == 0, f'update failed: {result.stderr}'
                        assert snapshot(install) == snapshot(upstream / 'skills'), 'install differs from upstream'
                    assert not (install / '.pipeline-update.lock').exists(), 'install lock leaked'
                    if pending == 'symlink':
                        assert (recovery / 'evidence').read_text() == 'keep linked evidence\n'
                except AssertionError as exc:
                    failures.append(f'{name} attempt {attempt + 1}: {exc}')
                    print('FAIL', failures[-1])
                    break
            else:
                print('PASS', name)

        scenario('copy normal refresh', 1)
        scenario('copy already current', 1, current=True)
        scenario('copy pending stale', 1, 'backup')
        scenario('copy pending current', 1, 'backup', current=True)
        scenario('copy only recovery', 1, 'backup', empty=True)
        scenario('copy dangling recovery', 1, 'dangling')
        scenario('copy linked recovery', 1, 'symlink')
        scenario('clone clean reaches fetch', 2)
        scenario('clone canonical pending', 2, 'backup')
        scenario('clone canonical only recovery', 2, 'backup', empty=True)
        scenario('clone own pending', 2, 'backup', source_pending=True)
        if failures:
            raise SystemExit(f'{len(failures)} scenario(s) failed')
    print('11 scenarios passed; pending recovery retried twice; all fixtures removed')


if __name__ == '__main__':
    main()
