from dlp.evaluate_detector_v2_development import (
    match_v2_event,
    method_summary,
    remap_manifest,
)


def _event(event_id, start, end, *, stall="S1", event_type="parking", method="forward", complete=True):
    return {
        "event_id": event_id,
        "scene_id": "SCENE",
        "agent_token": "AGENT",
        "stall_id": stall,
        "event_type": event_type,
        "method": method,
        "complete": complete,
        "censoring": "none" if complete else "right",
        "legacy_start_index": start,
        "legacy_end_index": end,
        "start_index": start + 1,
        "end_index": end + 1,
    }


def _manifest(item_id="A", event_id="old", start=10, end=20):
    return {
        "item_id": item_id,
        "event_id": event_id,
        "scene_id": "SCENE",
        "agent_token": "AGENT",
        "agent_type": "Car",
        "detector_stall_id": "S1",
        "detector_event_type": "parking",
        "detector_start_index": start,
        "detector_end_index": end,
    }


def test_match_v2_event_prefers_exact_event_id():
    exact = _event("old", 100, 200, stall="S2", event_type="unparking")
    geometrically_close = _event("new", 10, 20)

    assert match_v2_event(_manifest(), [geometrically_close, exact]) is exact


def test_match_v2_event_uses_stall_type_and_legacy_bounds_when_id_changed():
    wrong_stall = _event("wrong-stall", 10, 20, stall="S2")
    wrong_type = _event("wrong-type", 10, 20, event_type="unparking")
    intended = _event("intended", 12, 23)

    assert match_v2_event(_manifest(event_id="missing"), [wrong_stall, wrong_type, intended]) is intended


def test_remap_manifest_uses_v2_semantic_prediction_and_marks_missing_candidate():
    event = _event("new", 10, 20)
    rows = remap_manifest(
        [_manifest("A"), {**_manifest("B"), "agent_token": "OTHER"}],
        {"A", "B"},
        [event],
    )

    assert rows[0]["source_kind"] == "detector_positive"
    assert rows[0]["detector_start_index"] == 11
    assert rows[0]["detector_end_index"] == 21
    assert rows[0]["v2_event_id"] == "new"
    assert rows[1]["source_kind"] == "random_track"
    assert rows[1]["detector_event_type"] is None


def test_method_summary_applies_only_explicit_component_exclusions():
    labels = {
        "A": {"event_type": "parking", "method": "forward"},
        "B": {"event_type": "parking", "method": "reverse"},
        "C": {"event_type": "not_event", "method": "not_applicable"},
    }
    predictions = [
        {"item_id": "A", "source_kind": "detector_positive", "detector_event_type": "parking", "detector_method": "forward"},
        {"item_id": "B", "source_kind": "detector_positive", "detector_event_type": "parking", "detector_method": "forward"},
        {"item_id": "C", "source_kind": "random_track", "detector_event_type": None, "detector_method": None},
    ]

    summary = method_summary(labels, predictions, {"B": "predeclared label conflict"})

    assert summary == {
        "accuracy": 1.0,
        "matches": 1,
        "n": 1,
        "disagreements": [],
        "excluded": [{"item_id": "B", "reason": "predeclared label conflict"}],
    }
