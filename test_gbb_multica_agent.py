"""Offline roundtrip: real gbb, temporary credentials and a loopback webhook."""

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def check():
    wrapper = str(Path(__file__).with_name("gbb-multica-agent").resolve())
    endpoint = str(Path(__file__).with_name("gbb-multica-endpoint").resolve())
    answer = 'GBB_RUNTIME_OK\n中文，多行；保留 "引号"、$HOME 和 `字面量`。'
    prompt = '运行时连通性测试\n' + ('长任务 "quotes" $HOME `literal`\n' * 2000)
    with tempfile.TemporaryDirectory(prefix="gbb-offline-") as tmp:
        env = dict(os.environ, GBB_HOME=tmp, GBB_TARGET_BOT="bridge",
                   GBB_INSTANCE_ID="linux-alice", GBB_REPLY_ROUTE="alice", GBB_MULTICA_CONFIG_HOME=tmp,
                   GBB_NO_KEYCHAIN="1")
        task_dir = Path(tmp, 'task').resolve()
        task_dir.mkdir()
        instructions = 'Multica task instructions\n' + '完整上下文\n' * 6000 + 'END_CONTEXT'
        (task_dir / 'AGENTS.md').write_text(instructions)
        resources_dir = task_dir / '.multica/project'
        resources_dir.mkdir(parents=True)
        (resources_dir / 'resources.json').write_text(json.dumps({
            'project_id': 'p1', 'project_title': 'offline project', 'resources': [{
                'resource_type': 'local_directory', 'resource_ref': {
                    'local_path': '/original/project', 'execution_mode': 'worktree'}}]}))
        Path(tmp, 'server.json').write_text(json.dumps({
            'instance_id': 'linux-alice', 'gbb_home': tmp, 'node_bin': sys.executable,
        }))
        for key in ("GBB_WEBHOOK_URL", "GBB_WEBHOOK_KEY", "GBB_WAIT"):
            env.pop(key, None)
        env.pop("MULTICA_AGENT_ID", None)  # Keep this transport test offline; handoff has its own fake CLI tests.
        seen, mode, reply_delay = [], ["answer"], [0]
        received = threading.Event()
        errors = []

        class Webhook(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(payload)
                self.send_response(503 if mode[0] == "fail" else 200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                received.set()
                if mode[0] == "answer":
                    time.sleep(reply_delay[0])
                    pending = subprocess.run(
                        [sys.executable, endpoint, "--instance-id", "linux-alice", "inbox", payload["message_id"]],
                        capture_output=True, text=True, env=env, timeout=5,
                    )
                    if pending.returncode != 2:
                        errors.append("inbox must return 2 while waiting for an answer")
                    else:
                        message = json.loads(pending.stdout)
                        if message["id"] != payload["message_id"] or message["question"] != payload["summary"]:
                            errors.append("inbox must preserve the matching message's complete question")
                    reply = subprocess.run(
                        [sys.executable, endpoint, "--instance-id", "linux-alice", "answer", payload["message_id"], "-"],
                        input=answer, capture_output=True, text=True, env=env, timeout=5,
                    )
                    if reply.returncode:
                        errors.append(reply.stderr)
                elif mode[0] == "reject":
                    failed = subprocess.run(
                        [sys.executable, endpoint, "--instance-id", "linux-alice", "fail", payload["message_id"], "-"],
                        input="Bot cannot process this request", capture_output=True, text=True, env=env, timeout=5,
                    )
                    if failed.returncode:
                        errors.append(failed.stderr)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Webhook)
        url = f"http://127.0.0.1:{server.server_address[1]}/webhook"
        Path(tmp, "config.json").write_text(json.dumps({
            "defaultBot": "bridge", "hubBot": "bridge", "maxSummaryChars": 6000,
        }))
        Path(tmp, "credentials.json").write_text(json.dumps({"bridge": {"url": url, "key": "offline-only"}}))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            def run(*args, **changes):
                return subprocess.run(
                    [sys.executable, wrapper, *args], env=dict(env, **changes),
                    capture_output=True, text=True, timeout=10, cwd=task_dir,
                )

            def events(result):
                return [json.loads(line) for line in result.stdout.splitlines()]

            version = run("--version")
            assert version.returncode == 0 and version.stdout.strip() == "gbb-multica-agent 0.5.5"
            assert not seen, "version must never send a request"

            config_before = Path(tmp, "config.json").read_bytes()
            checked = run("--check")
            assert checked.returncode == 0, checked.stderr
            assert json.loads(checked.stdout)["ok"] is True
            assert not seen and not Path(tmp, "messages").exists(), "check must not create or send messages"
            assert Path(tmp, "config.json").read_bytes() == config_before

            result = run("-p", prompt, "--output-format", "json", "--allow-all", "--no-ask-user")
            stream = events(result)
            assert result.returncode == 0, result.stderr
            budget = re.search(r"waiting up to (\d+)s", result.stderr)
            assert budget and 3500 <= int(budget.group(1)) <= 3600, "unset GBB_WAIT must provide a one-hour budget"
            request = json.loads(seen[0]["summary"].split("REQUEST_JSON:\n", 1)[1])
            assert request["user_task"] == prompt, "complete task must survive gbb's default truncation limit"
            assert request['runtime_instructions']['content'] == instructions
            assert request['execution_context']['working_directory'] == str(task_dir)
            assert request['execution_context']['project']['local_path'] == '/original/project'
            assert request['execution_context']['target_local_device_name']
            assert seen[0]['bridge_instance'] == 'linux-alice' and seen[0]['reply_route'] == 'alice'
            assert f'gbb-multica-remote answer alice {seen[0]["message_id"]} -' in seen[0]['next'][0]
            assert request['reply_route'] == 'alice'
            keys = list(seen[0])
            assert all(keys.index(key) < keys.index('summary') for key in
                       ('message_id', 'bridge_instance', 'reply_route', 'next')), 'routing must precede long task text'
            assert len(seen[0]['text']) < 1000, 'text must not duplicate the full task'
            assert prompt not in seen[0]['text']
            assert os.environ.get("MULTICA_TOKEN", "no-token-sentinel") not in seen[0]["summary"]
            assert [e["data"]["deltaContent"] for e in stream if e["type"] == "assistant.message_delta"] == [answer]
            assert [e["data"]["content"] for e in stream if e["type"] == "assistant.message"] == [answer]
            assert stream[-1] == {"type": "result", "exitCode": 0}
            assert all("sessionId" not in e and "sessionId" not in e.get("data", {}) for e in stream)
            assert not errors, errors
            timing_path = Path(tmp, 'timing.jsonl')
            records = [json.loads(line) for line in timing_path.read_text().splitlines()]
            names = [r['event'] for r in records]
            for expected in ('task_start', 'round_start', 'message_created', 'webhook_send_start',
                             'webhook_send_end', 'inbox_start', 'inbox_end', 'reply_start',
                             'reply_saved', 'reply_end', 'answer_received', 'round_return', 'task_end'):
                assert expected in names, expected
            created = next(r for r in records if r['event'] == 'message_created')
            assert created['message_id'] == seen[0]['message_id']
            assert created['round'] == 1 and len(created['request_id']) == 32
            for r in records:
                assert r['v'] == 1 and r['ts'].endswith('Z')
                if 'elapsed_ms' in r: assert r['elapsed_ms'] >= 0
                if r['event'] in ('inbox_end', 'reply_saved', 'answer_received'):
                    assert r['request_id'] == created['request_id'] and r['task_id'] == created['task_id']
            assert records[-1]['event'] == 'task_end' and records[-1]['exit_code'] == 0
            assert timing_path.stat().st_mode & 0o777 == 0o600
            assert prompt not in timing_path.read_text() and answer not in timing_path.read_text()
            assert 'offline-only' not in timing_path.read_text() and url not in timing_path.read_text()
            answered = subprocess.run(
                [sys.executable, endpoint, '--instance-id', 'linux-alice', 'inbox', seen[0]['message_id']],
                capture_output=True, text=True, env=env, timeout=5,
            )
            assert answered.returncode == 0, answered.stderr
            assert json.loads(answered.stdout)['answer'] == answer

            # Both Python and Node logging must fail open without polluting JSON stdout.
            saved_log = timing_path.with_suffix('.saved')
            timing_path.rename(saved_log)
            timing_path.mkdir()
            try:
                unavailable = run('-p', 'logging failure must not break the bridge')
                assert unavailable.returncode == 0, unavailable.stderr
                assert 'GBB timing log unavailable' in unavailable.stderr
                assert events(unavailable)[-1]['exitCode'] == 0
            finally:
                timing_path.rmdir()
                saved_log.rename(timing_path)

            # File notifications are an optimization, never the only delivery path.
            reply_delay[0] = 0.35  # Answer after the waiter is running, not during send().
            for watcher_source in (
                "require('node:fs').watch = () => { throw Error('watch unavailable'); };",
                "require('node:fs').watch = () => ({on() {}, close() {}});",
                "require('node:fs').watch = (_, cb) => { const w = new (require('node:events').EventEmitter)(); w.close=()=>{}; setImmediate(()=>w.emit('error', Error('watch failed'))); return w; };",
            ):
                preload = Path(tmp, 'watch-test.cjs')
                preload.write_text(watcher_source)
                fallback = run('-p', 'watcher fallback test', NODE_OPTIONS='--require=' + str(preload))
                assert fallback.returncode == 0, fallback.stderr
                assert any(e['type'] == 'assistant.message' and e['data']['content'] == answer for e in events(fallback))
            reply_delay[0] = 0

            mode[0] = 'reject'
            before = len(seen)
            started = time.monotonic()
            rejected_by_bot = run('-p', 'Bot failure test', GBB_WAIT='300')
            assert rejected_by_bot.returncode == 1, rejected_by_bot.stderr
            assert time.monotonic() - started < 5, 'Bot failures must not wait for GBB_WAIT'
            assert len(seen) == before + 1 and not errors, 'failure must not trigger a retry'
            assert 'Bot cannot process this request' in rejected_by_bot.stderr
            assert events(rejected_by_bot)[-1]['exitCode'] == 1
            assert json.loads(timing_path.read_text().splitlines()[-1])['exit_code'] == 1
            mode[0] = 'answer'
            baseline = len(seen)

            for flag in ("--model", "--resume", "--unknown"):
                before = len(seen)
                rejected = run("-p", "must not send", flag, "unsupported")
                assert rejected.returncode != 0 and len(seen) == before
            for wait in ("0", "invalid"):
                rejected = run("-p", "must not send", GBB_WAIT=wait)
                assert rejected.returncode != 0 and len(seen) == baseline
            rejected = run('-p', 'must not send', GBB_REPLY_ROUTE='alice; touch /tmp/should-not-exist')
            assert rejected.returncode != 0 and len(seen) == baseline
            for overrides in ({'GBB_REPLY_ROUTE': ''}, {'GBB_INSTANCE_ID': ''}):
                rejected = run('-p', 'must not send', **overrides)
                assert rejected.returncode != 0 and len(seen) == baseline

            mode[0] = "fail"
            before = len(seen)
            failed = run("-p", "failure test")
            assert failed.returncode != 0 and len(seen) == before + 1, "do not retry webhook failures"
            assert any(e["type"] == "session.error" for e in events(failed))
            records = [json.loads(line) for line in timing_path.read_text().splitlines()]
            sent = [r for r in records if r['event'] == 'webhook_send_end'][-1]
            assert sent['ok'] is False and sent['http_status'] == 503
            assert records[-1]['event'] == 'task_end' and records[-1]['exit_code'] == 1

            mode[0] = "wait"
            timeout = run("-p", "timeout test", GBB_WAIT="1")
            assert timeout.returncode == 2 and events(timeout)[-1]["exitCode"] == 2
            records = [json.loads(line) for line in timing_path.read_text().splitlines()]
            assert any(r['event'] in ('answer_timeout', 'task_budget_expired') and r['task_id'] == records[-1]['task_id'] for r in records)
            assert records[-1]['event'] == 'task_end' and records[-1]['exit_code'] == 2

            received.clear()
            process = subprocess.Popen(
                [sys.executable, wrapper, "-p", "cancellation test"],
                env=dict(env, GBB_WAIT="900"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=task_dir,
            )
            try:
                assert received.wait(5), "request did not reach local webhook"
                children_path = Path(f"/proc/{process.pid}/task/{process.pid}/children")
                children = children_path.read_text().split()
                assert children, "expected the foreground gbb child"
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=5)
                assert process.returncode == 130
                terminal = json.loads(timing_path.read_text().splitlines()[-1])
                assert terminal['event'] == 'task_end' and terminal['exit_code'] == 130
                mid = seen[-1]['message_id']
                message = json.loads(Path(tmp, 'messages', mid + '.json').read_text())
                session = json.loads(Path(tmp, 'sessions', message['bridge']['session_id'] + '.json').read_text())
                assert session['status'] == 'closed' and session['exit_code'] == 130
                assert all(not Path(f"/proc/{pid}").exists() for pid in children), "cancel must reap gbb"
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print("PASS: real gbb local answer roundtrip, long/multiline prompt, no-send validation, failure, timeout and cancellation cleanup")


if __name__ == "__main__":
    check()
