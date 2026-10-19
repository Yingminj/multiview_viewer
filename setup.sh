#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${MULTIVIEW_PYTHON:-python3}"
if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"
echo '依赖就绪。运行 ./run.sh [episode目录]。'
