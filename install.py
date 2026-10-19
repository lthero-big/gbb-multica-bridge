#!/usr/bin/env python3
"""Interactive Linux server / macOS reply-router installer. Python stdlib only."""
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
    print('请先把 docs/grok-bot-prompt.md 交给已有 Bot，取得 webhook URL/key；已有 routine 可直接复用。')
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


def server(args, config_root, bin_dir):
    require(platform.system() == 'Linux', 'server 模式面向 Linux；Mac 请使用 mac 模式')
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
    profile = args.profile or previous.get('profile')
    workspace = args.workspace_id or previous.get('workspace_id')
    base = ['multica', *(['--profile', profile] if profile else []),
            *(['--workspace-id', workspace] if workspace else [])]
    daemon = json.loads(run([*base, 'daemon', 'status', '--output', 'json'],
                            text=True, capture_output=True).stdout)
    require(daemon.get('status') == 'running', '本机 Multica daemon 尚未运行')
    require(Path('/proc', str(int(daemon['pid']))).stat().st_uid == os.geteuid(),
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
        require(gbb.exists(), '尚未安装此脚本管理的 gbb 0.2.0；先运行 server 模式')
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
    print(f'服务器安装完成。Mac 回填路由：{route}；实例：{instance}')
    print(f'Endpoint：{endpoint}')
    print('接下来在 Mac 配置此路由；安装过程没有向 Grok 发送消息。')
    return 0


def prompt_for_routes(routes, helper):
    catalog = '\n'.join(f'- route `{name}` → SSH `{item["ssh_alias"]}`，实例 `{item["instance_id"]}`，Bot `{item.get("bot", "bridge")}`'
                        for name, item in sorted(routes.items()))
    return (ROOT / 'docs/grok-bot-prompt.md').read_text().replace('{{REMOTE_HELPER}}', str(helper)).replace('{{ROUTE_CATALOG}}', catalog)


def mac(args, config_root, bin_dir):
    require(platform.system() == 'Darwin', 'mac 模式请在运行 Grok Bot 客户端的 Mac 上执行')
    require(shutil.which('ssh'), '需要 OpenSSH 客户端')
    route_name = safe_name(ask(args.route or args.instance_id, '回填路由名（与服务器一致）'))
    config = read_config(config_root / 'routes.json')
    routes = config.setdefault('routes', {})
    previous = routes.get(route_name, {})
    alias = safe_name(ask(args.ssh_alias, '已配置的 SSH Host 别名', previous.get('ssh_alias')))
    instance = safe_name(args.instance_id or previous.get('instance_id') or route_name)
    endpoint = args.endpoint or previous.get('endpoint')
    if not endpoint:
        # Fixed discovery command: no caller text interpolated into a remote shell.
        discovered = run(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '--', alias,
                          'test -x "$HOME/.local/bin/gbb-multica-endpoint" && printf "%s\\n" "$HOME/.local/bin/gbb-multica-endpoint"'],
                         capture_output=True, text=True)
        endpoint = discovered.stdout.strip()
    item = dict(ssh_alias=alias, endpoint=endpoint, instance_id=instance,
                bot=args.bot or previous.get('bot') or 'bridge-' + instance)
    router = runpy.run_path(str(ROOT / 'gbb-multica-remote'))
    checked = run(router['ssh_command'](item, 'check'), capture_output=True, text=True)
    status = json.loads(checked.stdout)
    require(status.get('ok') is True and status.get('instance_id') == instance and status.get('gbb_version') == '0.2.0',
            '远端实例/版本/配置不匹配，未保存路由')
    if args.check:
        print(f'SSH 回填链路检查通过：{route_name} → {alias}；未发送 Grok 请求。')
        return 0
    helper = install_executable('gbb-multica-remote', bin_dir)
    routes[route_name] = item
    write_file(config_root / 'routes.json', json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    prompt = config_root / 'grok-bot-prompt.md'
    write_file(prompt, prompt_for_routes(routes, helper))
    print(f'Mac 配置完成。路由：{route_name} → {alias}；未发送 Grok 请求。')
    print(f'提示词：{prompt}；将内容交给已有 Grok Bot，并允许它使用 Mac 电脑执行能力。')
    print('检查命令：' + shlex.join([str(helper), 'check', route_name]))
    print('Mac 仅需 Python + SSH，不需要 Multica 登录凭证，也不需要另装本地 gbb。')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('mode', choices=('server', 'mac'))
    parser.add_argument('--check', action='store_true', help='只读检查已配置的部署，不发送 Grok 请求')
    parser.add_argument('--dry-run', action='store_true', help='仅显示步骤；不连接服务器、不写文件')
    for name in ('instance-id', 'reply-route', 'route', 'bot', 'profile', 'workspace-id',
                 'agent-name', 'gbb-home', 'ssh-alias', 'endpoint'):
        parser.add_argument('--' + name)
    args = parser.parse_args()
    require(sys.version_info >= (3, 9), '需要 Python 3.9+')
    if args.dry_run:
        print('server: 校验本地 gbb 0.2.0 包 → 用户目录安装 → 配置已有 Bot → 检查 daemon → 注册 runtime/Agent → SSH endpoint'
              if args.mode == 'server' else
              'mac: 使用已有 SSH alias → 核对远端实例 → 安装回填助手 → 合并路由 → 生成 Bot 提示词')
        return 0
    os.umask(0o077)
    config_root = Path(os.environ.get('GBB_MULTICA_CONFIG_HOME', str(Path.home() / '.config/gbb-multica'))).expanduser()
    bin_dir = Path.home() / '.local/bin'
    return (server if args.mode == 'server' else mac)(args, config_root, bin_dir)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # Subprocess exception text can contain arguments. Credentials are never arguments.
        print(f'安装/检查停止：{exc}。已完成的操作会保留；确认现状后可重跑。', file=sys.stderr)
        raise SystemExit(1)
    except (EOFError, KeyboardInterrupt):
        print('\n安装已停止；没有自动重试。', file=sys.stderr)
        raise SystemExit(130)
