#!/usr/bin/env python3
"""Protocol-validating local server for blinded DLP manual labels."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

EVENT_TYPES = frozenset({"parking", "unparking", "not_event"})
METHODS = frozenset({"forward", "reverse", "mixed", "unclear", "not_applicable"})
CENSORING = frozenset({"complete", "left", "right", "both", "not_applicable"})
EXCLUSIONS = frozenset({
    "none",
    "pull_through",
    "aborted",
    "loading_passenger_stop",
    "double_parking",
    "invalid_stall",
    "incomplete_trajectory",
    "other",
})
CONFIDENCE = frozenset({"high", "medium", "low"})
CONSISTENCY_WARNINGS = frozenset({
    "parking_end_not_sustained_parked",
    "unparking_end_not_established_aisle_travel",
})


def validate_label(payload: dict, allowed_items: set[str]) -> dict:
    """Validate and normalize one Protocol v1.0 manual label."""
    required = {
        "item_id",
        "event_type",
        "method",
        "start_index",
        "end_index",
        "censoring",
        "exclusion_reason",
        "confidence",
        "note",
    }
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"Missing fields: {', '.join(sorted(missing))}")
    if payload["item_id"] not in allowed_items:
        raise ValueError("Unknown validation item")
    if payload["event_type"] not in EVENT_TYPES:
        raise ValueError("Invalid event type")
    if payload["method"] not in METHODS:
        raise ValueError("Invalid method")
    if payload["censoring"] not in CENSORING:
        raise ValueError("Invalid censoring")
    if payload["exclusion_reason"] not in EXCLUSIONS:
        raise ValueError("Invalid exclusion reason")
    if payload["confidence"] not in CONFIDENCE:
        raise ValueError("Invalid confidence")
    if not isinstance(payload["note"], str) or len(payload["note"]) > 2000:
        raise ValueError("Note must be text no longer than 2000 characters")
    warnings_acknowledged = payload.get("warnings_acknowledged", [])
    if (
        not isinstance(warnings_acknowledged, list)
        or any(not isinstance(value, str) for value in warnings_acknowledged)
        or not set(warnings_acknowledged).issubset(CONSISTENCY_WARNINGS)
    ):
        raise ValueError("Invalid consistency warning acknowledgement")
    warnings_acknowledged = sorted(set(warnings_acknowledged))

    start = payload["start_index"]
    end = payload["end_index"]
    if start is not None and (not isinstance(start, int) or isinstance(start, bool)):
        raise ValueError("Start frame must be an integer")
    if end is not None and (not isinstance(end, int) or isinstance(end, bool)):
        raise ValueError("End frame must be an integer")
    if start is not None and end is not None and end < start:
        raise ValueError("End frame must be at or after start frame")

    if payload["event_type"] == "not_event":
        if payload["method"] != "not_applicable":
            raise ValueError("Not-event labels require not-applicable method")
        if payload["censoring"] != "not_applicable":
            raise ValueError("Not-event labels require not-applicable censoring")
        if start is not None or end is not None:
            raise ValueError("Not-event labels cannot have maneuver boundaries")
    else:
        if payload["method"] == "not_applicable":
            raise ValueError("Valid events require a maneuver method")
        censoring = payload["censoring"]
        if censoring == "not_applicable":
            raise ValueError("Valid events require censoring status")
        if censoring == "complete" and (start is None or end is None):
            raise ValueError("Complete events require both start and end frames")
        if censoring == "left" and (start is not None or end is None):
            raise ValueError("Left-censored events require only an end frame")
        if censoring == "right" and (start is None or end is not None):
            raise ValueError("Right-censored events require only a start frame")
        if censoring == "both" and (start is not None or end is not None):
            raise ValueError("Both-censored events cannot have observed boundaries")

    return {
        "item_id": payload["item_id"],
        "event_type": payload["event_type"],
        "method": payload["method"],
        "start_index": start,
        "end_index": end,
        "censoring": payload["censoring"],
        "exclusion_reason": payload["exclusion_reason"],
        "confidence": payload["confidence"],
        "note": payload["note"].strip(),
        "warnings_acknowledged": warnings_acknowledged,
    }


def save_label(labels_dir: Path, reviewer: str, label: dict) -> dict:
    """Atomically save current state and append every revision to an audit log."""
    labels_dir.mkdir(parents=True, exist_ok=True)
    state_path = labels_dir / f"{reviewer}.json"
    audit_path = labels_dir / f"{reviewer}.audit.jsonl"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    previous = state.get(label["item_id"])
    saved = dict(label)
    saved["reviewer"] = reviewer
    saved["revision"] = 1 if previous is None else int(previous["revision"]) + 1
    saved["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    state[label["item_id"]] = saved

    fd, temporary_name = tempfile.mkstemp(prefix=f".{reviewer}.", suffix=".json", dir=labels_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, state_path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(saved, sort_keys=True) + "\n")
    return saved


def create_server(
    host: str,
    port: int,
    app_dir: Path,
    package_dir: Path,
    labels_dir: Path,
) -> ThreadingHTTPServer:
    """Create a constrained server with no route to the internal manifest."""
    static_types = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/index.html": ("index.html", "text/html; charset=utf-8"),
        "/app.js": ("app.js", "text/javascript; charset=utf-8"),
        "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    }

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, value: object, status: int = 200, *, download: str | None = None) -> None:
            body = json.dumps(value, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json({"error": message}, status)

        def _reviewer(self, query: dict[str, list[str]]) -> str:
            reviewer = query.get("reviewer", [""])[0]
            if reviewer not in {"hugh", "hermes"}:
                raise ValueError("Reviewer must be hugh or hermes")
            if not (package_dir / reviewer / "index.json").exists():
                raise FileNotFoundError("Reviewer package is not ready")
            return reviewer

        def _index(self, reviewer: str) -> dict:
            return json.loads(
                (package_dir / reviewer / "index.json").read_text(encoding="utf-8")
            )

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                if parsed.path in static_types:
                    filename, content_type = static_types[parsed.path]
                    body = (app_dir / filename).read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return

                query = parse_qs(parsed.query)
                reviewer = self._reviewer(query)
                index = self._index(reviewer)
                if parsed.path == "/api/index":
                    self._send_json(index)
                    return
                if parsed.path == "/api/item":
                    item_id = query.get("id", [""])[0]
                    allowed = {item["item_id"] for item in index["items"]}
                    if item_id not in allowed:
                        raise ValueError("Unknown validation item")
                    item = json.loads(
                        (package_dir / reviewer / "items" / f"{item_id}.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self._send_json(item)
                    return
                if parsed.path in {"/api/state", "/api/export"}:
                    state_path = labels_dir / f"{reviewer}.json"
                    state = (
                        json.loads(state_path.read_text(encoding="utf-8"))
                        if state_path.exists()
                        else {}
                    )
                    self._send_json(
                        state,
                        download=f"dlp-{reviewer}-labels.json"
                        if parsed.path == "/api/export"
                        else None,
                    )
                    return
                self._send_error_json(404, "Not found")
            except ValueError as error:
                self._send_error_json(400, str(error))
            except FileNotFoundError as error:
                self._send_error_json(404, str(error))

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/api/label":
                self._send_error_json(404, "Not found")
                return
            try:
                reviewer = self._reviewer(parse_qs(parsed.query))
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 65536:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Label payload must be an object")
                index = self._index(reviewer)
                allowed = {item["item_id"] for item in index["items"]}
                saved = save_label(labels_dir, reviewer, validate_label(payload, allowed))
                self._send_json(saved, 201)
            except (ValueError, json.JSONDecodeError) as error:
                self._send_error_json(400, str(error))
            except FileNotFoundError as error:
                self._send_error_json(404, str(error))

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}", flush=True)

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--app", type=Path, default=root / "labeler")
    parser.add_argument("--packages", type=Path, default=root / "results" / "validation")
    parser.add_argument("--labels", type=Path, default=root / "results" / "validation" / "labels")
    args = parser.parse_args()
    server = create_server(args.host, args.port, args.app, args.packages, args.labels)
    print(f"DLP labeler listening on http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
