from dlp.build_boundary_calibration_package import select_calibration_items
from dlp.evaluate_boundary_calibration import (
    EXCLUDED_BOUNDARIES,
    summarize_boundary_errors,
)


def _manifest_row(item_id: str, event_id: str) -> dict:
    return {
        "item_id": item_id,
        "event_id": event_id,
        "scene_id": "DJI_0001",
        "agent_token": f"agent-{item_id}",
        "source_kind": "detector_positive",
        "detector_start_index": 10,
        "detector_end_index": 20,
    }


def test_select_calibration_items_keeps_complete_events_in_original_review_order():
    manifest = [
        _manifest_row("VAL-002", "event-2"),
        _manifest_row("VAL-001", "event-1"),
        _manifest_row("VAL-003", "event-3"),
        _manifest_row("VAL-132", "event-132"),
    ]
    labels = {
        "VAL-001": {"event_type": "parking", "censoring": "complete"},
        "VAL-002": {"event_type": "unparking", "censoring": "complete"},
        "VAL-003": {"event_type": "parking", "censoring": "right"},
        "VAL-132": {"event_type": "parking", "censoring": "complete"},
    }
    candidate_events = {
        "event-1": {"crossing_index": 15},
        "event-2": {"crossing_index": 17},
        "event-3": {"crossing_index": 19},
        "event-132": {"crossing_index": 21},
    }
    original_order = {"VAL-001": 2, "VAL-002": 1, "VAL-003": 3, "VAL-132": 4}

    selected = select_calibration_items(
        manifest,
        labels,
        candidate_events,
        original_order,
    )

    assert [item["item_id"] for item in selected] == ["VAL-002", "VAL-001"]
    assert [item["review_order"] for item in selected] == [1, 2]
    assert [item["detector_crossing_index"] for item in selected] == [17, 15]
    assert all(item["reviewer"] == "hugh" for item in selected)


def test_boundary_error_summary_reports_distribution_and_outliers():
    summary = summarize_boundary_errors(
        {
            "parking_start": [("A", -0.4), ("B", 0.8)],
            "parking_end": [("C", 2.4)],
        }
    )

    assert summary["parking_start"]["median_abs_s"] == 0.6
    assert summary["parking_end"]["over_2s"] == 1
    assert summary["overall"] == {
        "n": 3,
        "median_abs_s": 0.8,
        "mean_abs_s": 1.2,
        "max_abs_s": 2.4,
        "over_2s": 1,
    }


def test_boundary_calibration_exclusions_are_explicit_and_component_scoped():
    assert EXCLUDED_BOUNDARIES == {
        ("VAL-012", "parking_end"): "acknowledged non-sustained parked endpoint",
        ("VAL-044", "parking_end"): "acknowledged non-sustained parked endpoint",
        ("VAL-096", "parking_end"): "acknowledged non-sustained parked endpoint",
        ("VAL-099", "parking_end"): "acknowledged non-sustained parked endpoint",
        ("VAL-106", "parking_end"): "acknowledged non-sustained parked endpoint",
        ("VAL-124", "parking_end"): "acknowledged non-sustained parked endpoint",
        ("VAL-123", "unparking_start"): "annotation-target mismatch",
        ("VAL-123", "unparking_end"): "annotation-target mismatch",
        ("VAL-018", "unparking_end"): "acknowledged non-aisle endpoint",
    }
