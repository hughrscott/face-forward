import json
from pathlib import Path
import re
import threading
from urllib.request import Request, urlopen

import pytest

from dlp.label_server import create_server, save_label, validate_label


def _valid_label():
    return {
        "item_id": "VAL-001",
        "event_type": "parking",
        "method": "forward",
        "start_index": 10,
        "end_index": 25,
        "censoring": "complete",
        "exclusion_reason": "none",
        "confidence": "high",
        "note": "",
    }


def test_validate_label_enforces_complete_event_boundaries():
    label = validate_label(_valid_label(), {"VAL-001"})
    assert label["start_index"] == 10
    assert label["end_index"] == 25

    missing_end = dict(_valid_label(), end_index=None)
    with pytest.raises(ValueError, match="both start and end"):
        validate_label(missing_end, {"VAL-001"})

    reversed_bounds = dict(_valid_label(), start_index=30, end_index=20)
    with pytest.raises(ValueError, match="after start"):
        validate_label(reversed_bounds, {"VAL-001"})


def test_validate_label_preserves_explicit_consistency_warning_acknowledgements():
    warned = dict(
        _valid_label(),
        warnings_acknowledged=["parking_end_not_sustained_parked"],
    )

    label = validate_label(warned, {"VAL-001"})

    assert label["warnings_acknowledged"] == ["parking_end_not_sustained_parked"]
    with pytest.raises(ValueError, match="warning acknowledgement"):
        validate_label(
            dict(_valid_label(), warnings_acknowledged=["invented_warning"]),
            {"VAL-001"},
        )


def test_validate_label_accepts_not_event_without_maneuver_fields():
    label = dict(
        _valid_label(),
        event_type="not_event",
        method="not_applicable",
        start_index=None,
        end_index=None,
        censoring="not_applicable",
        exclusion_reason="loading_passenger_stop",
    )
    assert validate_label(label, {"VAL-001"})["event_type"] == "not_event"


def test_save_label_is_atomic_and_preserves_revision_history(tmp_path: Path):
    first = save_label(tmp_path, "hugh", validate_label(_valid_label(), {"VAL-001"}))
    second_input = dict(_valid_label(), confidence="medium", note="rechecked")
    second = save_label(tmp_path, "hugh", validate_label(second_input, {"VAL-001"}))

    state = json.loads((tmp_path / "hugh.json").read_text())
    audit = [json.loads(line) for line in (tmp_path / "hugh.audit.jsonl").read_text().splitlines()]
    assert first["revision"] == 1
    assert second["revision"] == 2
    assert state["VAL-001"]["confidence"] == "medium"
    assert [entry["revision"] for entry in audit] == [1, 2]


def test_http_api_serves_blind_items_and_persists_labels(tmp_path: Path):
    app_dir = tmp_path / "app"
    package_dir = tmp_path / "package"
    labels_dir = tmp_path / "labels"
    (package_dir / "hugh" / "items").mkdir(parents=True)
    app_dir.mkdir()
    (app_dir / "index.html").write_text("<!doctype html><title>Labeler</title>")
    (package_dir / "hugh" / "index.json").write_text(json.dumps({
        "reviewer": "hugh",
        "item_count": 1,
        "items": [{"item_id": "VAL-001", "payload_url": "items/VAL-001.json"}],
    }))
    (package_dir / "hugh" / "items" / "VAL-001.json").write_text(json.dumps({
        "item_id": "VAL-001", "trajectory": []
    }))

    server = create_server("127.0.0.1", 0, app_dir, package_dir, labels_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        index = json.loads(urlopen(f"{base}/api/index?reviewer=hugh").read())
        item = json.loads(urlopen(f"{base}/api/item?reviewer=hugh&id=VAL-001").read())
        request = Request(
            f"{base}/api/label?reviewer=hugh",
            data=json.dumps(_valid_label()).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        saved = json.loads(urlopen(request).read())
        state = json.loads(urlopen(f"{base}/api/state?reviewer=hugh").read())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert index["items"][0]["item_id"] == "VAL-001"
    assert item["item_id"] == "VAL-001"
    assert saved["revision"] == 1
    assert state["VAL-001"]["event_type"] == "parking"


def test_labeler_javascript_references_only_existing_dom_ids():
    app_dir = Path(__file__).parent / "dlp" / "labeler"
    javascript = (app_dir / "app.js").read_text()
    html = (app_dir / "index.html").read_text()
    referenced_ids = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', javascript))
    declared_ids = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', html))

    assert referenced_ids <= declared_ids, f"Missing DOM IDs: {sorted(referenced_ids - declared_ids)}"
    assert 'id="reviewAnchor"' in html
    assert "first sustained movement out of the parked state" in javascript
    assert "first frame beginning the sustained parked state" in javascript
    assert "warnings_acknowledged" in javascript
