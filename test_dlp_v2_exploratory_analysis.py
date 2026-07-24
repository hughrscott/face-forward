from dlp.run_v2_exploratory_analysis import (
    adverse_primary_false_positive_contrast,
    lifecycle_contrast,
    primary_events,
    trimmed_mean,
)


def _event(event_type, method, duration, *, complete=True, censoring="none", agent_type="Car"):
    return {
        "event_type": event_type,
        "method": method,
        "duration_seconds": duration,
        "complete": complete,
        "censoring": censoring,
        "agent_type": agent_type,
        "scene_id": "S1",
    }


def test_primary_events_applies_frozen_filter():
    rows = [
        _event("parking", "forward", 5),
        _event("parking", "unclear", 5),
        _event("parking", "forward", 5, complete=False),
        _event("parking", "forward", 5, agent_type="Motorcycle"),
    ]
    assert primary_events(rows) == [rows[0]]


def test_lifecycle_contrast_is_nose_in_minus_nose_out():
    groups = {
        ("parking", "forward"): [10, 12],
        ("parking", "reverse"): [20, 22],
        ("unparking", "forward"): [5, 7],
        ("unparking", "reverse"): [8, 10],
    }
    assert lifecycle_contrast(groups) == -7


def test_trimmed_mean_removes_both_tails():
    assert trimmed_mean([0, 1, 2, 3, 100], 0.2) == 2


def test_adverse_primary_false_positive_removes_fastest_forward_parking():
    groups = {
        ("parking", "forward"): [1, 5, 5, 5],
        ("parking", "reverse"): [10, 10],
        ("unparking", "forward"): [2, 2],
        ("unparking", "reverse"): [4, 4],
    }
    result = adverse_primary_false_positive_contrast(groups, 0.25)
    assert result["removed_forward_parking"] == 1
    assert result["contrast_seconds"] == -3
