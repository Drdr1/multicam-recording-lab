"""End-to-end: simulated rig -> RTSP server -> supervised recording -> health + sync reports.

Scenario (about 70 s of wall time):
  or-wide            H.264, reference camera
  or-wide-opposite   H.265, different camera type/codec
  instrument-table   H.264, +120 ms injected offset, unplugged for 6 s mid-recording
  field-detail       H.264, drops 3 frames every 8 s
  room-mic           AAC audio
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from mcrec import config, health, sync

ROOT = Path(__file__).resolve().parents[1]
MTX = os.environ.get("MEDIAMTX", str(ROOT / "tools" / "mediamtx"))
URL = "rtsp://127.0.0.1:8554"
PY = [sys.executable, "-m", "mcrec.cli"]

pytestmark = pytest.mark.skipif(not (Path(MTX).exists() and shutil.which("ffmpeg")),
                                reason="needs mediamtx and ffmpeg")


def _spawn(*args, **kw):
    return subprocess.Popen([*PY, *args], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def _kill(p):
    if p and p.poll() is None:
        p.send_signal(signal.SIGINT)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rig")
    rig = yaml.safe_load((ROOT / "config" / "rig.example.yml").read_text())
    rig.update(output_dir=str(tmp / "rec"), segment_seconds=15)
    cfg_path = tmp / "rig.yml"
    cfg_path.write_text(yaml.safe_dump(rig))

    procs = [subprocess.Popen([MTX, str(ROOT / "config" / "mediamtx.yml")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    time.sleep(1.5)
    procs += [
        _spawn("sim-video", "--url", f"{URL}/SN-WIDE-01"),
        _spawn("sim-video", "--url", f"{URL}/SN-WIDE-02", "--codec", "h265"),
        _spawn("sim-video", "--url", f"{URL}/SN-DETAIL-01", "--drop-every-s", "8"),
        _spawn("sim-audio", "--url", f"{URL}/MIC-ROOM-01"),
    ]
    table = _spawn("sim-video", "--url", f"{URL}/SN-TABLE-01", "--offset-ms", "120")
    time.sleep(2)
    rec = _spawn("record", str(cfg_path), "--duration", "62")
    time.sleep(25)
    _kill(table)  # unplug the instrument-table camera ...
    time.sleep(6)
    table = _spawn("sim-video", "--url", f"{URL}/SN-TABLE-01", "--offset-ms", "120")  # ... and plug it back
    rec.wait(timeout=120)
    for p in [table, *procs[1:], procs[0]]:
        _kill(p)
    cfg = config.load(cfg_path)
    events = [json.loads(l) for l in (cfg.output_dir / "events.jsonl").read_text().splitlines()]
    return cfg, health.report(cfg), sync.analyse(cfg), events


def _src(rep, name):
    return next(s for s in rep["sources"] if s["name"] == name)


def test_every_source_recorded_under_its_persistent_name(session):
    cfg, rep, _, _ = session
    for s in cfg.sources:
        files = list((cfg.output_dir / s.name).glob(f"{s.name}_r*"))
        assert files, f"no recording for {s.name}"
        assert _src(rep, s.name)["serial"] == s.serial
        assert _src(rep, s.name)["recorded_s"] > 40


def test_segments_cut_on_wall_clock_boundaries(session):
    cfg, _, _, _ = session
    # 15 s segments over ~60 s: a handful of files, not one per keyframe
    for name in ("or-wide", "field-detail"):
        assert 3 <= len(list((cfg.output_dir / name).glob(f"{name}_r*"))) <= 7


def test_healthy_streams_have_no_drops_or_restarts(session):
    _, rep, _, _ = session
    for name in ("or-wide", "or-wide-opposite", "room-mic"):
        s = _src(rep, name)
        assert s["restarts"] == 0, name
        assert s["interruptions"] == [], name
    assert _src(rep, "or-wide")["dropped_frames"] == 0


def test_dropped_frames_are_counted(session):
    _, rep, _, _ = session
    s = _src(rep, "field-detail")
    # 3 frames withheld every 8 s over ~60 s -> roughly 6-8 drop events
    assert 4 <= s["drop_events"] <= 9
    assert 2.5 <= s["dropped_frames"] / s["drop_events"] <= 3.5
    assert s["interruptions"] == []


def test_unplugged_camera_recovers_and_gap_is_reported(session):
    _, rep, _, events = session
    s = _src(rep, "instrument-table")
    assert len(s["interruptions"]) == 1
    assert 5.5 <= s["interruptions"][0]["seconds"] <= 15  # 6 s outage + reconnect time
    assert s["restarts"] >= 1
    restarts = [e for e in events if e["source"] == "instrument-table" and e["event"] == "start"]
    assert len(restarts) >= 2
    assert s["recorded_s"] > 40  # kept recording after the outage


def test_sync_offset_between_same_type_cameras_is_measured(session):
    _, _, sy, _ = session
    srcs = sy["sources"]
    assert srcs["or-wide"]["matched"] >= 4
    table = srcs["instrument-table"]
    assert table["matched"] >= 3
    # same codec and pipeline as the reference, so the measured offset is the injected 120 ms,
    # give or take one frame (15 fps -> 66.7 ms): each camera samples the flash on its own frame grid
    assert table["resolution_ms"] == 66.7
    assert 120 - 70 <= table["median_offset_ms"] <= 120 + 70
    assert abs(srcs["field-detail"]["median_offset_ms"]) <= 70


def test_codec_and_audio_latency_are_surfaced(session):
    _, _, sy, _ = session
    # different encoders add their own buffering; the report must show it, not hide it
    for name in ("or-wide-opposite", "room-mic"):
        s = sy["sources"][name]
        assert s["matched"] >= 3
        assert s["median_offset_ms"] is not None and s["jitter_ms"] is not None
