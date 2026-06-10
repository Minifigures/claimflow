"""Insurance-agent portal: adjudication queue, full case dossier, decision-email
drafting, and the atomic decide-and-notify endpoint.

The decision endpoint is the assessment's hard requirement: the agent's final decision
and the claimant notification email are ONE action — the workflow transition, the
Decision row, the audit events, and the Notification row all land in a single
transaction with one commit at the end. Precedent indexing (Chroma) is an external
side effect and is best-effort: its failure never fails the decision.
"""

import json
import logging
from datetime import date, datetime
from typing import Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import enforce_origin, get_settings_dep, require_role
from app.claimguard import audit
from app.config import Settings
from app.db import get_db
from app.llm import fallbacks
from app.llm.stages.claimant_email import draft_claimant_email
from app.models import (
    AdjudicationSummary,
    ArtifactStatus,
    AuditEventType,
    Claim,
    ClaimAction,
    ClaimHistory,
    ClaimState,
    Decision,
    DiagnosticReport,
    Document,
    Notification,
    RecommendationNote,
    Role,
    User,
)
from app.rag.indexer import index_closed_case
from app.services.notifications.service import send_claim_email
from app.workflow import state_machine
from app.workflow.state_machine import TERMINAL_STATES, TransitionError

router = APIRouter()
logger = logging.getLogger("claimflow.agent")

DOSSIER_STATES = TERMINAL_STATES | {ClaimState.ADJUDICATION}

_ArtifactT = TypeVar("_ArtifactT", DiagnosticReport, RecommendationNote, AdjudicationSummary)

# ---------------------------------------------------------------------- response models


class QueueItem(BaseModel):
    claim_id: int
    claim_ref: str
    claim_type: str
    claimant: str
    state: str
    submitted_at: str
    summary_status: str | None
    recommendation_lean: str | None
    confidence: float | None
    requires_mandatory_review: bool | None
    specialist_recommendation: str | None


class ClaimCore(BaseModel):
    id: int
    claim_ref: str
    claim_type: str
    description: str
    procedure_code: str
    diagnosis_code: str
    incident_date: date | None
    amount_claimed: float
    state: str
    created_at: datetime
    updated_at: datetime


class ClaimantInfo(BaseModel):
    full_name: str
    member_id: str | None
    preferred_language: str
    preferred_tone: str


class DossierDocument(BaseModel):
    id: int
    filename: str
    kind: str
    modality: str | None
    size_bytes: int
    has_preview: bool


class ArtifactOut(BaseModel):
    id: int
    status: str
    payload: dict | None
    generated_by: str
    fallback_reason: str | None
    requires_mandatory_review: bool
    created_at: datetime
    completed_at: datetime | None


class DiagnosticReportOut(ArtifactOut):
    modality: str | None
    modality_confidence: float | None
    authenticity_verdict: str | None
    authenticity_risk: float | None


class RecommendationNoteOut(ArtifactOut):
    recommendation: str | None
    confidence: float | None


class AdjudicationSummaryOut(ArtifactOut):
    recommendation_lean: str | None
    confidence: float | None


class HistoryRow(BaseModel):
    claim_type: str
    procedure_code: str
    diagnosis_code: str
    modality: str | None
    billed_amount: float
    outcome: str
    date_of_service: date | None
    decided_at: date | None


class HistoryStats(BaseModel):
    total: int
    approved: int
    rejected: int


class TimelineEntry(BaseModel):
    action: str
    from_state: str | None
    to_state: str
    actor_role: str
    note: str | None
    created_at: datetime


class NotificationOut(BaseModel):
    id: int
    subject: str
    body_text: str
    provider: str
    status: str
    created_at: datetime
    sent_at: datetime | None


class Dossier(BaseModel):
    claim: ClaimCore
    claimant: ClaimantInfo
    documents: list[DossierDocument]
    diagnostic_report: DiagnosticReportOut | None
    recommendation_note: RecommendationNoteOut | None
    adjudication_summary: AdjudicationSummaryOut | None
    claim_history: list[HistoryRow]
    history_stats: HistoryStats
    timeline: list[TimelineEntry]
    notifications: list[NotificationOut]


class DraftEmailRequest(BaseModel):
    decision: Literal["APPROVED", "REJECTED"]


class DraftEmailOut(BaseModel):
    subject: str
    greeting: str
    body_paragraphs: list[str]
    closing: str
    generated_by: str
    fallback_reason: str | None


class DecisionRequest(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None
    email_subject: str = ""
    email_body_text: str = ""


class DecisionOut(BaseModel):
    state: str
    notification_id: int
    notification_status: str
    case_ref: str | None


# ---------------------------------------------------------------------------- helpers


def _get_claim_or_404(session: Session, claim_id: int) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def _latest_artifact(
    session: Session, model: type[_ArtifactT], claim_id: int
) -> _ArtifactT | None:
    """Latest COMPLETE artifact, or the latest of any status when none is complete."""
    complete = session.scalar(
        select(model)
        .where(model.claim_id == claim_id, model.status == ArtifactStatus.COMPLETE)
        .order_by(model.id.desc())
        .limit(1)
    )
    if complete is not None:
        return complete
    return session.scalar(
        select(model).where(model.claim_id == claim_id).order_by(model.id.desc()).limit(1)
    )


def _artifact_payload(payload_json: str | None) -> dict | None:
    return json.loads(payload_json) if payload_json else None


def _first_name(full_name: str) -> str:
    parts = full_name.split()
    return parts[0] if parts else full_name


def _language(value: str) -> Literal["en", "fr"]:
    return "fr" if value == "fr" else "en"


def _tone(value: str) -> Literal["formal", "plain_language"]:
    return "formal" if value == "formal" else "plain_language"


def _fallback_email_text(claim: Claim, decision: Literal["APPROVED", "REJECTED"]) -> tuple[str, str]:
    """Server-side email fallback (deterministic template; the email never blocks the
    decision). Returns ``(subject, body_text)``."""
    claimant = claim.claimant
    draft = fallbacks.fallback_claimant_email(
        decision=decision,
        first_name=_first_name(claimant.full_name),
        language=_language(claimant.preferred_language),
        tone=_tone(claimant.preferred_tone),
        claim_ref=claim.claim_ref,
        claim_type=claim.claim_type,
    )
    body_text = "\n\n".join([draft.greeting, *draft.body_paragraphs, draft.closing])
    return draft.subject, body_text


def _key_findings(note: RecommendationNote | None) -> list[str]:
    """PII-free excerpt of the specialist note summary for the precedent index."""
    if note is None or not note.payload_json:
        return []
    payload = json.loads(note.payload_json)
    summary = str(payload.get("summary") or payload.get("impression") or "").strip()
    return [summary[:300]] if summary else []


# -------------------------------------------------------------------------- endpoints


@router.get("/queue", dependencies=[Depends(require_role(Role.INSURANCE_AGENT))])
def adjudication_queue(session: Session = Depends(get_db)) -> list[QueueItem]:
    claims = session.scalars(
        select(Claim)
        .where(Claim.state == ClaimState.ADJUDICATION)
        .order_by(Claim.updated_at.asc(), Claim.id.asc())
    ).all()
    items: list[QueueItem] = []
    for claim in claims:
        summary = _latest_artifact(session, AdjudicationSummary, claim.id)
        note = _latest_artifact(session, RecommendationNote, claim.id)
        items.append(
            QueueItem(
                claim_id=claim.id,
                claim_ref=claim.claim_ref,
                claim_type=claim.claim_type,
                claimant=claim.claimant.full_name,
                state=claim.state.value,
                submitted_at=claim.created_at.isoformat(),
                summary_status=summary.status.value if summary is not None else None,
                recommendation_lean=summary.recommendation_lean if summary is not None else None,
                confidence=summary.confidence if summary is not None else None,
                requires_mandatory_review=(
                    summary.requires_mandatory_review if summary is not None else None
                ),
                specialist_recommendation=(
                    note.recommendation.value
                    if note is not None and note.recommendation is not None
                    else None
                ),
            )
        )
    return items


@router.get(
    "/cases/{claim_id}/dossier",
    dependencies=[Depends(require_role(Role.INSURANCE_AGENT))],
)
def case_dossier(claim_id: int, session: Session = Depends(get_db)) -> Dossier:
    claim = _get_claim_or_404(session, claim_id)
    if claim.state not in DOSSIER_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                "dossier requires ADJUDICATION or a terminal state; "
                f"claim is {claim.state.value}"
            ),
        )
    claimant = claim.claimant

    documents = session.scalars(
        select(Document).where(Document.claim_id == claim.id).order_by(Document.id.asc())
    ).all()

    report = _latest_artifact(session, DiagnosticReport, claim.id)
    note = _latest_artifact(session, RecommendationNote, claim.id)
    summary = _latest_artifact(session, AdjudicationSummary, claim.id)

    history_rows: list[ClaimHistory] = []
    outcomes: list[str] = []
    if claimant.member_id:
        history_rows = list(
            session.scalars(
                select(ClaimHistory)
                .where(ClaimHistory.member_id == claimant.member_id)
                .order_by(ClaimHistory.date_of_service.desc(), ClaimHistory.id.desc())
                .limit(25)
            ).all()
        )
        outcomes = list(
            session.scalars(
                select(ClaimHistory.outcome).where(ClaimHistory.member_id == claimant.member_id)
            ).all()
        )

    decisions = session.scalars(
        select(Decision).where(Decision.claim_id == claim.id).order_by(Decision.id.asc())
    ).all()
    notifications = session.scalars(
        select(Notification).where(Notification.claim_id == claim.id).order_by(Notification.id)
    ).all()

    def artifact_fields(artifact: DiagnosticReport | RecommendationNote | AdjudicationSummary):
        return {
            "id": artifact.id,
            "status": artifact.status.value,
            "payload": _artifact_payload(artifact.payload_json),
            "generated_by": artifact.generated_by,
            "fallback_reason": artifact.fallback_reason,
            "requires_mandatory_review": artifact.requires_mandatory_review,
            "created_at": artifact.created_at,
            "completed_at": artifact.completed_at,
        }

    return Dossier(
        claim=ClaimCore(
            id=claim.id,
            claim_ref=claim.claim_ref,
            claim_type=claim.claim_type,
            description=claim.description,
            procedure_code=claim.procedure_code,
            diagnosis_code=claim.diagnosis_code,
            incident_date=claim.incident_date,
            amount_claimed=claim.amount_claimed,
            state=claim.state.value,
            created_at=claim.created_at,
            updated_at=claim.updated_at,
        ),
        claimant=ClaimantInfo(
            full_name=claimant.full_name,
            member_id=claimant.member_id,
            preferred_language=claimant.preferred_language,
            preferred_tone=claimant.preferred_tone,
        ),
        documents=[
            DossierDocument(
                id=d.id,
                filename=d.filename,
                kind=d.kind.value,
                modality=d.modality.value if d.modality else None,
                size_bytes=d.size_bytes,
                has_preview=d.preview_path is not None,
            )
            for d in documents
        ],
        diagnostic_report=(
            DiagnosticReportOut(
                **artifact_fields(report),
                modality=report.modality,
                modality_confidence=report.modality_confidence,
                authenticity_verdict=report.authenticity_verdict,
                authenticity_risk=report.authenticity_risk,
            )
            if report is not None
            else None
        ),
        recommendation_note=(
            RecommendationNoteOut(
                **artifact_fields(note),
                recommendation=note.recommendation.value if note.recommendation else None,
                confidence=note.confidence,
            )
            if note is not None
            else None
        ),
        adjudication_summary=(
            AdjudicationSummaryOut(
                **artifact_fields(summary),
                recommendation_lean=summary.recommendation_lean,
                confidence=summary.confidence,
            )
            if summary is not None
            else None
        ),
        claim_history=[
            HistoryRow(
                claim_type=row.claim_type,
                procedure_code=row.procedure_code,
                diagnosis_code=row.diagnosis_code,
                modality=row.modality,
                billed_amount=row.billed_amount,
                outcome=row.outcome,
                date_of_service=row.date_of_service,
                decided_at=row.decided_at,
            )
            for row in history_rows
        ],
        history_stats=HistoryStats(
            total=len(outcomes),
            approved=sum(1 for o in outcomes if o == "approved"),
            rejected=sum(1 for o in outcomes if o == "rejected"),
        ),
        timeline=[
            TimelineEntry(
                action=d.action.value,
                from_state=d.from_state.value if d.from_state else None,
                to_state=d.to_state.value,
                actor_role=d.actor_role,
                note=d.note,
                created_at=d.created_at,
            )
            for d in decisions
        ],
        notifications=[
            NotificationOut(
                id=n.id,
                subject=n.subject,
                body_text=n.body_text,
                provider=n.provider,
                status=n.status.value,
                created_at=n.created_at,
                sent_at=n.sent_at,
            )
            for n in notifications
        ],
    )


@router.post("/cases/{claim_id}/draft-email", dependencies=[Depends(enforce_origin)])
def draft_decision_email(
    claim_id: int,
    body: DraftEmailRequest,
    user: User = Depends(require_role(Role.INSURANCE_AGENT)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DraftEmailOut:
    claim = _get_claim_or_404(session, claim_id)
    if claim.state is not ClaimState.ADJUDICATION:
        raise HTTPException(
            status_code=409,
            detail=f"email drafting requires state ADJUDICATION; claim is {claim.state.value}",
        )
    claimant = claim.claimant
    result = draft_claimant_email(
        settings,
        session,
        claim_id=claim.id,
        decision=body.decision,
        first_name=_first_name(claimant.full_name),
        language=_language(claimant.preferred_language),
        tone=_tone(claimant.preferred_tone),
        claim_ref=claim.claim_ref,
        claim_type=claim.claim_type,
    )
    # PII note: the audit payload carries NO draft text (subject/greeting/body can hold
    # the claimant's first name) — only the decision and generation provenance.
    audit.append(
        session,
        AuditEventType.EMAIL_DRAFTED,
        claim_id=claim.id,
        actor_user_id=user.id,
        actor_role=user.role.value,
        payload={
            "decision": body.decision,
            "generated_by": result.generated_by,
            "fallback_reason": result.fallback_reason,
        },
    )
    session.commit()
    payload = result.payload
    return DraftEmailOut(
        subject=payload["subject"],
        greeting=payload["greeting"],
        body_paragraphs=payload["body_paragraphs"],
        closing=payload["closing"],
        generated_by=result.generated_by,
        fallback_reason=result.fallback_reason,
    )


@router.post("/cases/{claim_id}/decision", dependencies=[Depends(enforce_origin)])
def decide_claim(
    claim_id: int,
    body: DecisionRequest,
    user: User = Depends(require_role(Role.INSURANCE_AGENT)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DecisionOut:
    """The atomic endpoint: transition + Decision row + audit + notification email are
    one transaction with a single commit. Chroma precedent indexing is external to the
    DB transaction (best-effort, logged on failure, never blocks the decision)."""
    claim = _get_claim_or_404(session, claim_id)
    action = ClaimAction.APPROVE if body.action == "approve" else ClaimAction.REJECT
    decision_value: Literal["APPROVED", "REJECTED"] = (
        "APPROVED" if action is ClaimAction.APPROVE else "REJECTED"
    )
    try:
        state_machine.apply_transition(session, claim, action, actor=user, note=body.note)
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc

    audit.append(
        session,
        AuditEventType.DECISION_APPROVE
        if action is ClaimAction.APPROVE
        else AuditEventType.DECISION_REJECT,
        claim_id=claim.id,
        actor_user_id=user.id,
        actor_role=user.role.value,
        payload={"note_present": bool(body.note)},
    )

    # The notification email NEVER blocks the decision: empty fields fall back to the
    # deterministic template, and send_claim_email never raises on delivery failure.
    subject = body.email_subject.strip()
    body_text = body.email_body_text.strip()
    if not subject or not body_text:
        fallback_subject, fallback_body = _fallback_email_text(claim, decision_value)
        subject = subject or fallback_subject
        body_text = body_text or fallback_body
    notification = send_claim_email(
        session,
        settings,
        claim=claim,
        recipient=claim.claimant,
        subject=subject,
        body_text=body_text,
    )

    case_ref: str | None = None
    try:
        report = _latest_artifact(session, DiagnosticReport, claim.id)
        note = _latest_artifact(session, RecommendationNote, claim.id)
        case_ref = index_closed_case(
            settings,
            claim_ref=claim.claim_ref,
            modality=report.modality if report is not None else None,
            claim_type=claim.claim_type,
            procedure_code=claim.procedure_code,
            diagnosis_code=claim.diagnosis_code,
            recommendation=(
                note.recommendation.value
                if note is not None and note.recommendation is not None
                else None
            ),
            key_findings=_key_findings(note),
            decision=decision_value,
        )
    except Exception:  # noqa: BLE001 — precedent indexing must never fail the decision
        logger.exception("precedent indexing failed for claim %s; decision proceeds", claim.id)

    session.commit()
    return DecisionOut(
        state=claim.state.value,
        notification_id=notification.id,
        notification_status=notification.status.value,
        case_ref=case_ref,
    )
