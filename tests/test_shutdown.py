"""Exercise the real entry point: no external joins may hide lingering threads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_video


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import sys
import threading
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import video
import viewer
import window

scenario, config, directory = sys.argv[1:]
started = threading.Event()
RealReader = video.Reader

class Reader(RealReader):
    def __init__(self, path):
        started.set()
        if scenario != 'normal':
            threading.Event().wait()  # Simulate a stuck native decode/open.
        super().__init__(path)

    def close(self):
        super().close()
        print('reader closed', flush=True)

class Window(window.MainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        QTimer.singleShot(10, self.finish)

    def finish(self):
        if not started.is_set() or (scenario == 'normal' and not self.metadata):
            QTimer.singleShot(10, self.finish)
            return
        if scenario == 'disabled':
            self.rows[0][0].setChecked(False)
        elif scenario == 'reload':
            self.load()
        print('exit requested', flush=True)
        if scenario == 'app_quit':
            QApplication.instance().quit()
        else:
            self.close()

sys.argv = ['viewer.py', directory, '--config', config]
with patch('window.MainWindow', Window), patch('video.Reader', Reader):
    code = viewer.main()
print('main returned', flush=True)
raise SystemExit(code)
'''


class ShutdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_video.VideoTests.setUpClass()
        cls.directory = test_video.VideoTests.directory
        cls.config = cls.directory / 'shutdown.json'
        cls.config.write_text(json.dumps({'time_alignment': 'first_frame', 'views': [
            {'name': 'video', 'path': 'vfr.mkv', 'enabled': True}
        ]}))

    @classmethod
    def tearDownClass(cls):
        test_video.VideoTests.tearDownClass()

    def run_viewer(self, scenario):
        result = subprocess.run(
            [sys.executable, '-c', SCRIPT, scenario, str(self.config), str(self.directory)],
            cwd=ROOT, env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('exit requested', result.stdout)
        return result.stdout

    def test_normal_exit_closes_decoder_and_returns(self):
        output = self.run_viewer('normal')
        self.assertIn('reader closed', output)
        self.assertIn('main returned', output)

    def test_stuck_active_retired_and_app_quit_all_terminate(self):
        for scenario in ['active', 'disabled', 'reload', 'app_quit']:
            with self.subTest(scenario=scenario):
                output = self.run_viewer(scenario)
                self.assertNotIn('main returned', output)


if __name__ == '__main__':
    unittest.main()
