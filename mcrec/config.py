"""Rig configuration.

Every source is identified by a *persistent* hardware identity (serial / MAC), never by the
order it was plugged in or enumerated. Output folders are named after the logical role
("or-wide", "instrument-table"), so swapping a cable or a USB port never mixes up recordings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Source:
    serial: str  # persistent identity: camera serial, MAC, or udev by-id path
    name: str  # logical role, used for folder names and reports
    kind: str  # "video" | "audio"
    url: str
    fps: float = 0.0  # nominal frame rate (video only)


@dataclass(frozen=True)
class RigConfig:
    output_dir: Path
    segment_seconds: int
    sources: list[Source] = field(default_factory=list)
    stall_seconds: float = 6.0  # restart a recorder whose output stops growing for this long
    rtsp_timeout_s: float = 5.0
    event_period_s: float = 10.0  # simulator only: sync flash/beep every N seconds of wall time

    def by_name(self, name: str) -> Source:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(name)


def load(path: str | Path) -> RigConfig:
    raw = yaml.safe_load(Path(path).read_text())
    sources = []
    seen_serial, seen_name = set(), set()
    for s in raw["sources"]:
        src = Source(serial=str(s["serial"]), name=s["name"], kind=s["kind"],
                     url=s["url"].format(serial=s["serial"]), fps=float(s.get("fps", 0)))
        if src.kind not in {"video", "audio"}:
            raise ValueError(f"{src.name}: kind must be video or audio")
        if src.kind == "video" and src.fps <= 0:
            raise ValueError(f"{src.name}: video sources need fps")
        if src.serial in seen_serial or src.name in seen_name:
            raise ValueError(f"duplicate serial or name: {src.serial}/{src.name}")
        seen_serial.add(src.serial)
        seen_name.add(src.name)
        sources.append(src)
    return RigConfig(
        output_dir=Path(raw.get("output_dir", "recordings")),
        segment_seconds=int(raw.get("segment_seconds", 300)),
        sources=sources,
        stall_seconds=float(raw.get("stall_seconds", 6.0)),
        rtsp_timeout_s=float(raw.get("rtsp_timeout_s", 5.0)),
        event_period_s=float(raw.get("event_period_s", 10.0)),
    )
