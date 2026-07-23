# DLP exploratory analysis

This directory contains the first verified inspection of the UC Berkeley Dragon Lake Parking (DLP) trajectory dataset for Face Forward.

## Run

```bash
# Complete 30-scene dataset (150 JSON files, 7.466 GiB). Resumable at file level;
# every file is checked against Zenodo's published size and MD5.
python3 dlp/download_all_scenes.py

# Optional one-scene smoke test / historical prototype.
python3 dlp/download_scene_0001.py
python3 dlp/inspect_scene.py
python3 -m venv .tools/dlp-venv
.tools/dlp-venv/bin/pip install -r dlp/requirements.txt
.tools/dlp-venv/bin/python dlp/plot_candidates.py
```

The downloader uses the public Zenodo mirror of the Dryad deposit and verifies every file by size and MD5. Source data and derived local trajectory plots are excluded from git.

## What this proves

The deposited JSON directly provides scene, frame, agent, and instance tokens plus 25 Hz vehicle position, heading, speed, acceleration, dimensions, and linked trajectory order. In scene `DJI_0001`, the exploratory detector finds geometrically plausible parking and unparking candidates with simple endpoint-state and maneuver-envelope rules.

## What it does not prove

The current 8 m envelope and speed thresholds are provisional. The `mode` field in the deposited JSON is blank; Berkeley's toolkit derives it rather than supplying ground-truth maneuver labels. Parking method should ultimately be classified from signed motion during final stall crossing—not the majority direction over an entire maneuver episode. Event boundaries and classifications require hand-labeled validation before statistical inference.

Dataset: https://doi.org/10.5061/dryad.tht76hf5b
Zenodo mirror: https://zenodo.org/records/10084683
Toolkit: https://github.com/MPC-Berkeley/dlp-dataset
