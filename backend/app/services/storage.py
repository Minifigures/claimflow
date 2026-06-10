import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_CHUNK = 1024 * 1024


class UploadTooLargeError(Exception):
    pass


@dataclass
class StoredFile:
    storage_path: str
    sha256: str
    size_bytes: int


def save_upload(settings: Settings, claim_id: int, upload: UploadFile) -> StoredFile:
    """Stream an upload to disk under a server-chosen UUID name (never trust the
    client filename), hashing and size-capping as we go."""
    suffix = Path(upload.filename or "upload.bin").suffix.lower()[:8]
    target_dir = settings.upload_dir / str(claim_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{uuid.uuid4().hex}{suffix}"

    hasher = hashlib.sha256()
    size = 0
    try:
        with target.open("wb") as fh:
            while chunk := upload.file.read(_CHUNK):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError(f"upload exceeds {MAX_UPLOAD_BYTES} bytes")
                hasher.update(chunk)
                fh.write(chunk)
    except UploadTooLargeError:
        target.unlink(missing_ok=True)
        raise
    return StoredFile(storage_path=str(target), sha256=hasher.hexdigest(), size_bytes=size)
