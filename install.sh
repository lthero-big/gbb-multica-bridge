#!/usr/bin/env bash
# Run from a cloned checkout; private repositories require an authenticated clone.
set -euo pipefail
command -v python3 >/dev/null || { echo '需要 Python 3.9+；请先安装 Python 3。' >&2; exit 1; }
GBB_INSTALL_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python3 "$GBB_INSTALL_ROOT/install.py" "$@"
