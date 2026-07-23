# DLP Detector v2 Development Specification

Status: **frozen for implementation**
Date: 2026-07-23
Scope: detector and validation-instrument improvements only; aggregate forward/reverse timing outcomes remain out of bounds.

## Objective

Improve the DLP parking/unparking inventory without fitting to the aggregate timing result. Detector v2 must:

1. construct one coherent parked episode per agent/stall visit;
2. classify motion direction from robust evidence around the stall crossing;
3. separate candidate existence from eligibility for the timing analysis;
4. expose semantic maneuver boundaries separately from the legacy fixed 8 m envelope;
5. support a fresh, agent-disjoint human validation with an unambiguous annotation target.

The 50 adjudicated v1 labels are development evidence, not a final test set.

## Audit findings from detector v1

### Metrics are stage-specific

The v1 report initially collapsed distinct questions. V2 must report them separately:

- **candidate detection:** did the detector emit any candidate for a human event?;
- **candidate classification:** parking, unparking, or no event for the targeted candidate;
- **timing-set eligibility:** complete trajectory, primary vehicle, classifiable method, and observed boundaries;
- **method agreement:** forward/reverse at the stall crossing;
- **boundary agreement:** four components reported separately: parking start, parking end, unparking start, unparking end.

A human event is eligible for the primary timing analysis only when all of the following hold:

- vehicle type is `Car` or `Medium Vehicle`;
- event type is parking or unparking;
- method is forward or reverse;
- censoring is complete;
- start and end boundaries are both observed.

### Human-label quality limitations

The v1 interface displayed a parking-specific boundary rule for both event types. It defined a parking end (sustained parked state) but did not define an unparking end. Six saved complete parking labels ended while speed was at least 0.10 m/s; `VAL-132` ended at 2.64 m/s and at the final observed frame. These labels cannot be used as direct threshold-fitting targets.

The v1 payload could contain multiple adjacent candidates while the form forced one label. `VAL-123` targeted a left-censored parking candidate, but the same agent already had separate unparking candidates in the inventory. The human labeled the visible unparking episode. That is an annotation-target mismatch, not a detector failure.

Visual/kinematic review also shows that several apparent strict false positives are physically clear events:

- `VAL-053`: stationary inside a stall, then drives away;
- `VAL-081`: motion relative to heading supports forward departure, not the saved reverse label;
- `VAL-101` and `VAL-136`: trajectories support forward parking despite saved unclear method.

These cases remain in the audit trail but must not become per-item detector exceptions.

### Confirmed detector weaknesses

- `VAL-095`: method becomes unclear because the current classifier inspects only ±1 frame at the geometric crossing and all usable crossing frames are below the movement threshold. Wider nearest-moving evidence supports reverse.
- Same-agent, same-stall static fragments can generate overlapping parking/unparking candidates (`VAL-123`).
- Fixed 8 m envelope boundaries disagree most strongly with semantic parking starts and unparking ends. State-transition boundaries are substantially closer: v1 median absolute error was 0.48 s for unparking start, versus 2.18 s for parking start and 1.10 s for unparking end.
- V1 cannot emit a partial parking candidate when a trajectory enters a stall but ends before a sustained parked state. Such cases must never be promoted as complete events.

## Detector v2 design

### 1. Parked-episode construction

Construct parked episodes from same-stall static runs rather than treating every qualified run independently.

- Merge same-agent, same-stall static runs while the intervening trajectory remains inside that stall, regardless of brief speed spikes or in-stall repositioning.
- A new parked episode requires leaving the stall before a later static run.
- Emit at most one parking arrival and one unparking departure per parked episode.
- Preserve censored candidates, but never promote them into the primary timing set.
- Do not create a parking arrival from an in-stall pause when there is no stall-entry crossing and the agent was already parked in that stall.

### 2. Method classification

Classify forward/reverse at the stall boundary using nearest moving evidence, not only the three crossing-adjacent frames.

- Search symmetrically around the crossing for up to 0.5 s on each side.
- Use frames with speed at least 0.10 m/s.
- Require at least three usable motion signs.
- Classify forward/reverse only when at least 70% of usable signs agree; otherwise return mixed/unclear.
- The search window and thresholds are detector constants, not per-item exceptions.

### 3. Boundary fields

Retain the legacy 8 m envelope for reproducibility, but add explicit semantic fields:

- `legacy_start_index`, `legacy_end_index`: frozen v1 8 m boundaries;
- `start_index`, `end_index`: v2 semantic/state-transition boundaries;
- parking start: first sustained maneuvering change away from established aisle travel;
- parking end: first frame of the sustained parked episode;
- unparking start: first sustained movement from the parked episode;
- unparking end: first frame of sustained established aisle travel after stall exit.

State-transition rules remain deterministic. Maneuver-boundary change-point logic must use smoothed heading/path behavior and must be tested independently of the aggregate outcome.

### 4. Validation instrument v2

- Show event-specific instructions dynamically:
  - parking start/end;
  - unparking start/end.
- Include a neutral review anchor so the human labels the event nearest the sampled candidate, without revealing predicted type, method, or exact boundaries.
- For random tracks, choose an analogous seeded neutral anchor.
- Warn before saving a complete parking label whose endpoint is not followed by a sustained low-speed in-stall state.
- Warn before saving a complete unparking label when no established aisle-travel segment is observed after the selected end.
- Preserve warnings and overrides in the label record; do not silently rewrite human labels.
- Keep all v1 packages and labels immutable.

### 5. Validation sampling

The v2 held-out package must:

- exclude every agent used in the v1 150-item manifest;
- use detector-v2 outputs frozen before labeling;
- stratify across event type, method, eligibility boundary, and no-candidate tracks;
- never include two sampled items from the same agent;
- record sampling weights so precision estimates can be population-weighted;
- remain blind to aggregate forward/reverse timing outcomes.

## Development and promotion sequence

1. Add regression tests for parked-episode merging and nearest-moving method classification.
2. Implement detector v2 episode and method logic.
3. Add and test semantic boundary extraction.
4. Repair validation scoring to compare strict inclusion against human eligibility, not mere event existence.
5. Repair the labeler guidance and target anchoring.
6. Run detector v2 on all 30 scenes and evaluate only against the v1 development evidence, with inconsistent labels flagged rather than fitted.
7. Freeze detector-v2 code, constants, inventory hash, and validation protocol.
8. Generate a fresh agent-disjoint package for Hugh.
9. Promote only if the predeclared v2 gates pass on the untouched package.

If the held-out package fails, it becomes development data for v3; a new untouched sample is required for another promotion attempt.
