import json
import struct
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import av
import numpy as np
from mcap.writer import Writer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from alignment import (AUTO, apply_alignment, calculate_alignment,
                       configured_mode, episode_versions, supports_alignment)
from video import View


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'ego').mkdir()
        self.epoch = 1_790_000_000_000_000_000
        self.pts = [2_000_000_000, 2_100_000_000, 2_300_000_000]
        # A nonzero origin and variable intervals catch frame-number-based math.
        for name in ['cam_left.mkv', 'cam_right.mkv', 'ego/rgb.mp4']:
            with av.open(str(self.root / name), 'w', format='matroska') as c:
                stream = c.add_stream('ffv1', rate=10)
                stream.width, stream.height = 32, 24
                stream.pix_fmt = 'yuv420p'
                stream.time_base = stream.codec_context.time_base = Fraction(1, 1000)
                for pts in self.pts:
                    frame = av.VideoFrame.from_ndarray(np.zeros((24, 32, 3), dtype=np.uint8), format='rgb24')
                    frame.pts, frame.time_base = pts // 1_000_000, Fraction(1, 1000)
                    for packet in stream.encode(frame):
                        c.mux(packet)
                for packet in stream.encode():
                    c.mux(packet)
        self.host = {'software_version': 'v2.1.14~release', 'data_version': '3.2',
                     'video_details': [
                         {'name': 'cam_left.mkv', 'start_offset_us': (self.epoch - self.pts[0]) // 1000},
                         {'name': 'cam_right.mkv', 'start_offset_us': (self.epoch + 850_000_000 - self.pts[0]) // 1000}]}
        self.write_fixture()

    def write_fixture(self, *, exposure_ns=10_000_000, drift_ns=0, mid_drift_ns=0,
                      missing=False, duplicate=False):
        (self.root / 'metadata.json').write_text(json.dumps(self.host))
        end0 = self.epoch + 1_200_000_000
        ego = {'software_version': 'V1.0.6', 'sdk_version': 'V2.1.3',
               'time_sync_mapping': {'output_timestamp_domain': 'unix_ns', 'video_pts_reference': 'exposure_end'},
               'video_details': [{'name': 'rgb.mp4', 'start_offset_us': (end0 - self.pts[0]) // 1000}]}
        (self.root / 'ego/metadata.json').write_text(json.dumps(ego))
        with (self.root / 'ego/sensor.mcap').open('wb') as f:
            writer = Writer(f)
            writer.start()
            schema = writer.register_schema(name='SxrEgoCameraMeta', encoding='jsonschema', data=b'{}')
            channel = writer.register_channel(topic='rgb_metainfo', message_encoding='binary', schema_id=schema)
            for i, pts in enumerate(self.pts):
                if missing and i == 2:
                    continue
                end = end0 + pts - self.pts[0] + (drift_ns if i == 2 else 0)
                mid = end - exposure_ns // 2 + (mid_drift_ns if i == 2 else 0)
                payload = struct.pack('<3q4I', end - exposure_ns, mid, pts // 1000,
                                      0 if duplicate else i, i + 100, exposure_ns, 1)
                writer.add_message(channel, log_time=mid, publish_time=mid, data=payload)
            writer.finish()

    def test_aligns_nonzero_pts_using_exposure_end(self):
        result = calculate_alignment(self.root)
        self.assertEqual(result.status, 'aligned', result.message)
        self.assertEqual(result.offsets_ms[self.root / 'cam_left.mkv'], 0)
        self.assertEqual(result.offsets_ms[self.root / 'cam_right.mkv'], 850)
        self.assertEqual(result.offsets_ms[self.root / 'ego/rgb.mp4'], 1200)
        self.write_fixture(exposure_ns=30_000_000)
        self.assertEqual(calculate_alignment(self.root).offsets_ms[self.root / 'ego/rgb.mp4'], 1200)

    def test_midpoint_jitter_does_not_reject_stable_exposure_end(self):
        self.write_fixture(mid_drift_ns=200_000)
        result = calculate_alignment(self.root)
        self.assertEqual(result.status, 'aligned', result.message)
        self.assertEqual(result.offsets_ms[self.root / 'ego/rgb.mp4'], 1200)

    def test_only_exact_supported_release_reads_timing(self):
        for version in ['v2.1.11~release', 'v2.1.10', 'v2.1.140', 'v2.1.15', 'unknown']:
            self.host['software_version'] = version
            self.write_fixture()
            with patch('alignment.first_pts_ns', side_effect=AssertionError('must not probe')):
                self.assertEqual(calculate_alignment(self.root).status, 'unsupported')
        self.assertTrue(supports_alignment('2.1.14'))
        self.assertTrue(supports_alignment('v2.1.14~release'))

    def test_bad_or_missing_metainfo_never_partially_aligns(self):
        for options in [{'missing': True}, {'duplicate': True}, {'drift_ns': 200_000}]:
            self.write_fixture(**options)
            result = calculate_alignment(self.root)
            self.assertEqual(result.status, 'unavailable')
            self.assertFalse(result.offsets_ms)
        (self.root / 'ego/sensor.mcap').unlink()
        self.assertEqual(calculate_alignment(self.root).status, 'unavailable')

    def test_saved_offsets_are_replaced_and_other_versions_reset(self):
        views = [View('left', self.root / 'cam_left.mkv', True, 900),
                 View('ego', self.root / 'ego/rgb.mp4', True, 1200)]
        result = calculate_alignment(self.root)
        apply_alignment(views, result)
        apply_alignment(views, result)
        self.assertEqual([v.offset_ms for v in views], [0, 1200])
        self.host['software_version'] = 'v2.1.11'
        self.write_fixture()
        apply_alignment(views, calculate_alignment(self.root))
        self.assertEqual([v.offset_ms for v in views], [0, 0])

    def test_explicit_manual_mode_and_version_display(self):
        views = [View('left', self.root / 'cam_left.mkv', True, -125)]
        apply_alignment(views, calculate_alignment(self.root, 'manual'))
        self.assertEqual(views[0].offset_ms, -125)
        info = episode_versions(self.root)
        self.assertEqual(info['host_data_version'], '3.2')
        self.assertEqual(info['ego_sdk_version'], 'V2.1.3')
        (self.root / 'metadata.json').write_text('invalid')
        self.assertEqual(episode_versions(self.root)['host_software_version'], '未知')

    def test_setting_defaults_and_validation(self):
        config = self.root / 'views.json'
        config.write_text('{"views": []}')
        self.assertEqual(configured_mode(config), AUTO)
        config.write_text('{"time_alignment": "manual", "views": []}')
        self.assertEqual(configured_mode(config), 'manual')
        config.write_text('{"time_alignment": "broken", "views": []}')
        with self.assertRaises(ValueError):
            configured_mode(config)


if __name__ == '__main__':
    unittest.main()
