"""Claimant claim lifecycle + imaging queue. Documents are built via the ORM with real
temp PNGs (the documents router is exercised elsewhere); TestClient runs FastAPI
BackgroundTasks synchronously, so submit-then-assert needs no sleeping."""

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.claimguard import audit
from app.models import (
    ArtifactStatus,
    AuditEvent,
    Claim,
    ClaimAction,
    ClaimState,
    Decision,
    DiagnosticReport,
    Document,
    DocumentKind,
    Modality,
    Role,
    User,
)
from app.workflow.state_machine import apply_transition
from tests.conftest import DEMO_PASSWORD, login

CLAIM_BODY = {
    "claim_type": "imaging_diagnostics",
    "description": "Wrist X-ray after a fall on ice",
    "procedure_code": "73100",
    "diagnosis_code": "S62.10",
    "incident_date": "2026-05-20",
    "amount_claimed": 420.5,
}


def _create_claim(client: TestClient) -> dict:
    resp = client.post("/api/claims", json=CLAIM_BODY)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _attach_imaging_doc(
    session: Session,
    tmp_path: Path,
    claim_id: int,
    uploader_id: int,
    filename: str = "wrist_xray.png",
) -> Document:
    image_path = tmp_path / filename
    Image.new("L", (64, 64), color=128).save(image_path, format="PNG")
    data = image_path.read_bytes()
    doc = Document(
        claim_id=claim_id,
        uploader_id=uploader_id,
        kind=DocumentKind.IMAGING,
        modality=Modality.XRAY,
        filename=filename,
        mime="image/png",
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_path=str(image_path),
    )
    session.add(doc)
    session.commit()
    return doc


def test_create_claim_records_decision_and_audit(
    as_claimant: TestClient, session: Session, users: dict[str, User]
) -> None:
    body = _create_claim(as_claimant)
    assert body["state"] == "SUBMITTED"
    assert body["claim_ref"].startswith("CLM-")
    assert len(body["claim_ref"]) == 12
    assert body["amount_claimed"] == CLAIM_BODY["amount_claimed"]
    assert body["incident_date"] == CLAIM_BODY["incident_date"]

    decision = session.scalar(select(Decision).where(Decision.claim_id == body["id"]))
    assert decision is not None
    assert decision.action is ClaimAction.SUBMIT
    assert decision.from_state is None
    assert decision.to_state is ClaimState.SUBMITTED
    assert decision.actor_id == users["claimant"].id

    event_types = session.scalars(
        select(AuditEvent.event_type).where(AuditEvent.claim_id == body["id"])
    ).all()
    assert "workflow.transition" in event_types
    assert "claim.submit" in event_types

    valid, _ = audit.verify_chain(session)
    assert valid is True


def test_list_claims_newest_first_and_own_only(as_claimant: TestClient) -> None:
    first = _create_claim(as_claimant)
    second = _create_claim(as_claimant)

    resp = as_claimant.get("/api/claims")
    assert resp.status_code == 200
    assert [c["id"] for c in resp.json()] == [second["id"], first["id"]]


def test_analyze_without_imaging_document_is_422(as_claimant: TestClient) -> None:
    claim = _create_claim(as_claimant)
    resp = as_claimant.post(f"/api/claims/{claim['id']}/analyze")
    assert resp.status_code == 422


def test_analyze_completes_stub_pipeline(
    as_claimant: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _create_claim(as_claimant)
    _attach_imaging_doc(session, tmp_path, claim["id"], users["claimant"].id)

    resp = as_claimant.post(f"/api/claims/{claim['id']}/analyze")
    assert resp.status_code == 200, resp.text
    started = resp.json()
    assert started["status"] == "pending"

    # BackgroundTasks ran synchronously on response: stub stage-1 has completed.
    detail = as_claimant.get(f"/api/claims/{claim['id']}").json()
    assert detail["state"] == "IMAGING_REVIEW"

    report = detail["diagnostic_report"]
    assert report is not None
    assert report["id"] == started["report_id"]
    assert report["status"] == "complete"
    assert report["modality"] == "xray"
    assert report["authenticity_verdict"] == "authentic"
    # keyless stage-1c fallback pins confidence to 0.0 -> always mandatory review
    assert report["requires_mandatory_review"] is True
    assert report["error"] is None

    payload = report["payload"]
    assert payload["modality_assessment"] == "xray"
    assert payload["authenticity"]["verdict"] == "authentic"
    assert payload["authenticity"]["signals"]
    assert "disclaimer" in payload

    docs = detail["documents"]
    assert len(docs) == 1
    assert docs[0]["kind"] == "imaging"
    assert docs[0]["modality"] == "xray"
    assert docs[0]["has_preview"] is False

    session.expire_all()
    valid, _ = audit.verify_chain(session)
    assert valid is True


def test_tampered_filename_flags_mandatory_review(
    as_claimant: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _create_claim(as_claimant)
    _attach_imaging_doc(
        session, tmp_path, claim["id"], users["claimant"].id, filename="tampered_wrist_xray.png"
    )
    assert as_claimant.post(f"/api/claims/{claim['id']}/analyze").status_code == 200

    report = as_claimant.get(f"/api/claims/{claim['id']}").json()["diagnostic_report"]
    assert report["status"] == "complete"
    assert report["authenticity_verdict"] == "likely_fraudulent"
    assert report["requires_mandatory_review"] is True
    assert report["authenticity_risk"] >= 0.8


def test_timeline_has_submit_then_imaging_complete(
    as_claimant: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _create_claim(as_claimant)
    _attach_imaging_doc(session, tmp_path, claim["id"], users["claimant"].id)
    assert as_claimant.post(f"/api/claims/{claim['id']}/analyze").status_code == 200

    timeline = as_claimant.get(f"/api/claims/{claim['id']}/timeline").json()
    assert [e["action"] for e in timeline] == ["submit", "imaging_complete"]
    assert timeline[0]["actor_role"] == "claimant"
    assert timeline[0]["from_state"] is None
    assert timeline[0]["to_state"] == "SUBMITTED"
    assert timeline[1]["actor_role"] == "system"
    assert timeline[1]["from_state"] == "SUBMITTED"
    assert timeline[1]["to_state"] == "IMAGING_REVIEW"


def test_specialist_queue_shows_claim_and_guards_roles(
    client: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    login(client, "claimant@demo.ca")
    claim = _create_claim(client)
    _attach_imaging_doc(session, tmp_path, claim["id"], users["claimant"].id)
    assert client.post(f"/api/claims/{claim['id']}/analyze").status_code == 200

    login(client, "imaging@demo.ca")
    resp = client.get("/api/specialist/queue", params={"stage": "imaging"})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["claim_id"] == claim["id"]
    assert item["claim_ref"] == claim["claim_ref"]
    assert item["claim_type"] == CLAIM_BODY["claim_type"]
    assert item["claimant"] == "Casey Claimant"
    assert item["report_status"] == "complete"
    assert item["modality"] == "xray"
    assert item["authenticity_verdict"] == "authentic"
    # keyless stage-1c fallback always flags mandatory review (confidence 0.0)
    assert item["requires_mandatory_review"] is True

    assert client.get("/api/specialist/queue", params={"stage": "bogus"}).status_code == 422

    # staff may view the claim detail and timeline
    assert client.get(f"/api/claims/{claim['id']}").status_code == 200
    assert client.get(f"/api/claims/{claim['id']}/timeline").status_code == 200

    login(client, "claimant@demo.ca")
    assert client.get("/api/specialist/queue").status_code == 403
    login(client, "agent@demo.ca")
    assert client.get("/api/specialist/queue").status_code == 403


def test_resubmit_from_wrong_state_is_409(as_claimant: TestClient) -> None:
    claim = _create_claim(as_claimant)
    resp = as_claimant.post(f"/api/claims/{claim['id']}/resubmit", json={"note": "new scan"})
    assert resp.status_code == 409
    assert "resubmit" in resp.json()["detail"]
    assert as_claimant.get(f"/api/claims/{claim['id']}").json()["state"] == "SUBMITTED"


def test_resubmit_after_return_creates_new_report(
    as_claimant: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _create_claim(as_claimant)
    _attach_imaging_doc(session, tmp_path, claim["id"], users["claimant"].id)
    first = as_claimant.post(f"/api/claims/{claim['id']}/analyze").json()

    claim_row = session.get(Claim, claim["id"])
    assert claim_row is not None and claim_row.state is ClaimState.IMAGING_REVIEW
    apply_transition(
        session,
        claim_row,
        ClaimAction.RETURN_TO_CLAIMANT,
        actor=users["imaging_specialist"],
        note="image quality insufficient",
    )
    session.commit()

    resp = as_claimant.post(f"/api/claims/{claim['id']}/resubmit", json={"note": "rescanned"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "SUBMITTED"
    assert body["report_id"] is not None
    assert body["report_id"] != first["report_id"]
    assert body["report_status"] == "pending"

    detail = as_claimant.get(f"/api/claims/{claim['id']}").json()
    assert detail["state"] == "IMAGING_REVIEW"
    assert detail["diagnostic_report"]["id"] == body["report_id"]
    assert detail["diagnostic_report"]["status"] == "complete"

    timeline = as_claimant.get(f"/api/claims/{claim['id']}/timeline").json()
    assert [e["action"] for e in timeline] == [
        "submit",
        "imaging_complete",
        "return_to_claimant",
        "resubmit",
        "imaging_complete",
    ]

    session.expire_all()
    valid, _ = audit.verify_chain(session)
    assert valid is True


def test_double_analyze_is_409(
    as_claimant: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _create_claim(as_claimant)
    _attach_imaging_doc(session, tmp_path, claim["id"], users["claimant"].id)
    assert as_claimant.post(f"/api/claims/{claim['id']}/analyze").status_code == 200
    # first run already moved the claim to IMAGING_REVIEW
    assert as_claimant.post(f"/api/claims/{claim['id']}/analyze").status_code == 409


def test_analyze_with_active_report_is_409(
    as_claimant: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _create_claim(as_claimant)
    doc = _attach_imaging_doc(session, tmp_path, claim["id"], users["claimant"].id)
    session.add(
        DiagnosticReport(
            claim_id=claim["id"], document_id=doc.id, status=ArtifactStatus.PENDING
        )
    )
    session.commit()
    assert as_claimant.post(f"/api/claims/{claim['id']}/analyze").status_code == 409


def test_other_claimant_cannot_access_claim(
    client: TestClient, session: Session, users: dict[str, User]
) -> None:
    login(client, "claimant@demo.ca")
    claim = _create_claim(client)

    session.add(
        User(
            email="other@demo.ca",
            password_hash=hash_password(DEMO_PASSWORD),
            role=Role.CLAIMANT,
            full_name="Olive Other",
            member_id="MBR-2002",
        )
    )
    session.commit()
    login(client, "other@demo.ca")

    assert client.get(f"/api/claims/{claim['id']}").status_code == 404
    assert client.get(f"/api/claims/{claim['id']}/timeline").status_code == 404
    assert client.post(f"/api/claims/{claim['id']}/analyze").status_code == 404
    assert client.post(f"/api/claims/{claim['id']}/resubmit", json={"note": "x"}).status_code == 404
    assert client.get("/api/claims").json() == []
