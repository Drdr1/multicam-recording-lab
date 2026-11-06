"""Simulated capture devices, published over RTSP.

Each device is one process. Video devices emit a full-white "flash" frame burst and audio
devices emit a 1 kHz "beep" at every wall-clock multiple of `event_period` -- a digital
clapperboard that every source sees at the same true instant. A per-device `offset_ms`
shifts that instant to mimic capture/encode latency, so the sync analyser has a known
answer to find. `drop_every_s` periodically withholds frames to mimic USB/network drops.
"""
from __future__ import annotations

import math
import subprocess
import time

import numpy as np

W, H = 640, 360
SR = 48000
CHUNK_S = 0.02


def _publisher(cmd_in: list[str], url: str, codec: list[str]) -> subprocess.Popen:
    cmd = ["ffmpeg", "-loglevel", "error", *cmd_in, *codec, "-f", "rtsp", "-rtsp_transport", "tcp", url]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


def run_video(url: str, fps: float, period: float, offset_ms: float = 0.0, drop_every_s: float = 0.0,
              drop_frames: int = 3, codec: str = "h264", duration_s: float = 0.0) -> None:
    enc = (["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-g", str(int(fps))]
           if codec == "h264" else
           ["-c:v", "libx265", "-preset", "ultrafast", "-tune", "zerolatency", "-x265-params",
            f"keyint={int(fps)}:log-level=error"])
    p = _publisher(["-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"], url, enc)
    dark = np.full((H, W), 40, np.uint8)
    # a little texture so the encoder has something to do
    dark[::8, :] = 60
    white = np.full((H, W), 250, np.uint8)
    flash_len = 2 / fps
    off = offset_ms / 1000.0
    t0 = time.time()
    n = 0
    try:
        while True:
            target = t0 + n / fps
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
            now = time.time()
            if duration_s and now - t0 > duration_s:
                break
            n += 1
            if drop_every_s and (now - t0) % drop_every_s < drop_frames / fps and now - t0 > drop_every_s / 2:
                continue  # simulate a dropped frame: nothing reaches the encoder
            phase = (now - off) % period
            frame = white if phase < flash_len else dark
            p.stdin.write(frame.tobytes())
            p.stdin.flush()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        _close(p)


def run_audio(url: str, period: float, offset_ms: float = 0.0, duration_s: float = 0.0) -> None:
    p = _publisher(["-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-"], url, ["-c:a", "aac", "-b:a", "96k"])
    n_chunk = int(SR * CHUNK_S)
    off = offset_ms / 1000.0
    beep_len = 0.1
    t0 = time.time()
    k = 0
    rng = np.random.default_rng(1)
    try:
        while True:
            target = t0 + k * CHUNK_S
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)
            if duration_s and time.time() - t0 > duration_s:
                break
            chunk_start = t0 + k * CHUNK_S  # sample-accurate clock, not jittery time.time()
            ts = chunk_start + np.arange(n_chunk) / SR
            phase = np.mod(ts - off, period)
            tone = np.where(phase < beep_len, 0.6 * np.sin(2 * math.pi * 1000 * ts), 0.0)
            noise = rng.normal(0, 0.003, n_chunk)
            pcm = np.clip((tone + noise) * 32767, -32768, 32767).astype("<i2")
            p.stdin.write(pcm.tobytes())
            p.stdin.flush()
            k += 1
    except (BrokenPipeError, KeyboardInterrupt):
        pass
    finally:
        _close(p)


def _close(p: subprocess.Popen) -> None:
    try:
        p.stdin.close()
    except Exception:
        pass
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:
        p.kill()
