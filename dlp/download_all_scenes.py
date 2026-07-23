#!/usr/bin/env python3
"""Download and verify every JSON scene in the public DLP Zenodo deposit."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

RECORD_URL = "https://zenodo.org/api/records/10084683"
SCENE_FILE = re.compile(
    r"^DJI_\d{4}_(agents|frames|instances|obstacles|scene)\.json$"
)


@dataclass(frozen=True)
class DatasetFile:
    name: str
    size: int
    md5: str
    url: str


def parse_manifest(metadata: dict[str, Any]) -> list[DatasetFile]:
    """Return the sorted DLP scene files from a Zenodo record response."""
    result: list[DatasetFile] = []
    for raw in metadata.get("files", []):
        name = raw.get("key", "")
        if not SCENE_FILE.fullmatch(name):
            continue
        checksum = raw.get("checksum", "")
        if not checksum.startswith("md5:"):
            raise ValueError(f"Unsupported checksum for {name}: {checksum}")
        result.append(
            DatasetFile(
                name=name,
                size=int(raw["size"]),
                md5=checksum.removeprefix("md5:"),
                url=raw["links"]["self"],
            )
        )
    return sorted(result, key=lambda item: item.name)


def file_md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - repository checksum, not security
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, expected: DatasetFile) -> bool:
    return (
        path.exists()
        and path.stat().st_size == expected.size
        and file_md5(path) == expected.md5
    )


def download_file(
    expected: DatasetFile,
    destination: Path,
    *,
    retrieve: Callable[[str, Path], object] = urllib.request.urlretrieve,
) -> Path:
    """Download one file atomically, or reuse it when already verified."""
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / expected.name
    partial = destination / f"{expected.name}.partial"

    if verify_file(target, expected):
        return target
    if target.exists():
        target.unlink()

    if verify_file(partial, expected):
        partial.replace(target)
        return target
    if partial.exists():
        partial.unlink()

    try:
        retrieve(expected.url, partial)
        if not verify_file(partial, expected):
            raise RuntimeError(f"Download verification failed for {expected.name}")
        partial.replace(target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return target


def fetch_metadata(record_url: str = RECORD_URL) -> dict[str, Any]:
    request = urllib.request.Request(record_url, headers={"User-Agent": "FaceForward-DLP/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(__file__).resolve().parent / "data",
    )
    parser.add_argument("--record-url", default=RECORD_URL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    files = parse_manifest(fetch_metadata(args.record_url))
    scenes = {entry.name.split("_")[1] for entry in files}
    if len(files) != 150 or len(scenes) != 30:
        raise RuntimeError(
            f"Unexpected DLP manifest: {len(files)} scene files across {len(scenes)} scenes"
        )

    total_bytes = sum(entry.size for entry in files)
    print(
        f"DLP manifest: {len(files)} files, {len(scenes)} scenes, "
        f"{total_bytes / (1024**3):.3f} GiB"
    )
    if args.dry_run:
        return

    for index, entry in enumerate(files, start=1):
        target = args.destination / entry.name
        already_verified = verify_file(target, entry)
        print(
            f"[{index:03d}/{len(files)}] "
            f"{'VERIFIED' if already_verified else 'DOWNLOAD'} {entry.name}",
            flush=True,
        )
        download_file(entry, args.destination)

    print(f"COMPLETE: all {len(files)} files verified in {args.destination}")


if __name__ == "__main__":
    main()
