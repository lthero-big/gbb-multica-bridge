"""Bootstrap a Mac SSH identity using OpenSSH's own password prompt."""
import base64
import ipaddress
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys


# Read only the public key on stdin; preserve existing keys, options and comments.
# No password, private key or user-provided script is sent to the server.
INSTALL_PUBLIC_KEY = r'''
set -eu
umask 077
IFS= read -r public_key
key_type=${public_key%% *}
key_blob=${public_key#* }
key_blob=${key_blob%% *}
test -n "$key_type" && test -n "$key_blob"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
if ! awk -v type="$key_type" -v blob="$key_blob" '
  {for (i=1; i<NF; i++) if ($i==type && $(i+1)==blob) found=1}
  END {exit !found}
' "$HOME/.ssh/authorized_keys"; then
  # A newline also handles a pre-existing final line without a newline.
  printf '\n%s\n' "$public_key" >> "$HOME/.ssh/authorized_keys"
fi
'''


def config_quote(value):
    value = str(value)
    if any(c in value for c in '\r\n\0') or '${' in value:
        raise ValueError('SSH 路径不能包含换行、NUL 或 ${...}')
    # OpenSSH expands percent tokens even inside quoted path values.
    return '"' + value.replace('%', '%%').replace('\\', '\\\\').replace('"', '\\"') + '"'


def public_part(value):
    fields = value.strip().split()
    if len(fields) < 2 or '\n' in value.strip() or '\r' in value:
        raise ValueError('SSH 公钥格式无效')
    try:
        raw = base64.b64decode(fields[1], validate=True)
        length = int.from_bytes(raw[:4], 'big')
        if not length or raw[4:4 + length].decode('ascii') != fields[0]:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ValueError('SSH 公钥内容无效') from None
    return fields[0] + ' ' + fields[1] + ' gbb-multica\n'


def setup_ssh(alias, *, host, user, port, key_path, write_file):
    """Return a managed config path only after public-key authentication works."""
    if not shutil.which('ssh-keygen'):
        raise ValueError('需要 ssh-keygen；Mac 系统通常已提供')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', alias):
        raise ValueError('SSH alias 无效')
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    if ':' in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ValueError('IPv6 地址无效') from None
    elif not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,252}', host):
        raise ValueError('--ssh-host 只填写服务器域名/IP，不含用户名、协议或端口')
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}', user):
        raise ValueError('--ssh-user 必须是服务器的系统用户名')
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('--ssh-port 必须为 1–65535')
    ssh_dir = Path.home() / '.ssh'
    identity = Path(key_path or ssh_dir / ('gbb_multica_' + alias + '_ed25519')).expanduser().absolute()
    known_hosts = ssh_dir / 'known_hosts'
    config = ssh_dir / 'gbb-multica' / (alias + '.conf')
    config_quote(identity)
    config_quote(known_hosts)
    if identity.is_symlink() or identity.with_name(identity.name + '.pub').is_symlink():
        raise ValueError('SSH 密钥路径不能是符号链接，请传实际私钥路径')
    ssh_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not identity.exists():
        if identity.with_name(identity.name + '.pub').exists():
            raise ValueError('已存在同名公钥但没有私钥；不会覆盖，请换一个 --ssh-key 路径')
        identity.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Dedicated unencrypted key supports unattended Bot replies; it never leaves the Mac.
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(identity),
                        '-C', 'gbb-multica:' + alias], check=True, stdin=subprocess.DEVNULL, timeout=30)
        print(f'已生成专用 Ed25519 密钥：{identity}（私钥仅保存在 Mac）。', flush=True)
    if not identity.is_file() or identity.stat().st_uid != os.geteuid() or identity.stat().st_mode & 0o077:
        raise ValueError('私钥必须属于当前用户，且仅该用户可读写（通常权限 600）')
    # Derive from the selected private key, so a stale .pub cannot authorize a different identity.
    result = subprocess.run(['ssh-keygen', '-y', '-f', str(identity)], check=True,
                            stdin=subprocess.DEVNULL, text=True, stdout=subprocess.PIPE, timeout=60)
    public_key = public_part(result.stdout)
    public_file = identity.with_name(identity.name + '.pub')
    if public_file.exists():
        if public_part(public_file.read_text()) != public_key:
            raise ValueError('同名 .pub 公钥与所选私钥不匹配；未复制公钥，请先核对这对密钥')
    # Ignore unrelated Host/Match rules for this dedicated direct connection.
    base = ['ssh', '-T', '-F', '/dev/null', '-i', str(identity), '-p', str(port), '-l', user,
            '-o', 'IdentitiesOnly=yes', '-o', 'ConnectTimeout=10',
            '-o', 'UserKnownHostsFile=' + config_quote(known_hosts)]
    probe = [*base, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '--', host, 'true']
    checked = subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    if checked.returncode:
        if not sys.stdin.isatty():
            raise ValueError('首次复制公钥需要交互终端，请直接在 Mac 终端运行；密码由 SSH 读取')
        print(f'向 {user}@{host}:{port} 追加公钥。请核对主机指纹，并在 SSH 提示时输入服务器密码。', flush=True)
        # SSH reads its password from /dev/tty; stdin carries ONLY the public key.
        copied = subprocess.run([*base, '-o', 'BatchMode=no', '-o', 'NumberOfPasswordPrompts=1',
                                 '-o', 'PreferredAuthentications=publickey,password',
                                 '-o', 'PasswordAuthentication=yes', '-o', 'KbdInteractiveAuthentication=no',
                                 '-o', 'StrictHostKeyChecking=ask', '--', host,
                                 'sh -c ' + shlex.quote(INSTALL_PUBLIC_KEY)],
                                input=public_key, text=True, timeout=180)
        if copied.returncode:
            raise ValueError('公钥复制失败；未自动重试。请检查账号、密码及服务器是否允许该账号认证')
        checked = subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if checked.returncode:
            raise ValueError('公钥可能已追加，但免密检查未通过；检查 authorized_keys 策略；加密私钥需先加载到 ssh-agent')
    hostname = host.replace('%', '%%')
    text = (f'# Managed by gbb-multica-bridge; alias {alias}\nHost {alias}\n'
            f'    HostName {hostname}\n    User {user}\n    Port {port}\n'
            f'    IdentityFile {config_quote(identity)}\n    IdentitiesOnly yes\n'
            f'    UserKnownHostsFile {config_quote(known_hosts)}\n'
            '    BatchMode yes\n    StrictHostKeyChecking yes\n')
    write_file(config, text, 0o600)
    print(f'SSH 免密验证通过：{alias}；专用配置 {config}', flush=True)
    return {'ssh_config': str(config), 'ssh_connection': {
        'host': host, 'user': user, 'port': port, 'key_path': str(identity)}}
