"""Insurance-agent portal flow: queue -> dossier -> draft email -> atomic decision.

The decision endpoint carries the assessment's hard invariant: the final decision and
the claimant notification email are ONE action, so every claim in a terminal state must
have a Notification row — even when the agent submits empty email fields (server-side
template fallback). Artifacts are built via the ORM with realistic payloads produced by
the deterministic keyless fallback generators, exactly as the stage wiring would.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.claimguard import audit
from app.config import Settings
from app.llm import fallbacks
from app.llm.schemas import ADJUDICATION_NOTICE, ADVISORY_NOTICE, DIAGNOSTIC_DISCLAIMER
from app.ml.base import ImagingAnalysis
from app.models import (
    AdjudicationSummary,
    ArtifactStatus,
    AuditEvent,
    Claim,
    ClaimAction,
    ClaimHistory,
    ClaimState,
    Decision,
    DiagnosticReport,
    Document,
    DocumentKind,
    Modality,
    Notification,
    NotificationStatus,
    Recommendation,
    RecommendationNote,
    Role,
    User,
)
from app.rag.store import get_adjudicated_cases_collection, get_client
from tests.conftest import DEMO_PASSWORD, login

CLAIM_REF = "CLM-AGENT-0001"

# date desc order: physio (rejected) -> dental (approved) -> imaging (approved)
HISTORY_ROWS = [
    dict(
        claim_type="physiotherapy",
        procedure_code="P-200",
        diagnosis_code="M54.5",
        modality=None,
        billed_amount=95.0,
        outcome="rejected",
        date_of_service=date(2026, 1, 9),
        decided_at=date(2026, 1, 30),
    ),
    dict(
        claim_type="dental",
        procedure_code="D-110",
        diagnosis_code="K02.9",
        modality=None,
        billed_amount=180.0,
        outcome="approved",
        date_of_service=date(2025, 11, 2),
        decided_at=date(2025, 11, 20),
    ),
    dict(
        claim_type="imaging_diagnostics",
        procedure_code="73100",
        diagnosis_code="S62.10",
        modality="xray",
        billed_amount=350.0,
        outcome="approved",
        date_of_service=date(2024, 7, 14),
        decided_at=date(2024, 8, 1),
    ),
]


# ------------------------------------------------------- payloads from the fallback generators


def _report_payload() -> dict:
    analysis = ImagingAnalysis(
        modality="xray",
        modality_confidence=0.97,
        modality_probs={"xray": 0.97, "ct": 0.02, "mri": 0.01},
        authenticity_verdict="authentic",
        authenticity_risk=0.05,
        signals=[],
        quality_flags=[],
        backend="stub",
    )
    payload = fallbacks.fallback_diagnostic_report(analysis, declared_modality="xray").model_dump()
    # system-injected sections, exactly as stage 1 wiring adds them post-parse
    payload["authenticity"] = {"verdict": "authentic", "risk_score": 0.05, "signals": []}
    payload["classifier"] = {"modality": "xray", "confidence": 0.97}
    payload["disclaimer"] = DIAGNOSTIC_DISCLAIMER
    return payload


def _note_payload() -> dict:
    payload = fallbacks.fallback_recommendation(
        {
            "claim": {
                "claim_type": "imaging_diagnostics",
                "procedure_code": "73100",
                "diagnosis_code": "S62.10",
                "incident_date": "2026-05-20",
                "amount_claimed": 420.5,
            },
            "diagnostic_report": {
                "modality": "xray",
                "authenticity_verdict": "authentic",
                "authenticity_risk": 0.05,
                "requires_mandatory_review": False,
                "impression": "No acute abnormality.",
            },
            "uploads": [],
            "modality_for_procedure": "xray",
        }
    ).model_dump()
    payload["advisory_notice"] = ADVISORY_NOTICE
    payload["documents_reviewed"] = []
    return payload


def _summary_payload() -> dict:
    payload = fallbacks.fallback_adjudication(
        "SUPPORTS_CLAIM",
        {"total": 3, "approved": 2, "rejected": 1, "recent_12mo": 1, "prior_rejections": 1},
        [],
        "authentic",
    ).model_dump()
    payload["similar_case_outcomes"] = []
    payload["advisory_notice"] = ADJUDICATION_NOTICE
    return payload


# ----------------------------------------------------------------------------- fixtures


@pytest.fixture()
def case(session: Session, users: dict[str, User]) -> Claim:
    """A dossier-ready claim in ADJUDICATION with COMPLETE artifacts and member history."""
    claimant = users[Role.CLAIMANT.value]
    claim = Claim(
        claim_ref=CLAIM_REF,
        claimant_id=claimant.id,
        claim_type="imaging_diagnostics",
        description="Wrist X-ray after a fall on ice",
        procedure_code="73100",
        diagnosis_code="S62.10",
        incident_date=date(2026, 5, 20),
        amount_claimed=420.5,
        state=ClaimState.ADJUDICATION,
    )
    session.add(claim)
    session.flush()
    session.add(
        Document(
            claim_id=claim.id,
            uploader_id=claimant.id,
            kind=DocumentKind.IMAGING,
            modality=Modality.XRAY,
            filename="wrist_xray.png",
            mime="image/png",
            size_bytes=1024,
            sha256="0" * 64,
            storage_path="/nonexistent/wrist_xray.png",  # dossier never reads the file
        )
    )
    session.add(
        DiagnosticReport(
            claim_id=claim.id,
            status=ArtifactStatus.COMPLETE,
            payload_json=json.dumps(_report_payload()),
            generated_by="stub-analyzer",
            modality="xray",
            modality_confidence=0.97,
            authenticity_verdict="authentic",
            authenticity_risk=0.05,
        )
    )
    session.add(
        RecommendationNote(
            claim_id=claim.id,
            status=ArtifactStatus.COMPLETE,
            payload_json=json.dumps(_note_payload()),
            generated_by="fallback_template",
            fallback_reason="no_api_key",
            recommendation=Recommendation.SUPPORTS_CLAIM,
            confidence=0.0,
        )
    )
    session.add(
        AdjudicationSummary(
            claim_id=claim.id,
            status=ArtifactStatus.COMPLETE,
            payload_json=json.dumps(_summary_payload()),
            generated_by="fallback_template",
            fallback_reason="no_api_key",
            recommendation_lean="LEAN_APPROVE",
            confidence=0.0,
        )
    )
    for row in HISTORY_ROWS:
        session.add(ClaimHistory(member_id=claimant.member_id, **row))
    session.commit()
    return claim


@pytest.fixture()
def fr_case(session: Session, users: dict[str, User]) -> Claim:
    """A second claimant with French/formal preferences and her ADJUDICATION claim."""
    fr_user = User(
        email="claimant2@demo.ca",
        password_hash=hash_password(DEMO_PASSWORD),
        role=Role.CLAIMANT,
        full_name="Marie Tremblay",
        member_id="MBR-2002",
        preferred_language="fr",
        preferred_tone="formal",
    )
    session.add(fr_user)
    session.flush()
    claim = Claim(
        claim_ref="CLM-AGENT-0002",
        claimant_id=fr_user.id,
        claim_type="imaging",
        state=ClaimState.ADJUDICATION,
    )
    session.add(claim)
    session.commit()
    return claim


def _decision_body(action: str, **overrides: object) -> dict:
    body: dict = {"action": action, "note": None, "email_subject": "", "email_body_text": ""}
    body.update(overrides)
    return body


def _assert_terminal_claims_have_notifications(session: Session) -> None:
    """THE invariant: a claim in a terminal state always has a Notification row."""
    terminal = session.scalars(
        select(Claim).where(Claim.state.in_([ClaimState.APPROVED, ClaimState.REJECTED]))
    ).all()
    assert terminal, "expected at least one decided claim"
    for claim in terminal:
        notification_id = session.scalar(
            select(Notification.id).where(Notification.claim_id == claim.id).limit(1)
        )
        assert notification_id is not None, f"terminal claim {claim.id} has no notification"


# -------------------------------------------------------------------------------- queue


def test_queue_lists_adjudication_claim_and_blocks_claimant(
    as_agent: TestClient, case: Claim
) -> None:
    resp = as_agent.get("/api/agent/queue")
    assert resp.status_code == 200, resp.text
    [item] = resp.json()
    assert item["claim_id"] == case.id
    assert item["claim_ref"] == CLAIM_REF
    assert item["claim_type"] == "imaging_diagnostics"
    assert item["claimant"] == "Casey Claimant"
    assert item["state"] == "ADJUDICATION"
    assert item["submitted_at"]
    assert item["summary_status"] == "complete"
    assert item["recommendation_lean"] == "LEAN_APPROVE"
    assert item["confidence"] == 0.0
    assert item["requires_mandatory_review"] is False
    assert item["specialist_recommendation"] == "SUPPORTS_CLAIM"

    login(as_agent, "claimant@demo.ca")
    assert as_agent.get("/api/agent/queue").status_code == 403


# ------------------------------------------------------------------------------ dossier


def test_dossier_shape_is_complete(as_agent: TestClient, case: Claim) -> None:
    resp = as_agent.get(f"/api/agent/cases/{case.id}/dossier")
    assert resp.status_code == 200, resp.text
    dossier = resp.json()

    claim = dossier["claim"]
    assert claim["id"] == case.id
    assert claim["claim_ref"] == CLAIM_REF
    assert claim["state"] == "ADJUDICATION"
    assert claim["procedure_code"] == "73100"
    assert claim["amount_claimed"] == 420.5

    assert dossier["claimant"] == {
        "full_name": "Casey Claimant",
        "member_id": "MBR-1001",
        "preferred_language": "en",
        "preferred_tone": "plain_language",
    }

    [doc] = dossier["documents"]
    assert doc["filename"] == "wrist_xray.png"
    assert doc["kind"] == "imaging"
    assert doc["modality"] == "xray"

    report = dossier["diagnostic_report"]
    assert report["status"] == "complete"
    assert report["modality"] == "xray"
    assert report["authenticity_verdict"] == "authentic"
    assert report["payload"]["authenticity"] == {
        "verdict": "authentic",
        "risk_score": 0.05,
        "signals": [],
    }
    assert report["payload"]["disclaimer"] == DIAGNOSTIC_DISCLAIMER

    note = dossier["recommendation_note"]
    assert note["status"] == "complete"
    assert note["recommendation"] == "SUPPORTS_CLAIM"
    assert note["payload"]["recommendation"] == "SUPPORTS_CLAIM"
    assert note["payload"]["advisory_notice"] == ADVISORY_NOTICE

    summary = dossier["adjudication_summary"]
    assert summary["status"] == "complete"
    assert summary["recommendation_lean"] == "LEAN_APPROVE"
    payload = summary["payload"]
    assert payload["similar_case_outcomes"] == []
    assert payload["risk_factors"] == []
    assert payload["recommendation_lean"] == "LEAN_APPROVE"
    assert payload["advisory_notice"] == ADJUDICATION_NOTICE

    history = dossier["claim_history"]
    assert [row["outcome"] for row in history] == ["rejected", "approved", "approved"]
    assert [row["date_of_service"] for row in history] == [
        "2026-01-09",
        "2025-11-02",
        "2024-07-14",
    ]
    assert dossier["history_stats"] == {"total": 3, "approved": 2, "rejected": 1}

    assert dossier["timeline"] == []  # claim built via ORM: no Decision rows yet
    assert dossier["notifications"] == []

    assert as_agent.get("/api/agent/cases/999999/dossier").status_code == 404


def test_dossier_outside_adjudication_or_terminal_is_409(
    as_agent: TestClient, session: Session, users: dict[str, User]
) -> None:
    claim = Claim(
        claim_ref="CLM-AGENT-0009",
        claimant_id=users[Role.CLAIMANT.value].id,
        claim_type="imaging",
        state=ClaimState.SUBMITTED,
    )
    session.add(claim)
    session.commit()
    assert as_agent.get(f"/api/agent/cases/{claim.id}/dossier").status_code == 409


# -------------------------------------------------------------------------- draft email


def test_draft_email_keyless_falls_back_and_audits_without_body(
    as_agent: TestClient, case: Claim, session: Session
) -> None:
    resp = as_agent.post(f"/api/agent/cases/{case.id}/draft-email", json={"decision": "APPROVED"})
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert draft["generated_by"] == "fallback_template"
    assert draft["fallback_reason"] == "no_api_key"
    assert CLAIM_REF in draft["subject"]
    assert "Casey" in draft["greeting"]
    assert len(draft["body_paragraphs"]) >= 1
    assert draft["closing"]

    session.expire_all()
    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "email.drafted"))
    assert event is not None
    assert event.claim_id == case.id
    assert event.actor_role == "insurance_agent"
    assert json.loads(event.payload_json) == {
        "decision": "APPROVED",
        "generated_by": "fallback_template",
        "fallback_reason": "no_api_key",
    }
    # The audit log stays PII-free: no greeting/body text (it carries the first name).
    assert "Casey" not in event.payload_json

    valid, _ = audit.verify_chain(session)
    assert valid is True


def test_draft_email_respects_french_preference(as_agent: TestClient, fr_case: Claim) -> None:
    resp = as_agent.post(
        f"/api/agent/cases/{fr_case.id}/draft-email", json={"decision": "REJECTED"}
    )
    assert resp.status_code == 200, resp.text
    draft = resp.json()
    assert draft["generated_by"] == "fallback_template"
    assert "Décision concernant votre demande" in draft["subject"]  # fr + formal template
    assert "Marie" in draft["greeting"]
    assert any("30 jours" in p for p in draft["body_paragraphs"])


def test_draft_email_outside_adjudication_is_409(
    as_agent: TestClient, session: Session, users: dict[str, User]
) -> None:
    claim = Claim(
        claim_ref="CLM-AGENT-0010",
        claimant_id=users[Role.CLAIMANT.value].id,
        claim_type="imaging",
        state=ClaimState.SPECIALIST_REVIEW,
    )
    session.add(claim)
    session.commit()
    resp = as_agent.post(f"/api/agent/cases/{claim.id}/draft-email", json={"decision": "APPROVED"})
    assert resp.status_code == 409


# ----------------------------------------------------------------------------- decision


def test_decision_approve_notifies_audits_and_indexes_precedent(
    as_agent: TestClient,
    case: Claim,
    session: Session,
    settings: Settings,
    users: dict[str, User],
) -> None:
    collection = get_adjudicated_cases_collection(get_client(settings))
    count_before = collection.count()

    resp = as_agent.post(
        f"/api/agent/cases/{case.id}/decision",
        json=_decision_body(
            "approve",
            note="evidence supports the claim",
            email_subject=f"Good news about your claim {CLAIM_REF}",
            email_body_text="Hi Casey,\n\nYour claim was approved.\n\nThanks,\nClaimFlow",
        ),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "APPROVED"
    assert body["notification_status"] == "logged"
    assert body["case_ref"] is not None and body["case_ref"].startswith("CASE-")

    session.expire_all()
    claim = session.get(Claim, case.id)
    assert claim is not None and claim.state is ClaimState.APPROVED

    # the decision and the notification are one action: both rows exist post-commit
    notification = session.scalar(select(Notification).where(Notification.claim_id == case.id))
    assert notification is not None
    assert notification.id == body["notification_id"]
    assert notification.status is NotificationStatus.LOGGED  # console provider terminal state
    assert notification.provider == "console"
    assert notification.subject == f"Good news about your claim {CLAIM_REF}"
    assert "approved" in notification.body_text

    decision = session.scalar(select(Decision).where(Decision.claim_id == case.id))
    assert decision is not None
    assert decision.action is ClaimAction.APPROVE
    assert decision.from_state is ClaimState.ADJUDICATION
    assert decision.to_state is ClaimState.APPROVED
    assert decision.note == "evidence supports the claim"
    assert decision.actor_id == users[Role.INSURANCE_AGENT.value].id

    event_types = set(
        session.scalars(select(AuditEvent.event_type).where(AuditEvent.claim_id == case.id)).all()
    )
    assert {"workflow.transition", "decision.approve", "email.sent"} <= event_types
    approve_event = session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "decision.approve")
    )
    assert approve_event is not None
    assert approve_event.actor_user_id == users[Role.INSURANCE_AGENT.value].id
    assert approve_event.actor_role == "insurance_agent"
    assert json.loads(approve_event.payload_json) == {"note_present": True}

    # precedent indexed into the adjudicated-cases collection
    assert collection.count() == count_before + 1

    _assert_terminal_claims_have_notifications(session)
    valid, _ = audit.verify_chain(session)
    assert valid is True

    # terminal claims stay readable for post-decision review
    dossier = as_agent.get(f"/api/agent/cases/{case.id}/dossier")
    assert dossier.status_code == 200
    reviewed = dossier.json()
    assert reviewed["claim"]["state"] == "APPROVED"
    assert len(reviewed["notifications"]) == 1
    assert len(reviewed["timeline"]) == 1


def test_decision_reject_with_empty_email_uses_server_fallback(
    as_agent: TestClient, case: Claim, session: Session
) -> None:
    resp = as_agent.post(
        f"/api/agent/cases/{case.id}/decision", json=_decision_body("reject")
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "REJECTED"
    assert body["notification_status"] == "logged"

    session.expire_all()
    claim = session.get(Claim, case.id)
    assert claim is not None and claim.state is ClaimState.REJECTED

    # empty subject/body never block the decision: the template fallback fills both
    notification = session.scalar(select(Notification).where(Notification.claim_id == case.id))
    assert notification is not None
    assert notification.id == body["notification_id"]
    assert CLAIM_REF in notification.subject
    assert "Casey" in notification.body_text
    assert "30 days" in notification.body_text  # rejection copy carries the appeal window

    reject_event = session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "decision.reject")
    )
    assert reject_event is not None
    assert json.loads(reject_event.payload_json) == {"note_present": False}

    _assert_terminal_claims_have_notifications(session)
    valid, _ = audit.verify_chain(session)
    assert valid is True


def test_double_decision_is_409(as_agent: TestClient, case: Claim, session: Session) -> None:
    assert (
        as_agent.post(
            f"/api/agent/cases/{case.id}/decision", json=_decision_body("approve")
        ).status_code
        == 200
    )
    second = as_agent.post(f"/api/agent/cases/{case.id}/decision", json=_decision_body("reject"))
    assert second.status_code == 409
    assert "terminal state" in second.json()["detail"]

    session.expire_all()
    claim = session.get(Claim, case.id)
    assert claim is not None and claim.state is ClaimState.APPROVED  # first decision stands
    notifications = session.scalars(
        select(Notification).where(Notification.claim_id == case.id)
    ).all()
    assert len(notifications) == 1  # the rejected second attempt sent nothing


def test_non_agent_roles_cannot_use_agent_endpoints(
    client: TestClient, case: Claim, session: Session, users: dict[str, User]
) -> None:
    for email in ("claimant@demo.ca", "imaging@demo.ca", "specialist@demo.ca"):
        login(client, email)
        assert client.get(f"/api/agent/cases/{case.id}/dossier").status_code == 403
        assert (
            client.post(
                f"/api/agent/cases/{case.id}/draft-email", json={"decision": "APPROVED"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/agent/cases/{case.id}/decision", json=_decision_body("approve")
            ).status_code
            == 403
        )

    session.expire_all()
    claim = session.get(Claim, case.id)
    assert claim is not None and claim.state is ClaimState.ADJUDICATION  # untouched
    assert session.scalar(select(Notification).where(Notification.claim_id == case.id)) is None
