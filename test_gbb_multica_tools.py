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
REAL_CLI = shutil.which("multica")
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
                    "MULTICA_TOKEN": "mat_OFFLINE_TASK_CREDENTIAL_123456789",
                    "MULTICA_WORKSPACE_ID": "fake-workspace", "MULTICA_TASK_ID": "offline-task",
                    "GBB_HOME": str(self.root / "gbb"), "GBB_WAIT": "20"}
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
