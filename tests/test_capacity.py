from pathlib import Path

import yaml

from mcrec.capacity import Stream, plan, storage_tb

ROOT = Path(__file__).resolve().parents[1]


def test_raw_bandwidth_is_pixel_arithmetic():
    s = Stream("cam", 1, "raw", width=2448, height=2048, fps=30, bits_per_pixel=8)
    assert round(s.per_device_mbps(), 1) == 1203.2  # 2448*2048*30*8 / 1e6


def test_raw_5mp_overflows_gige_but_fits_usb3():
    p = plan([Stream("cam", 1, "raw", width=2448, height=2048, fps=30, bits_per_pixel=8, link="gige")], 10, 30)
    assert any("gige" in w for w in p["warnings"])
    p = plan([Stream("cam", 1, "raw", width=2448, height=2048, fps=30, bits_per_pixel=8, link="usb3_gen1")], 10, 30)
    assert p["warnings"] == []


def test_storage_maths():
    # 8 Mb/s for 1 h = 3.6 GB
    assert round(storage_tb(8, 1, 1) * 1000, 2) == 3.6


def test_example_or_room_plan():
    raw = yaml.safe_load((ROOT / "config" / "capacity.or-room.yml").read_text())
    p = plan([Stream(**s) for s in raw["streams"]], raw["hours_per_day"], raw["retention_days"])
    assert p["total_mbps"] > 1000  # dominated by the uncompressed streams
    assert p["storage_TB"] > 100  # why raw industrial cameras need compression or a short buffer
    assert abs(p["disk_write_target_MB_s"] - p["sustained_write_MB_s"] * 2) < 0.2
