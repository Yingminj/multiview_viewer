#!/usr/bin/env bash
# Copy the application without copying a non-relocatable virtual environment.
set -euo pipefail
APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEST="${1:-/home/dm/tool/multiview_viewer}"
if [[ -e "$DEST" ]]; then
    echo "目标已存在，为避免覆盖文件已停止：$DEST" >&2
    exit 1
fi
mkdir -p -- "$DEST"
for file in viewer.py window.py video.py views.json requirements.txt run.sh setup.sh install.sh README.md; do
    cp -- "$APP_DIR/$file" "$DEST/$file"
done
mkdir -p -- "$DEST/tests"
cp -- "$APP_DIR"/tests/*.py "$DEST/tests/"
if [[ -f "$APP_DIR/preview.png" ]]; then
    cp -- "$APP_DIR/preview.png" "$DEST/preview.png"
fi
chmod +x -- "$DEST/run.sh" "$DEST/setup.sh" "$DEST/install.sh"
echo "工具已安装到：$DEST"
echo "启动：$DEST/run.sh"
