import hashlib
import io
import re
from pathlib import Path

import pytest
from fastapi import UploadFile

from app.config import Settings
from app.services import storage
from app.services.storage import StoredFile, UploadTooLargeError, save_upload


def make_upload(data: bytes, filename: str) -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def test_save_upload_streams_and_hashes(settings: Settings) -> None:
    # ~3 MiB so the copy spans multiple 1 MiB chunks.
    data = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * (12 * 1024)
    assert len(data) > 2 * storage._CHUNK

    stored = save_upload(settings, 1, make_upload(data, "scan.png"))

    assert isinstance(stored, StoredFile)
    assert stored.size_bytes == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    target = Path(stored.storage_path)
    assert target.is_file()
    assert target.read_bytes() == data


def test_save_upload_uses_server_side_uuid_name(settings: Settings) -> None:
    stored = save_upload(settings, 7, make_upload(b"png-bytes", "../../evil.png"))

    target = Path(stored.storage_path)
    assert target.parent == settings.upload_dir / "7"
    assert target.resolve().is_relative_to(settings.upload_dir.resolve())
    assert ".." not in target.parts
    assert "evil" not in target.name
    assert re.fullmatch(r"[0-9a-f]{32}\.png", target.name), target.name
    assert target.is_file()


def test_save_upload_size_cap_removes_partial_file(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "MAX_UPLOAD_BYTES", 10)

    with pytest.raises(UploadTooLargeError):
        save_upload(settings, 3, make_upload(b"x" * 64, "big.png"))

    claim_dir = settings.upload_dir / "3"
    assert claim_dir.is_dir()
    assert list(claim_dir.iterdir()) == []


def test_save_upload_exactly_at_cap_is_allowed(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(storage, "MAX_UPLOAD_BYTES", 10)

    stored = save_upload(settings, 4, make_upload(b"x" * 10, "ok.bin"))

    assert stored.size_bytes == 10
    assert Path(stored.storage_path).read_bytes() == b"x" * 10
