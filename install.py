#!/usr/bin/env python3
"""Local deployment, runtime, reply and SSH setup; CLI and interactive wizard."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import runpy
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
ARCHIVE_SHA256 = 'f5f2815bbaa272f34e0115b86b2344d7d60a0ce2546b8ee37765637b735e1b79'
SAFE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}')


def require(value, message):
    if not value:
        raise ValueError(message)
    return value


def run(args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, timeout=kwargs.pop("timeout", 120), **kwargs)


def write_file(path, content, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not path.is_symlink(), f'拒绝覆盖符号链接：{path}')
    data = content.encode('utf-8')
    if path.exists() and path.read_bytes() == data:
        path.chmod(mode)
        return
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + f'.bak-{time.time_ns()}'))
    fd, temporary = tempfile.mkstemp(prefix='.gbb-install-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_config(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    require(isinstance(value, dict), f'配置必须是 JSON 对象：{path}')
    return value


def ask(value, label, default=None):
    if value is not None:
        return value
    if not sys.stdin.isatty():
        require(default is not None, f'非交互模式缺少参数：{label}')
        return default
    typed = input(label + (f' [{default}]' if default else '') + ': ').strip()
    return require(typed or default, f'{label}不能为空')


def safe_name(value):
    return require(isinstance(value, str) and SAFE_NAME.fullmatch(value),
                   '名称只能使用字母、数字、点、下划线、连字符，长度 1–80') and value


def install_executable(name, bin_dir):
    source = (ROOT / name).read_text()
    if source.startswith('#!/usr/bin/env python3'):
        source = source.replace('#!/usr/bin/env python3', '#!' + sys.executable, 1)
    destination = bin_dir / name
    write_file(destination, source, 0o755)
    return destination


def show_prompt(content, title):
    print(f'\n{title}：请复制下方两条分隔线之间的全部内容，粘贴到对应 Grok Bot。')
    print('========== Grok Bot 提示词开始 ==========')
    print(content.rstrip())
    print('========== Grok Bot 提示词结束 ==========\n', flush=True)


def configure_credentials(node, gbb, home, bot):
    config = read_config(home / 'config.json')
    credentials = read_config(home / 'credentials.json')
    old = credentials.get(bot, {})
    if (config.get('hubBot') == bot and isinstance(old, dict)
            and isinstance(old.get('url'), str) and old['url'].startswith('https://')
            and isinstance(old.get('key'), str) and old['key']):
        print(f'复用已有 {bot} webhook 凭证（未发送请求）。')
        return
    require(sys.stdin.isatty(), '缺少 webhook 配置。请在交互终端重跑并输入 URL/key。')
    show_prompt((ROOT / 'docs/grok-bot-prompt.md').read_text(), f'为 {bot} 配置 webhook 的初始模板')
    print('已有 routine 可复用原 URL/key；模板中的助手路径和路由将在回传端配置后自动填好并显示，请再复制更新到 Bot。')
    url = input('Webhook URL: ').strip()
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password,
            'Webhook URL 必须为 HTTPS URL')
    key = getpass.getpass('Webhook key（隐藏）: ').strip()
    require(key, 'Webhook key 不能为空')
    # Reuse gbb's own credential store without putting secrets in argv or logs.
    code = r'''
const fs = require('node:fs'), path = require('node:path');
const {pathToFileURL} = require('node:url');
(async () => {
  const {bot, url, key} = JSON.parse(fs.readFileSync(0, 'utf8'));
  const mod = await import(pathToFileURL(path.join(path.dirname(fs.realpathSync(process.argv[1])), 'config.js')).href);
  mod.setCredentials(bot, {url, key});
  mod.rememberBot(bot);
  const cfg = mod.loadConfig(); cfg.hubBot = bot; cfg.defaultBot = bot; mod.saveConfig(cfg);
})().catch(() => { console.error('GBB credential configuration failed'); process.exitCode = 1; });
'''
    result = subprocess.run([node, '-e', code, str(gbb)],
                            input=json.dumps(dict(bot=bot, url=url, key=key)),
                            text=True, capture_output=True, timeout=15,
                            env=dict(os.environ, GBB_HOME=str(home)))
    require(result.returncode == 0, 'gbb 凭证保存失败；未输出 URL/key，请检查当前用户目录权限')
    print('凭证已保存；未发送测试 ping，也未安装其他 CLI 的技能。')


def runtime(args, config_root, bin_dir):
    require(platform.system() in ('Linux', 'Darwin'), '运行端支持 Linux/macOS')
    task = (os.environ.get('MULTICA_TOKEN', '').startswith('mat_') or
            any(os.environ.get(k) for k in ('MULTICA_TASK_ID', 'MULTICA_AGENT_ID', 'MULTICA_TASK_CONFIG_ROOT')))
    require(not task or args.check, '请在 daemon 用户已登录 Multica 的普通终端执行，不能借用聊天任务凭证安装')
    previous = read_config(config_root / 'server.json')
    instance = safe_name(args.instance_id or previous.get('instance_id') or
                         re.sub(r'[^A-Za-z0-9._-]', '-', socket.gethostname().split('.')[0] + '-' + getpass.getuser())[:80])
    require(not previous or previous.get('instance_id') == instance,
            '此用户已配置另一个实例；每个系统用户使用一个实例，切换前请先核对原配置')
    bot = ask(args.bot, 'Grok Bot 名称/本地 alias', previous.get('bot') or 'bridge-' + instance)
    require(bot.strip() and not bot.startswith('-') and '\n' not in bot, 'Bot 名称无效')
    home = Path(args.gbb_home or previous.get('gbb_home') or str(Path.home() / '.grok-bot-bridge')).expanduser().resolve()
    node = require(shutil.which('node'), '需要 Node.js >= 20；安装后重跑即可')
    major = run([node, '--version'], capture_output=True, text=True).stdout.strip().lstrip('v').split('.')[0]
    require(major.isdigit() and int(major) >= 20, '需要 Node.js >= 20')
    npm = require(shutil.which('npm'), '需要 npm')
    require(shutil.which('multica'), '需要已安装、登录且 daemon 运行中的 Multica CLI')
    archive = ROOT / 'vendor/grok-bot-bridge-0.2.0.tgz'
    require(hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA256, 'gbb 0.2.0 安装包 SHA-256 不匹配，停止安装')
    prefix = Path.home() / '.local/share/gbb-multica/npm'
    gbb = prefix / 'bin/gbb'
    profile = args.profile if args.profile is not None else previous.get('profile')
    workspace = args.workspace_id if args.workspace_id is not None else previous.get('workspace_id')
    base = ['multica', *(['--profile', profile] if profile else []),
            *(['--workspace-id', workspace] if workspace else [])]
    daemon = json.loads(run([*base, 'daemon', 'status', '--output', 'json'],
                            text=True, capture_output=True).stdout)
    require(daemon.get('status') == 'running', '本机 Multica daemon 尚未运行')
    pid = int(daemon['pid'])
    require(pid > 0, 'daemon PID 无效，无法核实进程归属')
    daemon_uid = int(run(['ps', '-o', 'uid=', '-p', str(pid)], text=True, capture_output=True).stdout.strip())
    require(daemon_uid == os.geteuid(),
            '当前用户不是 daemon 的系统用户；停止安装，请换到正确账号')
    current = json.loads(run([*base, 'workspace', 'get', '--output', 'json'],
                             text=True, capture_output=True).stdout)
    workspace = current['id']
    require(any(w.get('id') == workspace for w in daemon.get('workspaces', [])),
            '目标工作区未关联到本机 daemon；先检查登录配置及 daemon')
    route = safe_name(args.reply_route or previous.get('reply_route') or instance)
    agent = args.agent_name or previous.get('agent_name') or 'Grok Bot Bridge · ' + instance
    setup = ['bash', str(ROOT / 'gbb-multica-setup.sh'), '--gbb-home', str(home),
             '--bin-dir', str(bin_dir), '--instance-id', instance, '--reply-route', route,
             '--agent-name', agent, '--target-bot', bot]
    if profile:
        setup += ['--profile', profile]
    if workspace:
        setup += ['--workspace-id', workspace]
    env = dict(os.environ, PATH=str(prefix / 'bin') + os.pathsep + os.environ.get('PATH', ''), GBB_HOME=str(home))
    if args.check:
        require(gbb.exists(), '尚未安装此脚本管理的 gbb 0.2.0；先安装运行端（runtime）')
        return subprocess.run(setup + ['--check'], env=env, timeout=120).returncode
    if not (gbb.exists() and previous.get('archive_sha256') == ARCHIVE_SHA256):
        run([npm, 'install', '--global', '--prefix', prefix, '--offline', '--ignore-scripts', archive])
    version = run([gbb, 'version'], env=env, text=True, capture_output=True).stdout.strip()
    require(version == '0.2.0', '安装后 gbb 版本不是 0.2.0')
    configure_credentials(node, gbb, home, bot)
    # This script verifies the daemon owner and guards active Agent tasks before registration.
    run(setup, env=env, timeout=600)
    endpoint = install_executable('gbb-multica-endpoint', bin_dir)
    install_executable('gbb-multica-setup.sh', bin_dir)
    saved = dict(instance_id=instance, bot=bot, gbb_home=str(home), gbb_bin=str(gbb),
                 node_bin=node, wrapper=str(bin_dir / 'gbb-multica-agent'), user=pwd.getpwuid(os.geteuid()).pw_name,
                 profile=profile, workspace_id=workspace, reply_route=route, agent_name=agent,
                 archive_sha256=ARCHIVE_SHA256)
    write_file(config_root / 'server.json', json.dumps(saved, ensure_ascii=False, indent=2) + '\n')
    run([endpoint, '--instance-id', instance, 'check'])
    print(f'运行端安装完成。回填路由：{route}；实例：{instance}')
    print(f'Endpoint：{endpoint}')
    if args.mode != 'local':
        print('接下来配置回传端（可在同机）；安装过程没有向 Grok 发送消息。')
    return 0


def prompt_for_routes(routes, helper):
    catalog = []
    for name, item in sorted(routes.items()):
        connection = '本机同用户' if item.get('transport') == 'local' else f'SSH `{item["ssh_alias"]}`'
        catalog.append(f'- route `{name}` → {connection}，实例 `{item["instance_id"]}`，Bot `{item.get("bot", "bridge")}`')
    return ((ROOT / 'docs/grok-bot-prompt.md').read_text()
            .replace('{{REMOTE_HELPER}}', str(helper))
            .replace('{{REMOTE_HELPER_COMMAND}}', shlex.quote(str(helper)))
            .replace('{{ROUTE_CATALOG}}', '\n'.join(catalog)))


def ssh_connection(args, config_root, alias, previous=None):
    """Save independent connections even if a later reply endpoint check fails."""
    from ssh_setup import setup_ssh
    config = read_config(config_root / 'ssh.json')
    connections = config.setdefault('connections', {})
    previous = connections.get(alias, previous or {})
    old = previous.get('ssh_connection', {})
    result = setup_ssh(alias,
        host=ask(args.ssh_host, '目标主机域名或 IP', old.get('host')),
        user=ask(args.ssh_user, '目标系统用户名', old.get('user')),
        port=args.ssh_port or old.get('port', 22),
        key_path=args.ssh_key or old.get('key_path'), write_file=write_file)
    connections[alias] = result
    write_file(config_root / 'ssh.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    return result


def ssh(args, config_root, bin_dir):
    require(shutil.which('ssh'), '需要 OpenSSH 客户端')
    connections = read_config(config_root / 'ssh.json').get('connections', {})
    alias = safe_name(ask(args.ssh_alias, 'SSH 连接别名', next(iter(connections)) if len(connections) == 1 else None))
    if args.check:
        item = dict(ssh_alias=alias, **connections.get(alias, {}))
        router = runpy.run_path(str(ROOT / 'gbb-multica-remote'))
        run([*router['ssh_base'](item), 'true'])
        print(f'SSH 免密连接检查通过：{alias}；未检查 Multica 或回填端点。')
        return 0
    result = ssh_connection(args, config_root, alias)
    print('SSH 配置完成，不依赖 Multica、gbb 或回传端安装。')
    print('登录命令：' + shlex.join(['ssh', '-F', result['ssh_config'], alias]))
    print('配置回传端时使用 --ssh-alias ' + alias + ' 即可复用此连接。')
    return 0


def reply(args, config_root, bin_dir):
    require(platform.system() in ('Linux', 'Darwin'), '回传端当前支持 Linux/macOS')
    route_name = safe_name(ask(args.route or args.instance_id, '回填路由名（与运行端 reply-route 一致）'))
    config = read_config(config_root / 'routes.json')
    routes = config.setdefault('routes', {})
    previous = routes.get(route_name, {})
    local_config = read_config(config_root / 'server.json')
    local_match = route_name == local_config.get('reply_route')
    instance = safe_name(args.instance_id or previous.get('instance_id') or
                         (local_config.get('instance_id') if local_match else None) or route_name)
    local_match = local_match and instance == local_config.get('instance_id')
    bootstrap = getattr(args, 'setup_ssh', False)
    previous_transport = previous.get('transport', 'ssh') if previous else None
    transport = (getattr(args, 'transport', None) or ('ssh' if args.ssh_alias or bootstrap else None) or
                 previous_transport or ('local' if local_match else 'ssh'))
    require(transport in ('local', 'ssh'), '回传方式必须为 local 或 ssh')
    require(not (bootstrap and args.check), '--setup-ssh 会修改密钥/公钥配置，不能与只读 --check 同用')
    item = dict(transport=transport, instance_id=instance,
                bot=args.bot or previous.get('bot') or (local_config.get('bot') if local_match else None) or 'bridge-' + instance)
    if transport == 'ssh':
        require(shutil.which('ssh'), '需要 OpenSSH 客户端')
        connections = read_config(config_root / 'ssh.json').get('connections', {})
        default_alias = previous.get('ssh_alias') or (next(iter(connections)) if len(connections) == 1 else None)
        alias = safe_name(ask(args.ssh_alias, 'SSH 连接别名', default_alias or
                              (('multica-' + route_name)[:80] if bootstrap else None)))
        extra = {key: previous[key] for key in ('ssh_config', 'ssh_connection')
                 if key in previous and alias == previous.get('ssh_alias')}
        extra.update(connections.get(alias, {}))
        if bootstrap:
            extra = ssh_connection(args, config_root, alias, extra)
        item.update(ssh_alias=alias, **extra)
    else:
        require(not bootstrap and not args.ssh_alias, '本机回填不使用 SSH 参数；不同系统用户请选择 ssh')
    router = runpy.run_path(str(ROOT / 'gbb-multica-remote'))
    same_target = (transport == previous.get('transport', 'ssh') and
                   (transport == 'local' or item.get('ssh_alias') == previous.get('ssh_alias')))
    endpoint = args.endpoint or (previous.get('endpoint') if same_target else None)
    if not endpoint:
        if transport == 'local':
            endpoint = str(Path.home() / '.local/bin/gbb-multica-endpoint')
        else:
            # Fixed discovery command: no caller text interpolated into a remote shell.
            discovered = run([*router['ssh_base'](item),
                              'test -x "$HOME/.local/bin/gbb-multica-endpoint" && printf "%s\\n" "$HOME/.local/bin/gbb-multica-endpoint"'],
                             capture_output=True, text=True)
            endpoint = discovered.stdout.strip()
    item['endpoint'] = endpoint
    checked = run(router['route_command'](item, 'check'), capture_output=True, text=True)
    status = json.loads(checked.stdout)
    require(status.get('ok') is True and status.get('instance_id') == instance and status.get('gbb_version') == '0.2.0',
            '目标实例/版本/配置不匹配，未保存路由')
    target = item.get('ssh_alias', '本机同用户')
    if args.check:
        print(f'回填链路检查通过：{route_name} → {target}；未发送 Grok 请求。')
        return 0
    helper = install_executable('gbb-multica-remote', bin_dir)
    routes[route_name] = item
    write_file(config_root / 'routes.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    prompt = config_root / 'grok-bot-prompt.md'
    content = prompt_for_routes(routes, helper)
    write_file(prompt, content)
    print(f'回传端配置完成。路由：{route_name} → {target}；未发送 Grok 请求。')
    print(f'完整提示词已保存：{prompt}')
    print('检查命令：' + shlex.join([str(helper), 'check', route_name]))
    show_prompt(content, '已填好助手路径和路由的完整提示词')
    print('请用以上完整内容更新已有 Bot 的处理说明，并允许其工具执行环境调用助手。')
    print('然后在 Multica 新聊天发送：请只回复 GBB_RUNTIME_OK。')
    return 0


def local(args, config_root, bin_dir):
    """Install both roles for this user, then bind the reply route without SSH."""
    result = runtime(args, config_root, bin_dir)
    if result:
        return result
    saved = read_config(config_root / 'server.json')
    require(saved, '本机运行端配置尚未保存；先完成本机部署，再执行 --check')
    endpoint = str(Path(saved['wrapper']).with_name('gbb-multica-endpoint'))
    if args.check:
        route = read_config(config_root / 'routes.json').get('routes', {}).get(saved['reply_route'], {})
        require(route.get('transport') == 'local' and route.get('instance_id') == saved['instance_id'] and
                route.get('endpoint') == endpoint, '本机回填路由缺失或不匹配；请重跑 local 安装')
        helper = bin_dir / 'gbb-multica-remote'
        require(helper.is_file() and os.access(helper, os.X_OK), '本机回填助手尚未安装；请重跑 local 安装')
        run([helper, 'check', saved['reply_route']])
        print('本机部署检查通过。')
        return 0
    reply_args = argparse.Namespace(**vars(args))
    reply_args.route = saved['reply_route']
    reply_args.instance_id = saved['instance_id']
    reply_args.bot = saved['bot']
    reply_args.transport = 'local'
    reply_args.endpoint = endpoint
    result = reply(reply_args, config_root, bin_dir)
    if not result:
        print('本机部署完成：运行端和回传端已配置，无需 SSH。')
    return result


# Keep imported functions and old CLI commands compatible with earlier installers.
server = runtime
mac = reply


def choose(label, options, default=1):
    print('\n' + label)
    for index, option in enumerate(options, 1):
        print(f'  {index}. {option}')
    while True:
        value = input(f'请选择 [{default}]: ').strip() or str(default)
        if value.isdigit() and 1 <= int(value) <= len(options):
            return int(value)
        print('请输入列表中的序号。')


def optional(label, default='', empty_hint='留空使用当前配置'):
    value = input(f'{label} [{default or empty_hint}]（输入 - 清空）: ').strip()
    return '' if value == '-' else value or default


def wizard_ssh(args, config_root, previous=None):
    connections = read_config(config_root / 'ssh.json').get('connections', {})
    if connections:
        print('已保存的 SSH 连接：' + '、'.join(sorted(connections)))
    suggested = (previous or {}).get('ssh_alias') or (next(iter(connections)) if len(connections) == 1 else 'multica-target')
    args.ssh_alias = safe_name(ask(args.ssh_alias, 'SSH 连接别名（可自定义）', suggested))
    old = connections.get(args.ssh_alias, previous or {}).get('ssh_connection', {})
    args.ssh_host = ask(args.ssh_host, '目标主机域名/IP（同机其他用户可填 localhost）', old.get('host'))
    args.ssh_user = ask(args.ssh_user, '目标系统用户名（回填时必须是 daemon 用户）', old.get('user'))
    args.ssh_port = int(ask(args.ssh_port, 'SSH 端口', old.get('port', 22)))
    args.ssh_key = ask(args.ssh_key, '本机私钥路径（不存在时生成）', old.get('key_path') or
                       str(Path.home() / '.ssh' / ('gbb_multica_' + args.ssh_alias + '_ed25519')))


def wizard(args, config_root):
    require(sys.stdin.isatty(), '交互向导需要终端；自动化执行请指定 local / runtime / reply / ssh 及参数')
    if not args.mode:
        selected = choose('GBB × Multica 配置向导（按职责选择，可在同一台机器上分别执行）',
                          ['本机部署：Grok Bot 与 Multica 在本机同用户运行，无需 SSH',
                           '运行端：安装 gbb，注册 Multica runtime / Agent',
                           '回传端：配置本机或 SSH 回填路由，生成 Bot 提示词',
                           'SSH：单独配置免密登录，无需安装运行端/回传端', '退出'])
        if selected == 5:
            return False
        args.mode = ('local', 'runtime', 'reply', 'ssh')[selected - 1]
    if args.mode in ('local', 'runtime'):
        previous = read_config(config_root / 'server.json')
        default_instance = re.sub(r'[^A-Za-z0-9._-]', '-', socket.gethostname().split('.')[0] + '-' + getpass.getuser())[:80]
        args.instance_id = safe_name(ask(args.instance_id, '运行端实例名（稳定标识）', previous.get('instance_id') or default_instance))
        args.bot = ask(args.bot, '已有 Grok Bot 的名称/本地 alias', previous.get('bot') or 'bridge-' + args.instance_id)
        print('Multica profile 是已有登录配置名，不是系统用户名；留空使用当前配置。')
        if args.profile is None:
            args.profile = optional('Multica profile', previous.get('profile') or '')
        if args.workspace_id is None:
            args.workspace_id = optional('工作区 UUID', previous.get('workspace_id') or '')
        args.agent_name = ask(args.agent_name, 'Multica Agent 名称（升级时填原名）', previous.get('agent_name') or 'Grok Bot Bridge · ' + args.instance_id)
        args.reply_route = safe_name(ask(args.reply_route, '回填路由名', previous.get('reply_route') or args.instance_id))
        args.gbb_home = ask(args.gbb_home, 'gbb 数据目录', previous.get('gbb_home') or str(Path.home() / '.grok-bot-bridge'))
        print(f'\n将为当前系统用户配置运行端：实例={args.instance_id}，Bot={args.bot}，Agent={args.agent_name}')
        print(f'Multica profile={args.profile or "当前配置"}，工作区={args.workspace_id or "当前工作区"}，回填路由={args.reply_route}')
        if args.mode == 'local':
            print('本次同时配置本机回传助手、路由和 Bot 提示词；不需要 SSH 或 SSH key。')
    elif args.mode == 'reply':
        routes = read_config(config_root / 'routes.json').get('routes', {})
        local = read_config(config_root / 'server.json')
        if routes:
            print('已有回填路由：' + '、'.join(sorted(routes)))
        default_route = local.get('reply_route') or (next(iter(routes)) if len(routes) == 1 else None)
        args.route = safe_name(ask(args.route or args.instance_id, '回填路由名（运行端 reply-route）', default_route))
        old = routes.get(args.route, {})
        local_instance = local.get('instance_id') if args.route == local.get('reply_route') else None
        args.instance_id = safe_name(ask(args.instance_id, '目标运行端实例名', old.get('instance_id') or local_instance or args.route))
        local_bot = local.get('bot') if args.instance_id == local.get('instance_id') else None
        args.bot = ask(args.bot, 'Bot 名称标注', old.get('bot') or local_bot or 'bridge-' + args.instance_id)
        if not args.transport:
            default = 1 if old.get('transport') == 'local' or (not old and local.get('instance_id') == args.instance_id) else 2
            args.transport = ('local', 'ssh')[choose('回填连接方式',
                ['本机直连：同一台机器、同一系统用户，无需 SSH', 'SSH：不同机器或不同系统用户（同机可连 localhost）'], default) - 1]
        if args.transport == 'ssh':
            bootstrap = args.setup_ssh or (not args.check and choose('SSH 登录方式',
                ['复用已有免密连接', '新建/修复免密连接（必要时输入一次目标账号密码）']) == 2)
            if bootstrap:
                args.setup_ssh = True
                wizard_ssh(args, config_root, old)
            else:
                connections = read_config(config_root / 'ssh.json').get('connections', {})
                if connections:
                    print('可复用 SSH 连接：' + '、'.join(sorted(connections)))
                args.ssh_alias = safe_name(ask(args.ssh_alias, 'SSH 连接别名', old.get('ssh_alias') or
                    (next(iter(connections)) if len(connections) == 1 else None)))
        if args.endpoint is None:
            same_target = (args.transport == old.get('transport', 'ssh') and
                           (args.transport == 'local' or args.ssh_alias == old.get('ssh_alias')))
            endpoint = old.get('endpoint', '') if same_target else ''
            args.endpoint = optional('运行端 endpoint 绝对路径', endpoint, '留空自动发现') or None
        print(f'\n将配置回传端：路由={args.route}，实例={args.instance_id}，连接={args.transport}，SSH={args.ssh_alias or "无需"}')
    else:
        if args.check:
            connections = read_config(config_root / 'ssh.json').get('connections', {})
            args.ssh_alias = safe_name(ask(args.ssh_alias, '要检查的 SSH 连接别名',
                next(iter(connections)) if len(connections) == 1 else None))
        else:
            wizard_ssh(args, config_root)
        print(f'\nSSH 连接：{args.ssh_alias}；不安装 Multica 或 gbb。')
    if (args.mode == 'ssh' and not args.check) or args.setup_ssh:
        print(f'SSH 目标：{args.ssh_user}@{args.ssh_host}:{args.ssh_port}；本机私钥：{args.ssh_key}')
    if args.check:
        print('本次仅检查，不修改配置。')
    if args.dry_run:
        print('本次仅展示，不连接或写入。')
        return True
    confirmed = input('确认执行？[y/N]: ').strip().lower() in ('y', 'yes')
    if not confirmed:
        print('已取消，未执行配置。')
    return confirmed


def build_parser():
    parser = argparse.ArgumentParser(
        prog='bash install.sh', description='配置本机一体部署、运行端、回传端或独立 SSH；不带参数进入交互菜单。',
        allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''示例（在仓库目录执行）：
  bash install.sh                         # 交互菜单
  bash install.sh local                   # 本机部署向导，无需 SSH
  bash install.sh runtime --interactive   # 运行端向导
  bash install.sh reply --interactive     # 回传端向导，可选择本机直连
  bash install.sh ssh                     # 独立 SSH 向导
  bash install.sh runtime --instance-id host-alice --bot bridge
  bash install.sh reply --route host-alice --transport local --bot bridge
  bash install.sh reply --route host-alice --ssh-alias multica-alice --bot bridge
  bash install.sh ssh --ssh-alias multica-alice --ssh-host example.com --ssh-user alice

local / runtime / reply / ssh 支持 Linux、macOS。本机同用户部署选 local，无需 SSH。
旧 server / mac 命令分别是 runtime / reply 的兼容别名。
--profile 选择已有 Multica 登录配置；省略使用当前配置，无需新建 profile。
更多参数与同机/跨机示例见 README.md。''')
    parser.add_argument('mode', nargs='?', choices=('local', 'runtime', 'reply', 'ssh', 'server', 'mac'),
                        help='local=本机一体部署；runtime=运行端；reply=回传端；ssh=独立免密配置；省略进入菜单')
    common = parser.add_argument_group('通用')
    common.add_argument('--interactive', '-i', action='store_true', help='逐项询问配置并确认；可以预填其他参数')
    common.add_argument('--instance-id', metavar='INSTANCE', help='运行端稳定标识；默认主机名-用户名；回传端默认 route')
    common.add_argument('--bot', metavar='BOT', help='运行端的 gbb Bot alias / 回传端提示词标注；默认 bridge-实例名')
    common.add_argument('--check', action='store_true', help='只读检查；不发送 Grok 请求；SSH 模式仅验证免密登录')
    common.add_argument('--dry-run', action='store_true', help='仅展示步骤；不连接、不写文件')
    runtime_options = parser.add_argument_group('本机部署 local / 运行端 runtime')
    runtime_options.add_argument('--profile', help='已有 Multica 登录配置；默认当前配置，不创建 profile')
    runtime_options.add_argument('--workspace-id', metavar='UUID', help='目标工作区 UUID；默认当前工作区')
    runtime_options.add_argument('--reply-route', metavar='ROUTE', help='回填路由名；默认 instance-id，须与回传端 route 一致')
    runtime_options.add_argument('--agent-name', metavar='NAME', help='创建/复用的 Multica Agent 名称；默认 Grok Bot Bridge · 实例名')
    runtime_options.add_argument('--gbb-home', metavar='DIR', help='运行端当前用户的 gbb 数据目录；默认 ~/.grok-bot-bridge')
    reply_options = parser.add_argument_group('回传端 reply')
    reply_options.add_argument('--route', metavar='ROUTE', help='回填路由名，须与运行端 reply-route 一致')
    reply_options.add_argument('--transport', choices=('local', 'ssh'), help='local=同机同用户直连；ssh=跨主机/用户；新路由匹配本机部署时自动选 local，否则 ssh')
    reply_options.add_argument('--endpoint', metavar='PATH', help='运行端 endpoint 绝对路径；默认发现 ~/.local/bin/gbb-multica-endpoint')
    ssh_options = parser.add_argument_group('独立 ssh / 回传端 SSH')
    ssh_options.add_argument('--ssh-alias', metavar='HOST', help='已有或新建的 SSH 连接别名；独立配置可被回传端复用')
    ssh_options.add_argument('--setup-ssh', action='store_true', help='reply 时先配置免密连接；ssh 模式本身即配置入口')
    ssh_options.add_argument('--ssh-host', metavar='HOST', help='目标域名/IP；同机不同用户可填 localhost')
    ssh_options.add_argument('--ssh-user', metavar='USER', help='目标系统用户名；回填时必须是 daemon 用户')
    ssh_options.add_argument('--ssh-port', metavar='PORT', type=int, help='目标 SSH 端口；默认 22')
    ssh_options.add_argument('--ssh-key', metavar='PATH', help='本机私钥路径；默认 ~/.ssh/gbb_multica_别名_ed25519')
    return parser


def validate_args(parser, args):
    connection_options = any(value is not None for value in (args.ssh_host, args.ssh_user, args.ssh_port, args.ssh_key))
    if args.mode == 'reply' and any(value is not None for value in
            (args.profile, args.workspace_id, args.reply_route, args.agent_name, args.gbb_home)):
        parser.error('Multica profile/工作区等运行端参数请用于 runtime 模式')
    if args.mode in ('local', 'runtime') and any(value is not None for value in (args.route, args.endpoint)):
        parser.error('--route/endpoint 用于回传端 reply；本机部署/运行端的路由参数是 --reply-route')
    if args.mode == 'runtime' and (args.setup_ssh or connection_options or args.ssh_alias or args.transport):
        parser.error('运行端不使用 SSH/transport 参数；请单独使用 ssh 或 reply')
    if args.mode == 'local' and (args.setup_ssh or connection_options or args.ssh_alias or args.transport == 'ssh'):
        parser.error('本机部署不使用 SSH 参数；跨机器/用户请分别使用 runtime / reply')
    if args.mode == 'reply' and connection_options and not args.setup_ssh:
        parser.error('--ssh-host/user/port/key 需与 --setup-ssh 一起使用，或单独使用 ssh 模式')
    if args.mode == 'reply' and args.transport == 'local' and (args.setup_ssh or connection_options or args.ssh_alias):
        parser.error('local 是同机同用户直连，不使用 SSH 参数')
    if args.check and (args.setup_ssh or connection_options):
        parser.error('SSH 配置参数不能与只读 --check 同用')
    if args.ssh_port is not None and not 1 <= args.ssh_port <= 65535:
        parser.error('--ssh-port 必须为 1–65535')
    if args.mode == 'ssh' and any((args.profile, args.workspace_id, args.reply_route, args.agent_name, args.gbb_home,
                                  args.route, args.transport, args.endpoint, args.instance_id, args.bot)):
        parser.error('独立 ssh 模式只接受 SSH 参数，不配置 Multica 或回填路由')


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.mode = {'server': 'runtime', 'mac': 'reply'}.get(args.mode, args.mode)
    validate_args(parser, args)
    require(sys.version_info >= (3, 9), '需要 Python 3.9+')
    config_root = Path(os.environ.get('GBB_MULTICA_CONFIG_HOME', str(Path.home() / '.config/gbb-multica'))).expanduser()
    interactive = args.interactive or args.mode is None or (len(sys.argv) == 2 and sys.stdin.isatty())
    if interactive:
        if not wizard(args, config_root):
            return 0
        validate_args(parser, args)
    if args.dry_run:
        print({'local': '本机部署：安装运行端 → 注册 runtime/Agent → 本机 endpoint → 本机回传路由 → Bot 提示词；无需 SSH',
               'runtime': '运行端：校验 gbb 0.2.0 → 用户目录安装 → 配置已有 Bot → 检查 daemon → 注册 runtime/Agent → 安装 endpoint',
               'reply': '回传端：选择本机/SSH → 核对运行端实例 → 安装回填助手 → 合并路由 → 生成 Bot 提示词',
               'ssh': 'SSH：生成/复用密钥 → 必要时一次密码登录追加公钥 → 验证免密 → 保存独立连接'}[args.mode])
        return 0
    os.umask(0o077)
    return {'local': local, 'runtime': runtime, 'reply': reply, 'ssh': ssh}[args.mode](args, config_root, Path.home() / '.local/bin')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f'安装/检查停止：{exc}。已完成的操作会保留；确认现状后可重跑。', file=sys.stderr)
        raise SystemExit(1)
    except (EOFError, KeyboardInterrupt):
        print('\n安装已停止；没有自动重试。', file=sys.stderr)
        raise SystemExit(130)
