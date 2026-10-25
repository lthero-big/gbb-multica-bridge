"""Offline checks for scripts/restore-reply.sh and its server-side pointer. No network, no real SSH."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
RESTORE = ROOT / 'scripts/restore-reply.sh'
SERVER = ROOT / 'scripts/restore-reply-server.sh'


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

    def test_help_lists_production_defaults(self):
        result = self.run_restore('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in ('152.53.141.33', 'server-root', 'multica-server', '--bot', '--check', '--dry-run'):
            self.assertIn(value, result.stdout)

    def test_dry_run_reuses_install_sh_with_defaults_and_writes_nothing(self):
        result = self.run_restore('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        install = str(ROOT / 'install.sh')
        self.assertIn(f'bash {install} ssh --ssh-alias multica-server --ssh-host 152.53.141.33 --ssh-user root '
                      f'--ssh-port 22 --ssh-key {self.home}/.ssh/id_ed25519', result.stdout)
        self.assertIn(f'bash {install} reply --route server-root --instance-id server-root --bot gbb '
                      '--transport ssh --ssh-alias multica-server --endpoint /root/.local/bin/gbb-multica-endpoint',
                      result.stdout)
        self.assertIn(f'bash {install} reply --route server-root --check', result.stdout)
        self.assertIn('gbb-multica-remote check server-root', result.stdout)
        self.assertEqual(list(self.home.iterdir()), [], 'dry-run must not write anything')

    def test_flags_override_defaults(self):
        result = self.run_restore('--dry-run', '--ssh-host', 'example.com', '--ssh-user', 'alice', '--ssh-port', '2222',
                                  '--ssh-alias', 'multica-alice', '--route', 'host-alice', '--instance-id', 'alice-1',
                                  '--bot', 'bridge', '--endpoint', '/home/alice/.local/bin/gbb-multica-endpoint')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--ssh-host example.com --ssh-user alice --ssh-port 2222', result.stdout)
        self.assertIn('ssh-keyscan -t ed25519 -p 2222 example.com', result.stdout)
        self.assertIn('reply --route host-alice --instance-id alice-1 --bot bridge --transport ssh '
                      '--ssh-alias multica-alice --endpoint /home/alice/.local/bin/gbb-multica-endpoint', result.stdout)

    def test_unknown_argument_fails(self):
        result = self.run_restore('--bogus')
        self.assertEqual(result.returncode, 2)

    def test_host_key_mismatch_is_not_written(self):
        fake = self.home / 'bin'
        fake.mkdir()
        key = self.home / 'hostkey'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', key], check=True)
        scan = fake / 'ssh-keyscan'
        scan.write_text('#!/bin/sh\necho "152.53.141.33 $(cat %s.pub | cut -d" " -f1,2)"\n' % key)
        scan.chmod(0o755)
        env = dict(self.env, PATH=f'{fake}:{self.env["PATH"]}')
        result = self.run_restore(env=env)
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

    def test_server_help_shows_restore_commands(self):
        result = subprocess.run(['bash', SERVER], env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('raw.githubusercontent.com/lthero-big/gbb-multica-bridge/main/scripts/restore-reply.sh', result.stdout)
        self.assertIn('/opt/gbb-multica-bridge/scripts/restore-reply.sh', result.stdout)


if __name__ == '__main__':
    unittest.main()
