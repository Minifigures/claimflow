"""Specialist workflow end-to-end through the API: forward -> stage-2 note ->
recommendation queue -> send-to-insurer -> stage-3 summary, plus the return /
request-further-testing / regenerate paths. TestClient runs BackgroundTasks
synchronously, so each action's inference completes before the next assertion."""

import hashlib
import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claimguard import audit
from app.models import (
    AdjudicationSummary,
    ArtifactStatus,
    Claim,
    ClaimState,
    DiagnosticReport,
    Document,
    DocumentKind,
    Modality,
    Notification,
    Recommendation,
    RecommendationNote,
    User,
)
from tests.conftest import login

SEED_TAMPERED = Path(__file__).resolve().parents[1] / "seed-assets" / "tampered_xray.dcm"

CLAIM_BODY = {
    "claim_type": "imaging",
    "description": "Wrist X-ray after a fall on ice",
    "procedure_code": "IMG-2-0042",
    "diagnosis_code": "S62.10",
    "incident_date": "2026-05-20",
    "amount_claimed": 420.5,
}


def _attach_imaging_doc(
    session: Session,
    tmp_path: Path,
    claim_id: int,
    uploader_id: int,
    filename: str,
    source: Path | None = None,
) -> Document:
    image_path = tmp_path / filename
    if source is not None:
        shutil.copyfile(source, image_path)
    else:
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


def _claim_in_imaging_review(
    client: TestClient,
    session: Session,
    users: dict[str, User],
    tmp_path: Path,
    *,
    filename: str = "wrist_xray.png",
    source: Path | None = None,
) -> dict:
    """Claim + imaging doc + COMPLETE stage-1 report, parked at IMAGING_REVIEW."""
    login(client, "claimant@demo.ca")
    resp = client.post("/api/claims", json=CLAIM_BODY)
    assert resp.status_code == 201, resp.text
    claim = resp.json()
    _attach_imaging_doc(
        session, tmp_path, claim["id"], users["claimant"].id, filename, source
    )
    assert client.post(f"/api/claims/{claim['id']}/analyze").status_code == 200
    session.expire_all()
    row = session.get(Claim, claim["id"])
    assert row is not None and row.state is ClaimState.IMAGING_REVIEW
    return claim


def _forward(client: TestClient, claim_id: int) -> dict:
    login(client, "imaging@demo.ca")
    resp = client.post(f"/api/specialist/cases/{claim_id}/forward")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_forward_to_adjudication_end_to_end(
    client: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _claim_in_imaging_review(client, session, users, tmp_path)

    forwarded = _forward(client, claim["id"])
    assert forwarded["state"] == "SPECIALIST_REVIEW"
    assert forwarded["note_status"] == "pending"  # status at response time; ran after

    session.expire_all()
    note = session.get(RecommendationNote, forwarded["note_id"])
    assert note is not None
    assert note.status is ArtifactStatus.COMPLETE
    assert note.error is None
    assert note.completed_at is not None
    assert note.recommendation is Recommendation.SUPPORTS_CLAIM  # authentic + xray matches IMG-2
    assert note.generated_by == "fallback_template"
    assert note.fallback_reason == "no_api_key"
    assert note.payload_json is not None
    payload = json.loads(note.payload_json)
    assert payload["recommendation"] == "SUPPORTS_CLAIM"
    assert "advisory_notice" in payload

    # double-forward: claim already left IMAGING_REVIEW
    assert client.post(f"/api/specialist/cases/{claim['id']}/forward").status_code == 409

    # recommendation queue is medical-specialist only
    resp = client.get("/api/specialist/queue", params={"stage": "recommendation"})
    assert resp.status_code == 403  # still logged in as imaging specialist
    login(client, "specialist@demo.ca")
    resp = client.get("/api/specialist/queue", params={"stage": "recommendation"})
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["claim_id"] == claim["id"]
    assert item["state"] == "SPECIALIST_REVIEW"
    assert item["note_status"] == "complete"
    assert item["recommendation"] == "SUPPORTS_CLAIM"
    assert item["requires_mandatory_review"] is True  # keyless fallback confidence 0.0

    # full case detail carries all artifact versions with parsed payloads
    detail = client.get(f"/api/specialist/cases/{claim['id']}").json()
    assert detail["claimant"] == "Casey Claimant"
    assert detail["claim_ref"] == claim["claim_ref"]
    assert len(detail["documents"]) == 1
    assert len(detail["diagnostic_reports"]) == 1
    assert detail["diagnostic_reports"][0]["status"] == "complete"
    assert detail["diagnostic_reports"][0]["payload"]["classifier"]["modality"] == "xray"
    assert detail["recommendation_notes"][0]["payload"]["recommendation"] == "SUPPORTS_CLAIM"

    resp = client.post(f"/api/specialist/cases/{claim['id']}/send-to-insurer")
    assert resp.status_code == 200, resp.text
    sent = resp.json()
    assert sent["state"] == "ADJUDICATION"

    session.expire_all()
    summary = session.get(AdjudicationSummary, sent["summary_id"])
    assert summary is not None
    assert summary.status is ArtifactStatus.COMPLETE
    assert summary.error is None
    assert summary.recommendation_lean == "LEAN_APPROVE"  # SUPPORTS_CLAIM + authentic
    assert summary.generated_by == "fallback_template"
    assert summary.fallback_reason == "no_api_key"
    assert summary.payload_json is not None
    payload = json.loads(summary.payload_json)
    assert isinstance(payload["similar_case_outcomes"], list)  # system-copied; may be empty
    assert payload["recommendation_lean"] == "LEAN_APPROVE"
    assert "advisory_notice" in payload

    session.expire_all()
    valid, _ = audit.verify_chain(session)
    assert valid is True


def test_return_path_notifies_claimant_and_records_timeline_note(
    client: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _claim_in_imaging_review(client, session, users, tmp_path)

    login(client, "imaging@demo.ca")
    resp = client.post(
        f"/api/specialist/cases/{claim['id']}/return",
        json={"note": "image too blurry; please rescan"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "RETURNED_TO_CLAIMANT"

    session.expire_all()
    notification = session.get(Notification, body["notification_id"])
    assert notification is not None
    assert notification.claim_id == claim["id"]
    assert notification.recipient_id == users["claimant"].id
    assert claim["claim_ref"] in notification.subject
    assert "image too blurry; please rescan" in notification.body_text
    assert "Casey" in notification.body_text  # first name only

    login(client, "claimant@demo.ca")
    timeline = client.get(f"/api/claims/{claim['id']}/timeline").json()
    returned = [e for e in timeline if e["action"] == "return_to_claimant"]
    assert len(returned) == 1
    assert returned[0]["note"] == "image too blurry; please rescan"
    assert returned[0]["to_state"] == "RETURNED_TO_CLAIMANT"

    valid, _ = audit.verify_chain(session)
    assert valid is True


def test_request_further_testing_path(
    client: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _claim_in_imaging_review(client, session, users, tmp_path)
    _forward(client, claim["id"])

    login(client, "specialist@demo.ca")
    resp = client.post(
        f"/api/specialist/cases/{claim['id']}/request-further-testing",
        json={"note": "please provide a follow-up CT study"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "PENDING_FURTHER_TESTING"

    session.expire_all()
    notification = session.get(Notification, body["notification_id"])
    assert notification is not None
    assert notification.recipient_id == users["claimant"].id
    assert "please provide a follow-up CT study" in notification.body_text

    login(client, "claimant@demo.ca")
    timeline = client.get(f"/api/claims/{claim['id']}/timeline").json()
    requested = [e for e in timeline if e["action"] == "request_further_testing"]
    assert len(requested) == 1
    assert requested[0]["note"] == "please provide a follow-up CT study"


def test_regenerate_failed_recommendation_note(
    client: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _claim_in_imaging_review(client, session, users, tmp_path)
    forwarded = _forward(client, claim["id"])

    # regenerate refuses while the latest note is COMPLETE
    login(client, "specialist@demo.ca")
    resp = client.post(
        f"/api/specialist/cases/{claim['id']}/regenerate",
        json={"stage": "recommendation"},
    )
    assert resp.status_code == 409

    note = session.get(RecommendationNote, forwarded["note_id"])
    assert note is not None
    note.status = ArtifactStatus.FAILED
    note.error = "boom (forced by test)"
    session.commit()

    # wrong role for the stage
    login(client, "imaging@demo.ca")
    assert (
        client.post(
            f"/api/specialist/cases/{claim['id']}/regenerate",
            json={"stage": "recommendation"},
        ).status_code
        == 403
    )

    login(client, "specialist@demo.ca")
    resp = client.post(
        f"/api/specialist/cases/{claim['id']}/regenerate",
        json={"stage": "recommendation"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "claim_id": claim["id"],
        "stage": "recommendation",
        "artifact_id": forwarded["note_id"],
        "status": "pending",
    }

    session.expire_all()
    note = session.get(RecommendationNote, forwarded["note_id"])
    assert note is not None
    assert note.status is ArtifactStatus.COMPLETE
    assert note.error is None
    assert note.recommendation is not None


def test_tampered_image_forces_further_testing_and_mandatory_review(
    client: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    assert SEED_TAMPERED.is_file()
    claim = _claim_in_imaging_review(
        client, session, users, tmp_path, filename="tampered_xray.dcm", source=SEED_TAMPERED
    )

    report = session.scalar(
        select(DiagnosticReport).where(DiagnosticReport.claim_id == claim["id"])
    )
    assert report is not None
    assert report.authenticity_verdict == "likely_fraudulent"
    assert report.requires_mandatory_review is True

    forwarded = _forward(client, claim["id"])
    session.expire_all()
    note = session.get(RecommendationNote, forwarded["note_id"])
    assert note is not None
    assert note.status is ArtifactStatus.COMPLETE
    assert note.recommendation is Recommendation.REQUIRES_FURTHER_TESTING  # fallback rule 1
    assert note.requires_mandatory_review is True
    payload = json.loads(note.payload_json or "{}")
    assert payload["recommendation"] == "REQUIRES_FURTHER_TESTING"


def test_wrong_roles_get_403_on_every_action(
    client: TestClient, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    claim = _claim_in_imaging_review(client, session, users, tmp_path)
    cases = f"/api/specialist/cases/{claim['id']}"

    login(client, "specialist@demo.ca")  # medical specialist cannot act on imaging stage
    assert client.post(f"{cases}/forward").status_code == 403
    assert client.post(f"{cases}/return", json={"note": "x"}).status_code == 403
    assert client.get("/api/specialist/queue", params={"stage": "imaging"}).status_code == 403

    login(client, "imaging@demo.ca")  # imaging specialist cannot act on specialist stage
    assert client.post(f"{cases}/send-to-insurer").status_code == 403
    assert client.post(f"{cases}/request-further-testing", json={"note": "x"}).status_code == 403
    assert (
        client.post(f"{cases}/regenerate", json={"stage": "adjudication"}).status_code == 403
    )

    login(client, "claimant@demo.ca")  # claimants never reach specialist endpoints
    assert client.get("/api/specialist/queue").status_code == 403
    assert client.get(cases).status_code == 403
    assert client.post(f"{cases}/forward").status_code == 403
    assert client.post(f"{cases}/return", json={"note": "x"}).status_code == 403
    assert client.post(f"{cases}/regenerate", json={"stage": "imaging"}).status_code == 403

    login(client, "agent@demo.ca")  # agents only regenerate adjudication artifacts
    assert client.get(cases).status_code == 403
    assert client.post(f"{cases}/forward").status_code == 403
    assert client.post(f"{cases}/regenerate", json={"stage": "imaging"}).status_code == 403
    # adjudication regenerate is allowed for agents, but there is no failed summary yet
    assert (
        client.post(f"{cases}/regenerate", json={"stage": "adjudication"}).status_code == 409
    )

    login(client, "imaging@demo.ca")
    assert client.get("/api/specialist/cases/999999").status_code == 404
