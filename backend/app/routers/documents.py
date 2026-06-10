import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import enforce_origin, get_current_user, get_settings_dep, require_role
from app.claimguard import audit
from app.config import Settings
from app.db import get_db
from app.models import (
    AuditEventType,
    Claim,
    ClaimState,
    Document,
    DocumentKind,
    Modality,
    Role,
    User,
)
from app.services import storage
from app.services.dicom_preview import process_dicom, sniff_kind

router = APIRouter()

DICOM_PROCESS_TIMEOUT_S = 30.0

# Claimants attach documents before triggering analysis (claims are created in
# SUBMITTED) and again when a reviewer sends the claim back for more evidence.
UPLOAD_STATES = frozenset(
    {ClaimState.SUBMITTED, ClaimState.RETURNED_TO_CLAIMANT, ClaimState.PENDING_FURTHER_TESTING}
)

STAFF_ROLES = frozenset(
    {Role.IMAGING_SPECIALIST, Role.MEDICAL_SPECIALIST, Role.INSURANCE_AGENT}
)

_MIME_BY_SNIFF = {
    "dicom": "application/dicom",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "pdf": "application/pdf",
}


class DocumentOut(BaseModel):
    id: int
    filename: str
    kind: str
    modality: str | None
    size_bytes: int
    sha256: str
    has_preview: bool


def _document_out(document: Document) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        filename=document.filename,
        kind=document.kind.value,
        modality=document.modality.value if document.modality else None,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        has_preview=document.preview_path is not None,
    )


@router.post("/upload/{claim_id}", dependencies=[Depends(enforce_origin)])
async def upload_document(
    claim_id: int,
    file: UploadFile = File(...),
    kind: DocumentKind = Form(...),
    modality: Modality | None = Form(None),
    user: User = Depends(require_role(Role.CLAIMANT)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DocumentOut:
    claim = session.get(Claim, claim_id)
    if claim is None or claim.claimant_id != user.id:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.state not in UPLOAD_STATES:
        raise HTTPException(
            status_code=409,
            detail=f"documents cannot be uploaded while the claim is {claim.state.value}",
        )

    try:
        stored = await asyncio.to_thread(storage.save_upload, settings, claim.id, file)
    except storage.UploadTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    stored_path = Path(stored.storage_path)
    try:
        detected = sniff_kind(stored_path, file.content_type or "")
    except ValueError as exc:
        stored_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    dicom_meta_json: str | None = None
    preview_path: str | None = None
    if detected == "dicom":
        kind = DocumentKind.IMAGING
        try:
            meta, preview_path = await asyncio.wait_for(
                asyncio.to_thread(process_dicom, stored_path),
                timeout=DICOM_PROCESS_TIMEOUT_S,
            )
        except TimeoutError as exc:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="DICOM processing timed out") from exc
        except ValueError as exc:
            stored_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        dicom_meta_json = json.dumps(meta)
    elif detected == "pdf":
        if kind is DocumentKind.IMAGING:
            kind = DocumentKind.MEDICAL_RECORD
        modality = None
    elif kind is not DocumentKind.IMAGING:  # png/jpeg under a non-imaging kind
        modality = None

    document = Document(
        claim_id=claim.id,
        uploader_id=user.id,
        kind=kind,
        modality=modality,
        filename=Path(file.filename or "upload.bin").name,
        mime=_MIME_BY_SNIFF[detected],
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        storage_path=stored.storage_path,
        preview_path=preview_path,
        dicom_meta_json=dicom_meta_json,
    )
    session.add(document)
    session.flush()
    audit.append(
        session,
        AuditEventType.DOCUMENT_UPLOAD,
        claim_id=claim.id,
        actor_user_id=user.id,
        actor_role=user.role.value,
        payload={
            "filename": document.filename,
            "kind": document.kind.value,
            "sha256": document.sha256,
        },
    )
    session.commit()
    return _document_out(document)


def _readable_document(document_id: int, user: User, session: Session) -> Document:
    """404 for unknown ids and for claimants who do not own the claim (no existence leak)."""
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if user.role not in STAFF_ROLES and document.claim.claimant_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}/file")
def download_document(
    document_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> FileResponse:
    document = _readable_document(document_id, user, session)
    path = Path(document.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Stored file is missing")
    return FileResponse(
        path,
        media_type=document.mime,
        filename=f"document-{document.id}{path.suffix}",  # server-side name, never the client's
    )


@router.get("/{document_id}/preview")
def download_preview(
    document_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> FileResponse:
    document = _readable_document(document_id, user, session)
    if document.preview_path is None:
        raise HTTPException(status_code=404, detail="No preview available")
    path = Path(document.preview_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No preview available")
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"document-{document.id}-preview.png",
    )
