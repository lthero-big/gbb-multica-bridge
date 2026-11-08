"""Route fallback, concurrent reply integrity and terminal failures. No live services."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
AGENT = runpy.run_path(str(ROOT / 'gbb-multica-agent'))


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gbb-reliability-')
        self.root = Path(self.tmp.name)
        self.store = self.root / 'store'
        (self.store / 'messages').mkdir(parents=True)
        self.config = {'instance_id': 'test-instance', 'reply_route': 'test-route',
                       'workspace_id': 'test-workspace', 'bot': 'test-bot',
                       'gbb_home': str(self.store)}
        self.save_config()
        (self.root / 'routes.json').write_text(json.dumps({'routes': {'test-route': {
            'transport': 'local', 'instance_id': 'test-instance',
            'endpoint': str(ROOT / 'gbb-multica-endpoint')}}}))
        self.env = {'GBB_MULTICA_CONFIG_HOME': str(self.root),
                    'MULTICA_WORKSPACE_ID': 'test-workspace'}
        self.patcher = patch.dict(os.environ, self.env, clear=True)
        self.patcher.start()
        self.message_id = 'mtest123'
        self.path = self.store / 'messages' / (self.message_id + '.json')
        self.message = {'id': self.message_id, 'question': 'original question',
                        'bridge': {'request_id': 'a' * 32, 'instance': 'test-instance',
                                   'expires_at': time.time() + 60}}
        self.path.write_text(json.dumps(self.message))

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def save_config(self):
        (self.root / 'server.json').write_text(json.dumps(self.config))

    def reply(self, text, action='answer'):
        return subprocess.run([sys.executable, str(ROOT / 'gbb-multica-remote'), action,
                               'test-route', self.message_id, '-'], input=text,
                              capture_output=True, text=True, env=dict(os.environ), timeout=5)

    def test_newly_rebound_agent_uses_verified_local_deployment(self):
        self.assertEqual(AGENT['bridge_settings'](),
                         ('test-bot', 'test-instance', 'test-route', str(self.store)))
        with patch.dict(os.environ, GBB_TARGET_BOT='specialist'):
            self.assertEqual(AGENT['bridge_settings']()[0], 'specialist')

    def test_partial_route_corrupt_config_workspace_and_store_mismatch_fail_closed(self):
        for env in ({'GBB_INSTANCE_ID': 'test-instance'}, {'GBB_REPLY_ROUTE': 'test-route'},
                    {'MULTICA_WORKSPACE_ID': 'other'}, {'GBB_HOME': str(self.root / 'other')}):
            with self.subTest(env=env), patch.dict(os.environ, env):
                with self.assertRaises(AGENT['BridgeError']):
                    AGENT['bridge_settings']()
        for value in ('{broken SECRET_VALUE', '[]', '{}', '{"instance_id":"test-instance"}'):
            (self.root / 'server.json').write_text(value)
            with self.assertRaises(AGENT['BridgeError']) as exc:
                AGENT['bridge_settings']()
            self.assertNotIn('SECRET_VALUE', str(exc.exception))

    def test_explicit_agent_route_preserves_legacy_and_other_deployments(self):
        (self.root / 'server.json').write_text('invalid')
        with patch.dict(os.environ, GBB_INSTANCE_ID='other-instance', GBB_REPLY_ROUTE='other-route',
                        GBB_HOME=str(self.store), GBB_TARGET_BOT='other-bot'):
            self.assertEqual(AGENT['bridge_settings'](),
                             ('other-bot', 'other-instance', 'other-route', str(self.store)))
        (self.root / 'server.json').unlink()
        self.assertEqual(AGENT['bridge_settings']()[1:3], ('', ''))

    def test_full_multiline_reply_atomic_idempotent_and_conflicts_rejected(self):
        answer = '中文\n' + 'large reply "quoted" $HOME `literal`\n' * 5000
        self.assertEqual(self.reply(answer).returncode, 0)
        first = self.path.read_bytes()
        self.assertEqual(json.loads(first)['answer'], answer.strip())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.reply(answer).returncode, 0)
        self.assertEqual(self.path.read_bytes(), first)
        result = self.reply('conflicting response')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('conflicting reply rejected', result.stderr)
        self.assertEqual(self.path.read_bytes(), first)

    def test_simultaneous_different_replies_accept_exactly_one(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.reply, ['first', 'second']))
        self.assertEqual(sorted(r.returncode for r in results), [0, 1])
        self.assertIn(json.loads(self.path.read_text())['answer'], ('first', 'second'))
        log = self.store / 'timing.jsonl'
        records = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(r['event'] == 'reply_start' for r in records), 2)
        self.assertEqual(sum(r['event'] == 'reply_saved' for r in records), 1)
        self.assertEqual(sum(r['event'] == 'reply_end' for r in records), 2)

    def test_held_reply_lock_fails_within_bound_without_writing(self):
        before = self.path.read_bytes()
        with (self.path.parent / ('.' + self.message_id + '.reply.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.reply('must not write')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reply is being written', result.stderr)
        self.assertEqual(self.path.read_bytes(), before)

    def test_expired_wrong_instance_and_stale_request_do_not_write(self):
        for change in ({'expires_at': time.time() - 1}, {'instance': 'other-instance'}):
            self.message['bridge'].update(change)
            self.path.write_text(json.dumps(self.message))
            before = self.path.read_bytes()
            self.assertNotEqual(self.reply('must not write').returncode, 0)
            self.assertEqual(self.path.read_bytes(), before)
        self.message['bridge'].update(expires_at=time.time() + 60, instance='test-instance')
        self.path.write_text(json.dumps(self.message))
        result = self.reply(json.dumps({'gbb_tool_protocol': 1, 'request_id': 'b' * 32, 'final': 'stale'}))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('answer', json.loads(self.path.read_text()))

    def test_failure_reaches_wrapper_as_terminal_error(self):
        result = self.reply('request cannot be processed', action='fail')
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(self.path.read_text())['answer']
        records = (self.store / 'timing.jsonl').read_text()
        self.assertNotIn('request cannot be processed', records)
        with self.assertRaisesRegex(AGENT['BridgeError'], 'request cannot be processed'):
            AGENT['parse_reply'](reply, 'a' * 32)
        for extra in ({'final': 'ambiguous'}, {'tool_calls': []}, {'error': ''}):
            value = {'gbb_tool_protocol': 1, 'request_id': 'a' * 32, 'error': 'failed', **extra}
            with self.assertRaises(AGENT['BridgeError']):
                AGENT['parse_reply'](json.dumps(value), 'a' * 32)

    def test_inflight_legacy_message_remains_answerable_during_upgrade(self):
        self.message.pop('bridge')
        self.path.write_text(json.dumps(self.message))
        result = self.reply('legacy response\n中文')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.path.read_text())['answer'], 'legacy response\n中文')


if __name__ == '__main__':
    unittest.main()
