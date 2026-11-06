from __future__ import annotations

import argparse
import json
import signal
import sys

import yaml

from . import capacity, config, health, simulator, sync
from .recorder import Supervisor


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mcrec", description="Multi-camera recording lab")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("sim-video", help="publish one simulated camera over RTSP")
    v.add_argument("--url", required=True)
    v.add_argument("--fps", type=float, default=15)
    v.add_argument("--period", type=float, default=10)
    v.add_argument("--offset-ms", type=float, default=0)
    v.add_argument("--drop-every-s", type=float, default=0)
    v.add_argument("--codec", choices=["h264", "h265"], default="h264")
    v.add_argument("--duration", type=float, default=0)

    a = sub.add_parser("sim-audio", help="publish one simulated microphone over RTSP")
    a.add_argument("--url", required=True)
    a.add_argument("--period", type=float, default=10)
    a.add_argument("--offset-ms", type=float, default=0)
    a.add_argument("--duration", type=float, default=0)

    r = sub.add_parser("record", help="record every source in the rig config")
    r.add_argument("config")
    r.add_argument("--duration", type=float, default=None)

    h = sub.add_parser("health", help="dropped frames / interruptions report")
    h.add_argument("config")

    s = sub.add_parser("sync", help="cross-source offset report from clapper events")
    s.add_argument("config")
    s.add_argument("--reference")

    c = sub.add_parser("capacity", help="bandwidth and storage plan")
    c.add_argument("plan")

    args = p.parse_args(argv)
    if args.cmd == "sim-video":
        simulator.run_video(args.url, args.fps, args.period, args.offset_ms, args.drop_every_s,
                            codec=args.codec, duration_s=args.duration)
    elif args.cmd == "sim-audio":
        simulator.run_audio(args.url, args.period, args.offset_ms, duration_s=args.duration)
    elif args.cmd == "record":
        sup = Supervisor(config.load(args.config))
        signal.signal(signal.SIGTERM, lambda *_: sup.stop())
        signal.signal(signal.SIGINT, lambda *_: sup.stop())
        sup.run(args.duration)
    elif args.cmd == "health":
        cfg = config.load(args.config)
        health.report(cfg)
        print((cfg.output_dir / "health.md").read_text())
    elif args.cmd == "sync":
        print(json.dumps(sync.analyse(config.load(args.config), args.reference), indent=2))
    elif args.cmd == "capacity":
        raw = yaml.safe_load(open(args.plan))
        streams = [capacity.Stream(**s) for s in raw["streams"]]
        print(json.dumps(capacity.plan(streams, raw["hours_per_day"], raw["retention_days"]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
