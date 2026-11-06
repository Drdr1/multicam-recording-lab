# Design notes: multi-camera recording for clinical workflow capture

These notes are the starting framework for a real deployment. Model numbers are examples to
compare, not a final selection. Prices and specifications must be checked against current
vendor data at selection time.

## 1. Views and what each must show

| View | Purpose | Typical camera | Notes |
|---|---|---|---|
| Room wide (×2, opposite corners) | Who is where, movement, phases of work | PoE IP, 4K, wide lens | Two views remove most occlusion by staff |
| Instrument table | Which instrument is picked up / returned | PoE IP 1080p–4K, narrow lens, ceiling mount | Fixed, top-down, consistent lighting |
| Detail / field | Fine hand actions | Industrial (USB3/GigE Vision) or high-res IP | Only if the model actually needs this detail |
| Depth (optional) | 3D position of people/objects | RGB-D (e.g. Orbbec Femto Bolt / Mega) | Justify against the model's needs, see §3 |
| Audio | Verbal cues, phase transitions | Boundary or ceiling mic → audio interface or IP mic | Needs its own sync check |
| Screen capture (optional) | Monitors, EHR, imaging | HDMI capture device | Usually lower fps is fine |

## 2. Compressed IP cameras vs industrial cameras

| | PoE IP (RTSP, H.264/H.265) | Industrial (GigE Vision / USB3 Vision) |
|---|---|---|
| Bandwidth | 4–16 Mb/s per camera | Hundreds to 1000+ Mb/s per camera, uncompressed |
| Cabling | One Ethernet cable, power included, 100 m | USB3: short active cables; GigE: PoE possible |
| Recording CPU | Near zero (stream copy) | Encoding on the workstation (GPU NVENC) or huge storage |
| Image control | Limited (auto exposure, compression artefacts) | Full: exposure, gain, global shutter, raw pixels |
| Sync | NTP/PTP timestamps, software alignment | Hardware trigger (GPIO) for frame-exact sync |
| Scale across sites | Easy, standard IT gear | Each site needs careful hardware integration |
| Software | Any RTSP recorder (ffmpeg, this lab) | Vendor SDK (e.g. Basler pylon, Orbbec SDK) or GenICam/Aravis |

Default recommendation for multi-site scale: **PoE IP cameras for room and table views**, and
industrial cameras only where the model needs uncompressed detail or frame-exact sync.

## 3. Is RGB-D worth it?

Worth it when the model needs metric 3D position (distance between people, reach into a sterile
field) and a 2-D multi-view setup can't provide it. Costs: USB3 bandwidth and short cable runs,
SDK dependence, extra storage (depth Y16 at 640×576×30 fps ≈ 177 Mb/s raw), and multi-device
sync cabling. Recommend a one-room pilot before committing every site.

## 4. Synchronisation, in order of strength

1. **Hardware trigger / genlock**: one signal fires every camera's shutter; frame-exact. Industrial
   and RGB-D cameras support it; most IP cameras don't.
2. **PTP (IEEE 1588)**: sub-millisecond clocks on cameras and workstation, timestamps embedded per
   frame. Needs PTP-capable cameras and switch.
3. **NTP + receive-time timestamps** (what this lab measures): every packet stamped on arrival.
   Accuracy is limited by encoder and network latency and, for video, by one frame period.
   Residual offsets are measured with a clapper and calibrated per camera.

Whichever is chosen, the test report states the measured offset and jitter per source.

## 5. Sizing (from `config/capacity.or-room.yml`)

Two 4K H.265 room cameras, two 1080p table/detail cameras, one uncompressed 5 MP industrial
camera, one RGB-D depth stream and a room microphone:

| Result | Value |
|---|---|
| Total bandwidth | 1,408 Mb/s |
| Sustained disk write | 176 MB/s (target 352 MB/s with 2× headroom) |
| Per recorded hour | ~634 GB |
| 10 h/day, 30 days | ~190 TB |

The uncompressed streams are 98% of that. Either compress them on the workstation's GPU
(NVENC) as they're captured, or keep raw only for the detail view and only as long as needed.
With compression, the same room is a few TB a month.

## 6. Workstation and network (template)

| Item | Requirement | Example options to compare |
|---|---|---|
| Workstation | 8+ cores, 32–64 GB RAM, NVIDIA GPU for NVENC if industrial/RGB-D | Workstation tower, rack server |
| USB controllers | One dedicated USB3 controller per high-bandwidth camera | PCIe 4-port cards with 4 independent controllers |
| NICs | Separate camera network from facility network | 2.5/10 GbE PCIe NIC |
| PoE switch | PoE+ per camera, enough budget for all ports, PTP if used | Managed PoE+ switch, VLAN for cameras |
| Recording storage | Sustained write ≥ 2× calculated rate | NVMe for capture, larger HDD/NAS for retention |
| Sync | Per §4 | Trigger hub / sync cables, PTP grandmaster |
| UPS | Ride out short power loss, clean shutdown on long loss | Line-interactive UPS with USB signalling |

## 7. Validation test plan (on the purchased hardware)

1. Concurrent recording of all sources for a full representative session length, plus 20%.
2. Persistent identification: reboot the workstation and re-plug cameras into different ports;
   every recording must land under the same role name.
3. Timestamps and sync: clapper (flash + sound) every few minutes; report offset and jitter
   per source with `mcrec sync`.
4. Audio alignment: same clapper, audio vs reference video.
5. Dropped frames: `mcrec health` on the full session; target and actual drop rate per source.
6. Recovery: pull a camera's cable, reboot one camera, kill the recorder process, cut power to
   the switch. Each event must recover on its own and appear in the report with its exact gap.
7. Storage: confirm sustained write rate under full load and the disk-full behaviour.

## 8. Risk register

| Risk | Effect | Mitigation |
|---|---|---|
| USB3 bandwidth sharing | Silent frame drops on industrial/RGB-D cameras | One controller per camera; measure drop rate under full load |
| Clock drift between devices | Views slowly drift apart over long sessions | NTP/PTP everywhere; clapper checks through the session |
| Encoder latency differs by camera | Constant offsets between views | Measure and calibrate per camera |
| Camera reboots or firmware updates mid-session | Gaps in recording | Supervisor restarts + documented gaps; disable auto-updates |
| Disk fills or slows | Recording stops or drops frames | Write-rate headroom, free-space alarms, retention rotation |
| Lighting (OR lights, displays) | Over/under-exposed views | Fixed exposure where possible; test under real lights |
| Patient privacy | Identifiable video and audio of patients and staff | Encrypt at rest, restrict access, define retention, follow the facility's consent and data rules |
| Vendor SDK lock-in | Hard to switch camera brands | Prefer standards (RTSP, GenICam) where the model allows |
