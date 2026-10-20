"""Real offline npm install + fake Multica and SSH. Never call production Bots/APIs."""
import argparse
from contextlib import redirect_stdout
import hashlib
import io
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
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), patch.object(INSTALL['platform'], 'system', return_value='Darwin'), \
                redirect_stdout(output):
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
        self.assertIn('========== Grok Bot 提示词开始 ==========\n' + prompt.rstrip() +
                      '\n========== Grok Bot 提示词结束 ==========', output.getvalue())
        self.assertIn(shlex.quote(str(bin_dir / 'gbb-multica-remote')) + ' answer', prompt)
        self.assertNotIn('OFFLINE_SECRET_NEVER_PRINT', output.getvalue())
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
        with patch.object(INSTALL['Path'], 'read_text', return_value='{{UNFILLED_FIELD}}'):
            with self.assertRaisesRegex(ValueError, '占位符'):
                INSTALL['prompt_for_routes']({}, '/home/alice/.local/bin/gbb-multica-remote')

    def test_mac_bootstrap_config_is_saved_and_used_for_all_ssh(self):
        config_root = self.mac_home / 'bootstrap-config'
        bin_dir = self.mac_home / 'bootstrap-bin'
        managed = str(self.mac_home / '.ssh/gbb-multica/server-alice.conf')
        setup_result = {'ssh_config': managed, 'ssh_connection': {
            'host': 'server.example', 'user': 'alice', 'port': 2222, 'key_path': '/tmp/fake-identity'}}
        environment = dict(self.env, HOME=str(self.mac_home), FAKE_SSH_MAP=str(self.ssh_map), FAKE_SSH_LOG=str(self.ssh_log))
        args = argparse.Namespace(route='bootstrapped-alice', instance_id='alice', ssh_alias='server-alice',
                                  endpoint=None, bot='bridge-alice', check=False, setup_ssh=True,
                                  ssh_host='server.example', ssh_user='alice', ssh_port=2222, ssh_key='/tmp/fake-identity')
        calls = []
        original_run = INSTALL['run']
        def run(arguments, **kwargs):
            calls.append(arguments)
            return original_run(arguments, **kwargs)
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(INSTALL['platform'], 'system', return_value='Darwin'), \
                patch('ssh_setup.setup_ssh', return_value=setup_result) as bootstrap, \
                patch.dict(INSTALL['mac'].__globals__, run=run):
            INSTALL['mac'](args, config_root, bin_dir)
            bootstrap.assert_called_once()
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call[call.index('-F') + 1], managed)
        route = json.loads((config_root / 'routes.json').read_text())['routes']['bootstrapped-alice']
        self.assertEqual(route['ssh_config'], managed)
        self.assertEqual(route['ssh_connection'], setup_result['ssh_connection'])

    def test_local_reply_on_same_host_without_ssh(self):
        environment = dict(self.env)
        command = ['bash', ROOT / 'install.sh', 'reply', '--route', 'alice',
                   '--instance-id', 'alice']
        before = self.ssh_log.read_bytes() if self.ssh_log.exists() else b''
        configured = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(configured.returncode, 0, configured.stderr)
        config_root = self.alice / '.config/gbb-multica'
        routes_file = config_root / 'routes.json'
        route = json.loads(routes_file.read_text())['routes']['alice']
        self.assertEqual(route['transport'], 'local')
        self.assertEqual(route['bot'], 'bridge-alice')
        self.assertNotIn('ssh_alias', route)
        self.assertIn('local (same machine and user)', (config_root / 'grok-bot-prompt.md').read_text())
        original = routes_file.read_bytes()
        checked = subprocess.run(command + ['--check'], env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(routes_file.read_bytes(), original)
        messages = Path(self.server['gbb_home']) / 'messages'
        messages.mkdir(exist_ok=True)
        message = messages / 'mlocal123.json'
        message.write_text(json.dumps({'id': 'mlocal123', 'question': 'local', 'askedAt': '2026-01-01'}))
        helper = self.alice / '.local/bin/gbb-multica-remote'
        answer = '同机回填\n保留 $HOME 和 `内容`'
        sent = subprocess.run([helper, 'answer', 'alice', 'mlocal123', '-'],
                              env=environment, input=answer, capture_output=True, text=True, timeout=15)
        self.assertEqual(sent.returncode, 0, sent.stderr)
        self.assertEqual(json.loads(message.read_text())['answer'], answer)
        self.assertEqual(self.ssh_log.read_bytes() if self.ssh_log.exists() else b'', before)
        wrong = subprocess.run(command + ['--instance-id', 'wrong'], env=environment,
                               capture_output=True, text=True, timeout=20)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertEqual(routes_file.read_bytes(), original)
        canonical = list(self.command)
        canonical[2] = 'runtime'
        checked = subprocess.run(canonical + ['--check'], env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_combined_local_install_replay_and_check_without_ssh(self):
        home = self.root / 'desktop'
        home.mkdir()
        store = home / 'gbb store'
        store.mkdir()
        (store / 'config.json').write_text(json.dumps({'hubBot': 'bridge-desktop', 'defaultBot': 'bridge-desktop'}))
        (store / 'credentials.json').write_text(json.dumps({'bridge-desktop': {
            'url': 'https://example.invalid/no-network', 'key': 'LOCAL_OFFLINE_ONLY'}}))
        state = home / 'state.json'
        state.write_text(json.dumps({'daemon_pid': os.getpid(), 'calls': [], 'writes': [], 'profiles': [], 'agents': [], 'env': {}}))
        config_root = home / 'config store'
        env = dict(self.env, HOME=str(home), FAKE_MULTICA_STATE=str(state),
                   GBB_MULTICA_CONFIG_HOME=str(config_root), FAKE_SSH_LOG=str(self.ssh_log))
        command = ['bash', ROOT / 'install.sh', 'local', '--instance-id', 'desktop', '--bot', 'bridge-desktop',
                   '--gbb-home', str(store), '--reply-route', 'desktop-reply', '--profile', 'desktop-login']
        before = self.ssh_log.read_bytes() if self.ssh_log.exists() else b''
        for _ in range(2):
            result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('LOCAL_OFFLINE_ONLY', result.stdout + result.stderr)
            prompt = (config_root / 'grok-bot-prompt.md').read_text()
            self.assertIn('========== Grok Bot 提示词开始 ==========\n' + prompt.rstrip() +
                          '\n========== Grok Bot 提示词结束 ==========', result.stdout)
            self.assertIn(shlex.quote(str(home / '.local/bin/gbb-multica-remote')) + ' answer', prompt)
            self.assertNotIn('/实际路径/', prompt)
            self.assertIn('Please rename yourself to "bridge-desktop".', prompt)
            self.assertIn('Routing: if "to" names another Bot, you are the hub.', prompt)
            self.assertIn('How to run local agents on my computer', prompt)
            self.assertIn('GBB_TOOL_PROTOCOL_V1', prompt)
            self.assertFalse(any('\u4e00' <= char <= '\u9fff' for char in prompt))
        saved = json.loads((config_root / 'server.json').read_text())
        route = json.loads((config_root / 'routes.json').read_text())['routes']['desktop-reply']
        self.assertEqual((route['transport'], route['instance_id'], route['bot']), ('local', 'desktop', 'bridge-desktop'))
        self.assertNotIn('ssh_alias', route)
        self.assertFalse((home / '.ssh').exists())
        self.assertFalse((config_root / 'ssh.json').exists())
        # Installation/check must not send a Grok request or create a local message.
        self.assertFalse((store / 'messages').exists())
        self.assertNotIn('{{', (config_root / 'grok-bot-prompt.md').read_text())
        snapshot = {p.name: p.read_bytes() for p in config_root.iterdir() if p.is_file()}
        checked = subprocess.run(command + ['--check'], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertNotIn('Grok Bot 提示词开始', checked.stdout)
        # The macOS branch uses the same portable ps command; real Mac validation is separate.
        with patch.dict(os.environ, env, clear=True), patch.object(INSTALL['platform'], 'system', return_value='Darwin'), \
                patch.object(INSTALL['sys'], 'argv', ['install.py', *command[2:], '--check']):
            self.assertEqual(INSTALL['main'](), 0)
        self.assertEqual(snapshot, {p.name: p.read_bytes() for p in config_root.iterdir() if p.is_file()})
        routes_path = config_root / 'routes.json'
        routes_path.write_text(json.dumps({'routes': {}}))
        missing_route = subprocess.run(command + ['--check'], env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(missing_route.returncode, 0)
        self.assertIn('本机回填路由缺失', missing_route.stderr)
        self.assertEqual(json.loads(routes_path.read_text()), {'routes': {}})
        routes_path.write_bytes(snapshot['routes.json'])
        after = json.loads(state.read_text())
        self.assertEqual(after['writes'].count('profile.create'), 1)
        self.assertEqual(after['writes'].count('agent.create'), 1)
        self.assertEqual(after['env']['GBB_REPLY_ROUTE'], 'desktop-reply')
        messages = store / 'messages'
        messages.mkdir()
        message = messages / 'mdesktop123.json'
        message.write_text(json.dumps({'id': 'mdesktop123', 'question': 'local test', 'askedAt': '2026-01-01'}))
        answer = '本机成功\n$HOME 和 `内容` 保持原样'
        replied = subprocess.run([home / '.local/bin/gbb-multica-remote', 'answer', 'desktop-reply', 'mdesktop123', '-'],
                                 env=env, input=answer, capture_output=True, text=True, timeout=15)
        self.assertEqual(replied.returncode, 0, replied.stderr)
        self.assertEqual(json.loads(message.read_text())['answer'], answer)
        self.assertEqual(saved['gbb_home'], str(store))
        self.assertEqual(self.ssh_log.read_bytes() if self.ssh_log.exists() else b'', before)

    def test_independent_ssh_then_reply_reuses_connection(self):
        config_root = self.mac_home / 'independent-config'
        bin_dir = self.mac_home / 'independent-bin'
        managed = str(self.mac_home / '.ssh/gbb-multica/server-alice.conf')
        metadata = {'ssh_config': managed, 'ssh_connection': {
            'host': 'example.com', 'user': 'alice', 'port': 22, 'key_path': '/tmp/test-private'}}
        environment = dict(self.env, HOME=str(self.mac_home), FAKE_SSH_MAP=str(self.ssh_map), FAKE_SSH_LOG=str(self.ssh_log))
        parser = INSTALL['build_parser']()
        args = parser.parse_args(['ssh', '--ssh-alias', 'server-alice', '--ssh-host', 'example.com', '--ssh-user', 'alice'])
        with patch.dict(os.environ, environment, clear=True), patch('ssh_setup.setup_ssh', return_value=metadata):
            INSTALL['ssh'](args, config_root, bin_dir)
        self.assertFalse((config_root / 'server.json').exists())
        self.assertFalse((config_root / 'routes.json').exists())
        self.assertFalse(bin_dir.exists())
        self.assertEqual(json.loads((config_root / 'ssh.json').read_text())['connections']['server-alice'], metadata)
        original = (config_root / 'ssh.json').read_bytes()
        args.check = True
        with patch.dict(os.environ, environment, clear=True):
            with patch.dict(INSTALL['ssh'].__globals__, run=lambda *a, **kw: subprocess.CompletedProcess(a[0], 0)):
                INSTALL['ssh'](args, config_root, bin_dir)
        self.assertEqual((config_root / 'ssh.json').read_bytes(), original)
        reply_args = parser.parse_args(['reply', '--route', 'ssh-alice', '--instance-id', 'alice', '--ssh-alias', 'server-alice'])
        calls = []
        original_run = INSTALL['run']
        def run(arguments, **kwargs):
            calls.append(arguments)
            return original_run(arguments, **kwargs)
        with patch.dict(os.environ, environment, clear=True), patch.dict(INSTALL['reply'].__globals__, run=run):
            INSTALL['reply'](reply_args, config_root, bin_dir)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call[call.index('-F') + 1], managed)
        self.assertEqual(json.loads((config_root / 'routes.json').read_text())['routes']['ssh-alice']['ssh_config'], managed)

    def test_interactive_cancel_and_optional_profile(self):
        root = self.root / 'cancel-wizard'
        env = dict(self.env, GBB_MULTICA_CONFIG_HOME=str(root))
        with patch.dict(os.environ, env, clear=True), patch.object(INSTALL['sys'].stdin, 'isatty', return_value=True), \
                patch.object(INSTALL['sys'], 'argv', ['install.py']), \
                patch('builtins.input', side_effect=['1', 'node', 'bridge', '', '', 'Original Agent', '', '', 'n']):
            self.assertEqual(INSTALL['main'](), 0)
        self.assertFalse(root.exists())
        with patch.dict(os.environ, env, clear=True), patch.object(INSTALL['sys'].stdin, 'isatty', return_value=True), \
                patch.object(INSTALL['sys'], 'argv', ['install.py', 'ssh']), \
                patch('builtins.input', side_effect=['test-alias', 'example.com', 'alice', '', '', 'n']):
            self.assertEqual(INSTALL['main'](), 0)
        self.assertFalse(root.exists())
        args = INSTALL['build_parser']().parse_args([])
        with patch.object(INSTALL['sys'].stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=['', 'node', 'bridge', '', '', 'Agent', '', '', 'y']):
            self.assertTrue(INSTALL['wizard'](args, root))
        self.assertEqual((args.profile, args.workspace_id), ('', ''))
        self.assertEqual(args.mode, 'local')
        self.assertIsNone(args.ssh_alias)

    def test_reply_wizard_completes_local_setup(self):
        config_root = self.root / 'wizard-local'
        config_root.mkdir()
        (config_root / 'server.json').write_text(json.dumps(self.server))
        env = dict(self.env, GBB_MULTICA_CONFIG_HOME=str(config_root))
        with patch.dict(os.environ, env, clear=True), patch.object(INSTALL['sys'].stdin, 'isatty', return_value=True), \
                patch.object(INSTALL['sys'], 'argv', ['install.py', 'reply']), \
                patch('builtins.input', side_effect=['wizard-alice', 'alice', 'bridge-alice', '1', '', 'y']):
            self.assertEqual(INSTALL['main'](), 0)
        route = json.loads((config_root / 'routes.json').read_text())['routes']['wizard-alice']
        self.assertEqual(route['transport'], 'local')
        self.assertEqual(route['instance_id'], 'alice')
        self.assertNotIn('ssh_alias', route)

    def test_no_args_menu_works_in_real_terminal(self):
        import pty
        import select
        import time
        master, slave = pty.openpty()
        env = dict(self.env, GBB_MULTICA_CONFIG_HOME=str(self.root / 'pty-no-write'))
        child = subprocess.Popen(['bash', ROOT / 'install.sh'], env=env, stdin=slave,
                                 stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        output = b''
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    output += os.read(master, 8192)
                if '请选择'.encode() in output:
                    break
            self.assertIn('本机部署'.encode(), output)
            self.assertIn('SSH'.encode(), output)
            self.assertIn('请选择'.encode(), output)
            os.write(master, b'5\n')
            self.assertEqual(child.wait(timeout=10), 0)
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            os.close(master)
        self.assertFalse((self.root / 'pty-no-write').exists())
        result = subprocess.run(['bash', ROOT / 'install.sh'], env=env, input='', capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('交互向导需要终端', result.stderr)

    def test_new_cli_guards_do_not_write(self):
        bad = [ ['reply', '--transport', 'local', '--setup-ssh'],
                ['ssh', '--workspace-id', 'unexpected'], ['ssh', '--check', '--ssh-host', 'example.com'],
                ['reply', '--ssh-host', 'example.com'], ['runtime', '--setup-ssh'],
                ['local', '--ssh-alias', 'other'], ['local', '--transport', 'ssh'], ['local', '--route', 'other'] ]
        root = self.root / 'guard-no-write'
        for arguments in bad:
            result = subprocess.run(['bash', ROOT / 'install.sh', *arguments],
                                    env=dict(self.env, GBB_MULTICA_CONFIG_HOME=str(root)),
                                    capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0, arguments)
            self.assertFalse(root.exists())
        with self.assertRaises(ValueError):
            REMOTE['route_command']({'transport': 'unsafe'}, 'check')

    def test_new_credentials_use_private_store_without_sending(self):
        home = self.root / 'fresh-gbb'
        home.mkdir(exist_ok=True, mode=0o700)
        output = io.StringIO()
        def input_url(_):
            # Initial setup must use the original gbb prompt, with no unresolved reply config.
            displayed = output.getvalue()
            prompt = displayed.split('========== Grok Bot 提示词开始 ==========\n', 1)[1].split(
                '\n========== Grok Bot 提示词结束 ==========', 1)[0]
            self.assertIn('Please rename yourself to "bridge-fresh".', prompt)
            self.assertIn('Name: Multica bridge', prompt)
            self.assertIn('If it already exists, update it and reuse its webhook URL and key.', prompt)
            self.assertIn("gbb answer <message_id> - <<'EOF'", prompt)
            self.assertIn('Routing: if "to" names another Bot, you are the hub.', prompt)
            self.assertIn('How to run local agents on my computer', prompt)
            self.assertNotIn('{{', prompt)
            self.assertNotIn('Configured Multica reply routes', prompt)
            self.assertIn('Created routine', displayed)
            self.assertIn('Webhook', displayed)
            self.assertIn('POST to', displayed)
            self.assertIn('key', displayed)
            self.assertIn('还会显示填好路径和路由的运行提示词', displayed)
            return 'https://example.invalid/webhook'
        with patch.object(INSTALL['sys'].stdin, 'isatty', return_value=True), \
                patch('builtins.input', side_effect=input_url), \
                patch.object(INSTALL['getpass'], 'getpass', return_value='FRESH_OFFLINE_KEY'), \
                redirect_stdout(output):
            INSTALL['configure_credentials'](shutil.which('node'), Path(self.server['gbb_bin']), home, 'bridge-fresh')
        self.assertNotIn('FRESH_OFFLINE_KEY', output.getvalue())
        self.assertNotIn('https://example.invalid/webhook', output.getvalue())
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
