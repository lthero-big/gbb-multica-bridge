"""Local tools and multi-round webhook tests. No production writes or Grok calls."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
MODULE = runpy.run_path(str(ROOT / "gbb-multica-agent"))
Tools, parse_reply, BridgeError = (MODULE[x] for x in ("Tools", "parse_reply", "BridgeError"))
REAL_CLI = os.environ.get("MULTICA_CLI_PATH") or shutil.which("multica")
FAKE_CLI = r'''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
a=sys.argv[1:]
if '--help' in a:
 os.execv(os.environ['REAL_MULTICA_FOR_HELP'],[os.environ['REAL_MULTICA_FOR_HELP'],*a])
assert a[:2]==['--workspace-id','fake-workspace'],a
assert os.environ['MULTICA_TOKEN']=='mat_OFFLINE_TASK_CREDENTIAL_123456789'
a=a[2:]
p=Path(os.environ['FAKE_MULTICA_STATE'])
s=json.loads(p.read_text())
s['calls'].append(a)
def val(flag,default=None):
 for i,x in enumerate(a):
  if x.startswith(flag+'='): return x.split('=',1)[1]
  if x==flag: return a[i+1]
 return default
def finish(data,code=0):
 p.write_text(json.dumps(s))
 print(json.dumps(data,ensure_ascii=False));sys.exit(code)
if a[:2]==['agent','tasks']: finish(s.get('tasks',[]))
if a[:2]==['issue','runs']: finish({'runs':s.get('runs',[])})
if a[:2]==['issue','run-messages']:
 if s.get('deny_messages'): finish({'error':'denied'},3)
 finish({'messages':s.get('messages',[])})
if a[:2]==['project','list']: finish(s['projects'])
if a[:2]==['project','create']:
 out={'id':'p1','title':val('--title'),'description':val('--description')}
 s['projects'].append(out)
 if val('--title')=='Broken JSON after commit':
  p.write_text(json.dumps(s));print('created but broken JSON');sys.exit(0)
 finish(out)
if a[:2]==['project','update']:
 s['projects'][0]['title']=val('--title');finish(s['projects'][0])
if a[:2]==['project','get']: finish(s['projects'][0])
if a[:2]==['repo','list']: finish(s['repos'])
if a[:2]==['repo','add']:
 s['repos'].append({'url':a[2]});finish(s['repos'])
if a[:2]==['issue','create']:
 body=Path(val('--description-file')).read_text()
 assert Path(val('--description-file')).parent==Path.cwd()
 assert Path(val('--description-file')).stat().st_mode&0o777==0o600
 out={'id':'i1','title':val('--title'),'description':body};s['issues'].append(out)
 print('Successfully created (stderr is not JSON)',file=sys.stderr);finish(out)
if a[:2]==['issue','update']:
 s['issues'][0]['title']=val('--title');finish(s['issues'][0])
if a[:2]==['issue','status']:
 s['issues'][0]['status']=a[3];finish(s['issues'][0])
if a[:2]==['issue','get']: finish(s['issues'][0])
if a[:2]==['attachment','download']:
 Path('artifact.ipa').write_bytes(b'OFFLINE_BINARY\x00\xff')
 if s.get('download_failure'): finish({'error':'denied'},3)
 finish({'saved':str(Path.cwd()/'artifact.ipa')})
if a[:3]==['issue','comment','add']:
 finish({'id':'comment1','content':Path(val('--content-file')).read_text()})
if a[:3]==['issue','comment','list']: finish({'comments':[]})
if a[:2]==['agent','get']:
 finish({'id':'agent1','custom_env':{'KEY':'NEVER_FORWARD'},'mcp_config':{'headers':'SECRET'},
         'description':os.environ['MULTICA_TOKEN'],'instructions':'普通说明'})
if a[:2]==['agent','update']:
 print('403: denied for this task',file=sys.stderr);finish({'error':'denied'},3)
if a[:2]==['label','list']: finish([{'id':'label1','description':'中'*60000}])
finish({'args':a})
'''


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="gbb-tools-test-")
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        exe = self.bin / "multica"
        exe.write_text(FAKE_CLI)
        exe.chmod(0o755)
        self.state_file = self.root / "state.json"
        self.state_file.write_text(json.dumps({"calls": [], "projects": [], "repos": [], "issues": []}))
        self.env = {"PATH": f"{self.bin}:/usr/bin:/bin", "LANG": "C.UTF-8",
                    "REAL_MULTICA_FOR_HELP": REAL_CLI, "FAKE_MULTICA_STATE": str(self.state_file),
                    "MULTICA_CLI_PATH": str(exe),
                    "MULTICA_TOKEN": "mat_OFFLINE_TASK_CREDENTIAL_123456789",
                    "MULTICA_WORKSPACE_ID": "fake-workspace", "MULTICA_TASK_ID": "offline-task",
                    "GBB_HOME": str(self.root / "gbb"), "GBB_WAIT": "20",
                    "GBB_MULTICA_CONFIG_HOME": str(self.root / "config")}
        self.patch = patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.tools = Tools(time.monotonic() + 60, self.root / "gbb/multica-runs/offline-task")
        self.counter = 0

    def tearDown(self):
        self.tools.close()
        self.patch.stop()
        self.tmp.cleanup()

    def state(self):
        return json.loads(self.state_file.read_text())

    def call(self, args, files=None, call_id=None):
        self.counter += 1
        return self.tools.call({"id": call_id or f"c{self.counter}", "name": "multica",
                               "arguments": {"args": args, "files": files or {}}})

    def prepare_handoff(self, task="offline-task"):
        self.env["MULTICA_AGENT_ID"] = os.environ["MULTICA_AGENT_ID"] = "next-agent"
        state = self.state()
        current = {"id": task, "agent_id": "next-agent", "issue_id": "actual-issue",
                   "created_at": "2026-11-09T02:00:00Z", "started_at": "2026-11-09T02:00:01Z", "status": "running"}
        prior = {"id": "prior-task", "agent_id": "previous-agent", "issue_id": "actual-issue",
                 "created_at": "2026-11-09T01:00:00Z", "started_at": "2026-11-09T01:00:01Z",
                 "status": "failed", "result": {"output": "PR is ready; verify before retrying the interrupted write."}}
        state.update(tasks=[current], runs=[current, prior], messages=[
            {"seq": 1, "type": "tool_use", "tool": "multica", "call_id": "old-write", "input": {"args": ["issue", "update", "actual-issue"]}},
            {"seq": 2, "type": "tool_result", "call_id": "old-write", "output_truncated": True,
             "output": "result_id:original-result; " + self.env["MULTICA_TOKEN"] + " mat_PREVIOUS_TASK_SECRET_123456789 " + "中" * 4000},
            {"seq": 3, "type": "text", "content": "HANDOFF_EVIDENCE"}])
        self.state_file.write_text(json.dumps(state))

    def test_handoff_uses_current_task_and_preserves_readable_provenance(self):
        self.prepare_handoff()
        state = self.state()
        state['runs'][1]['result']['output'] += self.env['MULTICA_TOKEN']
        state['deny_messages'] = True
        self.state_file.write_text(json.dumps(state))
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            context = self.tools.prior_context()
        self.assertEqual((context["status"], context["issue_id"], context["run_id"], context["agent_id"]),
                         ("summary_only", "actual-issue", "prior-task", "previous-agent"))
        self.assertEqual(context["run_status"], "failed")
        self.assertEqual(context["read_more"]["messages"], ["issue", "run-messages", "prior-task"])
        self.assertEqual(context['messages_status'], 'not_loaded')
        self.assertIn('PR is ready', context['conclusion'])
        self.assertNotIn(self.env['MULTICA_TOKEN'], json.dumps(context))
        self.assertEqual(len(self.state()["calls"]), 2)
        self.assertFalse(self.tools.calls, "history must not enter the new task's deduplication journal")

    def test_huge_transcript_is_never_requested_and_summary_is_bounded(self):
        self.prepare_handoff()
        state = self.state()
        state['runs'][1]['result']['output'] = 'SUMMARY_START' + '中' * 100000 + 'SUMMARY_END'
        state['messages'] = [{"seq": i, "type": "text", "content": "中" * 5000} for i in range(1000)]
        self.state_file.write_text(json.dumps(state))
        original = self.tools.cli
        def bounded_read(args, **kwargs):
            self.assertNotEqual(args[:2], ['issue', 'run-messages'], 'startup must never download the full log')
            return original(args, **kwargs)
        with patch.object(self.tools, 'cli', side_effect=bounded_read):
            context = self.tools.prior_context()
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False)), MODULE["PRIOR_CONTEXT_LIMIT"])
        self.assertTrue(context['truncated'])
        self.assertIn('SUMMARY_START', context['conclusion'])
        self.assertIn('SUMMARY_END', context['conclusion'])
        self.assertEqual(context['messages_status'], 'not_loaded')

    def test_handoff_missing_identity_chat_first_run_and_unmatched_task(self):
        self.assertIsNone(self.tools.prior_context())
        self.assertFalse(self.state()["calls"])
        self.prepare_handoff()
        state = self.state()
        state["runs"] = state["tasks"]
        self.state_file.write_text(json.dumps(state))
        self.assertEqual(self.tools.prior_context()["status"], "none")
        state = self.state()
        state["tasks"][0]["issue_id"] = ""
        self.state_file.write_text(json.dumps(state))
        self.assertIsNone(self.tools.prior_context())
        self.assertEqual(self.state()["calls"][-1][:2], ["agent", "tasks"])
        state = self.state()
        state["tasks"] = []
        self.state_file.write_text(json.dumps(state))
        self.assertEqual(self.tools.prior_context(), {"status": "unavailable", "missing": "current_task"})
        self.tools.available = False
        before = self.state()["calls"]
        self.assertIsNone(self.tools.prior_context())
        self.assertEqual(self.state()["calls"], before)

    def test_handoff_access_failure_timeout_and_invalid_json_do_not_abort_task(self):
        self.prepare_handoff()
        with patch.object(self.tools, 'cli', side_effect=[(0, json.dumps(self.state()['tasks']), ''),
                                                         subprocess.TimeoutExpired('hidden-secret', 1)]):
            context = self.tools.prior_context()
        self.assertEqual((context["status"], context["missing"]), ("unavailable", "issue_runs"))
        self.assertIn('read_more', context)
        for failure in (subprocess.TimeoutExpired("hidden-secret", 1), OSError("hidden-secret")):
            with patch.object(self.tools, "cli", side_effect=failure):
                self.assertEqual(self.tools.prior_context(), {"status": "unavailable", "missing": "current_task"})
        with patch.object(self.tools, "cli", return_value=(0, "invalid JSON hidden-secret", "")):
            self.assertNotIn("hidden-secret", json.dumps(self.tools.prior_context()))

    def test_handoff_skips_newer_concurrent_runs_and_queued_runs(self):
        self.prepare_handoff()
        state = self.state()
        state["runs"].insert(0, {"id": "future-task", "created_at": "2026-11-09T03:00:00Z",
                                 "started_at": "2026-11-09T03:00:01Z", "status": "running"})
        state["runs"].insert(0, {"id": "queued-task", "created_at": "2026-11-09T01:59:00Z", "status": "queued"})
        self.state_file.write_text(json.dumps(state))
        self.assertEqual(self.tools.prior_context()["run_id"], "prior-task")

    def test_malformed_run_metadata_reports_gap_instead_of_crashing(self):
        self.prepare_handoff()
        state = self.state()
        del state["runs"][1]["id"]
        self.state_file.write_text(json.dumps(state))
        context = self.tools.prior_context()
        self.assertEqual((context["status"], context["missing"]), ("unavailable", "issue_runs"))

    def test_create_edit_read_and_unicode_files(self):
        title = '项目 "引号" $HOME `touch unwanted` $(false)'
        p = self.call(["project", "create", "--title", title])
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["data"]["title"], title)
        self.assertTrue(self.call(["project", "update", "p1", "--title", "改名"])["ok"])
        self.assertEqual(self.call(["project", "get", "p1"])["data"]["title"], "改名")
        self.assertTrue(self.call(["repo", "add", "https://example.invalid/repo.git"])["ok"])
        body = '中文多行\n正文保留 \\n 字面量、$HOME、`quotes`。'
        result = self.call(["issue", "create", "--title", "测试", "--description-file", "@body.md"], {"body.md": body})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["data"]["description"], body)
        self.assertIn("Successfully created", result["stderr"])
        self.assertTrue(self.call(["issue", "update", "i1", "--title", "编辑后"])["ok"])
        self.assertTrue(self.call(["issue", "status", "i1", "backlog"])["ok"])
        self.assertEqual(self.call(["issue", "get", "i1"])["data"]["status"], "backlog")
        comment = self.call(["issue", "comment", "add", "i1", "--content-file", "@body.md"], {"body.md": body})
        self.assertEqual(comment["data"]["content"], body)
        self.assertFalse(list(ROOT.glob(".gbb-tool-*")), "temporary files must be cleaned")

    def test_dedup_across_ids_and_restarts(self):
        args = ["project", "create", "--title", "Once"]
        first = self.call(args, call_id="write1")
        self.assertEqual(self.call(args, call_id="write1"), first)
        self.assertEqual(self.call(args, call_id="write2"), first)
        self.tools.close()
        self.tools = Tools(time.monotonic() + 60, self.root / "gbb/multica-runs/offline-task")
        self.assertEqual(self.call(args, call_id="write1"), first)
        self.assertEqual(len(self.state()["projects"]), 1)
        self.assertFalse(self.call(["project", "create", "--title", "different"], call_id="write2")["ok"])

    def test_interrupted_write_is_never_retried(self):
        args = {"args": ["project", "create", "--title", "Uncertain"], "files": {}}
        fingerprint = hashlib.sha256(json.dumps(["multica", args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.tools.save({"id": "interrupted", "fingerprint": fingerprint, "write": True, "state": "started"})
        result = self.call(args["args"], call_id="new-id")
        self.assertFalse(result["ok"])
        self.assertTrue(result["write_may_have_committed"])
        self.assertEqual(self.state()["calls"], [])

    def test_no_owner_fallback_or_workspace_escape_or_host_files(self):
        attacks = [
            ["auth", "status"], ["daemon", "stop"], ["agent", "env", "get", "x"],
            ["project", "list", "--profile", "owner"], ["workspace", "get", "other-workspace"],
            ["project", "list", "--workspace-id=other"], ["project", "list", "--server-url=http://bad"],
            ["issue", "create", "--title", "bad", "--description-file", "/etc/passwd"],
            ["issue", "create", "--title", "bad", "--attachment", "/root/.multica/config.json"],
            ["attachment", "upload", "/etc/passwd"],
            ["autopilot", "get", "x", "--show-secrets"],
            ["issue", "comment", "add", "i1", "--content", "inline"],
        ]
        for args in attacks:
            with self.subTest(args=args):
                self.assertFalse(self.call(args)["ok"])
        self.assertFalse(self.call(["issue", "create", "--title", "bad"], {"../escape": "text"})["ok"])
        self.assertEqual(self.state()["calls"], [])
        self.tools.available = False
        self.assertFalse(self.call(["project", "list"])["ok"])
        self.assertEqual(self.state()["calls"], [])

    def test_redaction_403_and_bounded_comments(self):
        nested = self.tools.redact({'token': 'opaque-value', 'auth': {'client_secret': 'client-value',
                                    'private_key': 'private-value'}, 'title': 'ordinary title'})
        self.assertEqual(nested, {'token': '[redacted]', 'auth': {'client_secret': '[redacted]',
                                 'private_key': '[redacted]'}, 'title': 'ordinary title'})
        result = self.call(["agent", "get", "x"])
        self.assertNotIn("NEVER_FORWARD", json.dumps(result))
        self.assertNotIn(self.env["MULTICA_TOKEN"], json.dumps(result))
        denied = self.call(["agent", "update", "x", "--name", "new"])
        self.assertFalse(denied["ok"])
        self.assertEqual(denied["exit_code"], 3)
        self.assertIn("403", denied["stderr"])
        self.assertTrue(denied["write_may_have_committed"])
        self.call(["issue", "comment", "list", "i1"])
        self.assertIn("--roots-only", self.state()["calls"][-1])
        self.assertIn("--summary", self.state()["calls"][-1])

    def test_help_exposes_only_bridge_operations_and_usable_flags(self):
        group = self.tools.help(['workspace'])
        self.assertIn('workspace get', group)
        self.assertNotIn('workspace create', group)
        self.assertIn('download', self.tools.help(['attachment']))
        for command in (['issue', 'get'], ['issue', 'comment', 'add'], ['attachment', 'download']):
            help_text = self.tools.help(command)
            self.assertNotRegex(help_text, r'(?m)^  --(?:output(?:\s|$)|output-dir|help|content\s)')
            self.assertNotIn('$ multica', help_text)
        self.assertIn('--content-file', self.tools.help(['issue', 'comment', 'add']))
        # Raw output support remains available internally for automatic JSON execution.
        self.assertIn('--output', self.tools.cli_help(('issue', 'get')))
        self.assertEqual(self.state()['calls'], [])

    def test_comment_modes_preserve_explicit_selection_and_fail_before_cli(self):
        for selection in (['--thread', 'comment1', '--tail', '15'], ['--recent', '3'],
                          ['--since', '2026-11-10T00:00:00Z']):
            result = self.call(['issue', 'comment', 'list', 'i1', *selection])
            self.assertTrue(result['ok'], result)
            self.assertNotIn('--roots-only', self.state()['calls'][-1])
            self.assertNotIn('--summary', self.state()['calls'][-1])
        before = self.state()['calls']
        for selection in (['--tail', '15'], ['--roots-only', '--tail', '15'],
                          ['--roots-only', '--thread', 'c1'], ['--recent', '3', '--since', 'now']):
            self.assertFalse(self.call(['issue', 'comment', 'list', 'i1', *selection])['ok'])
        self.assertEqual(self.state()['calls'], before)

    def test_attachment_download_persists_and_replays_without_refetch(self):
        self.tools.read_only = True
        result = self.call(['attachment', 'download', 'attachment1'], call_id='download1')
        self.assertTrue(result['ok'], result)
        receipt = result['data']['downloaded_files'][0]
        path = Path(receipt['path'])
        self.assertTrue(path.is_relative_to(self.tools.journal.parent.resolve() / 'downloads'))
        data = b'OFFLINE_BINARY\x00\xff'
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(receipt['size_bytes'], len(data))
        self.assertEqual(receipt['sha256'], hashlib.sha256(data).hexdigest())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.tools.close()
        self.tools = Tools(time.monotonic() + 60, self.root / 'gbb/multica-runs/offline-task')
        self.assertEqual(self.call(['attachment', 'download', 'attachment1'], call_id='download1'), result)
        self.assertEqual(len(self.state()['calls']), 1)
        self.assertEqual(path.read_bytes(), data)
        for args in (['attachment', 'download', '../escape'],
                     ['attachment', 'download', 'attachment1', '--output-dir', '/tmp'],
                     ['attachment', 'download', 'attachment1', 'extra']):
            self.assertFalse(self.call(args)['ok'])
        self.assertEqual(len(self.state()['calls']), 1)

    def test_failed_or_unsafe_download_is_cleaned_and_never_claims_success(self):
        self.tools.cli_help(('attachment', 'download'))
        outside = self.root / 'untouched'
        outside.write_text('KEEP')
        outside.chmod(0o644)
        for mode in ('failed', 'empty', 'symlink', 'hardlink', 'directory', 'timeout'):
            def download(args, *, cwd):
                if mode == 'symlink': (cwd / 'bad').symlink_to(outside)
                if mode == 'hardlink': os.link(outside, cwd / 'bad')
                if mode == 'directory': (cwd / 'bad').mkdir()
                if mode in ('failed', 'timeout'): (cwd / 'partial').write_text('partial')
                if mode == 'timeout': raise subprocess.TimeoutExpired('fake', 1)
                return (3 if mode == 'failed' else 0), '', 'denied'
            with self.subTest(mode=mode), patch.object(self.tools, 'cli', side_effect=download):
                self.assertFalse(self.call(['attachment', 'download', 'attachment1'])['ok'])
                self.assertEqual(list((self.tools.journal.parent / 'downloads').iterdir()), [])
                self.assertEqual(outside.read_text(), 'KEEP')
                self.assertEqual(outside.stat().st_mode & 0o777, 0o644)
        cache = self.tools.journal.parent / 'downloads'
        cache.rmdir()
        cache.symlink_to(self.root, target_is_directory=True)
        self.assertFalse(self.call(['attachment', 'download', 'attachment1'])['ok'])

    def test_bad_write_response_and_timeout_are_not_retried(self):
        args = ["project", "create", "--title", "Broken JSON after commit"]
        first = self.call(args)
        self.assertFalse(first["ok"])
        self.assertTrue(first["write_may_have_committed"])
        self.assertEqual(self.call(args), first)
        self.assertEqual(len(self.state()["projects"]), 1)
        # A timeout after the validated command started is also an uncertain write.
        other = ["project", "create", "--title", "Timeout"]
        plan = self.tools.plan({"args": other})
        with patch.object(self.tools, "plan", return_value=plan), patch.object(
                self.tools, "execute_cli", side_effect=subprocess.TimeoutExpired("multica", 60)) as execute:
            failed = self.call(other)
            self.assertTrue(failed["write_may_have_committed"])
            self.assertEqual(self.call(other), failed)
            self.assertEqual(execute.call_count, 1)

    def test_optional_fields_keep_lists_small(self):
        self.call(["project", "create", "--title", "small"])
        result = self.tools.call({"id": "fields", "name": "multica", "arguments": {
            "args": ["project", "list"], "fields": ["id", "title"]}})
        self.assertEqual(result["data"], [{"id": "p1", "title": "small"}])

    def test_rerun_task_help_execution_and_write_deduplication(self):
        help_result = self.tools.call({"id": "rerun-help", "name": "multica_help",
                                      "arguments": {"command": ["issue", "rerun-task"]}})
        self.assertTrue(help_result['ok'], help_result)
        self.assertIn('--fresh', help_result['data'])
        self.assertIn('rerun-task', self.tools.help([])['operations']['issue'])
        self.assertEqual(self.state()['calls'], [], 'help must not rerun a task')
        args = ['issue', 'rerun-task', 'short-run', '--issue', 'TEST-1', '--fresh']
        first = self.call(args, call_id='rerun1')
        self.assertTrue(first['ok'], first)
        self.assertIn('--fresh', self.state()['calls'][0])
        self.assertEqual(self.call(args, call_id='rerun2'), first)
        self.tools.close()
        self.tools = Tools(time.monotonic() + 60, self.root / 'gbb/multica-runs/offline-task')
        self.assertEqual(self.call(args, call_id='rerun-after-restart'), first)
        self.assertEqual(len(self.state()['calls']), 1, 'reruns are writes and must not be repeated')
        self.assertFalse(self.call(args + ['--workspace-id', 'other'])['ok'])
        self.tools.available = False
        self.assertFalse(self.call(['issue', 'rerun-task', 'other-run'])['ok'])
        self.assertEqual(len(self.state()['calls']), 1, 'no owner-login fallback for reruns')

    def test_create_edit_command_shapes_use_installed_cli_flags(self):
        shapes = [
            ["project", "resource", "add", "p1", "--type", "github_repo", "--url", "https://example.invalid/a.git"],
            ["project", "resource", "update", "p1", "r1", "--ref", "main"],
            ["agent", "create", "--name", "Agent", "--runtime-id", "runtime1", "--instructions", "说明"],
            ["agent", "update", "agent1", "--name", "Agent edited"],
            ["label", "create", "--name", "Label"],
            ["label", "update", "label1", "--name", "Label edited"],
            ["squad", "create", "--name", "Team", "--leader", "agent1"],
            ["squad", "update", "squad1", "--instructions", "说明"],
            ["autopilot", "create", "--title", "Job", "--agent", "agent1", "--mode", "create_issue"],
            ["autopilot", "update", "job1", "--status", "paused"],
            ["issue", "wakeup", "create", "i1", "--until-status", "in_review", "--mode", "continuous", "--max-fires", "3"],
            ["issue", "comment", "update", "comment1", "--expected-revision", "2", "--content-file", "@body.md"],
        ]
        for args in shapes:
            with self.subTest(args=args):
                self.tools.plan({"args": args, "files": {"body.md": "正文"}})
        self.assertEqual(self.state()["calls"], [], "validation must not make platform writes")

    def test_large_results_can_be_paged_without_losing_text(self):
        first = self.call(["label", "list"])
        self.assertTrue(first["truncated"])
        self.assertEqual(len(first["content"]), 25000)
        page = self.tools.call({"id": "page2", "name": "multica_result", "arguments": {
            "result_id": first["result_id"], "offset": first["next_offset"], "limit": 40000}})
        self.assertEqual(page["result_id"], first["result_id"])
        self.assertEqual(page["offset"], 25000)
        self.assertTrue(page["content"].startswith("中"))

    def test_protocol_rejects_stale_malformed_or_ambiguous_calls(self):
        for payload in [
            {"gbb_tool_protocol": 1, "request_id": "stale", "tool_calls": []},
            {"gbb_tool_protocol": 1, "request_id": "now", "tool_calls": [], "final": "mixed"},
            {"gbb_tool_protocol": 1, "request_id": "now", "tool_calls": [{"id": "a", "name": "multica"}]},
        ]:
            with self.assertRaises(BridgeError):
                parse_reply(json.dumps(payload), "now")
        with self.assertRaises(BridgeError):
            parse_reply('{"gbb_tool_protocol":1, broken}', "now")
        self.assertEqual(parse_reply("plain answer", "now"), ("plain answer", None))

    def test_actual_gbb_multi_round_create_edit_readback(self):
        seen, errors = [], []
        class Webhook(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(handler):
                try:
                    payload = json.loads(handler.rfile.read(int(handler.headers["Content-Length"])))
                    request = json.loads(payload["summary"].split("REQUEST_JSON:\n", 1)[1])
                    seen.append(request)
                    self.assertNotIn(self.env["MULTICA_TOKEN"], json.dumps(payload))
                    reply = {"gbb_tool_protocol": 1, "request_id": request["request_id"]}
                    n = len(seen)
                    if n == 1 or request["user_task"] == "budgetcheck":
                        reply["tool_calls"] = [{"id": "create", "name": "multica", "arguments": {
                            "args": ["project", "create", "--title", "Loop test"]}}]
                    elif n == 2:
                        project_id = request["history"][-1]["result"]["data"]["id"]
                        reply["tool_calls"] = [{"id": "edit", "name": "multica", "arguments": {
                            "args": ["project", "update", project_id, "--title", "Loop edited"]}}]
                    elif n == 3:
                        reply["tool_calls"] = [{"id": "readback", "name": "multica", "arguments": {
                            "args": ["project", "get", "p1"]}}]
                    else:
                        self.assertEqual(request["history"][-1]["result"]["data"]["title"], "Loop edited")
                        reply["final"] = "创建、编辑、回读成功"
                    handler.send_response(200)
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    subprocess.run(["/usr/bin/gbb", "answer", payload["message_id"], "-"],
                        input=json.dumps(reply, ensure_ascii=False), text=True, capture_output=True,
                        env=self.env, timeout=5, check=True)
                except BaseException as exc:
                    errors.append(str(exc))
        server = ThreadingHTTPServer(("127.0.0.1", 0), Webhook)
        home = Path(self.env["GBB_HOME"])
        (home / "config.json").write_text(json.dumps({"defaultBot": "bridge", "maxSummaryChars": 10}))
        (home / "credentials.json").write_text(json.dumps({"bridge": {
            "url": f"http://127.0.0.1:{server.server_port}", "key": "offline-only"}}))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            # A separate task id avoids the unit test's journal lock.
            result = subprocess.run([sys.executable, str(ROOT / "gbb-multica-agent"), "-p", "创建并编辑测试项目"],
                env=dict(self.env, MULTICA_TASK_ID="roundtrip-task"), capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, (result.stderr, errors))
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(seen), 4)
            self.assertEqual(len([e for e in events if e["type"] == "tool.execution_complete"]), 3)
            self.assertEqual(events[-2]["data"]["content"], "创建、编辑、回读成功")
            self.assertFalse(errors, errors)
            timings = [json.loads(line) for line in (home / 'timing.jsonl').read_text().splitlines()]
            starts = [r for r in timings if r['event'] == 'tool_start']
            ends = [r for r in timings if r['event'] == 'tool_end']
            self.assertEqual([r['call_id'] for r in starts], ['create', 'edit', 'readback'])
            self.assertEqual([r['call_id'] for r in ends], ['create', 'edit', 'readback'])
            for start, end in zip(starts, ends):
                self.assertEqual(start['request_id'], end['request_id'])
                self.assertEqual(end['task_id'], 'roundtrip-task')
                self.assertTrue(end['ok'])
                self.assertGreaterEqual(end['elapsed_ms'], 0)
            before = len(self.state()["calls"])
            limited = subprocess.run([sys.executable, str(ROOT / "gbb-multica-agent"), "-p", "budgetcheck"],
                env=dict(self.env, MULTICA_TASK_ID="budget-task", GBB_MAX_ROUNDS="1"),
                capture_output=True, text=True, timeout=10)
            self.assertEqual(limited.returncode, 2, limited.stderr)
            self.assertEqual(len(self.state()["calls"]), before, "last-round writes must not execute without a continuation budget")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_single_webhook_dynamic_exchange_has_only_new_results(self):
        self.prepare_handoff("continuous-task")
        seen, messages, errors, duplicate_results = [], [], [], []
        bot_done = threading.Event()
        home = Path(self.env['GBB_HOME'])
        config = Path(self.env['GBB_MULTICA_CONFIG_HOME'])
        config.mkdir()
        (config / 'server.json').write_text(json.dumps({'instance_id': 'offline-instance', 'gbb_home': str(home),
                                                      'node_bin': sys.executable}))
        (config / 'routes.json').write_text(json.dumps({'routes': {'offline-route': {
            'transport': 'local', 'instance_id': 'offline-instance', 'endpoint': str(ROOT / 'gbb-multica-endpoint')}}}))
        env = dict(self.env, GBB_INSTANCE_ID='offline-instance', GBB_REPLY_ROUTE='offline-route',
                   MULTICA_TASK_ID='continuous-task')
        prompt = 'ORIGINAL_TASK_MUST_NOT_REPEAT'

        def helper(action, mid, body=None):
            command = [sys.executable, str(ROOT / 'gbb-multica-remote'), action, 'offline-route', mid]
            if body is not None:
                command.append('-')
            result = subprocess.run(command, input=body, capture_output=True, text=True, env=env, timeout=8)
            self.assertIn(result.returncode, (0, 2) if action == 'inbox' else (0,), result.stderr)
            return json.loads(result.stdout)

        class Webhook(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(handler):
                payload = json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
                seen.append(payload)
                handler.send_response(200)
                handler.send_header('Content-Length', '0')
                handler.end_headers()
                try:
                    message = helper('inbox', payload['message_id'])
                    initial = json.loads(message['question'].split('REQUEST_JSON:\n', 1)[1])
                    if initial['remaining_requests']:
                        self.assertEqual(initial['prior_context']['run_id'], 'prior-task')
                        self.assertEqual(initial['prior_context']['messages_status'], 'not_loaded')
                        self.assertIn('PR is ready', initial['prior_context']['conclusion'])
                        self.assertNotIn('HANDOFF_EVIDENCE', message['question'])
                        self.assertEqual(initial['bridge_version'], MODULE['VERSION'])
                        self.assertEqual(initial['protocol_revision'], hashlib.sha256(MODULE['INSTRUCTIONS'].encode()).hexdigest())
                        self.assertEqual(message['reply_commands']['exchange'],
                                         [str(ROOT / 'gbb-multica-remote'), 'exchange', 'offline-route', message['id'], '-'])
                    if initial['remaining_requests'] == 0:
                        closed = helper('exchange', message['id'], json.dumps({'gbb_tool_protocol': 1,
                            'request_id': initial['request_id'], 'tool_calls': [{'id': 'over_budget',
                            'name': 'multica', 'arguments': {'args': ['project', 'create', '--title', 'NEVER']}}]}))
                        self.assertEqual((closed['state'], closed['exit_code']), ('closed', 2))
                        return
                    first_id, first_reply = None, None
                    operations = [
                        ('create', ['project', 'create', '--title', 'Continuous']),
                        ('edit', None),  # Filled from the actual create result.
                        ('readback', ['project', 'get', 'p1']),
                        ('failed', ['unsupported', 'operation']),
                    ]
                    for index, (call_id, args) in enumerate(operations):
                        messages.append(message)
                        request = json.loads(message['question'].split('REQUEST_JSON:\n', 1)[1])
                        self.assertEqual(request['delivery_mode'], 'continuous')
                        self.assertEqual(request['interaction_mode'], 'non_interactive')
                        self.assertEqual(message['reply_commands']['answer'][-2:], [message['id'], '-'])
                        if index:
                            self.assertNotIn(prompt, message['question'])
                            self.assertNotIn('user_task', request)
                            self.assertNotIn('history', request)
                            self.assertNotIn('prior_context', request)
                            self.assertNotIn('HANDOFF_EVIDENCE', message['question'])
                            self.assertEqual(len(request['tool_results']), 1)
                            self.assertEqual(request['tool_results'][0]['call_id'], operations[index - 1][0])
                        if call_id == 'edit':
                            args = ['project', 'update', request['tool_results'][0]['result']['data']['id'], '--title', 'Edited']
                        reply = json.dumps({'gbb_tool_protocol': 1, 'request_id': request['request_id'],
                            'tool_calls': [{'id': call_id, 'name': 'multica', 'arguments': {'args': args}}] +
                            ([{'id': 'must_skip', 'name': 'multica', 'arguments': {'args': ['project', 'create', '--title', 'NEVER']}}]
                             if call_id == 'failed' else [])})
                        if index == 0:
                            first_id, first_reply = message['id'], reply
                        result = helper('exchange', message['id'], reply)
                        self.assertEqual(result['state'], 'ready')
                        message = result['message']
                        if index == 0:
                            duplicate = helper('exchange', first_id, first_reply)
                            self.assertEqual(duplicate['message']['id'], message['id'])
                            duplicate_results.append(duplicate)
                    messages.append(message)
                    final = json.loads(message['question'].split('REQUEST_JSON:\n', 1)[1])
                    self.assertFalse(final['tool_results'][0]['result']['ok'])
                    self.assertEqual(final['tool_results'][1]['unexecuted_call_ids'], ['must_skip'])
                    helper('answer', message['id'], json.dumps({'gbb_tool_protocol': 1,
                        'request_id': final['request_id'], 'final': 'CONTINUOUS_OK'}))
                except BaseException as exc:
                    errors.append(str(exc))
                finally:
                    bot_done.set()

        server = ThreadingHTTPServer(('127.0.0.1', 0), Webhook)
        (home / 'config.json').write_text(json.dumps({'defaultBot': 'bridge'}))
        (home / 'credentials.json').write_text(json.dumps({'bridge': {
            'url': f'http://127.0.0.1:{server.server_port}', 'key': 'offline-only'}}))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            result = subprocess.run([sys.executable, str(ROOT / "gbb-multica-agent"), '-p', prompt],
                                    env=env, capture_output=True, text=True, timeout=23)
            self.assertEqual(result.returncode, 0, (result.stderr, errors))
            self.assertTrue(bot_done.wait(3))
            self.assertFalse(errors, errors)
            self.assertEqual(len(seen), 1)
            self.assertEqual(len(messages), 5)
            self.assertEqual(len(list((home / 'messages').glob('*.json'))), 5,
                             'each round must create exactly one tracked inbox message')
            self.assertEqual(len(duplicate_results), 1)
            self.assertEqual(self.state()['projects'], [{'id': 'p1', 'title': 'Edited', 'description': None}])
            events = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(events[-2]['data']['content'], 'CONTINUOUS_OK')
            records = [json.loads(line) for line in (home / 'timing.jsonl').read_text().splitlines()]
            self.assertEqual(sum(r['event'] == 'webhook_send_start' for r in records), 1)
            self.assertEqual(sum(r['event'] == 'continuation_published' for r in records), 4)
            self.assertEqual(sum(r['event'] == 'reply_duplicate' for r in records), 1)
            session = json.loads(next((home / 'sessions').glob('*.json')).read_text())
            self.assertEqual((session['status'], session['exit_code']), ('closed', 0))
            before = self.state()['calls']
            bot_done.clear()
            limited = subprocess.run([sys.executable, str(ROOT / "gbb-multica-agent"), '-p', 'budgetcheck'],
                env=dict(env, MULTICA_TASK_ID='continuous-budget', MULTICA_AGENT_ID='', GBB_MAX_ROUNDS='1'),
                capture_output=True, text=True, timeout=8)
            self.assertEqual(limited.returncode, 2, limited.stderr)
            self.assertTrue(bot_done.wait(3))
            self.assertEqual(self.state()['calls'], before)
            self.assertFalse(errors, errors)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
