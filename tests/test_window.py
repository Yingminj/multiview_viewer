import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import test_video
from window import MainWindow


class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        test_video.VideoTests.setUpClass()
        cls.directory = test_video.VideoTests.directory
        # Three independent copies represent camera streams.
        (cls.directory / 'ego').mkdir()
        source = test_video.VideoTests.path.read_bytes()
        for name in ['ego/rgb.mp4', 'cam_left.mkv', 'cam_right.mkv', 'extra.mkv']:
            (cls.directory / name).write_bytes(source)
        cls.config = cls.directory / 'ui_config.json'
        cls.config.write_text(json.dumps({'views': [
            {'name': 'Ego', 'path': 'ego/rgb.mp4', 'enabled': True},
            {'name': '左相机', 'path': 'cam_left.mkv', 'enabled': True},
            {'name': '右相机', 'path': 'cam_right.mkv', 'enabled': True},
        ]}))

    @classmethod
    def tearDownClass(cls):
        test_video.VideoTests.tearDownClass()

    def setUp(self):
        self.window = MainWindow(self.config, self.directory)
        self.window.show()
        self.wait(lambda: len(self.window.metadata) == 3 and not self.window.batch)
        QTest.qWait(220)
        self.settle()

    def tearDown(self):
        workers = list(self.window.workers.values())
        self.window.close()
        for worker in workers:
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.app.processEvents()

    def wait(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            QTest.qWait(10)
        self.fail('Qt 操作等待超时')

    def settle(self):
        self.wait(lambda: not self.window.batch and not self.window.step_pending)

    def test_default_layout_and_real_rendering(self):
        w = self.window
        self.assertEqual(len(w.panels), 3)
        self.assertEqual(w.grid.getItemPosition(0), (0, 0, 1, 2))
        for canvas, info in w.panels.values():
            self.assertFalse(canvas.image.isNull())
            self.assertIn('帧时间', info.text())
            self.assertGreater(canvas.width(), 100)

    def test_seek_and_exact_variable_rate_steps(self):
        w = self.window
        w.seek(.3)
        self.settle()
        w.step(1)
        self.settle()
        self.assertAlmostEqual(w.position, .4)
        w.step(-1)
        self.settle()
        self.assertAlmostEqual(w.position, .3)
        self.assertTrue(all('00:00.300' in info.text() for _, info in w.panels.values()))

    def test_checkbox_add_remove_zoom_and_offset(self):
        w = self.window
        extra = next(i for i, view in enumerate(w.views) if view.path.name == 'extra.mkv')
        w.rows[extra][0].setChecked(True)
        self.settle()
        self.assertEqual(len(w.panels), 4)
        w.zoom(extra)
        self.settle()
        self.assertEqual(list(w.panels), [extra])
        w.zoom(None)
        self.settle()
        self.assertEqual(len(w.panels), 4)
        w.rows[extra][1].setValue(500)
        w.seek(.3)
        self.settle()
        self.assertIn('未开始', w.panels[extra][1].text())
        worker = w.workers[extra]
        w.rows[extra][0].setChecked(False)
        self.settle()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(w.panels), 3)

    def test_play_pause_slider_and_end(self):
        w = self.window
        w.play_button.click()
        self.wait(lambda: w.position > .15)
        w.play_button.click()
        self.settle()
        position = w.position
        QTest.qWait(100)
        self.assertEqual(position, w.position)
        QTest.mouseClick(w.slider, Qt.MouseButton.LeftButton,
                         pos=QPoint(w.slider.width() // 2, w.slider.height() // 2))
        self.settle()
        self.assertAlmostEqual(w.position, w.duration / 2, delta=.015)
        w.seek(w.duration)
        self.settle()
        self.assertTrue(all('已结束' in info.text() for _, info in w.panels.values()))
        w.toggle_play()
        self.assertTrue(w.playing)
        self.assertLess(w.position, .1)

    def test_missing_view_does_not_break_others_and_reload(self):
        w = self.window
        from video import View
        i = len(w.views)
        w.views.append(View('missing', self.directory / 'missing.mp4', True))
        w.create_row(i)
        w.sync_selection()
        self.settle()
        self.assertEqual(w.panels[i][0].message, '无法读取视频')
        self.assertFalse(w.panels[0][0].image.isNull())
        w.load()
        self.settle()
        self.assertEqual(len(w.workers), 3)

    def test_config_save_roundtrip(self):
        w = self.window
        w.rows[1][1].setValue(-125.5)
        destination = self.directory / 'saved.json'
        with patch('window.QFileDialog.getSaveFileName', return_value=(str(destination), 'JSON')):
            w.save_config()
        items = json.loads(destination.read_text())['views']
        self.assertEqual(items[1]['offset_ms'], -125.5)
        self.assertEqual(items[0]['path'], 'ego/rgb.mp4')

    def test_shortcuts_preserve_text_editing(self):
        w = self.window
        w.activateWindow()
        w.path.setFocus()
        w.path.setCursorPosition(5)
        QTest.keyClick(w.path, Qt.Key.Key_Left)
        self.assertEqual(w.path.cursorPosition(), 4)
        self.assertEqual(w.position, 0)
        w.panels[0][0].setFocus()
        QTest.keyClick(w.panels[0][0], Qt.Key.Key_Space)
        self.wait(lambda: w.playing)
        w.pause()


if __name__ == '__main__':
    unittest.main()
