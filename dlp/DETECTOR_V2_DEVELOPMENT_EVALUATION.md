# Detector v2 Development Evaluation

Date: 2026-07-24
Status: **development evidence evaluated; not held-out validation**

## Purpose

This report evaluates frozen detector v2 against the adjudicated 50-item v1
development evidence. It does not promote the detector. The v1 sample is no
longer held out, deliberately oversampled difficult boundary/rejected cases,
and used an annotation instrument with known target and endpoint defects.

The mechanical evaluator is `dlp/evaluate_detector_v2_development.py`. It maps
each sampled v1 trajectory to the frozen v2 ledger using exact event ID first,
then scene/agent, stall, event type, and nearest legacy boundaries. It never
reuses v1 detector predictions as v2 predictions.

## Sealed inputs

- v1 manifest: `0ec85739b7af7781f2d2e0580c6ed4636d5fed39659321f7945c175f72de4f96`
- adjudicated Hugh labels: `6706f568e3ba71b4e14d8b8ae0cc7d5a431b3290444148890f9cccad483d9c43`
- independent Hermes labels: `9d28ffcce9cd3f401f0758295a98bbe5671129c098155bfd8e385c415c44a113`
- detector-v2 candidate ledger: `0a6bbdb1872ecf3d67be1aa18fc30d5950e0e6f3da1a94f3e37c5b2db885dea7`
- corrected boundary labels: `bb00a15867f017a78a50a2ab71e0fbc2b82b80d55b1c531ad5e4cadc060c7b7b`

`VAL-123` is omitted from clean target metrics because the sampled target was a
left-censored parking candidate while the v1 one-event form caused the adjacent
unparking episode to be labeled. It remains in the raw 50-item audit.

## Development metrics

### Candidate existence

On the 49 cleanly targeted items:

| Metric | Result |
|---|---:|
| Broad candidate TP / FP / FN / TN | 35 / 6 / 1 / 7 |
| Broad candidate precision | 85.37% |
| Broad candidate recall | 97.22% |
| Broad candidate F1 | 90.91% |

The precision is deliberately unweighted on a sample that oversampled rejected
and boundary candidates. It is diagnostic, not a population precision estimate.
The fresh v2 package must retain sampling weights for the promotion estimate.

The seven event-existence disagreements are:

- `VAL-014`: v2 complete reverse parking; Hugh saved not-event;
- `VAL-025`: v2 left-censored unclear parking candidate; Hugh saved not-event;
- `VAL-032`: v2 complete unclear parking candidate; Hugh saved not-event;
- `VAL-034`: v2 right-censored unclear unparking candidate; Hugh saved not-event;
- `VAL-036`: v2 right-censored reverse unparking candidate; Hugh saved not-event;
- `VAL-053`: v2 complete reverse unparking; Hugh saved not-event, but the
  predeclared kinematic audit supports the detector over the saved label;
- `VAL-132`: no v2 candidate; Hugh saved complete forward parking, but the saved
  endpoint is the final observed frame at 2.64 m/s and is not a valid complete
  parked endpoint.

`VAL-132` is the sole broad no-candidate miss. Its event existence is useful
failure evidence even though its saved completeness and endpoint are invalid.

### Timing-set eligibility

Strict timing eligibility is not event detection. It additionally requires a
primary vehicle, complete boundaries, and a classifiable method.

| Metric | Result |
|---|---:|
| Strict-eligibility TP / FP / FN / TN | 28 / 6 / 1 / 14 |
| Strict-eligibility precision | 82.35% |
| Strict-eligibility recall | 96.55% |

Do not reinterpret strict-positive status as an accepted-event classifier:
`VAL-028` and `VAL-142` are detected motorcycle events, while `VAL-109` is a
detected censored event. They are absent from the primary timing set by design,
not missed events.

### Method

The predeclared audit found that `VAL-081`'s saved reverse label contradicts the
trajectory's forward motion evidence. Excluding only that method component:

- 30/31 matched classifiable event methods agree;
- method accuracy: **96.77%**;
- remaining disagreement: `VAL-142` (v2 reverse, Hugh forward; motorcycle).

V2 also resolves the confirmed `VAL-095` weakness: nearest-moving evidence now
classifies it as reverse rather than unclear.

### Corrected semantic boundaries

Timing uses the repaired 33-item calibration package, not the original v1 end
labels. Fifty-five eligible boundary components remain after component-scoped,
predeclared exclusions.

| Boundary | Legacy median | V2 semantic median |
|---|---:|---:|
| Parking start | 2.54 s | **0.46 s** |
| Parking end | 1.36 s | **0.38 s** |
| Unparking start | 0.52 s | **0.36 s** |
| Unparking end | 1.22 s | **0.70 s** |
| **Overall** | **1.20 s** | **0.44 s** |

The development timing target below 0.50 s passes. Three components remain over
2 s: `VAL-027` unparking start, `VAL-133` unparking end, and `VAL-140`
unparking end.

### Reviewer agreement

The adjudicated descriptive Hugh–Hermes event-type kappa is 0.7535. This is a
property of the old v1 review pass and instrument, not detector v2. It cannot be
improved or claimed as a v2 gate without a fresh independent review.

## Decision

Development evaluation is complete. The evidence supports freezing v2 rather
than tuning it further on this sample:

- the confirmed method defect is repaired;
- the semantic timing development gate passes;
- no inventory-scale invariants regress;
- residual event disagreements are explicitly preserved;
- unweighted precision and old reviewer agreement are not promoted as passing.

The next valid step is to freeze code, constants, hashes, and Protocol v2, then
generate a fresh agent-disjoint, weighted, blinded held-out package. Promotion
requires all predeclared gates on that untouched package.
