"""Version-gated alignment of the three original RGB videos.

Keep Unix arithmetic in integer nanoseconds. The viewer uses fixed offsets, so
only accept an Ego metainfo timeline that agrees with its actual PTS throughout
the recording (within 0.1 ms). Never infer a constant exposure correction.
"""
from __future__ import annotations

import json
import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import av

AUTO = "auto_v2_1_14"
MODES = (AUTO, "first_frame", "manual")
CAMERAS = ("cam_left.mkv", "cam_right.mkv", "ego/rgb.mp4")
UNIX_MIN_NS = 946684800_000_000_000
MAX_TIMELINE_ERROR_NS = 100_000


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{Path(path).name} 必须为 JSON 对象")
    return value


def configured_mode(config):
    mode = read_json(config).get("time_alignment", AUTO)
    if mode not in MODES:
        raise ValueError(f"未知时间对齐设置：{mode}")
    return mode


def episode_versions(directory):
    """Version display must remain available even with incomplete metadata."""
    result = {}
    for prefix, name in (("host", "metadata.json"), ("ego", "ego/metadata.json")):
        try:
            data = read_json(Path(directory) / name)
        except (OSError, ValueError):
            data = {}
        for key in ("software_version", "data_version", "hardware_version", "sdk_version"):
            result[f"{prefix}_{key}"] = str(data.get(key) or "未知")
    return result


def supports_alignment(version):
    # Deliberately exclude 2.1.140 and newer releases until verified.
    return re.fullmatch(r"v?2\.1\.14(?:[~+\-][A-Za-z0-9._~+\-]+)?", version) is not None


def start_ns(metadata, name):
    matches = [v for v in metadata.get("video_details", []) if v.get("name") == name]
    if len(matches) != 1:
        raise ValueError(f"{name} 缺少唯一的 video_details 起点")
    value = matches[0].get("start_offset_us")
    if isinstance(value, bool) or not isinstance(value, int) or value * 1000 < UNIX_MIN_NS:
        raise ValueError(f"{name} 的 start_offset_us 不是有效 Unix 微秒整数")
    return value * 1000


def first_pts_ns(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.codec_context.thread_count = 1
        frame = next(container.decode(stream))
        if frame.pts is None:
            raise ValueError(f"{path.name} 首帧缺少 PTS")
        return round(frame.pts * frame.time_base * 1_000_000_000)


def ego_exposure_end_origin(directory, metadata):
    from mcap.reader import NonSeekingReader

    mapping = metadata.get("time_sync_mapping", {})
    if mapping.get("output_timestamp_domain") != "unix_ns":
        raise ValueError("Ego 未声明 Unix 时间域")
    if mapping.get("video_pts_reference") != "exposure_end":
        raise ValueError("Ego 未声明 PTS 对应曝光完成时刻")
    # Streaming reader also handles these recordings' incomplete MCAP summaries.
    records = []
    with (directory / "ego/sensor.mcap").open("rb") as source:
        reader = NonSeekingReader(source, validate_crcs=True)
        for _, channel, message in reader.iter_messages(log_time_order=False):
            if channel.topic.lstrip("/") != "rgb_metainfo":
                continue
            if len(message.data) != 40:
                raise ValueError("rgb_metainfo 帧记录格式不支持")
            records.append(struct.unpack("<3q4I", message.data))
    if not records:
        raise ValueError("缺少 rgb_metainfo 曝光时间")
    records.sort(key=lambda r: r[3])
    pts = []
    path = directory / "ego/rgb.mp4"
    with av.open(str(path)) as container:
        for packet in container.demux(container.streams.video[0]):
            if packet.pts is not None:
                pts.append(round(packet.pts * packet.time_base * 1_000_000_000))
    pts.sort()
    if len(pts) != len(records):
        raise ValueError("Ego 视频帧数与 rgb_metainfo 不一致")
    origins = []
    previous_end = previous_pts = None
    for index, (record, pts_ns) in enumerate(zip(records, pts)):
        exposure_start, mid, meta_pts_us, frame_index, _, exposure_ns, _ = record
        exposure_end = exposure_start + exposure_ns
        if frame_index != index or exposure_start < UNIX_MIN_NS or exposure_ns <= 0:
            raise ValueError("Ego 曝光时间或帧索引无效")
        if not exposure_start <= mid <= exposure_end + MAX_TIMELINE_ERROR_NS:
            raise ValueError("Ego 曝光中点与曝光区间不一致")
        if previous_end is not None and (exposure_end <= previous_end or pts_ns <= previous_pts):
            raise ValueError("Ego 时间轴不是严格递增")
        if abs(pts_ns - meta_pts_us * 1000) > 1000:
            raise ValueError("Ego 视频 PTS 与曝光元信息不匹配")
        previous_end, previous_pts = exposure_end, pts_ns
        origins.append(exposure_end - pts_ns)
    if max(origins) - min(origins) > MAX_TIMELINE_ERROR_NS:
        raise ValueError("Ego 曝光完成时间与 PTS 存在变化，固定偏移无法可靠对齐")
    if first_pts_ns(path) != pts[0]:
        raise ValueError("Ego 首个可解码帧与元信息不匹配")
    reference = start_ns(metadata, "rgb.mp4") + pts[0]
    expected = records[0][0] + records[0][5]
    if abs(reference - expected) > MAX_TIMELINE_ERROR_NS:
        raise ValueError("Ego metadata 起点与曝光完成时刻不一致")
    return expected, max(origins) - min(origins)


@dataclass
class Alignment:
    mode: str
    status: str
    message: str
    offsets_ms: dict[Path, float] = field(default_factory=dict)


def calculate_alignment(directory, mode=AUTO):
    directory = Path(directory).expanduser().resolve()
    if mode not in MODES:
        raise ValueError(f"未知时间对齐设置：{mode}")
    if mode == "manual":
        return Alignment(mode, "manual", "手动偏移；按每路输入值播放")
    if mode == "first_frame":
        return Alignment(mode, "first_frame", "首帧对齐；三路均从各自 0 秒播放")
    try:
        metadata = read_json(directory / "metadata.json")
        version = str(metadata.get("software_version", "未知"))
        if not supports_alignment(version):
            return Alignment(mode, "unsupported", f"{version} 未启用自动对齐；按首帧播放")
        ego = read_json(directory / "ego/metadata.json")
        starts = {name: start_ns(metadata, name) + first_pts_ns(directory / name)
                  for name in CAMERAS[:2]}
        starts[CAMERAS[2]], _ = ego_exposure_end_origin(directory, ego)
        origin = min(starts.values())
        offsets = {(directory / name).resolve(): (value - origin) / 1_000_000
                   for name, value in starts.items()}
        if any(not math.isfinite(x) or x > 3_600_000 for x in offsets.values()):
            raise ValueError("三路起点跨度超出播放器范围")
        return Alignment(mode, "aligned", "v2.1.14 三路自动对齐已启用（Ego 曝光完成时刻）；其他视角未自动对齐", offsets)
    except Exception as exc:
        return Alignment(mode, "unavailable", f"自动对齐未启用，已按首帧播放：{exc}")


def apply_alignment(views, alignment):
    if alignment.mode != "manual":
        for view in views:
            # Replace, never add: saved offsets cannot be applied twice.
            view.offset_ms = alignment.offsets_ms.get(view.path.resolve(), 0.0)
