"""No live services: deadlines, recovery and abrupt owner death on a real local mailbox."""
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
AGENT = runpy.run_path(str(ROOT / 'gbb-multica-agent'))
ENDPOINT = runpy.run_path(str(ROOT / 'gbb-multica-endpoint'))


def eventually(check, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError('condition did not become true before deadline')


class StalledSessions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gbb-stall-')
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'store'
        (self.home / 'messages').mkdir(parents=True)
        self.config = {'instance_id': 'test', 'gbb_home': str(self.home), 'reply_route': 'local',
                       'node_bin': shutil.which('node'), 'bot': 'bridge'}
        self.config_root = self.root / 'config'
        self.config_root.mkdir()
        (self.config_root / 'server.json').write_text(json.dumps(self.config))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('MULTICA_', 'GBB_'))}
        self.env.update(GBB_HOME=str(self.home), GBB_NO_KEYCHAIN='1', GBB_WAIT='15',
                        GBB_IDLE_WARN='1', GBB_IDLE_TIMEOUT='2',
                        GBB_MULTICA_CONFIG_HOME=str(self.config_root), MULTICA_TASK_ID='test-task')
        self.processes, self.servers = [], []
        self.requests, self.errors = [], []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join()
        self.temp.cleanup()

    def message(self, mid):
        return json.loads((self.home / 'messages' / (mid + '.json')).read_text())

    def records(self):
        path = self.home / 'timing.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def start(self, callback=lambda mid, n: None, **env):
        gbb = shutil.which('gbb') or '/usr/bin/gbb'
        if not Path(gbb).is_file() or not shutil.which('node'):
            self.skipTest('requires the pinned gbb 0.2.0 and Node')
        owner = self
        class Webhook(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(payload['message_id'])
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
                self.wfile.flush()
                try:
                    callback(payload['message_id'], len(owner.requests))
                except BaseException as exc:
                    owner.errors.append(exc)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Webhook)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.servers.append((server, thread))
        (self.home / 'config.json').write_text(json.dumps({'defaultBot': 'bridge'}))
        (self.home / 'credentials.json').write_text(json.dumps({'bridge': {
            'url': f'http://127.0.0.1:{server.server_port}', 'key': 'offline-test'}}))
        wrapper = self.root / 'agent'
        wrapper.write_text((ROOT / 'gbb-multica-agent').read_text().replace(
            'GBB_BIN = "/usr/bin/gbb"', 'GBB_BIN = ' + repr(gbb)).replace(
            'NODE_BIN = "node"', 'NODE_BIN = ' + repr(shutil.which('node'))))
        process = subprocess.Popen([sys.executable, str(wrapper), '-p', 'ORIGINAL_TASK'],
                                   env=dict(self.env, **env), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        self.processes.append(process)
        return process

    def reply(self, mid, **fields):
        request = json.loads(self.message(mid)['question'].split('REQUEST_JSON:\n')[1])
        ENDPOINT['save_reply'](self.config, mid, json.dumps(dict(gbb_tool_protocol=1,
                              request_id=request['request_id'], **fields)))

    def finish(self, process, code):
        out, err = process.communicate(timeout=12)
        self.assertEqual(process.returncode, code, err)
        self.assertFalse(self.errors, self.errors)
        return [json.loads(line) for line in out.splitlines()]

    def test_idle_warning_timeout_and_late_reply_rejected_without_retry(self):
        process = self.start()
        events = self.finish(process, 3)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(sum('等待 Bot' in e.get('data', {}).get('content', '') for e in events), 1)
        self.assertIn('bot_idle_timeout', [r['event'] for r in self.records()])
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.reply(self.requests[0], final='LATE')
        self.assertNotIn('answer', self.message(self.requests[0]))

    def test_progress_extends_idle_but_not_absolute_deadline(self):
        def progress(mid, n):
            for _ in range(7):
                time.sleep(0.6)
                try:
                    ENDPOINT['progress'](self.config, mid)
                except ValueError:
                    return
        process = self.start(progress, GBB_WAIT='4')
        self.finish(process, 2)
        self.assertGreaterEqual(sum(r['event'] == 'bot_progress' for r in self.records()), 3)
        self.assertNotIn('bot_idle_timeout', [r['event'] for r in self.records()])
        self.assertEqual(len(self.requests), 1)

    def test_read_only_retry_restores_task_results_and_closes_old_session(self):
        def bot(mid, attempt):
            if attempt == 1:
                self.reply(mid, tool_calls=[{'id': 'h1', 'name': 'multica_help', 'arguments': {'command': []}}])
            else:
                req = json.loads(self.message(mid)['question'].split('REQUEST_JSON:\n')[1])
                self.assertEqual(req['user_task'], 'ORIGINAL_TASK')
                self.assertEqual(req['history'][0]['call_id'], 'h1')
                self.assertTrue(req['history'][0]['result']['ok'])
                self.assertEqual(req['recovery_attempt'], 1)
                self.assertTrue(req['read_only'])
                old = self.message(self.requests[0])['bridge']['session_id']
                self.assertEqual(json.loads((self.home / 'sessions' / (old + '.json')).read_text())['status'], 'closed')
                self.reply(mid, final='RECOVERED')
        events = self.finish(self.start(bot, GBB_RETRY_READ_ONLY='1'), 0)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(events[-2]['data']['content'], 'RECOVERED')
        self.assertEqual(sum(r['event'] == 'read_only_retry' for r in self.records()), 1)
        self.assertEqual(sum(r['event'] == 'tool_end' for r in self.records()), 1)

    def test_read_only_retry_is_bounded_and_total_budget_is_not_reset(self):
        events = self.finish(self.start(GBB_RETRY_READ_ONLY='1', GBB_WAIT='8'), 3)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(events[-1]['exitCode'], 3)
        self.assertEqual(sum(r['event'] == 'read_only_retry' for r in self.records()), 1)

    def test_ambiguous_http_failure_is_never_retried(self):
        # Exercise main's retry gate independently of the network sender.
        globals_ = AGENT['main'].__globals__
        def failed(request, timeout):
            return 1, '', ''
        with patch.dict(os.environ, dict(self.env, GBB_RETRY_READ_ONLY='1'), clear=True), \
                patch.object(sys, 'argv', ['agent', '-p', 'task']), \
                patch.dict(globals_, transport=failed), redirect_stdout(io.StringIO()):
            self.assertEqual(AGENT['main'](), 1)
        self.assertNotIn('read_only_retry', [r['event'] for r in self.records()])

    def test_sigkill_process_group_closes_session_and_refuses_progress(self):
        process = self.start()
        mid = eventually(lambda: self.requests and self.requests[0])
        sid = self.message(mid)['bridge']['session_id']
        path = self.home / 'sessions' / (sid + '.json')
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
        eventually(lambda: json.loads(path.read_text())['status'] == 'closed')
        state = json.loads(path.read_text())
        self.assertEqual((state['exit_code'], state['reason']), (130, 'owner_exit'))
        with self.assertRaisesRegex(ValueError, 'closed'):
            ENDPOINT['progress'](self.config, mid)
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.reply(mid, final='LATE')
        self.assertEqual(sum(r.get('reason') == 'owner_exit' for r in self.records()), 1)

    def test_sigkill_owner_only_also_reaps_the_waiting_transport(self):
        process = self.start()
        mid = eventually(lambda: self.requests and self.requests[0])
        sid = self.message(mid)['bridge']['session_id']
        process.kill()
        # Node inherited the stdout/stderr pipes: EOF proves the orphan waiter exited.
        process.communicate(timeout=5)
        self.assertEqual(json.loads((self.home / 'sessions' / (sid + '.json')).read_text())['status'], 'closed')

    def test_missing_owner_lease_rejects_immediately_even_before_guardian(self):
        session = AGENT['Session'](self.home, 'test-task', 'test')
        mid = 'mlease'
        message = {'id': mid, 'bridge': {'delivery_mode': 'continuous', 'task_id': 'test-task',
                   'session_id': session.id, 'instance': 'test', 'expires_at': time.time() + 30}}
        (self.home / 'messages' / (mid + '.json')).write_text(json.dumps(message))
        session.guardian.kill()
        session.guardian.wait()
        self.assertEqual(ENDPOINT['read_session'](self.config, message)['status'], 'running')
        session.lease.close()
        self.assertEqual(ENDPOINT['read_session'](self.config, message)['status'], 'closed')
        session.close(130)

    def test_read_only_mode_blocks_writes_before_cli_execution(self):
        with patch.dict(os.environ, dict(self.env, GBB_RETRY_READ_ONLY='1', MULTICA_TOKEN='mat_test',
                                        MULTICA_WORKSPACE_ID='workspace'), clear=True):
            tools = AGENT['Tools'](time.monotonic() + 10, self.home / 'multica-runs/test-task')
            try:
                with patch.object(tools, 'plan', return_value=(['project', 'create'], {}, {}, None)), \
                        patch.object(tools, 'execute_cli') as execute:
                    result = tools.call({'id': 'w1', 'name': 'multica', 'arguments': {}})
                self.assertFalse(result['ok'])
                self.assertIn('writes are disabled', result['error'])
                execute.assert_not_called()
                self.assertNotIn('"state": "started"', tools.journal.read_text())
            finally:
                tools.close()

    def test_uncertain_write_in_journal_disables_read_only_recovery(self):
        with patch.dict(os.environ, self.env, clear=True):
            tools = AGENT['Tools'](time.monotonic() + 10, self.home / 'multica-runs/test-task')
            tools.save({'id': 'prior_write', 'fingerprint': 'prior', 'write': True, 'state': 'started'})
            tools.close()
        attempts = []
        def idle(request, timeout):
            attempts.append(request)
            raise AGENT['BridgeError']('idle', 3)
        with patch.dict(os.environ, dict(self.env, GBB_RETRY_READ_ONLY='1'), clear=True), \
                patch.object(sys, 'argv', ['agent', '-p', 'task']), \
                patch.dict(AGENT['main'].__globals__, transport=idle), redirect_stdout(io.StringIO()):
            self.assertEqual(AGENT['main'](), 3)
        self.assertEqual(len(attempts), 1)
        self.assertNotIn('read_only_retry', [r['event'] for r in self.records()])

    def test_warning_clock_starts_only_after_send_and_polling_does_not_extend_it(self):
        request = dict(task_id='task', request_id='r', round=1, message_id='mnone', idle_warn=2, idle_timeout=4)
        with patch.dict(os.environ, self.env, clear=True):
            monitor = AGENT['ReplyMonitor'](request)
            with patch.dict(AGENT['ReplyMonitor'].__call__.__globals__['time'].__dict__, monotonic=lambda: 10):
                monitor(b'')
                self.assertIsNone(monitor.last)
                monitor(b'{"gbb_transport":"waiting"}\n')
            with patch.dict(AGENT['ReplyMonitor'].__call__.__globals__['time'].__dict__, monotonic=lambda: 15):
                with self.assertRaises(AGENT['BridgeError']) as error:
                    monitor(b'{"gbb_transport":"waiting"}\n')
                self.assertEqual(error.exception.code, 3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
