# Face Forward

**Face facts. Face forward.**

Face Forward is a small public-interest campaign about a small daily choice: parking nose-in. The project combines observed parking data, restrained argument, and visual design to test how effectively artificial intelligence can help a person make a well-researched case in an appealing format.

Website: [parkfaceforward.org](https://parkfaceforward.org)

## What is published

The First Edition website has five routes:

- `/` — campaign home
- `/research/` — observed evidence and study overview
- `/manifesto/` — the argument and its limits
- `/store/` — merchandise placeholder
- `/about/` — project purpose and AI disclosure

The earlier simulator, working paper, and editorial scaffold are intentionally absent from the public site.

## Observed evidence

The exploratory analysis uses the public-domain [UC Berkeley Dragon Lake Parking Dataset](https://doi.org/10.5061/dryad.tht76hf5b). Across 548 complete observed parking and unparking events in 30 recordings, the lifecycle comparison was:

- nose-in: 36.2 seconds
- reverse-entry / nose-out: 46.2 seconds
- observed difference: 10.0 seconds in favor of nose-in
- scene-cluster 95% interval: 3.72–16.11 seconds

These are exploratory observational results, not a causal safety study. Parking dwell time is excluded, and lifecycle totals are unpaired sums of entry and exit means. The website presents the trade-off, robustness checks, and limitations alongside the headline result.

## Repository structure

- `site/` — Astro static website
- `dlp/` — dataset download, event detection, validation, and aggregate analysis code
- `dlp/results/v2-exploratory-analysis.json` — frozen aggregate input used for the public charts
- `site/public/assets/research/` — generated chart assets
- `site/public/docs/` — downloadable evidence chart pack and summary CSV
- `docs/` — earlier simulation and methodology artifacts retained as project history

The 7.5 GB source dataset, trajectories, reviewer work products, model weights, virtual environments, and scratch scripts are intentionally excluded from Git.

## Build the website

```bash
cd site
npm install
npm run build
```

The static output is written to `site/dist/`.

## Regenerate the public evidence artifacts

```bash
python3 -m venv .tools/dlp-venv
.tools/dlp-venv/bin/pip install -r dlp/requirements.txt
.tools/dlp-venv/bin/python dlp/generate_v2_evidence_artifacts.py
```

This regenerates the SVG/PNG charts, PDF chart pack, and summary CSV from the frozen aggregate result.

## Authorship

The concept, design decisions, and final edits were made by a real person. AI systems assisted with research, analysis, implementation, drafting, and design exploration.
