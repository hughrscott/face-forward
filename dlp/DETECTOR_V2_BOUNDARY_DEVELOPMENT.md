# Detector v2 Boundary Development Report

Date: 2026-07-24
Status: **development gate passed; held-out validation not yet run**

## Inputs

- Calibration package: `dlp/results/v2-boundary-calibration/`
- Latest-revision label snapshot: 33/33 items
- Label snapshot SHA-256: `bb00a15867f017a78a50a2ab71e0fbc2b82b80d55b1c531ad5e4cadc060c7b7b`
- Comparison command:
  `python -m dlp.evaluate_boundary_calibration --output dlp/results/v2-boundary-calibration/internal/boundary_evaluation.json`

The package is development evidence. It is not an untouched validation set.

## Component eligibility

The evaluator compares 55 eligible observed boundaries. Nine boundary components
remain in the audit but are excluded from threshold fitting:

- `VAL-123` unparking start/end: annotation-target mismatch;
- `VAL-018` unparking end: saved endpoint explicitly acknowledged as not
  established aisle travel;
- parking end for `VAL-012`, `VAL-044`, `VAL-096`, `VAL-099`, `VAL-106`, and
  `VAL-124`: saved endpoint explicitly acknowledged as not sustained parked.

Exclusions are component-scoped. Other valid boundaries from the same item stay
in the evaluation.

## Development result

| Boundary component | Eligible n | Legacy median absolute error | Semantic median absolute error | Semantic max | Semantic >2 s |
|---|---:|---:|---:|---:|---:|
| Parking start | 16 | 2.54 s | **0.46 s** | 1.64 s | 0 |
| Parking end | 10 | 1.36 s | **0.38 s** | 1.44 s | 0 |
| Unparking start | 15 | 0.52 s | **0.36 s** | 3.00 s | 1 |
| Unparking end | 14 | 1.22 s | **0.70 s** | 2.96 s | 2 |
| **Overall** | **55** | **1.20 s** | **0.44 s** | **3.00 s** | **3** |

The predeclared aggregate development timing gate is median absolute boundary
error below 0.50 s. Detector v2 passes on development evidence at 0.44 s.
This is not a validation claim.

## Residual failures

Three eligible components remain above 2 s:

- `VAL-027` unparking start: -3.00 s;
- `VAL-133` unparking end: +2.96 s;
- `VAL-140` unparking end: -2.12 s.

Unparking end remains the weakest component. No further tuning is justified on
this development package after the aggregate gate passes; these residuals must
be tested in the untouched agent-disjoint validation sample.

## Inventory-scale verification

Command:
`python -m dlp.pipeline --data dlp/data --output dlp/results/v2-semantic-development`

- 30/30 scenes completed;
- 635 unique candidates, with the same event IDs as the prior v2-development
  inventory;
- 339 parking and 296 unparking;
- 554 complete and 81 censored;
- methods: 315 forward, 298 reverse, 1 mixed, 21 unclear;
- semantic start changed on 465 events;
- semantic end changed on 560 events;
- zero duplicate IDs, reversed intervals, negative durations, or timestamp-order
  violations;
- candidate ledger SHA-256:
  `0a6bbdb1872ecf3d67be1aa18fc30d5950e0e6f3da1a94f3e37c5b2db885dea7`;
- scene inventory SHA-256:
  `729632508657cc7c32bd6083f528289437d79f8892c60c3414be0b90b6960413`.

## Verification

- `66 passed` in the full repository test suite;
- semantic and legacy indices are both emitted for every candidate;
- Python compilation passed for the pipeline and calibration evaluator.

## Promotion decision

Detector-v2 boundary development is complete. Freeze only after the code,
constants, tests, development report, inventory, and hashes are committed.
Then generate a fresh agent-disjoint held-out package and apply the full
Protocol v2 gates. A held-out failure becomes development evidence for v3; it
must not be tuned and re-tested on the same sample.
