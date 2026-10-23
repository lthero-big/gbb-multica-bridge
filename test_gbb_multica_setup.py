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
def env_in():
 if "--custom-env-file" in a:
  path=val("--custom-env-file"); assert (os.stat(path).st_mode & 0o077)==0; return json.loads(Path(path).read_text())
 assert "--custom-env-stdin" in a
 return json.loads(sys.stdin.read())
# shape: None/"flat" = Multica 0.6.3, "custom_env" = 0.6.5, "wrapped" = plausible future envelopes,
# "unknown_env" = an env envelope the bridge must refuse to guess.
shape=s.get("shape")
def rows(v): return {"data":{"items":v},"next_cursor":None} if shape=="wrapped" else v
def obj(v): return {"data":v} if shape=="wrapped" else v
def env_out(agent_id, env):
 if shape=="custom_env": return {"agent_id":agent_id,"custom_env":env}
 if shape=="wrapped": return {"data":{"agent_id":agent_id,"env":[{"key":k,"value":v} for k,v in env.items()]}}
 if shape=="unknown_env": return {"agent_id":agent_id,"secrets":env}
 return env
out=None
raw=False
if "--help" in a:
 flags=["--help","--output","--custom-env-stdin","--custom-env-file","--limit"]
 flags=[f for f in flags if f not in s.get("missing_flags",[])]
 out="Usage:\n  multica "+" ".join(a[:-1])+" [flags]\n\nFlags:\n"+"".join("      "+f+" string\n" for f in flags); raw=True
 print(out); sys.exit(0)
if a[:2]==["workspace","get"]:
 out={"id":"setup-test-workspace","name":"Fake workspace"}
elif a[:2]==["daemon","status"]:
 out={"pid":s["daemon_pid"],"status":"running","daemon_id":"setup-test-daemon",
      "device_name":"local-test-machine","workspaces":[{"id":"setup-test-workspace"}]}
 if shape=="wrapped": out={"data":{"daemon":dict(out,status="RUNNING",workspaces=["setup-test-workspace"])}}
elif a[:3]==["runtime","profile","list"]: out=rows(s["profiles"])
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
elif a[:2]==["agent","list"]: out=rows(s["agents"])
elif a[:3]==["agent","env","get"]: out=env_out(a[3],s["env"])
elif a[:3]==["agent","env","set"]:
 write("env.set"); s["env"]={k:v for k,v in env_in().items() if k not in s.get("drop_env",[])}; out=env_out(a[3],s["env"])
elif a[:2]==["agent","tasks"]: out=rows(s.get("runs",[]))
elif a[:2]==["agent","create"]:
 write("agent.create"); s["env"]=env_in()
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
if not raw and (a[:2] in (["workspace","get"],["agent","get"],["agent","create"],["agent","update"]) or
                a[:3] in (["runtime","profile","create"],["runtime","profile","update"])):
 out=obj(out)
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
            assert first["env"]=={"GBB_HOME":str(home),"GBB_TARGET_BOT":"bridge","GBB_WAIT":"3600"}
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

            reset(profiles=[profile.copy()],agents=[agent.copy()],env={"GBB_HOME":"/wrong-machine","GBB_WAIT":"900","EXISTING_SECRET":"KEEP_FAKE_SECRET"})
            passed(run())
            fixed=state()
            assert fixed["agents"][0]["runtime_id"]=="local-runtime" and fixed["agents"][0]["model"]==""
            assert fixed["env"]["EXISTING_SECRET"]=="KEEP_FAKE_SECRET" and fixed["env"]["GBB_HOME"]==str(home)
            assert fixed["env"]["GBB_WAIT"]=="3600", "upgrade the previous 15-minute budget"
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

            # Multica output shapes: 0.6.3 flat env (above), 0.6.5 {"agent_id","custom_env"},
            # and a plausible future release that wraps every response in envelopes.
            for shape in ("custom_env", "wrapped"):
                reset(shape=shape, profiles=[profile.copy()], agents=[agent.copy()],
                      env={"GBB_HOME":"/wrong-machine","EXISTING_SECRET":"KEEP_FAKE_SECRET"})
                assert run("--check").returncode==2 and state()["writes"]==[], shape
                passed(run())
                shaped=state()
                assert shaped["env"]=={"GBB_HOME":str(home),"GBB_TARGET_BOT":"bridge","GBB_WAIT":"3600",
                                       "EXISTING_SECRET":"KEEP_FAKE_SECRET"}, (shape, shaped["env"])
                assert shaped["agents"][0]["runtime_id"]=="local-runtime" and shaped["writes"].count("env.set")==1
                passed(run())
                repeat=[w for w in state()["writes"][len(shaped["writes"]):] if w!="set-path"]
                assert repeat==[], f"{shape}: a second run must not rewrite the agent: {repeat}"
                passed(run("--check"))
                reset(shape=shape)
                passed(run())
                assert state()["writes"].count("agent.create")==1 and state()["env"]["GBB_TARGET_BOT"]=="bridge"

            reset(shape="unknown_env", profiles=[profile.copy()], agents=[agent.copy()],
                  env={"EXISTING_SECRET":"KEEP_FAKE_SECRET"})
            unknown=run()
            assert unknown.returncode==1 and state()["writes"]==[], unknown.stderr
            assert "multica agent env get" in unknown.stderr and "[agent_id, secrets]" in unknown.stderr, unknown.stderr
            assert "KEEP_FAKE_SECRET" not in unknown.stdout + unknown.stderr
            assert run("--check").returncode==1

            # CLIs without --custom-env-stdin receive the env through a private file, never argv.
            reset(missing_flags=["--custom-env-stdin"], profiles=[profile.copy()], agents=[agent.copy()],
                  env={"EXISTING_SECRET":"KEEP_FAKE_SECRET"})
            passed(run())
            filed=state()
            assert filed["env"]["EXISTING_SECRET"]=="KEEP_FAKE_SECRET" and filed["env"]["GBB_HOME"]==str(home)
            env_files=[c[c.index("--custom-env-file")+1] for c in filed["calls"] if "--custom-env-file" in c]
            assert env_files and not any(Path(f).exists() for f in env_files)
            assert not any("KEEP_FAKE_SECRET" in arg for call in filed["calls"] for arg in call)
            reset(missing_flags=["--custom-env-stdin","--custom-env-file"])
            no_env_input=run()
            assert no_env_input.returncode==1 and "--custom-env-stdin" in no_env_input.stderr

            # Readback compares only the keys setup owns and names mismatches without values.
            reset(profiles=[profile.copy()], agents=[agent.copy()], drop_env=["GBB_WAIT"],
                  env={"EXISTING_SECRET":"KEEP_FAKE_SECRET"})
            mismatch=run()
            assert mismatch.returncode==1 and "配置回读不一致" in mismatch.stderr and "GBB_WAIT" in mismatch.stderr
            assert "GBB_HOME" not in mismatch.stderr and "3600" not in mismatch.stderr

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
    print("PASS: create/reuse, repeat runs, correct-machine binding, env merge, no secret output, check mode, task identity, active-run guard, missing config, partial-write recovery, Multica 0.6.3/0.6.5/wrapped output shapes, env-file fallback and key-only readback")


def check_normalizers():
    """Shared Multica JSON normalizers: one per command shape, embedded copy kept in sync."""
    import re
    import runpy
    agent_path = SCRIPT.with_name("gbb-multica-agent")
    embedded = re.search(r"WRAPPER_SOURCE = r\'\'\'(.*?)\n\'\'\'\n", SCRIPT.read_text(), re.S)
    assert embedded and embedded.group(1) + "\n" == agent_path.read_text(), \
        "gbb-multica-setup.sh WRAPPER_SOURCE must stay identical to gbb-multica-agent"
    n = runpy.run_path(str(agent_path))
    env, rows, obj, daemon = n["multica_env"], n["multica_rows"], n["multica_object"], n["multica_daemon"]
    want = {"GBB_HOME": "/h", "GBB_WAIT": "3600"}
    for value in (want,                                               # 0.6.3
                  {"agent_id": "a", "custom_env": want},              # 0.6.5
                  {"env": want}, {"data": {"custom_env": want}},      # plausible future envelopes
                  {"data": {"env": [{"key": k, "value": v} for k, v in want.items()]}},
                  [{"name": k, "value": v} for k, v in want.items()]):
        assert env(value) == want, value
    assert env({}) == {} and env({"agent_id": "a", "custom_env": {}}) == {}
    assert env({"agent_id": "a", "custom_env": None}) == {}
    for bad in ({"agent_id": "a", "secrets": {"K": "SECRET_VALUE"}}, {"K": 1}, "text", [1]):
        try:
            env(bad)
        except n["MulticaShapeError"] as exc:
            assert "multica agent env get" in str(exc) and "SECRET_VALUE" not in str(exc)
        else:
            raise AssertionError(bad)
    row = {"id": "r1", "name": "x"}
    for value in ([row], {"data": [row]}, {"items": [row]}, {"agents": [row]},
                  {"data": {"items": [row]}, "next_cursor": None}, {"edges": [{"node": row}]}, {"nodes": [row]},
                  {"data": [{"node": row}]}):
        assert rows(value, "agent list", ("agents",)) == [row], value
    try:
        rows({"total": 1, "agent": row}, "agent list", ("agents",))
    except n["MulticaShapeError"] as exc:
        assert "agent list" in str(exc) and "[agent, total]" in str(exc)
    else:
        raise AssertionError("unknown list shape accepted")
    for value in (row, {"data": row}, {"agent": row}, {"data": {"agent": row}}):
        assert obj(value, "agent get", ("agent",)) == row
    flat = {"status": "running", "pid": 7, "daemon_id": "d", "workspaces": [{"id": "w"}]}
    wrapped = {"data": {"daemon": {"state": "RUNNING", "pid": "7", "id": "d", "workspaces": ["w"]}}}
    for value in (flat, wrapped):
        got = daemon(value)
        assert (got["status"], n["multica_int"](got["pid"]), got["daemon_id"], got["workspace_ids"]) == ("running", 7, "d", ["w"])
    assert n["multica_help_flags"]("Flags:\n  -h, --help   help\n      --output string\n") == {"--help", "--output"}
    project = n["project_fields"]
    assert project({"data": {"items": [{"id": 1, "title": "t", "secret": "x"}]}}, ["id", "title"]) == [{"id": 1, "title": "t"}]
    assert project({"id": 1, "title": "t", "x": 2}, ["id"]) == {"id": 1}
    print("PASS: Multica JSON normalizers (0.6.3 flat, 0.6.5 custom_env, wrapped envelopes, unknown-shape errors) and embedded wrapper sync")


if __name__=="__main__":
    check_normalizers()
    check()
