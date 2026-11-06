"""Bandwidth, storage and link-budget calculator with every assumption written down.

Two kinds of camera need different maths:
* compressed (IP/RTSP, PoE): bitrate is set on the camera, so bandwidth = bitrate.
* uncompressed (USB3 Vision / GigE Vision / RGB-D over USB): bandwidth =
  width x height x fps x bits-per-pixel, before any compression the workstation applies.
"""
from __future__ import annotations

from dataclasses import dataclass

# usable payload rates, deliberately conservative (protocol overhead, real controllers)
LINKS_MBPS = {
    "gige": 940.0,  # 1 GbE after Ethernet/IP/UDP overhead
    "2.5gbe": 2350.0,
    "10gbe": 9400.0,
    "usb3_gen1": 3200.0,  # 5 Gb/s signalling, ~400 MB/s practical
}


@dataclass(frozen=True)
class Stream:
    name: str
    count: int
    kind: str  # "compressed" | "raw" | "audio"
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bitrate_mbps: float = 0.0  # compressed / audio
    bits_per_pixel: float = 0.0  # raw (8 = Mono8/Bayer8, 12 = Bayer12p, 16 = depth Y16)
    link: str = ""

    def per_device_mbps(self) -> float:
        if self.kind == "raw":
            return self.width * self.height * self.fps * self.bits_per_pixel / 1e6
        return self.bitrate_mbps

    def total_mbps(self) -> float:
        return self.per_device_mbps() * self.count


def storage_tb(total_mbps: float, hours_per_day: float, days: float) -> float:
    return total_mbps / 8 * 3600 * hours_per_day * days / 1e6  # MB -> TB (decimal)


def plan(streams: list[Stream], hours_per_day: float, retention_days: float, disk_write_headroom: float = 2.0) -> dict:
    rows = []
    warnings = []
    for s in streams:
        per = s.per_device_mbps()
        row = {"stream": s.name, "count": s.count, "per_device_mbps": round(per, 1),
               "total_mbps": round(s.total_mbps(), 1)}
        if s.link:
            cap = LINKS_MBPS[s.link]
            util = per / cap
            row["link"] = s.link
            row["link_utilisation"] = round(util, 3)
            if util > 0.7:
                warnings.append(f"{s.name}: {util:.0%} of a {s.link} link per device -- give each device its "
                                f"own controller/port, or lower resolution/fps/bit depth")
        rows.append(row)
    total = sum(s.total_mbps() for s in streams)
    write_mb_s = total / 8
    return {
        "streams": rows,
        "total_mbps": round(total, 1),
        "sustained_write_MB_s": round(write_mb_s, 1),
        "disk_write_target_MB_s": round(write_mb_s * disk_write_headroom, 1),
        "per_session_hour_GB": round(total / 8 * 3600 / 1000, 1),
        "storage_TB": round(storage_tb(total, hours_per_day, retention_days), 2),
        "warnings": warnings,
    }
