import json
import sys
import tempfile
import time
import unittest
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video import Reader, Worker, discover


class VideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.path = cls.directory / "vfr.mkv"
        # Deliberately non-uniform timestamps and a nonzero first PTS.
        cls.timestamps = [2.0, 2.1, 2.3, 2.4, 2.8, 3.0]
        with av.open(str(cls.path), "w") as container:
            stream = container.add_stream("ffv1", rate=10)
            stream.width, stream.height = 96, 64
            stream.pix_fmt = "yuv420p"
            stream.time_base = Fraction(1, 1000)
            stream.codec_context.time_base = Fraction(1, 1000)
            for i, timestamp in enumerate(cls.timestamps):
                array = np.full((64, 96, 3), i * 40, dtype=np.uint8)
                frame = av.VideoFrame.from_ndarray(array, format="rgb24")
                frame.pts = round(timestamp * 1000)
                frame.time_base = Fraction(1, 1000)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.reader = Reader(self.path)
        self.addCleanup(self.reader.close)

    def test_nonzero_origin_and_variable_timestamps(self):
        self.assertAlmostEqual(self.reader.origin, 2.0)
        self.assertAlmostEqual(self.reader.duration, 1.1)
        for target, expected in [(0, 0), (.15, .1), (.35, .3), (.75, .4), (.9, .8)]:
            self.assertAlmostEqual(self.reader.timestamp(self.reader.at(target)), expected)

    def test_forward_and_backward_steps_use_actual_pts(self):
        self.assertAlmostEqual(self.reader.step(.3, 1), .4)
        self.assertAlmostEqual(self.reader.step(.3, -1), .1)
        self.assertEqual(self.reader.step(0, -1), 0)

    def test_end_holds_last_frame_and_seek_back(self):
        self.assertAlmostEqual(self.reader.timestamp(self.reader.at(100)), 1.0)
        self.assertAlmostEqual(self.reader.step(100, 1), 1.0)
        self.assertAlmostEqual(self.reader.timestamp(self.reader.at(.15)), .1)

    def test_preview_preserves_aspect_and_pixels(self):
        image = self.reader.image(self.reader.at(.8), (48, 48))
        self.assertEqual(image.size, (48, 32))
        self.assertTrue(140 < np.asarray(image).mean() < 180)

    def test_discovery_keeps_missing_default_and_adds_unchecked(self):
        config = self.directory / "config.json"
        config.write_text(json.dumps({"views": [
            {"name": "missing", "path": "missing.mp4", "enabled": True, "offset_ms": 20}
        ]}))
        views = discover(self.directory, config)
        self.assertTrue(views[0].enabled)
        self.assertEqual(views[0].offset_ms, 20)
        self.assertEqual(views[1].path, self.path)
        self.assertFalse(views[1].enabled)

    def test_worker_catches_decode_errors(self):
        worker = Worker(self.directory / "missing.mp4")
        worker.request(1, 0, (48, 32))
        generation, value = worker.results.get(timeout=5)
        self.assertEqual(generation, 1)
        self.assertIn("error", value)
        worker.stop()
        worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_worker_replaces_pending_requests_and_stops(self):
        worker = Worker(self.path)
        try:
            for generation in range(100):
                worker.request(generation, .8, (48, 32))
            deadline = time.monotonic() + 5
            while True:
                generation, value = worker.results.get(timeout=max(.01, deadline - time.monotonic()))
                if generation == 99:
                    break
            self.assertAlmostEqual(value["pts"], .8)
            self.assertEqual(value["image"].size, (48, 32))
            worker.request(100, -.5, (48, 32))
            _, value = worker.results.get(timeout=3)
            self.assertTrue(value["waiting"])
        finally:
            worker.stop()
            worker.join(3)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
