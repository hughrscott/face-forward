# Detector v2 Exploratory Timing Findings

Date: 2026-07-24
Status: **exploratory evidence; detector did not pass formal promotion gates**

## Decision and scope

The detector-v2 promotion target was not met: broad weighted precision was
92.2% versus a 95% target, and weighted median boundary error was 0.56 s
versus a target below 0.50 s. Recall (100%) and method accuracy (95.5%)
passed. Hugh accepted reporting the observed performance and the ambiguity of
real maneuvers rather than beginning another detector-tuning cycle.

This is therefore one bounded exploratory analysis. It does not relabel,
retune, or promote detector v2.

Machine-readable result:
`dlp/results/v2-exploratory-analysis.json`

SHA-256:
`625f7f300afcaea4e2f494902d70f4d15426a89e33896e2bf5911145aba4c154`

## Population

The primary filter was frozen before this analysis:

- complete, uncensored events;
- cars and medium vehicles;
- forward or reverse method classification;
- all 30 DLP scenes;
- no timing-outlier removal.

It retained 548 of 635 v2 candidates.

The 92.2% precision result applies to the broad candidate generator, including
censored boundary candidates. The primary timing filter excludes those
candidates. Within the complete detector-positive strata used by the timing
analysis, the held-out weighted precision estimate was 98.0% (31 of 32
reviewed items were events). Both values are reported; the narrower estimate
does not retroactively convert the failed broad promotion gate into a pass.

## Observed component durations

| Component | Method | n | Mean | Median | 10% trimmed mean |
|---|---|---:|---:|---:|---:|
| Parking entry | Forward / nose-in | 102 | 15.61 s | 10.84 s | 12.20 s |
| Parking entry | Reverse / nose-out | 197 | 36.79 s | 29.68 s | 32.45 s |
| Unparking exit | Forward from nose-out | 184 | 9.41 s | 7.84 s | 8.16 s |
| Unparking exit | Reverse from nose-in | 65 | 20.59 s | 16.88 s | 18.66 s |

Forward entry was 21.18 s faster on average than reverse entry. Forward exit
was 11.18 s faster than reverse exit. The observed exit result therefore
confirms the genuine operational advantage of backing into a conventional
stall; the lifecycle question is whether that advantage repays the larger
reverse-entry cost.

## Unpaired lifecycle

Following the frozen definition:

- nose-in lifecycle = forward entry + reverse exit;
- nose-out lifecycle = reverse entry + forward exit;
- parking dwell time is excluded.

| Orientation | Sum of component means |
|---|---:|
| Nose-in / Face Forward | 36.20 s |
| Nose-out / reverse entry | 46.20 s |
| Nose-in minus nose-out | **-10.00 s** |

The 10% trimmed-mean contrast was **-9.75 s**, so the sign and magnitude were
not driven by a few extreme durations.

A deterministic 10,000-draw scene-cluster bootstrap gave:

- 95% interval: **-16.11 to -3.72 s**;
- nose-in faster in 99.94% of draws;
- nose-in more than the frozen 2-second practical margin faster in 99.53% of
  draws.

The interval is descriptive of these 30 recordings, not evidence of causal or
multi-site generalizability.

## Sensitivity checks

Negative values favor nose-in. Values below -2 s exceed the frozen practical
margin.

| Scenario | Lifecycle contrast |
|---|---:|
| Raw primary analysis | -10.00 s |
| 10% trimmed means | -9.75 s |
| Adverse strict-primary false-positive removal | -8.57 s |
| Raw plus observed start/end boundary medians, all adverse | -4.24 s |
| Adverse false-positive removal plus observed boundary medians | **-2.81 s** |
| Adverse false-positive removal plus 1.0 s at every boundary | **-0.57 s** |

The false-positive stress removes the fastest 12.5% of forward-parking events,
matching the sole false positive observed in that held-out strict stratum, and
assumes no offsetting false positives in the other strict strata. The observed
boundary stress then moves every component mean in the direction least
favorable to nose-in using the held-out weighted medians (0.52 s at starts and
0.92 s at ends). Even under that combined adverse scenario, nose-in remains
2.81 s faster.

The final one-second-per-boundary scenario is intentionally harsher than the
observed overall median and moves every error in the same unfavorable
direction. Under that assumption, the difference falls inside the practical-
equivalence margin. The result is therefore robust to the evidence-based
stress test but not to every conceivable systematic-error assignment.

Method classification passed at 95.5%. The held-out set contained one method
disagreement: a 27.16-second parking event labeled forward by Hugh and reverse
by the detector. Method error remains an additional limitation rather than a
basis for another tuning cycle.

## Bounded conclusion

The DLP recordings provide **exploratory evidence** that nose-in parking had a
faster unpaired maneuver lifecycle in this lot. The central estimate is about
10 seconds, and it remains just beyond the preregistered 2-second practical
margin under the combined evidence-based false-positive and boundary-error
stress test.

This is not a claim that detector v2 achieved its formal validation target, that
nose-in is always faster, or that the observed association is causal. Public
copy should say that the study targeted 95% broad precision and observed 92.2%,
that timing boundaries had a 0.56-second median error, and that real maneuvers
were not always clear-cut.

## Limitations that must travel with the finding

- one parking lot and self-selected parking method;
- unpaired lifecycle sums rather than the same driver's paired entry and exit;
- broad candidate precision missed the predeclared target;
- timing error narrowly missed the predeclared target;
- method classification is imperfect;
- no causal adjustment for driver, vehicle, traffic, stall, or aisle conditions;
- no direct measurement of safety, visibility, courtesy, pedestrian conflict,
  or parking quality;
- parking over or straddling lines still counts as parking—the study recognizes
  event occurrence, not parking quality.

No additional detector tuning is recommended for this First Edition result.
