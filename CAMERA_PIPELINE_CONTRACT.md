# Empirical Camera Pipeline Contract

Version: 1.0
Producer: `camera_pipeline.py`
Consumers: statistical analysis, publication figure generator, optional frontend data loader

## Data model

A completed maneuver is one continuously tracked vehicle that:

1. appears in the calibrated aisle;
2. enters the configured stall and becomes stationary (`speed <= 0.2 m/s`);
3. later moves from that stall; and
4. reaches the aisle again.

Parking dwell time is excluded. `total_cycle_s = entry_duration_s + exit_duration_s`.

`strategy` is determined at the parked state. A parked vehicle whose heading has a non-negative dot product with the configured stall inward vector is `forward` (nose-in); the opposite heading is `reverse` (back-in).

## CLI surface

All commands exit 0 on success and nonzero on invalid input/runtime failure.

### `infer`

Runs Ultralytics YOLOv8n detection and Supervision ByteTrack tracking on an MP4 file or RTSP URL.

```text
camera_pipeline.py infer \
  --source <mp4-or-rtsp> \
  --config <lot-config.json> \
  --output <maneuvers.csv> \
  [--observations <normalized.jsonl>] \
  [--model yolov8n.pt] \
  [--max-frames N]
```

Stdout JSON:

```json
{"maneuvers": 42, "output": "data/maneuvers.csv"}
```

### `normalize`

Converts previously saved normalized JSONL observations into the same CSV. This makes the scientific state machine reproducible without rerunning YOLO.

```text
camera_pipeline.py normalize --input frames.jsonl --config lot.json --output maneuvers.csv
```

### `probe`

Uses `ffprobe` to return source codec, resolution, average frame rate, and duration metadata as JSON.

```text
camera_pipeline.py probe --source <mp4-or-rtsp>
```

### `validate`

Compares hand labels and predictions joined by `maneuver_id` when both files contain it, otherwise by `timestamp`.

```text
camera_pipeline.py validate --truth hand.csv --predicted predicted.csv
```

Stdout JSON:

```json
{
  "matched": 200,
  "strategy_accuracy": 0.91,
  "los_blocked_accuracy": 0.84,
  "strategy_gate_passed": true,
  "los_blocked_gate_passed": true
}
```

### `figure`

Generates a two-panel PDF/PNG/SVG (format inferred from extension): entry-time empirical CDFs and conflict-rate comparison with Wilson 95% confidence intervals.

```text
camera_pipeline.py figure --empirical maneuvers.csv --simulation simulation.csv --output comparison.pdf
```

Required simulation columns: `strategy,entry_duration_s,critical_conflict`. The current v1 simulation CSV does not contain `entry_duration_s`; the command intentionally fails rather than substituting total cycle time.

## `maneuvers.csv`

UTF-8, comma-delimited, exactly one row per complete maneuver and this exact ordered header:

```text
timestamp,strategy,entry_duration_s,exit_duration_s,total_cycle_s,los_blocked,suv_adjacent,ped_count,near_miss_ttc_s,conflict_flag
```

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | ISO-8601 string | UTC timestamp at initial aisle observation |
| `strategy` | enum | exactly `forward` or `reverse` |
| `entry_duration_s` | decimal | aisle appearance through stationary parked state |
| `exit_duration_s` | decimal | first movement from parked state through aisle return |
| `total_cycle_s` | decimal | entry + exit; excludes dwell |
| `los_blocked` | `True`/`False` | any exit-frame LOS ray intersects an adjacent tracked vehicle box |
| `suv_adjacent` | `True`/`False` | adjacent bus/truck class or calibrated footprint proxy >=4.8 m |
| `ped_count` | integer | unique pedestrian track IDs observed during exit |
| `near_miss_ttc_s` | decimal or empty | minimum constant-velocity TTC within a 0.75 m collision envelope; empty when none |
| `conflict_flag` | `True`/`False` | minimum TTC exists and is <1.5 s |

The SUV field is a proxy because COCO YOLO has no SUV class. It must be checked during the hand-coded pilot.

## Normalized observation JSONL

One JSON object per processed frame:

```json
{
  "timestamp_s": 24.0,
  "timestamp": "2026-07-20T12:00:24+00:00",
  "vehicles": [
    {
      "track_id": 7,
      "speed_mps": 1.0,
      "in_stall": false,
      "in_aisle": true,
      "heading": [0.0, -1.0],
      "los_blocked": true,
      "suv_adjacent": true,
      "pedestrian_ttcs": {"3": 0.9}
    }
  ]
}
```

Normalized observations contain no pixels or faces. They are the preferred reproducibility artifact when raw-footage retention is not required.

## Calibration contract

`LotConfig` requires:

- `source_id`: stable lot/camera identifier;
- `homography`: 3x3 image-pixel to ground-metre projection;
- `aisle_polygon_m`, `stall_polygon_m`: simple polygons in ground metres;
- `stall_inward_vector`: nonzero 2D vector;
- `aisle_center_m`: LOS target in ground metres;
- `confidence`: detector threshold in `(0,1]`;
- `frame_stride`: positive integer;
- `adjacent_distance_m`: maximum center distance for adjacent occluders.

`camera/lot_config.example.json` is schema documentation only. Its identity homography is not valid site calibration.

## Resource behavior

- Frames are processed sequentially and not retained.
- Optional JSONL observations are written as a stream, not buffered.
- Each track retains five calibrated positions.
- Tracks absent for five minutes are expired.
- The model is loaded once per process.

These properties bound application memory, but the required 24-hour stability gate still requires a real 24-hour source run on the Oracle instance.

## Current hard dependencies

Python 3.11; versions are pinned in `requirements-empirical.txt`. The pipeline uses YOLOv8n, Supervision ByteTrack, OpenCV, and Matplotlib. Model weights are downloaded by Ultralytics on first use and cached locally at no API cost.
