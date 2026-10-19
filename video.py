"""Timestamp based decoding. Each Reader belongs to exactly one worker thread."""
from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue

import av

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpg", ".mpeg"}
EPSILON = 1e-7


@dataclass
class View:
    name: str
    path: Path
    enabled: bool = False
    offset_ms: float = 0.0


def discover(directory: Path, config: Path) -> list[View]:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"目录不存在：{directory}")
    raw = json.loads(config.read_text(encoding="utf-8"))
    views, seen = [], set()
    for item in raw["views"]:
        path = (directory / Path(item["path"]).expanduser()).resolve()
        if path in seen:
            continue
        offset = float(item.get("offset_ms", 0))
        if not math.isfinite(offset) or abs(offset) > 3600000:
            raise ValueError("时间偏移必须在 -3600000 到 3600000 毫秒之间")
        views.append(View(str(item["name"]), path, bool(item.get("enabled", False)), offset))
        seen.add(path)
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            resolved = path.resolve()
            if resolved not in seen:
                views.append(View(path.relative_to(directory).as_posix(), resolved))
                seen.add(resolved)
    return views


class Reader:
    def __init__(self, path: Path):
        self.container = av.open(str(path))
        try:
            self.stream = self.container.streams.video[0]
            # Keep many active views from each creating dozens of decoder threads.
            self.stream.codec_context.thread_count = 2
            self.stream.thread_type = "FRAME"
            self.iterator = iter(self.container.decode(self.stream))
            first = next(self.iterator)
            if first.pts is None:
                raise ValueError("首帧缺少时间戳，无法可靠同步")
            self.origin = float(first.pts * first.time_base)
            self.current = first
            self.future = None
            self.eof = False
            self.fps = float(self.stream.average_rate or 30)
            if self.stream.duration is not None:
                self.duration = float(self.stream.duration * self.stream.time_base)
            elif self.container.duration is not None:
                start = (self.container.start_time or 0) / av.time_base
                self.duration = self.container.duration / av.time_base + start - self.origin
            else:
                self.duration = 0.0
            # Container duration semantics differ for nonzero starts (e.g. MKV
            # may report the final absolute timestamp). Resolve this ambiguity
            # from packet timestamps without retaining packets or decoding frames.
            if self.stream.duration is None and abs(self.origin) > EPSILON:
                end = self.origin
                with av.open(str(path)) as probe:
                    for packet in probe.demux(probe.streams.video[0]):
                        if packet.pts is not None:
                            packet_end = float(packet.pts * packet.time_base)
                            packet_end += float(packet.duration * packet.time_base) if packet.duration else 1 / self.fps
                            end = max(end, packet_end)
                self.duration = end - self.origin
            self.duration = max(0.0, self.duration)
            self.width, self.height = first.width, first.height
        except BaseException:
            self.container.close()
            raise

    def close(self):
        self.container.close()

    def timestamp(self, frame):
        if frame.pts is None:
            raise ValueError("视频帧缺少时间戳，无法可靠同步")
        return float(frame.pts * frame.time_base) - self.origin

    def _next(self):
        try:
            return next(self.iterator)
        except StopIteration:
            self.eof = True
            if self.current is not None:
                self.duration = self.timestamp(self.current) + 1 / self.fps
            return None

    def _seek(self, target):
        # Seek to a preceding keyframe, then decode forward for accurate selection.
        ticks = math.floor((target + self.origin) / float(self.stream.time_base))
        self.container.seek(ticks, stream=self.stream, backward=True, any_frame=False)
        self.iterator = iter(self.container.decode(self.stream))
        self.current = self.future = None
        self.eof = False

    def at(self, target: float):
        target = max(0.0, target)
        if self.duration > 0:
            target = min(target, self.duration)
        current_time = self.timestamp(self.current) if self.current is not None else -1.0
        if target < current_time - EPSILON or (target - current_time > 1.5 and not self.eof):
            # Avoid an exact EOF seek, which some containers reject.
            self._seek(min(target, max(0, self.duration - 0.05)) if self.duration else target)
        if self.current is None:
            self.current = self._next()
        if self.current is None:
            raise ValueError("没有可解码的图像")
        while True:
            if self.future is None and not self.eof:
                self.future = self._next()
            if self.future is None or self.timestamp(self.future) > target + EPSILON:
                break
            self.current, self.future = self.future, None
        return self.current

    def step(self, target: float, direction: int):
        current = self.at(target)
        current_time = self.timestamp(current)
        if direction > 0:
            return self.timestamp(self.future) if self.future is not None else current_time
        if current_time <= EPSILON:
            return 0.0
        return self.timestamp(self.at(max(0.0, current_time - 0.00001)))

    def image(self, frame, size):
        width, height = size
        ratio = min(width / frame.width, height / frame.height, 1.0)
        return frame.reformat(width=max(1, int(frame.width * ratio)),
                              height=max(1, int(frame.height * ratio)), format="rgb24").to_image()


class Worker(threading.Thread):
    """One replaceable pending request and one result: memory stays bounded."""
    def __init__(self, path):
        # On application exit let an in-progress decode finish and close its
        # container before Python tears down the FFmpeg extension modules.
        super().__init__(daemon=False, name=f"video:{Path(path).name}")
        self.path = path
        self.condition = threading.Condition()
        self.pending = None
        self.stopping = False
        self.results = Queue(maxsize=1)
        self.start()

    def request(self, generation, target, size, direction=0):
        with self.condition:
            self.pending = (generation, target, size, direction)
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.pending = None
            self.condition.notify()

    def run(self):
        reader = None
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopping or self.pending is not None)
                    if self.stopping:
                        break
                    generation, target, size, direction = self.pending
                    self.pending = None
                try:
                    if reader is None:
                        reader = Reader(self.path)
                    if direction:
                        value = {"step": reader.step(target, direction)}
                    else:
                        frame = reader.at(target)
                        value = {"image": reader.image(frame, size), "pts": reader.timestamp(frame),
                                 "duration": reader.duration, "resolution": (reader.width, reader.height),
                                 "fps": reader.fps, "ended": target >= reader.duration - 1e-5 and reader.duration > 0,
                                 "waiting": target < 0}
                except Exception as exc:
                    value = {"error": f"{type(exc).__name__}: {exc}"}
                if self.stopping:
                    break
                try:
                    self.results.get_nowait()
                except Empty:
                    pass
                try:
                    self.results.put_nowait((generation, value))
                except Full:
                    pass
        finally:
            if reader is not None:
                reader.close()
