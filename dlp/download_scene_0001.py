#!/usr/bin/env python3
"""Download and verify DLP scene DJI_0001 from the public Zenodo mirror."""
from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

BASE = "https://zenodo.org/api/records/10084683/files"
FILES = {
    "DJI_0001_agents.json": (17_975, "7e184b0263418a648af182732640c361"),
    "DJI_0001_frames.json": (9_191_621, "26a67e4538827c55dcba024a8145a1c5"),
    "DJI_0001_instances.json": (63_826_170, "ba0a70070900439879d486bb1f05e7bd"),
    "DJI_0001_obstacles.json": (46_636, "4189596c8ef359ad53e3c6d78b53f9d5"),
    "DJI_0001_scene.json": (9_767, "55744422ce23bb3f7a3d951352121060"),
}


def md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - repository checksum, not security
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    destination = Path(__file__).resolve().parent / "data"
    destination.mkdir(parents=True, exist_ok=True)

    for name, (expected_size, expected_md5) in FILES.items():
        path = destination / name
        if path.exists() and path.stat().st_size == expected_size and md5(path) == expected_md5:
            print(f"VERIFIED {name}")
            continue

        url = f"{BASE}/{name}/content"
        partial = path.with_suffix(path.suffix + ".partial")
        print(f"DOWNLOAD {name}", flush=True)
        urllib.request.urlretrieve(url, partial)
        if partial.stat().st_size != expected_size:
            raise RuntimeError(f"Size mismatch for {name}: {partial.stat().st_size} != {expected_size}")
        actual_md5 = md5(partial)
        if actual_md5 != expected_md5:
            raise RuntimeError(f"Checksum mismatch for {name}: {actual_md5} != {expected_md5}")
        partial.replace(path)
        print(f"VERIFIED {name}")


if __name__ == "__main__":
    main()
