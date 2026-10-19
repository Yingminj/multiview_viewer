#!/usr/bin/env python3
"""Entry point and display-independent video diagnostics."""
import argparse
import json
import time
from pathlib import Path

from video import Reader, discover

HERE = Path(__file__).resolve().parent


def check_desktop_dependencies():
    """Catch missing X11 libraries before QApplication can abort the process."""
    import ctypes
    import os
    import sys
    if not sys.platform.startswith("linux"):
        return
    platform = os.environ.get("QT_QPA_PLATFORM", "").split(":", 1)[0]
    if platform and platform != "xcb":
        return
    if not platform and (os.environ.get("WAYLAND_DISPLAY") or
                         os.environ.get("XDG_SESSION_TYPE") == "wayland"):
        return
    from PySide6.QtCore import QLibraryInfo
    plugin = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms/libqxcb.so"
    try:
        ctypes.CDLL(str(plugin))
    except OSError as exc:
        hint = ""
        if "libxcb-cursor.so.0" in str(exc):
            hint = "\n请在 Ubuntu 终端执行：\n  sudo apt update\n  sudo apt install libxcb-cursor0\n然后重新运行原来的启动命令。"
        raise RuntimeError(f"Qt X11 桌面依赖加载失败：{exc}{hint}") from exc


def check(directory, config):
    report = []
    for view in discover(directory, config):
        if not view.enabled:
            continue
        reader = None
        try:
            reader = Reader(view.path)
            samples = []
            for target in (0, min(5, reader.duration / 2), reader.duration / 2, 1, reader.duration):
                start = time.monotonic()
                frame = reader.at(target)
                image = reader.image(frame, (800, 450))
                samples.append({"target_s": round(target, 6), "frame_s": round(reader.timestamp(frame), 6),
                                "preview_size": image.size,
                                "decode_ms": round((time.monotonic() - start) * 1000, 1)})
            report.append({"name": view.name, "path": str(view.path), "duration_s": reader.duration,
                           "resolution": [reader.width, reader.height], "fps": reader.fps, "samples": samples})
        except Exception as exc:
            report.append({"name": view.name, "error": str(exc)})
        finally:
            if reader:
                reader.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any("error" in item for item in report) else 0


def main():
    parser = argparse.ArgumentParser(description="多视角视频查看器 · PySide6")
    parser.add_argument("directory", nargs="?", type=Path, help="episode 数据目录")
    parser.add_argument("--config", type=Path, default=HERE / "views.json")
    parser.add_argument("--check", action="store_true", help="无窗口检查默认视频及时间跳转")
    args = parser.parse_args()
    if args.check:
        if args.directory is None:
            parser.error("--check 需要数据目录")
        return check(args.directory, args.config)
    import os
    import sys
    if sys.platform.startswith("linux") and not any(os.environ.get(k) for k in
            ("DISPLAY", "WAYLAND_DISPLAY", "QT_QPA_PLATFORM")):
        parser.exit(1, "未检测到桌面显示环境。请在 Ubuntu 桌面终端启动，或使用 --check 检查数据。\n")
    try:
        check_desktop_dependencies()
    except RuntimeError as exc:
        parser.exit(1, f"{exc}\n")
    from PySide6.QtWidgets import QApplication
    from window import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName("多视角视频查看器")
    window = MainWindow(args.config, args.directory)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
