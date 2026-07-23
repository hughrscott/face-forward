import pytest

from dlp.validation_analysis import compare_validation_labels


def _label(
    item_id,
    event_type,
    method="not_applicable",
    start=None,
    end=None,
    censoring=None,
):
    return {
        "item_id": item_id,
        "event_type": event_type,
        "method": method,
        "start_index": start,
        "end_index": end,
        "censoring": censoring or ("complete" if event_type != "not_event" else "not_applicable"),
        "exclusion_reason": "none",
        "confidence": "high",
        "note": "",
    }


def test_compare_validation_labels_computes_frozen_gate_metrics():
    manifest = [
        {
            "item_id": "A",
            "source_kind": "detector_positive",
            "agent_type": "Car",
            "detector_event_type": "parking",
            "detector_method": "forward",
            "detector_start_index": 10,
            "detector_end_index": 20,
        },
        {
            "item_id": "B",
            "source_kind": "detector_positive",
            "agent_type": "Car",
            "detector_event_type": "unparking",
            "detector_method": "reverse",
            "detector_start_index": 10,
            "detector_end_index": 20,
        },
        {"item_id": "C", "source_kind": "boundary", "agent_type": "Car", "detector_event_type": "parking", "detector_method": "unclear", "detector_start_index": 3, "detector_end_index": 9},
        {"item_id": "D", "source_kind": "random_track", "agent_type": "Car", "detector_event_type": None, "detector_method": None, "detector_start_index": None, "detector_end_index": None},
        {"item_id": "E", "source_kind": "detector_positive", "agent_type": "Car", "detector_event_type": "parking", "detector_method": "forward", "detector_start_index": 10, "detector_end_index": 20},
    ]
    hugh = {
        "A": _label("A", "parking", "forward", 12, 18),
        "B": _label("B", "not_event"),
        "C": _label("C", "parking", "reverse", 4, 8),
        "D": _label("D", "not_event"),
        "E": _label("E", "parking", "forward", 12, None, censoring="right"),
    }
    hermes = {
        "A": _label("A", "parking", "forward", 11, 19),
        "B": _label("B", "not_event"),
        "C": _label("C", "not_event"),
        "D": _label("D", "not_event"),
        "E": _label("E", "parking", "forward", 12, None, censoring="right"),
    }
    fps = {item_id: 2.0 for item_id in hugh}

    result = compare_validation_labels(hugh, hermes, manifest, fps)

    assert result["event_counts"] == {"tp": 3, "fp": 1, "fn": 0, "tn": 1}
    assert result["event_precision"] == pytest.approx(3 / 4)
    assert result["event_recall"] == pytest.approx(1.0)
    assert result["event_f1"] == pytest.approx(6 / 7)
    assert result["strict_analyzable_event_counts"] == {"tp": 1, "fp": 2, "fn": 1, "tn": 1}
    assert result["strict_analyzable_event_precision"] == pytest.approx(1 / 3)
    assert result["strict_analyzable_event_recall"] == pytest.approx(0.5)
    assert result["candidate_detection_counts"] == {"tp": 3, "fp": 1, "fn": 0, "tn": 1}
    assert result["candidate_detection_precision"] == pytest.approx(3 / 4)
    assert result["candidate_detection_recall"] == pytest.approx(1.0)
    assert result["method_accuracy"] == pytest.approx(1.0)
    assert result["timing_boundary_median_absolute_error_seconds"] == pytest.approx(1.0)
    assert result["timing_start_median_absolute_error_seconds"] == pytest.approx(1.0)
    assert result["timing_end_median_absolute_error_seconds"] == pytest.approx(0.75)
    assert result["hugh_hermes_event_disagreements"] == ["C"]
    assert result["gates"]["event_precision"] is False
    assert result["gates"]["event_recall"] is True
    assert result["gates"]["method_accuracy"] is True
    assert result["gates"]["timing_boundary_error"] is False


def test_compare_validation_labels_reports_perfect_categorical_agreement():
    manifest = [
        {"item_id": "A", "source_kind": "random_track", "detector_event_type": None, "detector_method": None, "detector_start_index": None, "detector_end_index": None},
        {"item_id": "B", "source_kind": "random_track", "detector_event_type": None, "detector_method": None, "detector_start_index": None, "detector_end_index": None},
    ]
    labels = {"A": _label("A", "not_event"), "B": _label("B", "not_event")}

    result = compare_validation_labels(labels, labels, manifest, {"A": 25.0, "B": 25.0})

    assert result["hugh_hermes_event_kappa"] == pytest.approx(1.0)
    assert result["gates"]["categorical_agreement"] is True
