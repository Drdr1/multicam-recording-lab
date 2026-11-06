# multicam-recording-lab

A test bench for **multi-camera + microphone recording systems**: the kind used to capture
operating rooms, labs or production lines for computer-vision work. It answers the questions
that decide whether a recording rig can be trusted, *before* the hardware is bought:

- Does every camera keep recording for a full session, and is every dropped frame counted?
- When a camera is unplugged or reboots, does recording recover by itself, and is the gap documented?
- Are all sources on one timeline, and **how far apart in milliseconds** are they actually?
- How much bandwidth, disk throughput and storage does a given camera plan really need?

> **Status: lab, not field-proven.** Everything here runs against *simulated* cameras and a
> simulated microphone published over RTSP (a real RTSP server, real H.264/H.265/AAC encoders,
> real ffmpeg recording). It has not been run against physical GigE Vision / USB3 Vision
> cameras, Orbbec RGB-D sensors, or hardware sync generators. Those need the vendor SDKs and
> are covered in the design notes, not in the tests.

## What's in it

| Part | What it does |
|---|---|
| `mcrec sim-video` / `sim-audio` | Simulated camera or mic. Flashes white / beeps at every wall-clock multiple of N seconds (a digital clapperboard), with an injectable offset, codec choice (H.264/H.265) and frame-drop pattern. |
| `mcrec record` | Supervisor: one ffmpeg per source, **stream copy** (no re-encode), wall-clock timestamps, segments cut on clock boundaries, restart with backoff on exit **or stall**, every event in `events.jsonl`. |
| `mcrec health` | Per-source report from the files on disk: frames, dropped frames, drop rate, interruptions with exact length, restarts. |
| `mcrec sync` | Finds the clapper in every source (brightness spike / tone onset) and reports offset, jitter and resolution per source against a reference camera. |
| `mcrec capacity` | Bandwidth, link utilisation, disk write rate and storage from an explicit plan; warns when a stream won't fit its link. |

Sources are identified by **serial**, not by port order or connection order, and recorded into
a folder named after their role (`or-wide`, `instrument-table`). Swapping a cable never mixes up views.

## The test scenario (runs in CI)

Four cameras of two codec types and one microphone record for about a minute:

- `instrument-table` gets a +120 ms offset and is **unplugged for 6 s** mid-session
- `field-detail` withholds 3 frames every 8 s
- `or-wide-opposite` uses H.265, everything else H.264

Results from a run:

```
| Source           | Serial       | Recorded | Segments | Frames | Dropped | Drop rate | Interruptions | Restarts |
| or-wide          | SN-WIDE-01   | 62s      | 5        | 929    | 0       | 0.000%    | none          | 0        |
| or-wide-opposite | SN-WIDE-02   | 62s      | 5        | 930    | 0       | 0.000%    | none          | 0        |
| instrument-table | SN-TABLE-01  | 53s      | 5        | 797    | 0       | 0.000%    | 9.176s        | 3        |
| field-detail     | SN-DETAIL-01 | 62s      | 5        | 911    | 24      | 2.567%    | none          | 0        |
| room-mic         | MIC-ROOM-01  | 63s      | 5        | audio  | -       | -         | none          | 0        |
```

The tests assert that the unplugged camera **recovered on its own** and the gap is reported
(6 s outage + reconnect), that dropped frames are counted in groups of 3, that healthy streams
show no drops or restarts, and that the 120 ms offset is measured to within one frame period.

Two findings the bench surfaced, both of which matter on real hardware:

1. **Video sync resolution is one frame.** At 15 fps a flash can only be located to ±66.7 ms,
   because each camera samples on its own frame grid. Sub-frame sync needs hardware triggering
   (GPIO/genlock) or PTP-locked cameras, not software timestamps.
2. **Encoders add their own delay.** The H.265 camera and the AAC microphone show a steady
   offset from the H.264 reference even with zero injected offset. It's measurable and constant
   (low jitter), so it's calibrated out per source, not ignored.

## Run it

```bash
pip install -r requirements.txt
# RTSP server (single static binary)
curl -sSL https://github.com/bluenviron/mediamtx/releases/download/v1.9.3/mediamtx_v1.9.3_linux_amd64.tar.gz | tar -xz -C tools mediamtx
pytest -q                                       # ~75 s, includes the full scenario
python -m mcrec.cli capacity config/capacity.or-room.yml
```

Recording a real rig is the same command with real RTSP URLs in `config/rig.example.yml`.

## Design notes

[`docs/DESIGN.md`](docs/DESIGN.md) covers the architecture for an operating-room rig: IP vs
industrial vs RGB-D cameras, synchronisation options, workstation and storage sizing, a bill of
materials template with alternatives, the validation test plan, and a risk register.
