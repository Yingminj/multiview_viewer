"""Run real-video UI checks offscreen and optionally save a window preview."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from window import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    app = QApplication([])
    window = MainWindow(ROOT / 'views.json', args.directory)
    window.show()

    def wait(condition):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        raise AssertionError('真实视频 GUI 操作超时')

    def settle():
        wait(lambda: not window.batch and not window.step_pending)

    try:
        wait(lambda: len(window.metadata) == 3 and not window.batch)
        QTest.qWait(250)
        settle()
        assert all(not canvas.image.isNull() for canvas, _ in window.panels.values())
        window.seek(10)
        settle()
        window.step(1)
        settle()
        next_time = window.position
        window.step(-1)
        settle()
        assert window.position < next_time
        window.toggle_play()
        wait(lambda: window.position > next_time + .5)
        window.pause()
        settle()
        extra = next(i for i, view in enumerate(window.views) if view.path.name == 'stereo_left.mkv')
        window.rows[extra][0].setChecked(True)
        settle()
        assert len(window.panels) == 4
        assert not window.panels[extra][0].image.isNull()
        window.rows[extra][0].setChecked(False)
        settle()
        window.seek(window.duration)
        settle()
        assert all('已结束' in info.text() for _, info in window.panels.values())
        window.seek(10)
        settle()
        if args.screenshot:
            assert window.grab().save(str(args.screenshot))
        print(json.dumps({'status': 'passed', 'discovered_views': len(window.views),
                          'duration_s': window.duration,
                          'checks': ['三路画面', '前后逐帧', '播放暂停', '增加和取消第四视角', '结束状态', '反向跳转'],
                          'screenshot': str(args.screenshot or '')}, ensure_ascii=False, indent=2))
    finally:
        workers = list(window.workers.values())
        window.close()
        for worker in workers:
            worker.join(5)


if __name__ == '__main__':
    main()
