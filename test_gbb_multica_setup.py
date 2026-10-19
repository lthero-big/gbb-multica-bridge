"""Exercise setup against a fake Multica CLI; never touch the real API or Bots."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile

SCRIPT = Path(__file__).with_name("gbb-multica-setup.sh").resolve()
FAKE_CLI = r'''#!/usr/bin/python3
import json,os,sys
from pathlib import Path
p=Path(os.environ["FAKE_MULTICA_STATE"])
s=json.loads(p.read_text())
a=sys.argv[1:]
while a[:1] in (["--workspace-id"], ["--profile"]):
 if a[0]=='--profile': s.setdefault('login_profiles',[]).append(a[1])
 a=a[2:]
s["calls"].append(a)
def val(flag): return a[a.index(flag)+1]
def write(kind): s["writes"].append(kind)
out=None
raw=False
if a[:2]==["workspace","get"]:
 out={"id":"setup-test-workspace","name":"Fake workspace"}
elif a[:2]==["daemon","status"]:
 out={"pid":s["daemon_pid"],"status":"running","daemon_id":"setup-test-daemon",
      "device_name":"local-test-machine","workspaces":[{"id":"setup-test-workspace"}]}
elif a[:3]==["runtime","profile","list"]: out=s["profiles"]
elif a[:3]==["runtime","profile","create"]:
 write("profile.create")
 out={"id":"test-profile","display_name":val("--display-name"),"protocol_family":"copilot",
      "command_name":"gbb-multica-agent","enabled":True,"fixed_args":[]}
 s["profiles"].append(out)
 if s.pop("bad_create_json_once",False): raw=True; out="created, response formatting failed"
elif a[:3]==["runtime","profile","update"]:
 write("profile.update"); s["profiles"][0]["enabled"]=True; out=s["profiles"][0]
elif a[:3]==["runtime","profile","set-path"]:
 write("set-path"); s["path"]=val("--path"); out="Pinned local path"; raw=True
elif a[:2]==["runtime","list"]:
 out=[{"id":"foreign-runtime","profile_id":"test-profile","daemon_id":"different-machine","status":"online"}]
 if s.get("path"):
  out.append({"id":"local-runtime","profile_id":"test-profile","daemon_id":"setup-test-daemon","status":"online"})
elif a[:2]==["agent","list"]: out=s["agents"]
elif a[:3]==["agent","env","get"]: out=s["env"]
elif a[:3]==["agent","env","set"]:
 assert "--custom-env-stdin" in a
 write("env.set"); s["env"]=json.loads(sys.stdin.read()); out=s["env"]
elif a[:2]==["agent","tasks"]: out=s.get("runs",[])
elif a[:2]==["agent","create"]:
 assert "--custom-env-stdin" in a
 write("agent.create"); s["env"]=json.loads(sys.stdin.read())
 out={"id":"test-agent","name":val("--name"),"runtime_id":val("--runtime-id"),
      "model":"","custom_args":[],"max_concurrent_tasks":1,"instructions":val("--instructions")}
 s["agents"].append(out)
elif a[:2]==["agent","update"]:
 write("agent.update"); out=s["agents"][0]
 out.update(runtime_id=val("--runtime-id"),model=val("--model"),thinking_level="",service_tier="",
            custom_args=json.loads(val("--custom-args")),max_concurrent_tasks=int(val("--max-concurrent-tasks")),
            instructions=val("--instructions"))
elif a[:2]==["agent","get"]: out=s["agents"][0]
else:
 print("Unexpected fake CLI call",file=sys.stderr); sys.exit(8)
p.write_text(json.dumps(s))
print(out if raw else json.dumps(out))
'''


def check():
    with tempfile.TemporaryDirectory(prefix="gbb-setup-test-") as tmp:
        root = Path(tmp)
        fake_bin, home, install_dir = root / "fake-bin", root / "gbb", root / "installed"
        fake_bin.mkdir()
        home.mkdir()
        fake_cli = fake_bin / "multica"
        fake_cli.write_text(FAKE_CLI)
        fake_cli.chmod(0o755)
        fake_gbb = fake_bin / "gbb"
        fake_gbb.write_text('#!/bin/sh\necho "ERROR: setup must not call gbb" >&2\nexit 90\n')
        fake_gbb.chmod(0o755)
        (fake_bin / "package.json").write_text('{"type":"module"}')
        (fake_bin / "config.js").write_text('''
export function loadConfig() { return {defaultBot: 'bridge'}; }
export function getCredentials() { return {url: 'https://example.invalid/webhook', key: 'offline'}; }
''')
        (fake_bin / "messages.js").write_text('''
export function newMessage() { throw new Error('MUST_NOT_CREATE_MESSAGE'); }
export function waitForAnswer() { throw new Error('MUST_NOT_WAIT'); }
''')
        webhook_source = '''
export const VERSION = '0.2.0';
export function send() { throw new Error('MUST_NOT_SEND'); }
export function buildPayload() { throw new Error('MUST_NOT_BUILD_PAYLOAD'); }
'''
        (fake_bin / "webhook.js").write_text(webhook_source)
        state_path = root / "state.json"
        # Deliberately give fake commands no real Multica identity or webhook env.
        env = {"PATH":f"{fake_bin}:/usr/bin:/bin", "LANG":"C.UTF-8", "FAKE_MULTICA_STATE":str(state_path)}
        def reset(**changes):
            state = {"daemon_pid":os.getpid(),"calls":[],"writes":[],"profiles":[],"agents":[],"env":{}}
            state.update(changes)
            state_path.write_text(json.dumps(state))
            (home / "config.json").write_text(json.dumps({"defaultBot":"bridge","hubBot":"bridge"}))
            (home / "credentials.json").write_text(json.dumps({"bridge":{"url":"https://example.invalid/webhook","key":"DO_NOT_PRINT_FAKE_KEY"}}))
        def state(): return json.loads(state_path.read_text())
        def run(*args, task=False):
            return subprocess.run(["bash",str(SCRIPT),"--gbb-home",str(home),"--bin-dir",str(install_dir),*args],
                                  env=dict(env, **({"MULTICA_TASK_ID":"fake-task"} if task else {})),
                                  capture_output=True,text=True,timeout=15)
        def passed(result):
            assert result.returncode==0,(result.stdout,result.stderr)
            assert "DO_NOT_PRINT_FAKE_KEY" not in result.stdout + result.stderr
            assert "KEEP_FAKE_SECRET" not in result.stdout + result.stderr
        profile={"id":"test-profile","display_name":"Grok Bot Bridge","protocol_family":"copilot",
                 "command_name":"gbb-multica-agent","fixed_args":[],"enabled":True}
        agent={"id":"test-agent","name":"Grok Bot Bridge 测试","runtime_id":"foreign-runtime",
               "model":"unsupported-model","custom_args":["--unsupported"],"max_concurrent_tasks":6}
        try:
            reset()
            checked=run("--check")
            assert checked.returncode==2 and state()["writes"]==[] and not install_dir.exists()
            passed(run())
            first=state()
            assert first["writes"].count("profile.create")==1 and first["writes"].count("agent.create")==1
            assert first["agents"][0]["runtime_id"]=="local-runtime", "must ignore the foreign runtime even when it appears first"
            assert first["env"]=={"GBB_HOME":str(home),"GBB_TARGET_BOT":"bridge","GBB_WAIT":"900"}
            wrapper=install_dir/"gbb-multica-agent"
            assert os.access(wrapper,os.X_OK)
            version=subprocess.run([str(wrapper),"--version"],env=env,capture_output=True,text=True,check=True)
            assert version.stdout.strip()=="gbb-multica-agent 0.4.0"
            passed(run())
            second=state()
            assert second["writes"].count("profile.create")==1 and second["writes"].count("agent.create")==1
            assert "agent.update" not in second["writes"] and "env.set" not in second["writes"]
            passed(run("--check"))

            reset()
            passed(run('--profile', 'alice', '--instance-id', 'linux-alice', '--reply-route', 'alice'))
            routed = state()
            assert routed['login_profiles'] and set(routed['login_profiles']) == {'alice'}
            assert routed['env']['GBB_INSTANCE_ID'] == 'linux-alice' and routed['env']['GBB_REPLY_ROUTE'] == 'alice'
            collision = run('--instance-id', 'linux-bob')
            assert collision.returncode == 1 and '其他部署实例' in collision.stderr

            reset(profiles=[profile.copy()],agents=[agent.copy()],env={"GBB_HOME":"/wrong-machine","EXISTING_SECRET":"KEEP_FAKE_SECRET"})
            passed(run())
            fixed=state()
            assert fixed["agents"][0]["runtime_id"]=="local-runtime" and fixed["agents"][0]["model"]==""
            assert fixed["env"]["EXISTING_SECRET"]=="KEEP_FAKE_SECRET" and fixed["env"]["GBB_HOME"]==str(home)
            assert fixed["writes"].count("agent.update")==1 and fixed["writes"].count("env.set")==1
            assert not any("KEEP_FAKE_SECRET" in arg for call in fixed["calls"] for arg in call)

            reset()
            rejected=run(task=True)
            assert rejected.returncode==1 and state()["calls"]==[], "task identity must not be used to configure host profiles"
            reset(profiles=[profile.copy()],agents=[dict(agent,runtime_id="local-runtime",model="",custom_args=[],max_concurrent_tasks=1,
                    instructions=first['agents'][0]['instructions'])],path=str(wrapper))
            passed(run("--check",task=True))
            assert not any(call[:3]==["agent","env","get"] for call in state()["calls"])

            reset(profiles=[profile.copy(),profile.copy()])
            assert run().returncode==1 and state()["writes"]==[]
            reset()
            (home/"credentials.json").unlink()
            assert run().returncode==1 and state()["writes"]==[]
            reset(profiles=[profile.copy()],agents=[agent.copy()],runs=[{"status":"running"}])
            assert run().returncode==1 and state()["writes"]==[]

            reset(daemon_pid=0)
            assert run().returncode==1 and state()["writes"]==[]
            other_uid = next(entry.pw_uid for entry in pwd.getpwall() if entry.pw_uid != os.geteuid())
            fake_ps = fake_bin / "ps"
            fake_ps.write_text(f'#!/bin/sh\necho {other_uid}\n')
            fake_ps.chmod(0o755)
            try:
                reset()
                wrong_owner = run()
                assert wrong_owner.returncode==1 and state()["writes"]==[]
                assert "请以该用户执行" in wrong_owner.stderr
            finally:
                fake_ps.unlink()

            reset(bad_create_json_once=True)
            assert run().returncode==1
            assert state()["writes"].count("profile.create")==1 and len(state()["profiles"])==1
            passed(run())
            assert state()["writes"].count("profile.create")==1, "a committed write with bad response must not be retried blindly"

            reset()
            before_wrapper = wrapper.read_bytes()
            (fake_bin / "webhook.js").write_text("export const VERSION = '0.3.0';\n")
            incompatible = run()
            assert incompatible.returncode == 1 and "module-api:missing=" in incompatible.stderr
            assert state()["writes"] == [] and wrapper.read_bytes() == before_wrapper
            (fake_bin / "webhook.js").write_text(webhook_source)
        finally:
            lock=Path(pwd.getpwuid(os.geteuid()).pw_dir)/".local/state/gbb-multica/setup-test-workspace-setup-test-daemon.lock"
            lock.unlink(missing_ok=True)
    print("PASS: create/reuse, repeat runs, correct-machine binding, env merge, no secret output, check mode, task identity, active-run guard, missing config and partial-write recovery")


if __name__=="__main__":
    check()
