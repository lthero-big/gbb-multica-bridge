"""SSH bootstrap tests: temporary homes/keys only, never change host authorized_keys."""
import os
from pathlib import Path
import pwd
import runpy
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import ssh_setup

ROOT = Path(__file__).resolve().parent
INSTALL = runpy.run_path(str(ROOT / 'install.py'))
REMOTE = runpy.run_path(str(ROOT / 'gbb-multica-remote'))
REAL_RUN = subprocess.run


class SSHSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='gbb-ssh-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.mac = self.root / 'mac with space'
        self.mac.mkdir()
        self.server = self.root / 'server'
        (self.server / '.ssh').mkdir(parents=True)
        self.original = '# Existing key without trailing newline\nssh-ed25519 EXISTING_BLOB old-user'
        (self.server / '.ssh/authorized_keys').write_text(self.original)
        self.environment = patch.dict(os.environ, HOME=str(self.mac))
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def setup(self, **changes):
        options = dict(host='server.example', user='alice', port=2222, key_path=None,
                       write_file=INSTALL['write_file'])
        options.update(changes)
        return ssh_setup.setup_ssh('multica-alice', **options)

    def test_once_password_key_only_upload_and_idempotent_config(self):
        calls, installed = [], [False]
        def run(args, **kwargs):
            if args[0] != 'ssh':
                return REAL_RUN(args, **kwargs)
            calls.append((args, kwargs))
            if 'NumberOfPasswordPrompts=1' in args:
                self.assertIn('StrictHostKeyChecking=ask', args)
                body = kwargs['input']
                self.assertTrue(body.startswith('ssh-ed25519 '))
                self.assertNotIn('PRIVATE KEY', body)
                copied = REAL_RUN(['sh', '-c', ssh_setup.INSTALL_PUBLIC_KEY], input=body,
                                  text=True, env=dict(os.environ, HOME=str(self.server)))
                installed[0] = copied.returncode == 0
                return copied
            return subprocess.CompletedProcess(args, 0 if installed[0] else 255)
        (self.mac / '.ssh').mkdir()
        original_config = self.mac / '.ssh/config'
        original_config.write_text('Host personal\n    HostName personal.example\n')
        with patch.object(ssh_setup.subprocess, 'run', side_effect=run), patch.object(sys.stdin, 'isatty', return_value=True):
            result = self.setup()
            identity = Path(result['ssh_connection']['key_path'])
            before = identity.read_bytes()
            repeated = self.setup(key_path=str(identity))
        self.assertEqual(result, repeated)
        self.assertEqual(identity.read_bytes(), before)
        self.assertEqual(identity.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len([c for c, _ in calls if 'NumberOfPasswordPrompts=1' in c]), 1)
        self.assertEqual(original_config.read_text(), 'Host personal\n    HostName personal.example\n')
        authorized = (self.server / '.ssh/authorized_keys').read_text()
        self.assertTrue(authorized.startswith(self.original))
        public_key = ssh_setup.public_part(Path(str(identity) + '.pub').read_text()).strip()
        self.assertEqual(authorized.count(public_key), 1)
        self.assertEqual((self.server / '.ssh/authorized_keys').stat().st_mode & 0o777, 0o600)
        config = Path(result['ssh_config'])
        self.assertEqual(config.stat().st_mode & 0o777, 0o600)
        # Parse the generated configuration with actual OpenSSH, including spaces in HOME.
        parsed = REAL_RUN(['ssh', '-G', '-F', str(config), 'multica-alice'],
                          capture_output=True, text=True, check=True)
        self.assertIn('hostname server.example', parsed.stdout)
        self.assertIn('user alice', parsed.stdout)
        self.assertIn('port 2222', parsed.stdout)
        command = REMOTE['ssh_command'](dict(result, ssh_alias='multica-alice',
                                            instance_id='linux-alice', endpoint='/home/alice/endpoint'), 'check')
        self.assertEqual(command[command.index('-F') + 1], str(config))

    def test_remote_append_deduplicates_even_with_different_comment(self):
        key = self.root / 'identity'
        REAL_RUN(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
        public_key = ssh_setup.public_part(Path(str(key) + '.pub').read_text())
        body = ' '.join(public_key.split()[:2])
        (self.server / '.ssh/authorized_keys').write_text('restrict ' + body + ' another-comment\n')
        before = (self.server / '.ssh/authorized_keys').read_bytes()
        for _ in range(2):
            REAL_RUN(['sh', '-c', ssh_setup.INSTALL_PUBLIC_KEY], input=public_key,
                     text=True, env=dict(os.environ, HOME=str(self.server)), check=True)
        self.assertEqual((self.server / '.ssh/authorized_keys').read_bytes(), before)

    def test_failed_copy_not_retried_and_readonly_flags_rejected(self):
        calls = []
        def run(args, **kwargs):
            if args[0] == 'ssh':
                calls.append(args)
                return subprocess.CompletedProcess(args, 255)
            return REAL_RUN(args, **kwargs)
        with patch.object(ssh_setup.subprocess, 'run', side_effect=run), patch.object(sys.stdin, 'isatty', return_value=True):
            with self.assertRaisesRegex(ValueError, '公钥复制失败'):
                self.setup()
        self.assertEqual(len([c for c in calls if 'NumberOfPasswordPrompts=1' in c]), 1)
        self.assertFalse((self.mac / '.ssh/gbb-multica/multica-alice.conf').exists())
        for flags in (['mac', '--setup-ssh', '--check'], ['server', '--setup-ssh'],
                      ['mac', '--ssh-host', 'server.example'], ['mac', '--setup-ssh', '--ssh-port', '0']):
            result = REAL_RUN(['bash', ROOT / 'install.sh', *flags], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_invalid_connection_input_is_rejected_before_key_creation(self):
        for changed in (dict(host='host; touch /tmp/bad'), dict(user='-oProxyCommand=bad'),
                        dict(port=65536), dict(key_path='/tmp/${BAD}/key')):
            with self.assertRaises(ValueError):
                self.setup(**changed)
        self.assertFalse((self.mac / '.ssh').exists())

    def test_mismatched_public_key_is_never_uploaded(self):
        identity, another = self.root / 'identity', self.root / 'another'
        for key in (identity, another):
            REAL_RUN(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
        Path(str(identity) + '.pub').write_bytes(Path(str(another) + '.pub').read_bytes())
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return REAL_RUN(args, **kwargs)
        with patch.object(ssh_setup.subprocess, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, '公钥与所选私钥不匹配'):
                self.setup(key_path=str(identity))
        self.assertFalse(any(args[0] == 'ssh' for args in calls))
        self.assertEqual((self.server / '.ssh/authorized_keys').read_text(), self.original)

    @unittest.skipUnless(shutil.which('sshd') and os.geteuid() == 0 and Path('/run/sshd').exists(),
                         'isolated local sshd integration requires root and an installed sshd')
    def test_actual_openssh_upload_then_key_login(self):
        host_key, seed = self.root / 'host-key', self.root / 'bootstrap-key'
        for key in (host_key, seed):
            REAL_RUN(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)], check=True)
        authorized = self.server / '.ssh/authorized_keys'
        authorized.write_text(Path(str(seed) + '.pub').read_text())
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = self.root / 'sshd_config'
        config.write_text(f'''Port {port}
ListenAddress 127.0.0.1
HostKey {host_key}
PidFile {self.root / 'sshd.pid'}
AuthorizedKeysFile {authorized}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
PermitRootLogin prohibit-password
AllowUsers {pwd.getpwuid(os.geteuid()).pw_name}
LogLevel ERROR
''')
        # This sshd serves only loopback and a temporary authorized_keys file.
        with (self.root / 'sshd.log').open('w') as log:
            daemon = subprocess.Popen([shutil.which('sshd'), '-D', '-e', '-f', str(config)], stdout=log, stderr=log)
            try:
                for _ in range(100):
                    if daemon.poll() is not None:
                        self.fail('isolated sshd exited: ' + (self.root / 'sshd.log').read_text())
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail('isolated sshd did not become ready')
                ssh_dir = self.mac / '.ssh'
                ssh_dir.mkdir(mode=0o700)
                host_public = ' '.join(Path(str(host_key) + '.pub').read_text().split()[:2])
                (ssh_dir / 'known_hosts').write_text(f'[127.0.0.1]:{port} {host_public}\n')
                def bootstrap_login(args, **kwargs):
                    if args[0] == 'ssh' and 'NumberOfPasswordPrompts=1' in args:
                        # Use a temporary pre-authorized identity in place of a password.
                        # Redirect remote HOME so the script cannot touch the real account.
                        index = args.index('--')
                        args = [*args[:index], '-i', str(seed), *args[index:]]
                        args[-1] = 'env HOME=' + shlex.quote(str(self.server)) + ' ' + args[-1]
                    return REAL_RUN(args, **kwargs)
                with patch.object(ssh_setup.subprocess, 'run', side_effect=bootstrap_login), patch.object(sys.stdin, 'isatty', return_value=True):
                    result = self.setup(host='127.0.0.1', user=pwd.getpwuid(os.geteuid()).pw_name, port=port)
                actual = REAL_RUN(['ssh', '-T', '-F', result['ssh_config'], '--', 'multica-alice', 'printf GBB_SSH_OK'],
                                  capture_output=True, text=True, timeout=15)
                self.assertEqual(actual.returncode, 0, actual.stderr)
                self.assertEqual(actual.stdout, 'GBB_SSH_OK')
                self.assertEqual(authorized.read_text().count('gbb-multica'), 1)
            finally:
                daemon.terminate()
                daemon.wait(timeout=10)


if __name__ == '__main__':
    unittest.main()
