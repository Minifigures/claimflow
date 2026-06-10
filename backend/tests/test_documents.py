import json
import uuid
from io import BytesIO
from pathlib import Path

import numpy as np
import pydicom
from fastapi.testclient import TestClient
from PIL import Image
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.claimguard import audit
from app.models import AuditEvent, Claim, ClaimState, Document, Role, User
from app.services import storage
from tests.conftest import DEMO_PASSWORD, login


def _make_claim(
    session: Session, claimant: User, state: ClaimState = ClaimState.SUBMITTED
) -> Claim:
    claim = Claim(
        claim_ref=f"CLM-{uuid.uuid4().hex[:8].upper()}",
        claimant_id=claimant.id,
        claim_type="imaging",
        state=state,
    )
    session.add(claim)
    session.commit()
    return claim


def _make_claimant(session: Session, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password(DEMO_PASSWORD),
        role=Role.CLAIMANT,
        full_name="Other Claimant",
        member_id="MBR-9999",
    )
    session.add(user)
    session.commit()
    return user


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("L", (1, 1), 128).save(buf, format="PNG")
    return buf.getvalue()


def _dicom_bytes(tmp_path: Path) -> bytes:
    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = CTImageStorage
    file_meta.MediaStorageSOPInstanceUID = generate_uid()
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    ds = Dataset()
    ds.file_meta = file_meta
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
    ds.PatientName = "Doe^Jane"
    ds.PatientID = "PHI-12345"
    ds.Modality = "CT"
    ds.StudyDate = "20240115"
    ds.Manufacturer = "TestScan"
    ds.Rows = 4
    ds.Columns = 4
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = np.arange(16, dtype=np.uint16).reshape(4, 4).tobytes()

    out = tmp_path / "source.dcm"
    ds.save_as(out, enforce_file_format=True)
    return out.read_bytes()


def _upload(
    client: TestClient,
    claim_id: int,
    content: bytes,
    filename: str,
    mime: str,
    *,
    kind: str = "imaging",
    modality: str | None = None,
):
    data = {"kind": kind}
    if modality is not None:
        data["modality"] = modality
    return client.post(
        f"/api/documents/upload/{claim_id}",
        files={"file": (filename, content, mime)},
        data=data,
    )


def test_dicom_upload_deidentifies_and_renders_preview(
    as_claimant: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _make_claim(session, users["claimant"])
    resp = _upload(
        as_claimant,
        claim.id,
        _dicom_bytes(tmp_path),
        "study.dcm",
        "application/dicom",
        modality="ct",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "imaging"
    assert body["modality"] == "ct"
    assert body["has_preview"] is True

    document = session.scalar(select(Document).where(Document.id == body["id"]))
    assert document is not None

    # PHI blanked on disk — the stored study is de-identified at rest.
    ds = pydicom.dcmread(document.storage_path)
    assert str(ds.PatientName) == ""
    assert str(ds.PatientID) == ""

    meta = json.loads(document.dicom_meta_json)
    assert meta["Modality"] == "CT"
    assert meta["StudyDate"] == "2024"  # year only
    assert document.preview_path is not None
    assert Path(document.preview_path).exists()

    preview = as_claimant.get(f"/api/documents/{body['id']}/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG")


def test_png_upload_keeps_declared_modality(
    as_claimant: TestClient, session: Session, users: dict[str, User]
) -> None:
    claim = _make_claim(session, users["claimant"])
    resp = _upload(as_claimant, claim.id, _png_bytes(), "scan.png", "image/png", modality="xray")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "imaging"
    assert body["modality"] == "xray"
    assert body["has_preview"] is False


def test_unsupported_file_rejected(
    as_claimant: TestClient, session: Session, users: dict[str, User]
) -> None:
    claim = _make_claim(session, users["claimant"])
    # Lying extension + mime: magic-byte sniffing must still reject it.
    resp = _upload(
        as_claimant, claim.id, b"just some text", "fake.dcm", "application/dicom", kind="other"
    )
    assert resp.status_code == 400
    assert "unsupported" in resp.json()["detail"]


def test_oversize_upload_rejected(
    as_claimant: TestClient, session: Session, users: dict[str, User], monkeypatch
) -> None:
    monkeypatch.setattr(storage, "MAX_UPLOAD_BYTES", 16)
    claim = _make_claim(session, users["claimant"])
    resp = _upload(as_claimant, claim.id, _png_bytes(), "scan.png", "image/png")
    assert resp.status_code == 413


def test_upload_rejected_in_non_upload_state(
    as_claimant: TestClient, session: Session, users: dict[str, User]
) -> None:
    claim = _make_claim(session, users["claimant"], state=ClaimState.IMAGING_REVIEW)
    resp = _upload(as_claimant, claim.id, _png_bytes(), "scan.png", "image/png")
    assert resp.status_code == 409


def test_non_owner_upload_rejected(
    as_claimant: TestClient, session: Session, users: dict[str, User]
) -> None:
    other = _make_claimant(session, "other@demo.ca")
    claim = _make_claim(session, other)
    resp = _upload(as_claimant, claim.id, _png_bytes(), "scan.png", "image/png")
    assert resp.status_code == 404


def test_file_access_owner_staff_and_other_claimant(
    as_claimant: TestClient, session: Session, users: dict[str, User]
) -> None:
    claim = _make_claim(session, users["claimant"])
    resp = _upload(as_claimant, claim.id, _png_bytes(), "scan.png", "image/png", modality="xray")
    assert resp.status_code == 200, resp.text
    doc_id = resp.json()["id"]

    owner = as_claimant.get(f"/api/documents/{doc_id}/file")
    assert owner.status_code == 200
    assert owner.headers["content-disposition"].startswith("attachment")
    assert "scan.png" not in owner.headers["content-disposition"]  # server-side name

    login(as_claimant, "imaging@demo.ca")
    assert as_claimant.get(f"/api/documents/{doc_id}/file").status_code == 200
    login(as_claimant, "specialist@demo.ca")
    assert as_claimant.get(f"/api/documents/{doc_id}/file").status_code == 200
    login(as_claimant, "agent@demo.ca")
    assert as_claimant.get(f"/api/documents/{doc_id}/file").status_code == 200

    _make_claimant(session, "other@demo.ca")
    login(as_claimant, "other@demo.ca")
    assert as_claimant.get(f"/api/documents/{doc_id}/file").status_code == 404
    assert as_claimant.get(f"/api/documents/{doc_id}/preview").status_code == 404
    assert as_claimant.get("/api/documents/999999/file").status_code == 404


def test_upload_audited_and_chain_valid(
    as_claimant: TestClient, session: Session, users: dict[str, User]
) -> None:
    claim = _make_claim(session, users["claimant"])
    resp = _upload(as_claimant, claim.id, _png_bytes(), "scan.png", "image/png")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "document.upload"))
    assert event is not None
    assert event.claim_id == claim.id
    assert event.actor_user_id == users["claimant"].id
    assert event.actor_role == "claimant"
    payload = json.loads(event.payload_json)
    assert payload["filename"] == "scan.png"
    assert payload["kind"] == "imaging"
    assert payload["sha256"] == body["sha256"]

    ok, checked = audit.verify_chain(session)
    assert ok is True
    assert checked >= 2  # at least login + upload
