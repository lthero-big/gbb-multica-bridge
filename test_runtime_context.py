"""Task-local context, full instruction forwarding and continuous/recovery boundaries."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
AGENT = runpy.run_path(str(ROOT / 'gbb-multica-agent'))


class RuntimeContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='gbb-context-')
        self.root = Path(self.tmp.name).resolve()
        self.cwd = Path.cwd()
        os.chdir(self.root)
        self.env = patch.dict(os.environ, {
            'GBB_HOME': str(self.root / 'store'), 'GBB_NO_KEYCHAIN': '1',
            'GBB_MULTICA_CONFIG_HOME': str(self.root / 'config'),
            'MULTICA_TASK_ID': 'context-task', 'GBB_WAIT': '30',
            'GBB_IDLE_WARN': '1', 'GBB_IDLE_TIMEOUT': '2',
        }, clear=True)
        self.env.start()
        self.old_handler = signal.getsignal(signal.SIGTERM)

    def tearDown(self):
        signal.signal(signal.SIGTERM, self.old_handler)
        self.env.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def resources(self, refs=None):
        self.write('.multica/project/resources.json', json.dumps({
            'project_id': 'project1', 'project_title': 'Kmoe', 'resources': refs or [{
                'resource_type': 'local_directory', 'resource_ref': {
                    'local_path': '/original/Kmoe', 'execution_mode': 'worktree'}}]}))

    def test_full_instructions_project_and_distinct_working_directory(self):
        body = '任务指令\n' * 15000 + 'END_OF_INSTRUCTIONS\n'
        self.write('AGENTS.md', body)
        self.resources()
        with patch.object(AGENT['socket'], 'gethostname', return_value='runtime.local'):
            result = AGENT['runtime_context']()
        ctx = result['execution_context']
        self.assertEqual(ctx['runtime_device_name'], 'runtime.local')
        self.assertEqual(ctx['target_local_device_name'], 'runtime.local')
        self.assertEqual(ctx['local_execution_status'], 'unverified')
        self.assertEqual(ctx['working_directory'], str(self.root))
        self.assertEqual(ctx['project']['local_path'], '/original/Kmoe')
        self.assertEqual(ctx['project']['execution_mode'], 'worktree')
        self.assertEqual(ctx['project']['name'], 'Kmoe')
        self.assertEqual(result['runtime_instructions']['content'], body)

    def test_explicit_device_name_and_multiple_resources_are_not_guessed(self):
        self.resources([{'resource_type': 'local_directory', 'resource_ref': {'local_path': p}}
                        for p in ['/one', '/two']])
        with patch.dict(os.environ, GBB_LOCAL_DEVICE_NAME='My Grok Mac'):
            ctx = AGENT['runtime_context']()['execution_context']
        self.assertEqual(ctx['target_local_device_name'], 'My Grok Mac')
        self.assertEqual(ctx['target_device_source'], 'GBB_LOCAL_DEVICE_NAME')
        self.assertNotIn('local_path', ctx['project'])
        self.assertEqual(len(ctx['project']['resources']), 2)
        for value in ['', ' ', 'bad\nname', 'x' * 256]:
            with self.subTest(value=value), patch.dict(os.environ, GBB_LOCAL_DEVICE_NAME=value):
                with self.assertRaisesRegex(ValueError, 'GBB_LOCAL_DEVICE_NAME'):
                    AGENT['runtime_context']()

    def test_missing_fallback_and_parent_instructions_are_not_read(self):
        self.write('AGENTS.md', 'parent instructions must not be collected')
        child = self.root / 'child'
        child.mkdir()
        os.chdir(child)
        result = AGENT['runtime_context']()
        self.assertEqual(result['runtime_instructions']['status'], 'missing')
        self.assertEqual(result['execution_context']['project']['status'], 'missing')
        target = child / '.github/copilot-instructions.md'
        target.parent.mkdir()
        target.write_text('fallback instructions')
        self.assertEqual(AGENT['runtime_context']()['runtime_instructions']['content'], 'fallback instructions')
        (child / 'AGENTS.md').write_text('primary')
        self.assertEqual(AGENT['runtime_context']()['runtime_instructions']['content'], 'primary')

    def test_unreadable_oversize_invalid_and_escaping_files_expose_gaps(self):
        path = self.write('AGENTS.md', 'x' * (AGENT['RUNTIME_CONTEXT_FILE_LIMIT'] + 1))
        result = AGENT['runtime_context']()['runtime_instructions']
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('content', result)
        path.write_bytes(b'\xffPRIVATE')
        self.assertNotIn('PRIVATE', json.dumps(AGENT['runtime_context']()))
        self.assertEqual(AGENT['runtime_context']()['runtime_instructions']['status'], 'unavailable')
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(AGENT['runtime_context']()['runtime_instructions']['status'], 'unavailable')
        path.unlink()
        with tempfile.TemporaryDirectory() as outside:
            external = Path(outside) / 'private'
            external.write_text('HOST_DATA_MUST_NOT_BE_READ')
            path.symlink_to(external)
            result = AGENT['runtime_context']()['runtime_instructions']
            self.assertEqual(result['status'], 'unavailable')
            self.assertNotIn('HOST_DATA', json.dumps(result))
        path.unlink()
        path.symlink_to(path.name)
        self.assertEqual(AGENT['runtime_context']()['runtime_instructions']['status'], 'unavailable')
        path.unlink()
        with patch.object(AGENT['os'], 'open', side_effect=PermissionError('PRIVATE_OS_ERROR')):
            result = AGENT['runtime_context_file'](self.root, 'AGENTS.md')
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('PRIVATE_OS_ERROR', json.dumps(result))
        for data in ['{BROKEN_PRIVATE_JSON', '[]', '{"resources":[{"resource_ref":"bad"}]}']:
            self.write('.multica/project/resources.json', data)
            project = AGENT['runtime_context']()['execution_context']['project']
            self.assertEqual(project['status'], 'unavailable')
            self.assertNotIn('BROKEN_PRIVATE', json.dumps(project))

    def run_main(self, continuous=True, recover=False):
        requests = []
        sessions = []
        class Session:
            def __init__(self, *args):
                self.id = 'session' + str(len(sessions))
                sessions.append(self)
            def close(self, *args):
                pass
        def transport(req, timeout):
            data = json.loads(req['prompt'].split('REQUEST_JSON:\n', 1)[1])
            requests.append(data)
            self.assertEqual(data['interaction_mode'], 'non_interactive')
            # The initial snapshot must survive both continued calls and a new recovery run.
            (self.root / 'AGENTS.md').write_text('CHANGED_AFTER_START')
            if len(requests) == 1:
                reply = {'tool_calls': [{'id': 'h1', 'name': 'multica_help', 'arguments': {'command': []}}]}
            elif recover and len(requests) == 2:
                raise AGENT['BridgeError']('idle', 3)
            else:
                reply = {'final': 'DONE'}
            return 0, json.dumps(dict(gbb_tool_protocol=1, request_id=data['request_id'], **reply)), ''
        changes = {'GBB_INSTANCE_ID': 'test', 'GBB_REPLY_ROUTE': 'test'} if continuous else {}
        if recover:
            changes['GBB_RETRY_READ_ONLY'] = '1'
        with patch.dict(os.environ, changes), patch.object(sys, 'argv', ['agent', '-p', 'TASK']), \
                patch.dict(AGENT['main'].__globals__, transport=transport, Session=Session), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(AGENT['main'](), 0)
        return requests

    def test_context_redaction_first_turn_only_and_no_content_in_timing(self):
        secret = 'private-env-credential-123456'
        self.write('AGENTS.md', 'TASK_CONTEXT_MARKER ' + secret)
        self.resources()
        resource_file = self.root / '.multica/project/resources.json'
        resources = json.loads(resource_file.read_text())
        resources['resources'][0]['resource_ref']['token'] = 'opaque-private-credential'
        resource_file.write_text(json.dumps(resources))
        with patch.dict(os.environ, PRIVATE_API_KEY=secret):
            requests = self.run_main()
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]['runtime_instructions']['content'], 'TASK_CONTEXT_MARKER [redacted]')
        self.assertNotIn(secret, json.dumps(requests))
        self.assertNotIn('opaque-private-credential', json.dumps(requests))
        for key in ['execution_context', 'runtime_instructions', 'user_task', 'history']:
            self.assertNotIn(key, requests[1])
        self.assertEqual(requests[1]['tool_results'][0]['call_id'], 'h1')
        log = (self.root / 'store/timing.jsonl').read_text()
        self.assertNotIn('TASK_CONTEXT_MARKER', log)
        self.assertNotIn(secret, log)

    def test_recovery_and_legacy_webhooks_restore_same_startup_snapshot(self):
        for recover in [False, True]:
            with self.subTest(recover=recover):
                self.write('AGENTS.md', 'ORIGINAL_CONTEXT')
                requests = self.run_main(continuous=recover, recover=recover)
                self.assertEqual(requests[0]['runtime_instructions']['content'], 'ORIGINAL_CONTEXT')
                self.assertEqual(requests[-1]['runtime_instructions'], requests[0]['runtime_instructions'])
                self.assertEqual(requests[-1]['execution_context'], requests[0]['execution_context'])
                if recover:
                    self.assertTrue(requests[-1]['read_only'])
                    self.assertEqual(requests[-1]['recovery_attempt'], 1)
                    self.assertEqual(requests[-1]['history'][0]['call_id'], 'h1')


if __name__ == '__main__':
    unittest.main()
