"""Post-recording health report: dropped frames, interruptions, coverage per source.

Works only from what is on disk (packet timestamps via ffprobe) plus the supervisor's
events.jsonl, so it can be run on any recording after the fact, including ones made by
another tool, as long as they carry wall-clock timestamps.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import RigConfig, Source

INTERRUPTION_S = 2.0  # a gap longer than this is an interruption, not dropped frames


def packet_times(path: Path, stream: str) -> list[float]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", "packet=pts_time",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=False).stdout
    ts = []
    for line in out.splitlines():
        line = line.strip().rstrip(",")
        if line and line != "N/A":
            t = float(line)
            if t > 1e9:  # wall-clock epoch time; anything else is a muxer artefact, not a frame
                ts.append(t)
    return ts


@dataclass
class SourceHealth:
    name: str
    serial: str
    kind: str
    segments: int = 0
    empty_segments: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    recorded_s: float = 0.0
    frames: int = 0
    expected_frames: int = 0
    dropped_frames: int = 0
    drop_events: int = 0
    interruptions: list[dict] = field(default_factory=list)
    restarts: int = 0

    @property
    def drop_rate(self) -> float:
        return self.dropped_frames / self.expected_frames if self.expected_frames else 0.0


def analyse_source(src: Source, root: Path, events: list[dict]) -> SourceHealth:
    h = SourceHealth(name=src.name, serial=src.serial, kind=src.kind)
    files = sorted((root / src.name).glob(f"{src.name}_r*"))
    ts: list[float] = []
    for f in files:
        h.segments += 1
        t = packet_times(f, "v:0" if src.kind == "video" else "a:0")
        if not t:
            h.empty_segments += 1
        ts.extend(t)
    h.restarts = sum(1 for e in events if e["source"] == src.name and e["event"] == "start") - 1
    h.restarts = max(h.restarts, 0)
    if not ts:
        return h
    ts.sort()
    h.first_ts, h.last_ts = ts[0], ts[-1]
    if src.kind == "audio":
        nominal = _median_step(ts)
    else:
        nominal = 1.0 / src.fps
    h.frames = len(ts)
    for a, b in zip(ts, ts[1:]):
        gap = b - a
        if gap > INTERRUPTION_S:
            h.interruptions.append({"from": a, "to": b, "seconds": round(gap, 3)})
        elif gap > 1.5 * nominal and src.kind == "video":
            h.dropped_frames += round(gap / nominal) - 1
            h.drop_events += 1
    lost = sum(i["seconds"] for i in h.interruptions)
    h.recorded_s = round(h.last_ts - h.first_ts - lost, 3)
    if src.kind == "video":
        h.expected_frames = h.frames + h.dropped_frames
    return h


def _median_step(ts: list[float]) -> float:
    steps = sorted(b - a for a, b in zip(ts, ts[1:]) if b > a)
    return steps[len(steps) // 2] if steps else 0.0


def report(cfg: RigConfig) -> dict:
    root = cfg.output_dir
    events_file = root / "events.jsonl"
    events = [json.loads(line) for line in events_file.read_text().splitlines()] if events_file.exists() else []
    rows = [analyse_source(s, root, events) for s in cfg.sources]
    data = {"sources": [dict(asdict(r), drop_rate=round(r.drop_rate, 5)) for r in rows]}
    (root / "health.json").write_text(json.dumps(data, indent=2))
    (root / "health.md").write_text(to_markdown(rows))
    return data


def to_markdown(rows: list[SourceHealth]) -> str:
    lines = ["# Recording health", "",
             "| Source | Serial | Recorded | Segments | Frames | Dropped | Drop rate | Interruptions | Restarts |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        inter = ", ".join(f"{i['seconds']}s" for i in r.interruptions) or "none"
        frames = str(r.frames) if r.kind == "video" else "audio"
        dropped = str(r.dropped_frames) if r.kind == "video" else "-"
        rate = f"{r.drop_rate:.3%}" if r.kind == "video" else "-"
        lines.append(f"| {r.name} | {r.serial} | {r.recorded_s:.0f}s | {r.segments} | {frames} | {dropped} | "
                     f"{rate} | {inter} | {r.restarts} |")
    return "\n".join(lines) + "\n"
