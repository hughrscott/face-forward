# Camera recon and RTSP runbook

Status: implementation ready; site discovery is blocked until the two NVRs or their subnet routers are reachable over Tailscale and credentials are supplied out of band.

## 1. Required inventory (one row per school)

Record locally; do not commit credentials.

| School | NVR make/model | Tailscale host/IP or advertised LAN subnet | Camera channel(s) covering stalls | Recording retention |
|---|---|---|---|---|
| West U | required | required | required | target >=30 days |
| Heights | required | required | required | target >=30 days |

The Oracle node currently sees no NVR-named peer or advertised private subnet in `tailscale status`; only personal devices are visible. An NVR itself does not need Tailscale if a subnet router at the school advertises the NVR LAN.

## 2. Network discovery

Install nmap if it is not already present, then scan only the explicitly authorized NVR IP/subnet:

```bash
nmap -Pn -sT -p 80,443,554,8000,8080 <authorized-ip-or-cidr>
```

Do not scan `/24` around a Tailscale 100.x address: each 100.x address is a tailnet node, not a conventional LAN neighbor.

## 3. Vendor RTSP main-stream paths

Keep credentials in the shell environment, never in committed files or command history.

| Vendor | Main stream path |
|---|---|
| Hikvision | `/Streaming/Channels/<channel>01` |
| Dahua | `/cam/realmonitor?channel=<channel>&subtype=0` |
| Uniview | `/unicast/c<channel>/s0/live` |
| Axis | `/axis-media/media.amp?camera=<channel>` |

Example:

```bash
read -rsp 'RTSP password: ' RTSP_PASSWORD; printf '\n'
export RTSP_URL="rtsp://<user>:${RTSP_PASSWORD}@<nvr-ip>:554/Streaming/Channels/101"
.venv/bin/python camera_pipeline.py probe --source "$RTSP_URL"
```

`probe` reports codec, width, height, frame rate, and available duration metadata. For visual FOV confirmation, use a short authorized export or local `ffplay -rtsp_transport tcp "$RTSP_URL"`; do not leave a viewer running from this task.

## 4. Retention check

Use the NVR playback UI to inspect the oldest available recording on each relevant channel. Record the oldest timestamp and verify continuous coverage. A live RTSP stream cannot prove 30-day retention; this must be checked through NVR playback/export.

## 5. Ground-plane calibration

Copy `camera/lot_config.example.json` per lot. Replace the identity homography with a 3x3 image-to-ground homography computed from at least four surveyed, non-collinear points. Coordinates and polygons are in metres. Configure one stall/camera run at a time:

- `aisle_polygon_m`: drivable aisle region
- `stall_polygon_m`: target stall
- `stall_inward_vector`: direction a nose-in vehicle faces when parked
- `aisle_center_m`: LOS ray target
- `frame_stride`: process every Nth frame (2 is a CPU-cost starting point)

The example values are not valid calibration data.

## 6. Pilot and backfill

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-empirical.txt

# Short pilot first; saves normalized observations without retaining frames in memory.
.venv/bin/python camera_pipeline.py infer \
  --source pilot.mp4 \
  --config camera/west-u-camera-1.json \
  --output data/west-u-pilot-maneuvers.csv \
  --observations data/west-u-pilot-observations.jsonl \
  --max-frames 18000

# Full exported file or RTSP stream; omit --max-frames.
.venv/bin/python camera_pipeline.py infer \
  --source "$RTSP_URL" \
  --config camera/west-u-camera-1.json \
  --output data/west-u-maneuvers.csv
```

The adapter streams frames, keeps only five positions per active track, and expires tracks absent for five minutes. It does not buffer video in RAM.

## 7. Hand validation

Copy 200 timestamp-matched rows and hand-label `strategy` and `los_blocked`. Preserve either `timestamp` (the normal CSV key) or a shared `maneuver_id` in both files.

```bash
.venv/bin/python camera_pipeline.py validate \
  --truth data/hand-coded-200.csv \
  --predicted data/auto-coded-200.csv
```

Gates are strict: strategy accuracy >85%; LOS-blocked accuracy >80%.

## 8. Publication figure

The current `simulation_results.csv` lacks `entry_duration_s`; it cannot support Panel A without regenerating simulation output with that metric. Once both inputs have it:

```bash
.venv/bin/python camera_pipeline.py figure \
  --empirical data/maneuvers.csv \
  --simulation data/simulation-with-entry-duration.csv \
  --output docs/empirical_vs_simulation.pdf
```
