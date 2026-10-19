"""Offline roundtrip: real gbb, temporary credentials and a loopback webhook."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def check():
    wrapper = str(Path(__file__).with_name("gbb-multica-agent").resolve())
    answer = 'GBB_RUNTIME_OK\n中文，多行；保留 "引号"、$HOME 和 `字面量`。'
    prompt = '运行时连通性测试\n' + ('长任务 "quotes" $HOME `literal`\n' * 2000)
    with tempfile.TemporaryDirectory(prefix="gbb-offline-") as tmp:
        env = dict(os.environ, GBB_HOME=tmp, GBB_TARGET_BOT="bridge", GBB_WAIT="3",
                   GBB_INSTANCE_ID="linux-alice", GBB_REPLY_ROUTE="alice")
        for key in ("GBB_WEBHOOK_URL", "GBB_WEBHOOK_KEY"):
            env.pop(key, None)
        seen, mode = [], ["answer"]
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
                    reply = subprocess.run(
                        ["/usr/bin/gbb", "answer", payload["message_id"], "-"],
                        input=answer, capture_output=True, text=True, env=env, timeout=5,
                    )
                    if reply.returncode:
                        errors.append(reply.stderr)

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
                    capture_output=True, text=True, timeout=10,
                )

            def events(result):
                return [json.loads(line) for line in result.stdout.splitlines()]

            version = run("--version")
            assert version.returncode == 0 and version.stdout.strip() == "gbb-multica-agent 0.3.0"
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
            request = json.loads(seen[0]["summary"].split("REQUEST_JSON:\n", 1)[1])
            assert request["user_task"] == prompt, "complete task must survive gbb's default truncation limit"
            assert seen[0]['bridge_instance'] == 'linux-alice' and seen[0]['reply_route'] == 'alice'
            assert f'gbb-multica-remote answer alice {seen[0]["message_id"]} -' in seen[0]['next'][0]
            assert request['reply_route'] == 'alice'
            assert os.environ.get("MULTICA_TOKEN", "no-token-sentinel") not in seen[0]["summary"]
            assert [e["data"]["deltaContent"] for e in stream if e["type"] == "assistant.message_delta"] == [answer]
            assert [e["data"]["content"] for e in stream if e["type"] == "assistant.message"] == [answer]
            assert stream[-1] == {"type": "result", "exitCode": 0}
            assert all("sessionId" not in e and "sessionId" not in e.get("data", {}) for e in stream)
            assert not errors, errors

            for flag in ("--model", "--resume", "--unknown"):
                before = len(seen)
                rejected = run("-p", "must not send", flag, "unsupported")
                assert rejected.returncode != 0 and len(seen) == before
            for wait in ("0", "invalid"):
                rejected = run("-p", "must not send", GBB_WAIT=wait)
                assert rejected.returncode != 0 and len(seen) == 1
            rejected = run('-p', 'must not send', GBB_REPLY_ROUTE='alice; touch /tmp/should-not-exist')
            assert rejected.returncode != 0 and len(seen) == 1

            mode[0] = "fail"
            before = len(seen)
            failed = run("-p", "failure test")
            assert failed.returncode != 0 and len(seen) == before + 1, "do not retry webhook failures"
            assert any(e["type"] == "session.error" for e in events(failed))

            mode[0] = "wait"
            timeout = run("-p", "timeout test", GBB_WAIT="1")
            assert timeout.returncode == 2 and events(timeout)[-1]["exitCode"] == 2

            received.clear()
            process = subprocess.Popen(
                [sys.executable, wrapper, "-p", "cancellation test"],
                env=dict(env, GBB_WAIT="900"), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                assert received.wait(5), "request did not reach local webhook"
                children_path = Path(f"/proc/{process.pid}/task/{process.pid}/children")
                children = children_path.read_text().split()
                assert children, "expected the foreground gbb child"
                process.send_signal(signal.SIGTERM)
                process.communicate(timeout=5)
                assert process.returncode == 130
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
