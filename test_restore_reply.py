"""Offline checks for scripts/restore-reply.sh and its server-side helper. No network, no real SSH."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
RESTORE = ROOT / 'scripts/restore-reply.sh'
SERVER = ROOT / 'scripts/restore-reply-server.sh'
HOST = 'restore.example.test'
REQUIRED = ('--ssh-host', HOST, '--ssh-user', 'alice', '--route', 'host-alice')


class RestoreReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gbb-restore-test-')
        self.home = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GBB_')}
        self.env['HOME'] = str(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def run_restore(self, *args, env=None):
        return subprocess.run(['bash', RESTORE, *args], env=env or self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)

    def test_scripts_embed_no_deployment_host_or_fingerprint(self):
        for path in (RESTORE, SERVER):
            text = path.read_text()
            self.assertIsNone(re.search(r'\b\d{1,3}(\.\d{1,3}){3}\b', text), f'{path.name} embeds an IPv4 address')
            self.assertIsNone(re.search(r'SHA256:[A-Za-z0-9+/]{43}', text), f'{path.name} embeds a host fingerprint')

    def test_host_user_and_route_are_required(self):
        result = self.run_restore('--dry-run')
        self.assertEqual(result.returncode, 2)
        for flag in ('--ssh-host', '--ssh-user', '--route'):
            self.assertIn(flag, result.stderr.splitlines()[-1])
        result = self.run_restore('--dry-run', '--route', 'host-alice', '--ssh-user', 'alice')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--ssh-host', result.stderr.splitlines()[-1])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_help_uses_placeholders(self):
        result = self.run_restore('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('YOUR_HOST', result.stdout)
        self.assertIn('--host-key-sha256', result.stdout)

    def test_dry_run_reuses_install_sh_and_writes_nothing(self):
        result = self.run_restore('--dry-run', *REQUIRED)
        self.assertEqual(result.returncode, 0, result.stderr)
        install = str(ROOT / 'install.sh')
        self.assertIn(f'bash {install} ssh --ssh-alias multica-host-alice --ssh-host {HOST} --ssh-user alice '
                      f'--ssh-port 22 --ssh-key {self.home}/.ssh/gbb_multica_multica-host-alice_ed25519', result.stdout)
        self.assertIn(f'bash {install} reply --route host-alice --transport ssh --ssh-alias multica-host-alice\n',
                      result.stdout)
        self.assertIn(f'bash {install} reply --route host-alice --check', result.stdout)
        self.assertIn('gbb-multica-remote check host-alice', result.stdout)
        self.assertEqual(list(self.home.iterdir()), [], 'dry-run must not write anything')

    def test_optional_flags_are_forwarded(self):
        result = self.run_restore('--dry-run', *REQUIRED, '--ssh-port', '2222', '--ssh-alias', 'multica-alice',
                                  '--instance-id', 'alice-1', '--bot', 'bridge', '--host-key-sha256', 'SHA256:x',
                                  '--endpoint', '/home/alice/.local/bin/gbb-multica-endpoint')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'--ssh-host {HOST} --ssh-user alice --ssh-port 2222', result.stdout)
        self.assertIn(f'ssh-keyscan -t ed25519 -p 2222 {HOST}', result.stdout)
        self.assertIn('reply --route host-alice --transport ssh --ssh-alias multica-alice --instance-id alice-1 '
                      '--bot bridge --endpoint /home/alice/.local/bin/gbb-multica-endpoint', result.stdout)

    def test_check_needs_only_route(self):
        result = self.run_restore('--check', '--route', 'host-alice', '--repo-dir', str(self.home / 'missing'))
        self.assertNotIn('缺少必填参数', result.stderr)

    def test_unknown_argument_fails(self):
        self.assertEqual(self.run_restore('--bogus').returncode, 2)

    def test_unknown_host_without_fingerprint_stops_when_non_interactive(self):
        result = self.run_restore(*REQUIRED)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--host-key-sha256', result.stderr)
        self.assertFalse((self.home / '.ssh/known_hosts').exists())

    def test_host_key_mismatch_is_not_written(self):
        fake = self.home / 'bin'
        fake.mkdir()
        key = self.home / 'hostkey'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key], check=True)
        scan = fake / 'ssh-keyscan'
        scan.write_text('#!/bin/sh\necho "%s $(cut -d" " -f1,2 %s.pub)"\n' % (HOST, key))
        scan.chmod(0o755)
        env = dict(self.env, PATH=f'{fake}:{self.env["PATH"]}')
        result = self.run_restore(*REQUIRED, '--host-key-sha256', 'SHA256:' + 'A' * 43, env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('主机指纹不符', result.stderr)
        self.assertFalse((self.home / '.ssh/known_hosts').exists())

    def test_server_authorize_is_idempotent_and_validates(self):
        key = self.home / 'k'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key], check=True)
        public = key.with_suffix('.pub').read_text().strip()
        for _ in range(2):
            ok = subprocess.run(['bash', SERVER, 'authorize', public], env=self.env, capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = subprocess.run(['bash', SERVER, 'authorize', 'not a key'], env=self.env, capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        authorized = (self.home / '.ssh/authorized_keys').read_text()
        self.assertEqual(authorized.count(public.split()[1]), 1)
        self.assertEqual(oct((self.home / '.ssh/authorized_keys').stat().st_mode & 0o777), '0o600')

    def test_server_help_uses_placeholders(self):
        result = subprocess.run(['bash', SERVER], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--ssh-host YOUR_HOST --ssh-user YOUR_USER --route YOUR_ROUTE', result.stdout)


if __name__ == '__main__':
    unittest.main()
