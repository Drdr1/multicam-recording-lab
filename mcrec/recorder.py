"""Recording supervisor: one ffmpeg per source, no re-encoding, self-healing.

* Stream copy (`-c copy`): the camera's own H.264/H.265 is written to disk untouched, so a
  workstation records many streams with almost no CPU.
* Wall-clock timestamps (`-use_wallclock_as_timestamps 1 -copyts`): every packet carries the
  epoch time it reached the workstation, so all files share one timeline. With NTP/PTP on
  the cameras and the host, that timeline is what synchronisation is measured against.
* Segmented Matroska: a crash or power cut costs at most one open segment, and MKV stays
  readable even when the last segment was never closed.
* Supervision: a recorder that exits (camera rebooted, cable pulled) or whose output stops
  growing (stall) is restarted with backoff. Every event goes to events.jsonl, which the
  health report turns into documented interruptions.
"""
from __future__ import annotations

import json
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import RigConfig, Source


def ffmpeg_cmd(src: Source, cfg: RigConfig, out_dir: Path, run: int) -> list[str]:
    ext = "mkv" if src.kind == "video" else "mka"
    pattern = str(out_dir / f"{src.name}_r{run:03d}_%Y%m%dT%H%M%S.{ext}")
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
        "-progress", "pipe:1", "-stats_period", "1",  # heartbeat for stall detection
        "-rtsp_transport", "tcp",
        "-timeout", str(int(cfg.rtsp_timeout_s * 1_000_000)),
        "-use_wallclock_as_timestamps", "1",
        "-i", src.url,
        "-map", "0", "-c", "copy", "-copyts",
        # cut on wall-clock boundaries (e.g. :00, :05, :10) so every source's segments line up
        "-f", "segment", "-segment_time", str(cfg.segment_seconds), "-segment_atclocktime", "1",
        "-segment_format", "matroska", "-strftime", "1",
        "-flush_packets", "1",  # write packets as they arrive: a power cut loses seconds, not minutes
        "-segment_list", str(out_dir / f"segments_r{run:03d}.csv"), "-segment_list_type", "csv",
        pattern,
    ]


@dataclass
class _Worker:
    src: Source
    proc: subprocess.Popen | None = None
    run: int = 0
    started: float = 0.0
    last_progress: str = ""
    last_growth: float = 0.0
    backoff: float = 1.0
    next_start: float = 0.0
    stderr_tail: list[str] = field(default_factory=list)


class Supervisor:
    def __init__(self, cfg: RigConfig):
        self.cfg = cfg
        self.root = cfg.output_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.events = (self.root / "events.jsonl").open("a")
        self.workers = [_Worker(s) for s in cfg.sources]
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def log(self, source: str, event: str, **kw) -> None:
        rec = {"t": time.time(), "source": source, "event": event, **kw}
        with self._lock:
            self.events.write(json.dumps(rec) + "\n")
            self.events.flush()

    def _dir(self, src: Source) -> Path:
        d = self.root / src.name
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _read_progress(w: _Worker, proc: subprocess.Popen) -> None:
        """ffmpeg -progress emits key=value blocks; out_time_us advances only while packets flow."""
        for line in proc.stdout:
            if line.startswith("out_time_us=") and line.strip() != "out_time_us=N/A":
                if line != w.last_progress:
                    w.last_progress = line
                    w.last_growth = time.time()

    def _start(self, w: _Worker) -> None:
        w.run += 1
        cmd = ffmpeg_cmd(w.src, self.cfg, self._dir(w.src), w.run)
        w.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        now = time.time()
        w.started, w.last_growth, w.last_progress = now, now, ""
        threading.Thread(target=self._read_progress, args=(w, w.proc), daemon=True).start()
        self.log(w.src.name, "start", run=w.run, serial=w.src.serial)

    def _stop_proc(self, w: _Worker, reason: str) -> None:
        if w.proc and w.proc.poll() is None:
            w.proc.send_signal(signal.SIGINT)  # lets ffmpeg close the segment cleanly
            try:
                w.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                w.proc.kill()
                w.proc.wait()
        err = (w.proc.stderr.read() if w.proc and w.proc.stderr else "")[-500:]
        self.log(w.src.name, "stop", run=w.run, reason=reason, rc=w.proc.returncode if w.proc else None,
                 stderr=err.strip())
        w.proc = None

    def tick(self) -> None:
        now = time.time()
        for w in self.workers:
            if w.proc is None:
                if now >= w.next_start:
                    self._start(w)
                continue
            if w.proc.poll() is not None:
                self._stop_proc(w, "exited")
                w.next_start = now + w.backoff
                self.log(w.src.name, "restart_scheduled", in_s=w.backoff)
                w.backoff = min(w.backoff * 2, 30.0)
                continue
            if now - w.last_growth <= self.cfg.stall_seconds:
                if now - w.started > 10:
                    w.backoff = 1.0  # healthy for a while: reset backoff
            else:
                self._stop_proc(w, "stalled")
                w.next_start = now + 1.0
                self.log(w.src.name, "restart_scheduled", in_s=1.0)

    def run(self, duration_s: float | None = None, interval: float = 0.5) -> None:
        self.log("*", "supervisor_start", sources=[s.name for s in self.cfg.sources])
        end = time.time() + duration_s if duration_s else None
        try:
            while not self._stop.is_set() and (end is None or time.time() < end):
                self.tick()
                time.sleep(interval)
        finally:
            for w in self.workers:
                if w.proc:
                    self._stop_proc(w, "shutdown")
            self.log("*", "supervisor_stop")
            self.events.close()

    def stop(self) -> None:
        self._stop.set()
