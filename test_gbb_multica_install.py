"""Real offline npm install + fake Multica and SSH. Never call production Bots/APIs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_gbb_multica_setup import FAKE_CLI

ROOT = Path(__file__).resolve().parent
INSTALL = runpy.run_path(str(ROOT / 'install.py'))
REMOTE = runpy.run_path(str(ROOT / 'gbb-multica-remote'))


class InstallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='gbb-install-test-')
        cls.root = Path(cls.tmp.name)
        cls.fake = cls.root / 'fake-bin'
        cls.fake.mkdir()
        for name, source in {
            'multica': FAKE_CLI.replace('setup-test-workspace', 'install-test-workspace').replace('setup-test-daemon', 'install-test-daemon'),
            'ssh': '''#!/usr/bin/env python3
import json,os,shlex,subprocess,sys
from pathlib import Path
args=sys.argv[1:]; alias=args[args.index('--')+1]; command=args[-1]
mapping=json.loads(Path(os.environ['FAKE_SSH_MAP']).read_text())
if alias not in mapping: sys.exit(9)
with open(os.environ['FAKE_SSH_LOG'],'a') as log: log.write(alias+'\\n')
target=mapping[alias]
env=dict(os.environ,HOME=target['home'],GBB_MULTICA_CONFIG_HOME=target['config'])
if command.startswith('test -x '):
 print(str(Path(target['home'])/'.local/bin/gbb-multica-endpoint'));sys.exit(0)
sys.exit(subprocess.run(shlex.split(command),env=env).returncode)
'''
        }.items():
            item = cls.fake / name
            item.write_text(source)
            item.chmod(0o755)
        cls.alice = cls.root / 'alice'
        cls.alice.mkdir()
        home = cls.alice / '.grok-bot-bridge'
        home.mkdir()
        (home / 'config.json').write_text(json.dumps({'hubBot': 'bridge-alice', 'defaultBot': 'bridge-alice'}))
        (home / 'credentials.json').write_text(json.dumps({'bridge-alice': {'url': 'https://example.invalid/no-network', 'key': 'OFFLINE_SECRET_NEVER_PRINT'}}))
        cls.state = cls.root / 'state.json'
        cls.state.write_text(json.dumps({'daemon_pid': os.getpid(), 'calls': [], 'writes': [], 'profiles': [], 'agents': [], 'env': {}}))
        # Deliberately exclude every inherited Multica token and webhook override.
        cls.env = {'HOME': str(cls.alice), 'PATH': str(cls.fake) + os.pathsep + os.environ['PATH'],
                   'LANG': 'C.UTF-8', 'FAKE_MULTICA_STATE': str(cls.state)}
        cls.command = ['bash', str(ROOT / 'install.sh'), 'server', '--instance-id', 'alice',
                       '--bot', 'bridge-alice', '--profile', 'alice-login']
        first = subprocess.run(cls.command, env=cls.env, capture_output=True, text=True, timeout=90)
        assert first.returncode == 0, (first.stdout, first.stderr)
        assert 'OFFLINE_SECRET_NEVER_PRINT' not in first.stdout + first.stderr
        cls.server = json.loads((cls.alice / '.config/gbb-multica/server.json').read_text())
        cls.mac_home = cls.root / 'mac'
        cls.mac_home.mkdir()
        cls.ssh_map = cls.root / 'ssh.json'
        cls.ssh_log = cls.root / 'ssh.log'
        cls.bob = cls.root / 'bob'
        cls.bob.mkdir()
        (cls.bob / '.config/gbb-multica').mkdir(parents=True)
        cls.bob_gbb = cls.bob / 'gbb with space'
        cls.bob_gbb.mkdir()
        for name in ('config.json', 'credentials.json'):
            shutil.copy2(home / name, cls.bob_gbb / name)
        bob_config = dict(cls.server, instance_id='bob', gbb_home=str(cls.bob_gbb), user='bob')
        (cls.bob / '.config/gbb-multica/server.json').write_text(json.dumps(bob_config))
        cls.ssh_map.write_text(json.dumps({
            'server-alice': {'home': str(cls.alice), 'config': str(cls.alice / '.config/gbb-multica')},
            'server-bob': {'home': str(cls.bob), 'config': str(cls.bob / '.config/gbb-multica')},
        }))

    @classmethod
    def tearDownClass(cls):
        lock = Path(pwd.getpwuid(os.geteuid()).pw_dir) / '.local/state/gbb-multica/install-test-workspace-install-test-daemon.lock'
        lock.unlink(missing_ok=True)
        cls.tmp.cleanup()

    def test_server_offline_install_replay_and_check(self):
        before = json.loads(self.state.read_text())
        self.assertEqual(before['env']['GBB_REPLY_ROUTE'], 'alice')
        self.assertEqual(before['env']['GBB_INSTANCE_ID'], 'alice')
        self.assertEqual(set(before['login_profiles']), {'alice-login'})
        marker = self.alice / '.config/gbb-multica/server.json'
        self.assertEqual(marker.stat().st_mode & 0o777, 0o600)
        for suffix in ([], ['--check']):
            result = subprocess.run(self.command + suffix, env=self.env, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
        after = json.loads(self.state.read_text())
        self.assertEqual(after['writes'].count('agent.create'), 1)
        self.assertEqual(after['writes'].count('profile.create'), 1)
        self.assertEqual(hashlib.sha256((ROOT / 'vendor/grok-bot-bridge-0.2.0.tgz').read_bytes()).hexdigest(), INSTALL['ARCHIVE_SHA256'])

    def test_mac_routes_answers_and_instance_guard(self):
        config_root = self.mac_home / '.config/gbb-multica'
        bin_dir = self.mac_home / '.local/bin'
        environment = dict(self.env, HOME=str(self.mac_home), FAKE_SSH_MAP=str(self.ssh_map), FAKE_SSH_LOG=str(self.ssh_log))
        args = argparse.Namespace(route='alice', instance_id='alice', ssh_alias='server-alice', endpoint=None,
                                  bot='bridge-alice', check=False)
        with patch.dict(os.environ, environment, clear=True), patch.object(INSTALL['platform'], 'system', return_value='Darwin'):
            INSTALL['mac'](args, config_root, bin_dir)
            args.route = args.instance_id = 'bob'
            args.ssh_alias, args.endpoint, args.bot = 'server-bob', self.server['wrapper'].replace('gbb-multica-agent', 'gbb-multica-endpoint'), 'bridge-bob'
            INSTALL['mac'](args, config_root, bin_dir)
        routes = json.loads((config_root / 'routes.json').read_text())['routes']
        self.assertEqual(set(routes), {'alice', 'bob'})
        prompt = (config_root / 'grok-bot-prompt.md').read_text()
        self.assertNotIn('{{', prompt)
        self.assertIn('server-alice', prompt)
        self.assertIn('server-bob', prompt)
        reply = '中文\n{"final":"保留 $HOME、`字面量`、引号"}\n第二行'
        for name, home in (('alice', Path(self.server['gbb_home'])), ('bob', self.bob_gbb)):
            messages = home / 'messages'
            messages.mkdir(exist_ok=True)
            (messages / 'mtest123.json').write_text(json.dumps({'id': 'mtest123', 'question': name, 'askedAt': '2026-01-01'}))
        helper = bin_dir / 'gbb-multica-remote'
        log_before = self.ssh_log.read_bytes()
        unknown = subprocess.run([helper, 'answer', 'unknown', 'mtest123', '-'], input='MUST_NOT_SEND',
                                 env=environment, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(unknown.returncode, 0)
        self.assertEqual(self.ssh_log.read_bytes(), log_before)
        sent = subprocess.run([helper, 'answer', 'alice', 'mtest123', '-'], input=reply,
                              env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertEqual(json.loads((Path(self.server['gbb_home']) / 'messages/mtest123.json').read_text())['answer'], reply)
        self.assertNotIn('answer', json.loads((self.bob_gbb / 'messages/mtest123.json').read_text()))
        sent = subprocess.run([helper, 'answer', 'bob', 'mtest123', '-'], input='BOB_OK',
                              env=environment, capture_output=True, text=True, timeout=15)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertEqual(json.loads((self.bob_gbb / 'messages/mtest123.json').read_text())['answer'], 'BOB_OK')
        routes['alice']['instance_id'] = 'wrong-instance'
        (config_root / 'routes.json').write_text(json.dumps({'routes': routes}))
        rejected = subprocess.run([helper, 'answer', 'alice', 'mtest123', '-'], input='MUST_NOT_WRITE',
                                  env=environment, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(json.loads((Path(self.server['gbb_home']) / 'messages/mtest123.json').read_text())['answer'], reply)

    def test_router_validation_and_shell_quoting(self):
        route = dict(ssh_alias='server-alice', instance_id='alice', endpoint="/home/a user/bin/gbb's endpoint")
        command = REMOTE['ssh_command'](route, 'answer', 'm1234')
        self.assertEqual(shlex.split(command[-1]), [route['endpoint'], '--instance-id', 'alice', 'answer', 'm1234', '-'])
        for changed in ({'ssh_alias': '-oProxyCommand=bad'}, {'instance_id': 'a;bad'}, {'endpoint': '/tmp/x\nbad'}):
            with self.assertRaises(ValueError):
                REMOTE['ssh_command'](dict(route, **changed), 'check')
        with self.assertRaises(ValueError):
            REMOTE['ssh_command'](route, 'answer', '../../config')

    def test_new_credentials_use_private_store_without_sending(self):
        home = self.root / 'fresh-gbb'
        home.mkdir(exist_ok=True, mode=0o700)
        with patch.object(INSTALL['sys'].stdin, 'isatty', return_value=True), \
                patch('builtins.input', return_value='https://example.invalid/webhook'), \
                patch.object(INSTALL['getpass'], 'getpass', return_value='FRESH_OFFLINE_KEY'):
            INSTALL['configure_credentials'](shutil.which('node'), Path(self.server['gbb_bin']), home, 'bridge-fresh')
        credential = json.loads((home / 'credentials.json').read_text())['bridge-fresh']
        self.assertEqual(credential['key'], 'FRESH_OFFLINE_KEY')
        self.assertEqual((home / 'credentials.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((home / 'config.json').read_text())['hubBot'], 'bridge-fresh')
        self.assertFalse((home / 'messages').exists())
        self.assertFalse((home / 'notify.log').exists())

    def test_dry_run_and_task_install_guard(self):
        result = subprocess.run(['bash', ROOT / 'install.sh', 'mac', '--dry-run'],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        before = self.state.read_bytes()
        result = subprocess.run(self.command, env=dict(self.env, MULTICA_TASK_ID='task-test'),
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
