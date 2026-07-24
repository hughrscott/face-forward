# Detector v2 Freeze Record

Date: 2026-07-24
Status: **frozen for held-out validation; no held-out labels collected**

## Frozen revisions

- Detector implementation commit: `b03cb09`
- Development evaluation commit: `55a1267`
- Protocol/package implementation commit: `0cfcd6c`
- Protocol: `2.0-heldout`
- Sampling seed: `20260724`

## Package

Primary package: `dlp/results/v2-heldout/`

- Manifest items: 150
- Hugh blinded subset: 50
- Hermes blinded set: 150
- Detector positives: 100
- Boundary candidates: 30
- Random no-candidate tracks: 20
- Unique sampled agents: 150
- Development-agent overlap: 0
- Labels present at freeze: no

The complete machine-readable record, including first- and second-stage sampling
weights and hashes of the detector, protocol, selector, builder, server, and
browser instrument, is:

`dlp/results/v2-heldout/internal/freeze_record.json`

## Immutable hashes

- Candidate ledger: `0a6bbdb1872ecf3d67be1aa18fc30d5950e0e6f3da1a94f3e37c5b2db885dea7`
- Held-out manifest: `dd53c150be0bf8e32ce241459690c2a9b57043c3b934a673be5cc57e94febfd0`
- Detector pipeline: `7bd598e9bae6b339273f5303797bb264779343437ff55feb3b2a6b343ca6eac4`
- Detector spec: `749ba5ed071664bcd95e84cf2bd7e4abd1f98013ebdc36bac2d228baa4d1f0d2`
- Protocol builder: `1837ab0f18aa3491810779d80353bd3b8440736f4078f865a3d01ad4f8f2ab7c`
- Selection implementation: `4c343cb9f57bd60fdb0f5fcb25f15f3a3fd4c2cd628913ce4d0364daedc860ee`
- Freeze record: `9b9e6416fa700684db6ab4a4a8489e022dc6545812e26552287a302a02caf903`

## Verification

Two independent builds were generated from the same frozen inputs. All 205 files
were byte-identical. The complete relative-path/file-hash map had digest:

`7232f8d27d862da2d764181633775316c49bddfe6de649cebf380e875598ac65`

The audit also verified:

- all 150 scene/agent pairs are unique;
- no scene/agent pair appears in the v1 development manifest;
- all first- and second-stage weights are internally consistent;
- all browser-visible review anchors exist in their trajectories;
- browser-visible indexes and payloads contain no detector classification,
  detector boundary, sampling-stratum, or sampling-weight fields;
- the package contains 150 Hermes payloads and 50 Hugh payloads;
- no label state existed at freeze;
- the full test suite passed: 72 passed, 0 failed.

The redundant deterministic-rebuild directory is not part of the frozen package
and may be removed after this record is committed.

## Next gate

Run fresh blinded reviews without modifying detector or package inputs. Apply the
weighted Protocol v2 gates only after Hugh's 50 items and the corresponding
independent Hermes labels are complete. Development results must not be pooled
into the held-out estimates.
