#!/usr/bin/env bash
# 回传端一键恢复：Grok Bot 电脑被清空/重装后，复用 install.sh 重建 SSH 连接与回填路由。
# 不改运行端、不碰 webhook URL/key、不发送 Grok 请求；可重复执行。
#
# 默认值对应 lthero 的生产部署（root@152.53.141.33，路由 server-root，Bot gbb）；
# 其他部署用参数覆盖，参数说明见 --help。
set -euo pipefail

# ---- 部署默认值（可用参数覆盖） ----
HOST=152.53.141.33
SSH_USER=root
PORT=22
ALIAS=multica-server
ROUTE=server-root
INSTANCE=server-root
BOT=gbb
KEY="$HOME/.ssh/id_ed25519"
ENDPOINT=/root/.local/bin/gbb-multica-endpoint
# 运行端 ED25519 主机指纹；known_hosts 缺失时用它核对 ssh-keyscan 结果。空值=交给 SSH 交互确认。
HOST_KEY_SHA256='SHA256:GT6s3rTlqp6/PrjLIRn0oLLyf4JyxY1XkvVWx+eMJOE'
REPO_URL=https://github.com/lthero-big/gbb-multica-bridge.git
REPO_DIR=""
REF=main
MODE=restore   # restore | check | dry-run

usage() {
  cat <<USAGE
用法：bash scripts/restore-reply.sh [选项]
      bash <(curl -fsSL https://raw.githubusercontent.com/lthero-big/gbb-multica-bridge/main/scripts/restore-reply.sh) [选项]

在回传端（Grok Bot 所在电脑）恢复 gbb-multica 回填助手、SSH 连接和路由。
步骤：取得仓库 → 核对主机指纹 → install.sh ssh（已可用则复用）→ install.sh reply → 检查。

选项（括号内为默认值）：
  --ssh-host HOST        运行端主机 ($HOST)
  --ssh-user USER        运行端 daemon 用户 ($SSH_USER)
  --ssh-port PORT        SSH 端口 ($PORT)
  --ssh-alias NAME       SSH 连接别名 ($ALIAS)
  --ssh-key PATH         本机私钥 ($KEY)
  --route NAME           回填路由 ($ROUTE)
  --instance-id ID       运行端实例 ($INSTANCE)
  --bot NAME             Bot 标注 ($BOT)
  --endpoint PATH        运行端 endpoint ($ENDPOINT)
  --host-key-sha256 FP   运行端 ED25519 主机指纹；传空串则改为交互确认
  --repo-url URL         仓库地址 ($REPO_URL)
  --repo-dir DIR         仓库目录；默认用脚本所在仓库，否则 ~/.local/share/gbb-multica-bridge/repo
  --ref REF              克隆/更新的分支 ($REF)
  --check                只做只读检查（install.sh reply --check + 助手 check）
  --dry-run              只打印将执行的命令，不连接、不写文件
  -h, --help             显示本帮助

新电脑没有已授权的私钥时，脚本会生成密钥并打印公钥及授权命令后退出（退出码 3）；
授权后重跑即可。不会读取、传输或保存任何密码、webhook URL/key。
USAGE
}

log() { printf '==> %s\n' "$*"; }
die() { printf '恢复停止：%s\n' "$*" >&2; exit "${2:-1}"; }
show() { printf '    $'; printf ' %q' "$@"; printf '\n'; }
# 执行或在 --dry-run 下只打印
act() { show "$@"; [[ $MODE == dry-run ]] || "$@"; }

parse_args() {
  while (($#)); do
    case "$1" in
      --ssh-host) HOST=${2:?}; shift ;;
      --ssh-user) SSH_USER=${2:?}; shift ;;
      --ssh-port) PORT=${2:?}; shift ;;
      --ssh-alias) ALIAS=${2:?}; shift ;;
      --ssh-key) KEY=${2:?}; shift ;;
      --route) ROUTE=${2:?}; shift ;;
      --instance-id) INSTANCE=${2:?}; shift ;;
      --bot) BOT=${2:?}; shift ;;
      --endpoint) ENDPOINT=${2:?}; shift ;;
      --host-key-sha256) HOST_KEY_SHA256=${2-}; shift ;;
      --repo-url) REPO_URL=${2:?}; shift ;;
      --repo-dir) REPO_DIR=${2:?}; shift ;;
      --ref) REF=${2:?}; shift ;;
      --check) MODE=check ;;
      --dry-run) MODE=dry-run ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; die "未知参数：$1" 2 ;;
    esac
    shift
  done
  [[ $PORT =~ ^[0-9]+$ ]] || die "--ssh-port 必须是数字" 2
}

# 1. 仓库：脚本在仓库内就直接用；否则克隆/快进更新到 REPO_DIR
locate_repo() {
  local here
  here=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || true)
  if [[ -z $REPO_DIR && -n $here && -f $here/../install.sh && -f $here/../gbb-multica-remote ]]; then
    REPO_DIR=$(cd "$here/.." && pwd)
    log "使用脚本所在仓库：$REPO_DIR（不自动更新；需要时先 git pull）"
    return
  fi
  REPO_DIR=${REPO_DIR:-$HOME/.local/share/gbb-multica-bridge/repo}
  command -v git >/dev/null || die "需要 git 来获取仓库"
  if [[ -d $REPO_DIR/.git ]]; then
    log "更新仓库：$REPO_DIR"
    act git -C "$REPO_DIR" fetch --quiet origin "$REF"
    act git -C "$REPO_DIR" checkout --quiet "$REF"
    act git -C "$REPO_DIR" merge --quiet --ff-only "origin/$REF"
  else
    log "克隆仓库到：$REPO_DIR"
    act mkdir -p "$(dirname -- "$REPO_DIR")"
    act git clone --quiet --branch "$REF" "$REPO_URL" "$REPO_DIR"
  fi
}

install_sh() { act bash "$REPO_DIR/install.sh" "$@"; }

# 2. known_hosts 缺少运行端时，用固定指纹核对 ssh-keyscan 结果后写入
ensure_host_key() {
  local known="$HOME/.ssh/known_hosts" name="$HOST" scanned fp
  [[ $PORT == 22 ]] || name="[$HOST]:$PORT"
  if [[ -f $known ]] && ssh-keygen -F "$name" -f "$known" >/dev/null 2>&1; then
    log "known_hosts 已有 $name，复用"
    return
  fi
  if [[ -z $HOST_KEY_SHA256 ]]; then
    log "known_hosts 没有 $name，且未指定指纹；稍后由 SSH 交互确认"
    return
  fi
  log "known_hosts 没有 $name，按指纹 $HOST_KEY_SHA256 核对后写入"
  [[ $MODE == dry-run ]] && { show ssh-keyscan -t ed25519 -p "$PORT" "$HOST"; return; }
  scanned=$(ssh-keyscan -T 10 -t ed25519 -p "$PORT" "$HOST" 2>/dev/null) || true
  [[ -n $scanned ]] || die "ssh-keyscan 取不到 $HOST 的 ED25519 主机密钥"
  fp=$(ssh-keygen -lf - <<<"$scanned" | awk '{print $2}')
  [[ $fp == "$HOST_KEY_SHA256" ]] || die "主机指纹不符：期望 $HOST_KEY_SHA256，实际 $fp。未写入 known_hosts；确认服务器是否重装后用 --host-key-sha256 指定"
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  (umask 077; printf '%s\n' "$scanned" >>"$known")
}

ssh_reusable() {
  [[ $MODE == dry-run ]] && return 1
  bash "$REPO_DIR/install.sh" ssh --ssh-alias "$ALIAS" --check >/dev/null 2>&1
}

key_authorized() {
  ssh -T -F /dev/null -i "$KEY" -p "$PORT" -l "$SSH_USER" -o IdentitiesOnly=yes -o BatchMode=yes \
      -o ConnectTimeout=10 -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
      -- "$HOST" true >/dev/null 2>&1
}

print_authorize_help() {
  local pub
  pub=$(ssh-keygen -y -f "$KEY" | cut -d' ' -f1,2)
  cat <<HELP

本机私钥 $KEY 还没有被 $SSH_USER@$HOST 授权。复制下面的公钥，在任意一台能登录该服务器的电脑上执行其一：

  ssh $SSH_USER@$HOST 'bash /root/restore-gbb-reply.sh authorize "$pub gbb-multica:$ALIAS"'
  # 或：ssh $SSH_USER@$HOST 'umask 077; mkdir -p ~/.ssh; echo "$pub gbb-multica:$ALIAS" >> ~/.ssh/authorized_keys'

或者在本机交互终端重跑本脚本，按 SSH 提示输入一次服务器密码（ssh-copy-id 也可以）。
授权后用同样的方式重跑本脚本（脚本会复用这把私钥）。
HELP
}

# 3. SSH：已可用就复用；否则经 install.sh ssh 配置（缺私钥且无终端时先生成并提示授权）
ensure_ssh() {
  if ssh_reusable; then
    log "SSH 连接 $ALIAS 已可用，复用"
    return
  fi
  if [[ $MODE != dry-run && ! -t 0 ]]; then
    if [[ ! -e $KEY ]]; then
      log "生成私钥 $KEY（Ed25519，无口令，仅存本机）"
      mkdir -p "$(dirname -- "$KEY")"
      ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C "gbb-multica:$ALIAS" </dev/null
    fi
    key_authorized || { print_authorize_help; exit 3; }
  fi
  log "配置 SSH 连接 $ALIAS → $SSH_USER@$HOST:$PORT"
  install_sh ssh --ssh-alias "$ALIAS" --ssh-host "$HOST" --ssh-user "$SSH_USER" \
             --ssh-port "$PORT" --ssh-key "$KEY"
}

reply_args() {
  printf '%s\0' --route "$ROUTE" --instance-id "$INSTANCE" --bot "$BOT" \
                --transport ssh --ssh-alias "$ALIAS" --endpoint "$ENDPOINT"
}

run_checks() {
  local helper="$HOME/.local/bin/gbb-multica-remote"
  log "检查回填链路（只读，不发送 Grok 请求）"
  install_sh reply --route "$ROUTE" --check
  [[ $MODE == dry-run || -x $helper ]] || die "缺少回填助手 $helper；去掉 --check 重跑以安装"
  act "$helper" check "$ROUTE"
}

main() {
  parse_args "$@"
  command -v python3 >/dev/null || die "需要 Python 3.9+"
  command -v ssh >/dev/null && command -v ssh-keygen >/dev/null || die "需要 OpenSSH 客户端（ssh、ssh-keygen）"
  [[ $MODE == dry-run ]] && log "dry-run：只打印步骤，不连接、不写文件"
  locate_repo
  if [[ $MODE != check ]]; then
    ensure_host_key
    ensure_ssh
    log "安装回填助手并写入路由 $ROUTE（复用已有配置）"
    local args=()
    mapfile -d '' args < <(reply_args)
    install_sh reply "${args[@]}"
  fi
  run_checks
  [[ $MODE == dry-run ]] && { log "dry-run 结束；SSH 已可用时实际运行会跳过 install.sh ssh"; return; }
  cat <<DONE

回传端已就绪：路由 $ROUTE → $ALIAS ($SSH_USER@$HOST)，助手 $HOME/.local/bin/gbb-multica-remote。
下一步：
  1. 确认 Grok Bot 的「Multica bridge」例程说明里的助手路径仍是 $HOME/.local/bin/gbb-multica-remote、
     路由仍是 $ROUTE（完整提示词：\${GBB_MULTICA_CONFIG_HOME:-~/.config/gbb-multica}/grok-bot-prompt.md）。
     webhook URL/key 和运行端没有改动，无需重配。
  2. 在 Multica 新聊天发送“请只回复 GBB_RUNTIME_OK”，收到回答即整条链路恢复。
DONE
}

main "$@"
