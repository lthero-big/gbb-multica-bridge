#!/usr/bin/env bash
# 运行端（Multica daemon 所在服务器）上的回传端恢复入口；安装为 /root/restore-gbb-reply.sh 的符号链接。
# 回传端在 Grok Bot 电脑上，本脚本只负责：说明恢复命令、授权新回传端公钥、更新仓库副本。
# 不改运行端配置、不碰 webhook、不发送 Grok 请求。
set -euo pipefail
REPO_DIR=${GBB_REPO_DIR:-/opt/gbb-multica-bridge}
RESTORE="$REPO_DIR/scripts/restore-reply.sh"
RAW=https://raw.githubusercontent.com/lthero-big/gbb-multica-bridge/main/scripts/restore-reply.sh

usage() {
  local host=${GBB_PUBLIC_HOST:-152.53.141.33}
  cat <<USAGE
gbb-multica 回传端一键恢复（在被清空的 Grok Bot 电脑上执行，而不是在这台服务器上）：

  # 新电脑还没有登录本服务器的私钥：从 GitHub 获取并运行
  bash <(curl -fsSL $RAW)

  # 新电脑已能免密 SSH 到本服务器：直接用这里的副本
  bash <(ssh $(id -un)@$host cat $RESTORE)

若脚本提示私钥未授权（退出码 3），把它打印的公钥在本服务器授权后重跑：
  bash $0 authorize 'ssh-ed25519 AAAA... gbb-multica:multica-server'

其他子命令：
  bash $0 update   # 快进更新 $REPO_DIR（不重装运行端）
  bash $0 print    # 输出恢复脚本内容
USAGE
}

authorize() {
  local key=${1:?用法：authorize '<公钥一行>'} type blob file="$HOME/.ssh/authorized_keys"
  ssh-keygen -lf - <<<"$key" >/dev/null 2>&1 || { echo '公钥格式无效，未写入' >&2; exit 2; }
  read -r type blob _ <<<"$key"
  umask 077; mkdir -p "$HOME/.ssh"; touch "$file"
  if awk -v t="$type" -v b="$blob" '{for(i=1;i<NF;i++) if($i==t && $(i+1)==b) f=1} END{exit !f}' "$file"; then
    echo "公钥已在 $file 中，无需重复添加"
  else
    printf '\n%s\n' "$key" >>"$file"
    echo "已追加到 $file：$(ssh-keygen -lf - <<<"$key")"
  fi
}

case "${1:-help}" in
  authorize) shift; authorize "$@" ;;
  update) git -C "$REPO_DIR" pull --ff-only ;;
  print) cat "$RESTORE" ;;
  help|-h|--help) usage ;;
  *) usage >&2; exit 2 ;;
esac
