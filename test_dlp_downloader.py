import hashlib
from pathlib import Path

import pytest

from dlp.download_all_scenes import (
    DatasetFile,
    download_file,
    parse_manifest,
    verify_file,
)


def test_parse_manifest_selects_and_sorts_all_scene_json_files():
    metadata = {
        "files": [
            {
                "key": "DJI_0002_instances.json",
                "size": 3,
                "checksum": "md5:900150983cd24fb0d6963f7d28e17f72",
                "links": {"self": "https://example.test/2"},
            },
            {
                "key": "README.md",
                "size": 1,
                "checksum": "md5:0cc175b9c0f1b6a831c399e269772661",
                "links": {"self": "https://example.test/readme"},
            },
            {
                "key": "DJI_0001_agents.json",
                "size": 3,
                "checksum": "md5:900150983cd24fb0d6963f7d28e17f72",
                "links": {"self": "https://example.test/1"},
            },
        ]
    }

    files = parse_manifest(metadata)

    assert [file.name for file in files] == [
        "DJI_0001_agents.json",
        "DJI_0002_instances.json",
    ]
    assert files[0].md5 == "900150983cd24fb0d6963f7d28e17f72"


def test_verify_file_requires_matching_size_and_checksum(tmp_path: Path):
    path = tmp_path / "sample.json"
    path.write_bytes(b"abc")
    expected = DatasetFile(
        name=path.name,
        size=3,
        md5=hashlib.md5(b"abc").hexdigest(),  # noqa: S324 - repository checksum
        url="https://example.test/sample",
    )

    assert verify_file(path, expected) is True
    path.write_bytes(b"abd")
    assert verify_file(path, expected) is False


def test_download_file_is_resumable_and_uses_atomic_partial(tmp_path: Path):
    payload = b"abc"
    entry = DatasetFile(
        name="DJI_0001_agents.json",
        size=len(payload),
        md5=hashlib.md5(payload).hexdigest(),  # noqa: S324 - repository checksum
        url="https://example.test/sample",
    )
    calls = []

    def retrieve(url: str, path: Path) -> None:
        calls.append((url, Path(path).name))
        Path(path).write_bytes(payload)

    target = download_file(entry, tmp_path, retrieve=retrieve)
    assert target.read_bytes() == payload
    assert calls == [(entry.url, f"{entry.name}.partial")]
    assert not (tmp_path / f"{entry.name}.partial").exists()

    target = download_file(entry, tmp_path, retrieve=retrieve)
    assert target.read_bytes() == payload
    assert len(calls) == 1


def test_download_file_rejects_bad_download(tmp_path: Path):
    entry = DatasetFile(
        name="DJI_0001_agents.json",
        size=3,
        md5=hashlib.md5(b"abc").hexdigest(),  # noqa: S324 - repository checksum
        url="https://example.test/sample",
    )

    def retrieve(url: str, path: Path) -> None:
        Path(path).write_bytes(b"bad")

    with pytest.raises(RuntimeError, match="verification failed"):
        download_file(entry, tmp_path, retrieve=retrieve)

    assert not (tmp_path / entry.name).exists()
