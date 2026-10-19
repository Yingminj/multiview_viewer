#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Probe interpreters without changing the user's existing environments.
candidates=("${MULTIVIEW_PYTHON:-}" "$APP_DIR/.venv/bin/python" "/home/dm/miniconda3/envs/lerobot/bin/python" "python3")
for candidate in "${candidates[@]}"; do
    if [[ -n "$candidate" ]] && command -v "$candidate" >/dev/null 2>&1 && \
       "$candidate" -c 'import av, PySide6.QtWidgets, PIL, numpy' >/dev/null 2>&1; then
        exec "$candidate" "$APP_DIR/viewer.py" "$@"
    fi
done
echo '缺少运行依赖。请先运行 ./setup.sh，或用 MULTIVIEW_PYTHON 指定已安装 PySide6、av、Pillow、numpy 的 Python。' >&2
exit 1
