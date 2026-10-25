#!/usr/bin/env bash
# 运行端（Multica daemon 所在服务器）上的辅助入口：显示回传端恢复命令、授权新回传端公钥、更新仓库副本。
# 回传端在 Grok Bot 电脑上，本脚本不在服务器上恢复任何东西；不改运行端配置、不碰 webhook、不发送 Grok 请求。
# 不含任何部署信息；需要真实主机/用户/指纹的一键命令请放在服务器上的私有包装脚本里，不要提交到仓库。
set -euo pipefail
REPO_DIR=${GBB_REPO_DIR:-/opt/gbb-multica-bridge}
RESTORE="$REPO_DIR/scripts/restore-reply.sh"
RAW=https://raw.githubusercontent.com/lthero-big/gbb-multica-bridge/main/scripts/restore-reply.sh

usage() {
  cat <<USAGE
gbb-multica 回传端一键恢复（在被清空的 Grok Bot 电脑上执行，而不是在这台服务器上）：

  bash <(curl -fsSL $RAW) \\
    --ssh-host YOUR_HOST --ssh-user YOUR_USER --route YOUR_ROUTE \\
    --host-key-sha256 "\$(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub | awk '{print \$2}')"   # 在本服务器上求值

本服务器 ED25519 主机指纹（核对用）：
  $(ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub 2>/dev/null | awk '{print $2}' || echo '（读取失败）')

若恢复脚本提示私钥未授权（退出码 3），把它打印的公钥在本服务器授权后重跑：
  bash $0 authorize 'ssh-ed25519 AAAA... gbb-multica:ALIAS'

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
