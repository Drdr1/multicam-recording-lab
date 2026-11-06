"""Measure cross-source synchronisation from recordings.

Finds the clapper events in every source -- a brightness spike in video, a tone onset in
audio -- and compares their wall-clock timestamps. On real hardware the same tool works
with a physical clapper, an LED flashing from a sync generator, or a hand clap seen and
heard by every device.

What it measures is *residual* offset on the shared wall-clock timeline: capture latency,
encoder buffering, network delay and clock error added together. That is the number that
matters for multi-view analysis, and the number the test report should state per camera.
"""
from __future__ import annotations

import json
import statistics
import subprocess
from pathlib import Path

import numpy as np

from .config import RigConfig, Source

SR = 48000


def video_events(path: Path, threshold: float = 150.0) -> list[float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-f", "lavfi", "-i", f"movie={path},scale=64:36,signalstats",
         "-show_entries", "frame=pts_time:frame_tags=lavfi.signalstats.YAVG", "-of", "csv=p=0"],
        capture_output=True, text=True, check=False).stdout
    events, prev_bright = [], False
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or parts[0] in ("", "N/A"):
            continue
        t, y = float(parts[0]), float(parts[1])
        bright = y > threshold
        if bright and not prev_bright:
            events.append(t)
        prev_bright = bright
    return events


def audio_events(path: Path, threshold: float = 0.2, min_gap_s: float = 1.0) -> list[float]:
    first = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", "-read_intervals", "%+#1", str(path)],
        capture_output=True, text=True, check=False).stdout.split()
    if not first:
        return []
    t0 = float(first[0].rstrip(","))
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
        capture_output=True, check=False).stdout
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if x.size == 0:
        return []
    win = int(SR * 0.002)
    env = np.sqrt(np.convolve(x * x, np.ones(win) / win, mode="same"))
    above = np.flatnonzero(env > threshold)
    events, last = [], -1e9
    for i in above:
        t = t0 + i / SR
        if t - last > min_gap_s:
            events.append(t)
        last = t
    return events


def source_events(src: Source, root: Path) -> list[float]:
    ev: list[float] = []
    for f in sorted((root / src.name).glob(f"{src.name}_r*")):
        ev.extend(video_events(f) if src.kind == "video" else audio_events(f))
    return sorted(ev)


def analyse(cfg: RigConfig, reference: str | None = None) -> dict:
    root = cfg.output_dir
    period = cfg.event_period_s
    per_source = {s.name: source_events(s, root) for s in cfg.sources}
    ref = reference or next(s.name for s in cfg.sources if s.kind == "video")
    # match events across sources by the clapper slot they belong to
    slots: dict[str, dict[int, float]] = {}
    for name, evs in per_source.items():
        slots[name] = {}
        for t in evs:
            slots[name].setdefault(round(t / period), t)
    src_fps = {s.name: s.fps for s in cfg.sources}
    result = {"reference": ref, "event_period_s": period, "sources": {}}
    for name, s in slots.items():
        common = sorted(set(s) & set(slots[ref]))
        offs = [(s[k] - slots[ref][k]) * 1000 for k in common]
        result["sources"][name] = {
            "events": len(per_source[name]),
            "matched": len(offs),
            "median_offset_ms": round(statistics.median(offs), 1) if offs else None,
            "jitter_ms": round(statistics.pstdev(offs), 1) if len(offs) > 1 else None,
            "max_abs_offset_ms": round(max(abs(o) for o in offs), 1) if offs else None,
            # a frame-based clapper can only be located to within one frame period
            "resolution_ms": round(1000 / src_fps[name], 1) if src_fps[name] else round(1000 / SR, 3),
        }
    (root / "sync.json").write_text(json.dumps(result, indent=2))
    return result
