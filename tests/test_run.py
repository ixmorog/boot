"""Public launcher tests with fixture credentials and no real network or services.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pty
import select
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "run"
FIRST_COMMIT = "1" * 40
SECOND_COMMIT = "2" * 40
TOKEN = "FIXTURE_ONLY_NOT_A_GITHUB_TOKEN"
CREDENTIAL_ENV = (
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
    "GH_DEBUG", "GH_CONFIG_DIR", "GH_HOST", "GH_NO_UPDATE_NOTIFIER",
)

MOCK = r'''#!__PYTHON__
import io, json, os, pathlib, sys, tarfile, tempfile
root = pathlib.Path(os.environ['BOOT_TEST_ROOT'])
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
scenario = os.environ.get('BOOT_TEST_SCENARIO', 'success')

def record(**extra):
    with (root / 'events.jsonl').open('a') as out:
        out.write(json.dumps({'command': name, 'argv': args, **extra}) + '\n')

def fail(code=41):
    print('synthetic fixture failure', file=sys.stderr)
    sys.exit(code)

def bounded(path):
    path = pathlib.Path(path).absolute()
    if not path.is_relative_to(root):
        raise RuntimeError('Mock path is outside the test directory')
    return path

if name == 'mktemp':
    prefix = pathlib.Path(args[-1]).name.split('XXXX')[0]
    path = pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    record(created=str(path))
    print(path)
elif name == 'curl':
    record()
    if scenario == 'curl': fail()
    url = next(arg for arg in args if arg.startswith('https://'))
    dest = bounded(args[args.index('--output') + 1])
    if not url.endswith('_linux_amd64.tar.gz'):
        raise RuntimeError('Unexpected download URL')
    directory = url.rsplit('/', 1)[1][:-7]
    with tarfile.open(dest, 'w:gz') as archive:
        data = (root / 'mock-command').read_bytes()
        info = tarfile.TarInfo(directory + '/bin/gh')
        info.size, info.mode = len(data), 0o755
        archive.addfile(info, io.BytesIO(data))
elif name == 'sha256sum':
    record()
    if scenario == 'hash': fail()
    sys.stdin.buffer.read()
elif name == 'gh':
    record()
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN', 'GH_DEBUG'):
        if key in os.environ:
            raise RuntimeError('An inherited credential variable reached gh')
    directory = bounded(os.environ['GH_CONFIG_DIR'])
    if directory != root / 'workstation-boot/auth':
        raise RuntimeError('gh is using an unexpected credential directory')
    host_file = directory / 'hosts.yml'
    if args[:2] == ['auth', 'token']:
        if not host_file.is_file() or host_file.read_text() != '__TOKEN__\n':
            sys.exit(1)
        print('__TOKEN__')
    elif args[:2] == ['auth', 'login']:
        directory.mkdir(parents=True, exist_ok=True)
        host_file.write_text('incomplete fixture\n' if scenario in {'auth', 'missing-token'} else '__TOKEN__\n')
        if scenario == 'auth': fail()
    elif args and args[0] == 'api':
        endpoint = next(arg for arg in args if 'repos/' in arg)
        if '/commits/main' in endpoint:
            if scenario == 'resolve': fail()
            print('invalid-commit' if scenario == 'invalid-commit' else os.environ['BOOT_TEST_COMMIT'])
        elif '/tarball/' in endpoint:
            if scenario == 'download': fail()
            if scenario == 'archive':
                sys.stdout.buffer.write(b'not a tar archive')
                sys.exit(0)
            commit = endpoint.rsplit('/', 1)[1]
            script = ('#!/usr/bin/env bash\n'
                '[[ $(umask) == 0022 ]] || exit 86\n'
                'for key in GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN GITHUB_ENTERPRISE_TOKEN GH_DEBUG GH_CONFIG_DIR GH_HOST GH_NO_UPDATE_NOTIFIER; do\n'
                '    [[ ! -v $key ]] || exit 87\n'
                'done\n'
                'for item in "$BOOT_TEST_ROOT"/workstation-fetch.*; do [[ ! -e $item ]] || exit 88; done\n'
                '[[ -f "$BOOT_TEST_ROOT/workstation-boot/auth/hosts.yml" ]] || exit 89\n'
                f'printf "%s\\n" "{commit}" >> "$BOOT_TEST_ROOT/bootstrap-ran"\n'
                'exit "${BOOT_TEST_CHILD_STATUS:-0}"\n').encode()
            with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
                for path, data in [('workstation/bootstrap', script), ('workstation/install', b'#!/bin/sh\nexit 99\n')]:
                    info = tarfile.TarInfo('fixture-repo/' + path)
                    info.size, info.mode = len(data), 0o755
                    archive.addfile(info, io.BytesIO(data))
        else:
            raise RuntimeError('Unexpected API endpoint')
    else:
        raise RuntimeError('Unexpected gh invocation, including remote logout or auth status')
else:
    record()
    raise RuntimeError('Forbidden real command: ' + name)
'''.replace('__PYTHON__', sys.executable).replace('__TOKEN__', TOKEN)


class PublicLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='workstation-launcher-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.session = self.root / 'workstation-boot'
        self.auth = self.session / 'auth'
        commands = self.root / 'bin'
        commands.mkdir()
        executable = self.root / 'mock-command'
        executable.write_text(MOCK)
        executable.chmod(0o755)
        for name in ('curl', 'sha256sum', 'mktemp', 'gh', 'wget', 'git', 'pacman', 'sudo',
                     'ssh', 'scp', 'systemctl', 'timedatectl', 'loginctl', 'mount', 'umount'):
            (commands / name).symlink_to(executable)
        self.env = {**os.environ, 'PATH': str(commands) + ':/usr/bin:/bin',
                    'BOOT_TEST_ROOT': str(self.root), 'BOOT_TEST_COMMIT': FIRST_COMMIT,
                    'TERM': 'xterm-256color'}
        for key in CREDENTIAL_ENV:
            self.env.pop(key, None)
        # Deliberately inject fake inherited credentials to test isolation.
        for key in CREDENTIAL_ENV:
            self.env[key] = 'INHERITED_FIXTURE_MUST_NOT_BE_USED'
        self.wrapper = self.root / 'wrapper'
        self.wrapper.write_text('source "$1"\nshift\n'
            'require_live() { [[ ${BOOT_TEST_LIVE_FAILURE:-0} == 0 ]]; }\n'
            'session_path() { printf "%s\\n" "$BOOT_TEST_ROOT/workstation-boot"; }\n'
            'main "$@"\n')

    def events(self):
        path = self.root / 'events.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def gh_calls(self, prefix):
        return [event for event in self.events()
                if event['command'] == 'gh' and event['argv'][:len(prefix)] == prefix]

    def seed_auth(self):
        self.auth.mkdir(mode=0o700, parents=True)
        self.session.chmod(0o700)
        host_file = self.auth / 'hosts.yml'
        host_file.write_text(TOKEN + '\n')
        host_file.chmod(0o600)

    def invoke(self, scenario='success', *, args=(), terminal=True, piped_stdin=False):
        self.env['BOOT_TEST_SCENARIO'] = scenario
        argv = ['/usr/bin/bash', str(self.wrapper), str(SCRIPT), *args]
        if not terminal:
            result = subprocess.run(argv, env=self.env, cwd=self.root, input=b'',
                                    capture_output=True, timeout=10)
            return result.returncode, (result.stdout + result.stderr).decode(errors='replace')
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(argv, env=self.env, cwd=self.root,
                stdin=subprocess.PIPE if piped_stdin else slave, stdout=slave, stderr=slave)
            os.close(slave)
            slave = -1
            if process.stdin:
                process.stdin.close()
            output = bytearray()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    output.extend(data)
                elif process.poll() is not None:
                    break
            else:
                process.kill()
                process.wait()
                self.fail('Launcher test exceeded timeout')
            return process.wait(timeout=2), output.decode(errors='replace')
        finally:
            os.close(master)
            if slave != -1:
                os.close(slave)

    def assert_saved_auth(self):
        self.assertEqual((self.auth / 'hosts.yml').read_text(), TOKEN + '\n')
        self.assertEqual(self.session.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.auth.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.auth / 'hosts.yml').stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.root.glob('workstation-fetch.*')), [])

    def test_success_preserves_private_auth_and_child_has_no_credential_environment(self):
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assert_saved_auth()
        self.assertEqual((self.root / 'bootstrap-ran').read_text().splitlines(), [FIRST_COMMIT])
        self.assertNotIn(TOKEN, output)
        self.assertEqual(len(self.gh_calls(['auth', 'login'])), 1)

    def test_second_run_reuses_login_but_downloads_current_main(self):
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.env['BOOT_TEST_COMMIT'] = SECOND_COMMIT
        code, output = self.invoke()
        self.assertEqual(code, 0, output)
        self.assert_saved_auth()
        self.assertEqual(len(self.gh_calls(['auth', 'login'])), 1)
        api = self.gh_calls(['api'])
        self.assertEqual(sum('/commits/main' in event['argv'][1] for event in api), 2)
        self.assertEqual([event['argv'][1].rsplit('/', 1)[1] for event in api
                          if '/tarball/' in event['argv'][1]], [FIRST_COMMIT, SECOND_COMMIT])
        self.assertEqual((self.root / 'bootstrap-ran').read_text().splitlines(),
                         [FIRST_COMMIT, SECOND_COMMIT])

    def test_child_failure_preserves_login_and_original_exit_status(self):
        self.env['BOOT_TEST_CHILD_STATUS'] = '31'
        code, output = self.invoke()
        self.assertEqual(code, 31, output)
        self.assert_saved_auth()
        self.assertNotIn(TOKEN, output)

    def test_failures_after_login_preserve_auth_and_do_not_launch_installer(self):
        for scenario in ('resolve', 'invalid-commit', 'download', 'archive'):
            with self.subTest(scenario=scenario):
                code, output = self.invoke(scenario)
                self.assertNotEqual(code, 0, output)
                self.assert_saved_auth()
                self.assertFalse((self.root / 'bootstrap-ran').exists())
                self.assertNotIn(TOKEN, output)
        self.assertEqual(len(self.gh_calls(['auth', 'login'])), 1)

    def test_cli_download_and_checksum_failures_preserve_existing_auth(self):
        self.seed_auth()
        for scenario in ('curl', 'hash'):
            with self.subTest(scenario=scenario):
                code, output = self.invoke(scenario)
                self.assertNotEqual(code, 0, output)
                self.assert_saved_auth()
                self.assertFalse((self.root / 'bootstrap-ran').exists())
        self.assertEqual(self.gh_calls(['auth', 'login']), [])

    def test_incomplete_login_is_removed(self):
        for scenario in ('auth', 'missing-token'):
            with self.subTest(scenario=scenario):
                code, output = self.invoke(scenario)
                self.assertNotEqual(code, 0, output)
                self.assertFalse(self.auth.exists())
                self.assertEqual(list(self.root.glob('workstation-fetch.*')), [])
                self.assertFalse((self.root / 'bootstrap-ran').exists())
                self.assertNotIn(TOKEN, output)

    def test_live_and_terminal_guards_run_before_writes_or_downloads(self):
        self.env['BOOT_TEST_LIVE_FAILURE'] = '1'
        code, _ = self.invoke()
        self.assertNotEqual(code, 0)
        self.env['BOOT_TEST_LIVE_FAILURE'] = '0'
        for terminal, piped in ((False, False), (True, True)):
            with self.subTest(terminal=terminal, piped=piped):
                code, _ = self.invoke(terminal=terminal, piped_stdin=piped)
                self.assertNotEqual(code, 0)
        self.assertEqual(self.events(), [])
        self.assertFalse(self.session.exists())

    def test_unknown_arguments_fail_before_writes(self):
        code, _ = self.invoke(args=('--unknown',), terminal=False)
        self.assertEqual(code, 2)
        self.assertFalse(self.session.exists())
        self.assertEqual(self.events(), [])

    def test_logout_needs_no_terminal_and_only_removes_local_auth(self):
        self.seed_auth()
        marker = self.session / 'keep-this-file'
        marker.write_text('fixture')
        code, output = self.invoke(args=('--logout',), terminal=False)
        self.assertEqual(code, 0, output)
        self.assertFalse(self.auth.exists())
        self.assertTrue(marker.exists())
        self.assertEqual(self.events(), [])
        self.assertFalse((self.root / 'bootstrap-ran').exists())
        code, output = self.invoke(args=('--logout',), terminal=False)
        self.assertEqual(code, 0, output)
        self.assertEqual(self.events(), [])

    def test_logout_without_session_does_not_create_it(self):
        code, output = self.invoke(args=('--logout',), terminal=False)
        self.assertEqual(code, 0, output)
        self.assertFalse(self.session.exists())
        self.assertEqual(self.events(), [])

    def test_logout_still_requires_live_guard(self):
        self.seed_auth()
        self.env['BOOT_TEST_LIVE_FAILURE'] = '1'
        code, _ = self.invoke(args=('--logout',), terminal=False)
        self.assertNotEqual(code, 0)
        self.assert_saved_auth()
        self.assertEqual(self.events(), [])

    def test_session_and_auth_symlinks_are_rejected_for_run_and_logout(self):
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        marker = outside / 'keep'
        marker.write_text('fixture')
        for target in (self.session, self.auth):
            with self.subTest(path=target.name):
                if target == self.auth:
                    self.session.mkdir(mode=0o700)
                target.symlink_to(outside, target_is_directory=True)
                for args in ((), ('--logout',)):
                    code, _ = self.invoke(args=args)
                    self.assertNotEqual(code, 0)
                    self.assertTrue(target.is_symlink())
                    self.assertTrue(marker.exists())
                target.unlink()
        self.assertEqual(self.events(), [])

    def test_nonprivate_session_and_auth_permissions_are_rejected(self):
        for target in (self.session, self.auth):
            with self.subTest(path=target.name):
                if not self.session.exists():
                    self.session.mkdir(mode=0o700)
                if target == self.auth:
                    self.auth.mkdir(mode=0o700)
                target.chmod(0o755)
                for args in ((), ('--logout',)):
                    code, _ = self.invoke(args=args)
                    self.assertNotEqual(code, 0)
                    self.assertTrue(target.exists())
                target.chmod(0o700)
        self.assertEqual(self.events(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
