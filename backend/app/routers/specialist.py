"""Specialist work queues and case actions (imaging review and specialist review)."""

import json
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import enforce_origin, get_current_user, get_settings_dep, require_role
from app.config import Settings
from app.db import get_db
from app.models import (
    AdjudicationSummary,
    ArtifactStatus,
    Claim,
    ClaimAction,
    ClaimState,
    DiagnosticReport,
    Document,
    RecommendationNote,
    Role,
    User,
)
from app.services import inference_runner
from app.services.notifications.service import render_claim_returned, send_claim_email
from app.workflow import state_machine
from app.workflow.state_machine import TransitionError

router = APIRouter()

_ACTIVE_STATUSES = (ArtifactStatus.PENDING, ArtifactStatus.RUNNING)

_REGENERATE_ROLES: dict[str, frozenset[Role]] = {
    "imaging": frozenset({Role.IMAGING_SPECIALIST}),
    "recommendation": frozenset({Role.MEDICAL_SPECIALIST}),
    "adjudication": frozenset({Role.MEDICAL_SPECIALIST, Role.INSURANCE_AGENT}),
}


class QueueItem(BaseModel):
    claim_id: int
    claim_ref: str
    claim_type: str
    claimant: str
    state: str
    submitted_at: str
    report_status: str | None
    modality: str | None
    authenticity_verdict: str | None
    authenticity_risk: float | None
    requires_mandatory_review: bool | None


class RecommendationQueueItem(BaseModel):
    claim_id: int
    claim_ref: str
    claim_type: str
    claimant: str
    state: str
    submitted_at: str
    note_status: str | None
    recommendation: str | None
    confidence: float | None
    requires_mandatory_review: bool | None


class CaseDocument(BaseModel):
    id: int
    filename: str
    kind: str
    modality: str | None
    has_preview: bool


class CaseReport(BaseModel):
    id: int
    status: str
    modality: str | None
    modality_confidence: float | None
    authenticity_verdict: str | None
    authenticity_risk: float | None
    requires_mandatory_review: bool
    payload: dict | None
    generated_by: str
    fallback_reason: str | None
    error: str | None


class CaseNote(BaseModel):
    id: int
    status: str
    recommendation: str | None
    confidence: float | None
    requires_mandatory_review: bool
    payload: dict | None
    generated_by: str
    fallback_reason: str | None
    error: str | None


class CaseDetail(BaseModel):
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
    claimant: str
    documents: list[CaseDocument]
    diagnostic_reports: list[CaseReport]
    recommendation_notes: list[CaseNote]


class NoteBody(BaseModel):
    note: str = Field(min_length=1)


class RegenerateBody(BaseModel):
    stage: Literal["imaging", "recommendation", "adjudication"]


class ForwardOut(BaseModel):
    claim_id: int
    state: str
    note_id: int
    note_status: str


class ReturnOut(BaseModel):
    claim_id: int
    state: str
    notification_id: int


class SendToInsurerOut(BaseModel):
    claim_id: int
    state: str
    summary_id: int
    summary_status: str


class RegenerateOut(BaseModel):
    claim_id: int
    stage: str
    artifact_id: int
    status: str


def _parsed(payload_json: str | None) -> dict | None:
    return json.loads(payload_json) if payload_json else None


def _get_claim_or_404(session: Session, claim_id: int) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def _transition_or_409(
    session: Session,
    claim: Claim,
    action: ClaimAction,
    *,
    actor: User,
    note: str | None = None,
) -> None:
    try:
        state_machine.apply_transition(session, claim, action, actor=actor, note=note)
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc


def _latest_report(session: Session, claim_id: int) -> DiagnosticReport | None:
    return session.scalar(
        select(DiagnosticReport)
        .where(DiagnosticReport.claim_id == claim_id)
        .order_by(DiagnosticReport.id.desc())
        .limit(1)
    )


def _latest_note(session: Session, claim_id: int) -> RecommendationNote | None:
    return session.scalar(
        select(RecommendationNote)
        .where(RecommendationNote.claim_id == claim_id)
        .order_by(RecommendationNote.id.desc())
        .limit(1)
    )


def _latest_summary(session: Session, claim_id: int) -> AdjudicationSummary | None:
    return session.scalar(
        select(AdjudicationSummary)
        .where(AdjudicationSummary.claim_id == claim_id)
        .order_by(AdjudicationSummary.id.desc())
        .limit(1)
    )


def _require(user: User, *roles: Role) -> None:
    if user.role not in roles:
        raise HTTPException(status_code=403, detail="Insufficient role")


def _claims_in_state(session: Session, state: ClaimState) -> list[Claim]:
    return list(
        session.scalars(
            select(Claim)
            .where(Claim.state == state)
            .order_by(Claim.updated_at.asc(), Claim.id.asc())
        ).all()
    )


def _send_returned_email(
    session: Session, settings: Settings, claim: Claim, reason: str
) -> int:
    """Render the claim-returned email to the claimant and persist the Notification.

    Also reused by request-further-testing: the template wording ("we need more
    information before we can continue") is generic enough for both flows.
    """
    name_parts = claim.claimant.full_name.split()
    first_name = name_parts[0] if name_parts else claim.claimant.full_name
    subject, body_text = render_claim_returned(
        claim.claim_ref, reason=reason, first_name=first_name
    )
    notification = send_claim_email(
        session,
        settings,
        claim=claim,
        recipient=claim.claimant,
        subject=subject,
        body_text=body_text,
    )
    return notification.id


@router.get("/queue")
def work_queue(
    stage: Literal["imaging", "recommendation"] = "imaging",
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[QueueItem] | list[RecommendationQueueItem]:
    """Per-stage work queue; the role gate matches the requested stage."""
    if stage == "imaging":
        _require(user, Role.IMAGING_SPECIALIST)
        items: list[QueueItem] = []
        for claim in _claims_in_state(session, ClaimState.IMAGING_REVIEW):
            report = _latest_report(session, claim.id)
            items.append(
                QueueItem(
                    claim_id=claim.id,
                    claim_ref=claim.claim_ref,
                    claim_type=claim.claim_type,
                    claimant=claim.claimant.full_name,
                    state=claim.state.value,
                    submitted_at=claim.created_at.isoformat(),
                    report_status=report.status.value if report is not None else None,
                    modality=report.modality if report is not None else None,
                    authenticity_verdict=(
                        report.authenticity_verdict if report is not None else None
                    ),
                    authenticity_risk=(
                        report.authenticity_risk if report is not None else None
                    ),
                    requires_mandatory_review=(
                        report.requires_mandatory_review if report is not None else None
                    ),
                )
            )
        return items

    _require(user, Role.MEDICAL_SPECIALIST)
    rec_items: list[RecommendationQueueItem] = []
    for claim in _claims_in_state(session, ClaimState.SPECIALIST_REVIEW):
        note = _latest_note(session, claim.id)
        rec_items.append(
            RecommendationQueueItem(
                claim_id=claim.id,
                claim_ref=claim.claim_ref,
                claim_type=claim.claim_type,
                claimant=claim.claimant.full_name,
                state=claim.state.value,
                submitted_at=claim.created_at.isoformat(),
                note_status=note.status.value if note is not None else None,
                recommendation=(
                    note.recommendation.value
                    if note is not None and note.recommendation is not None
                    else None
                ),
                confidence=note.confidence if note is not None else None,
                requires_mandatory_review=(
                    note.requires_mandatory_review if note is not None else None
                ),
            )
        )
    return rec_items


@router.get("/cases/{claim_id}")
def case_detail(
    claim_id: int,
    user: User = Depends(require_role(Role.IMAGING_SPECIALIST, Role.MEDICAL_SPECIALIST)),
    session: Session = Depends(get_db),
) -> CaseDetail:
    claim = _get_claim_or_404(session, claim_id)
    documents = session.scalars(
        select(Document).where(Document.claim_id == claim.id).order_by(Document.id.asc())
    ).all()
    reports = session.scalars(
        select(DiagnosticReport)
        .where(DiagnosticReport.claim_id == claim.id)
        .order_by(DiagnosticReport.id.desc())
    ).all()
    notes = session.scalars(
        select(RecommendationNote)
        .where(RecommendationNote.claim_id == claim.id)
        .order_by(RecommendationNote.id.desc())
    ).all()
    return CaseDetail(
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
        claimant=claim.claimant.full_name,
        documents=[
            CaseDocument(
                id=d.id,
                filename=d.filename,
                kind=d.kind.value,
                modality=d.modality.value if d.modality else None,
                has_preview=d.preview_path is not None,
            )
            for d in documents
        ],
        diagnostic_reports=[
            CaseReport(
                id=r.id,
                status=r.status.value,
                modality=r.modality,
                modality_confidence=r.modality_confidence,
                authenticity_verdict=r.authenticity_verdict,
                authenticity_risk=r.authenticity_risk,
                requires_mandatory_review=r.requires_mandatory_review,
                payload=_parsed(r.payload_json),
                generated_by=r.generated_by,
                fallback_reason=r.fallback_reason,
                error=r.error,
            )
            for r in reports
        ],
        recommendation_notes=[
            CaseNote(
                id=n.id,
                status=n.status.value,
                recommendation=n.recommendation.value if n.recommendation else None,
                confidence=n.confidence,
                requires_mandatory_review=n.requires_mandatory_review,
                payload=_parsed(n.payload_json),
                generated_by=n.generated_by,
                fallback_reason=n.fallback_reason,
                error=n.error,
            )
            for n in notes
        ],
    )


@router.post("/cases/{claim_id}/forward", dependencies=[Depends(enforce_origin)])
def forward_case(
    claim_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_role(Role.IMAGING_SPECIALIST)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ForwardOut:
    claim = _get_claim_or_404(session, claim_id)
    existing = _latest_note(session, claim.id)
    if existing is not None and existing.status in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"a recommendation note is already {existing.status.value} for this claim",
        )
    _transition_or_409(session, claim, ClaimAction.FORWARD, actor=user)
    note = RecommendationNote(claim_id=claim.id, status=ArtifactStatus.PENDING)
    session.add(note)
    session.flush()
    session.commit()
    inference_runner.schedule_stage2(background_tasks, settings, note.id)
    return ForwardOut(
        claim_id=claim.id,
        state=claim.state.value,
        note_id=note.id,
        note_status=note.status.value,
    )


@router.post("/cases/{claim_id}/return", dependencies=[Depends(enforce_origin)])
def return_case(
    claim_id: int,
    body: NoteBody,
    user: User = Depends(require_role(Role.IMAGING_SPECIALIST)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ReturnOut:
    claim = _get_claim_or_404(session, claim_id)
    _transition_or_409(
        session, claim, ClaimAction.RETURN_TO_CLAIMANT, actor=user, note=body.note
    )
    notification_id = _send_returned_email(session, settings, claim, body.note)
    session.commit()
    return ReturnOut(
        claim_id=claim.id, state=claim.state.value, notification_id=notification_id
    )


@router.post("/cases/{claim_id}/send-to-insurer", dependencies=[Depends(enforce_origin)])
def send_to_insurer(
    claim_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_role(Role.MEDICAL_SPECIALIST)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> SendToInsurerOut:
    claim = _get_claim_or_404(session, claim_id)
    existing = _latest_summary(session, claim.id)
    if existing is not None and existing.status in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"an adjudication summary is already {existing.status.value} for this claim",
        )
    _transition_or_409(session, claim, ClaimAction.SEND_TO_INSURER, actor=user)
    summary = AdjudicationSummary(claim_id=claim.id, status=ArtifactStatus.PENDING)
    session.add(summary)
    session.flush()
    session.commit()
    inference_runner.schedule_stage3(background_tasks, settings, summary.id)
    return SendToInsurerOut(
        claim_id=claim.id,
        state=claim.state.value,
        summary_id=summary.id,
        summary_status=summary.status.value,
    )


@router.post(
    "/cases/{claim_id}/request-further-testing", dependencies=[Depends(enforce_origin)]
)
def request_further_testing(
    claim_id: int,
    body: NoteBody,
    user: User = Depends(require_role(Role.MEDICAL_SPECIALIST)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ReturnOut:
    """Send the claim back for more evidence; notifies the claimant with the
    claim-returned email template (its wording covers both return flows)."""
    claim = _get_claim_or_404(session, claim_id)
    _transition_or_409(
        session, claim, ClaimAction.REQUEST_FURTHER_TESTING, actor=user, note=body.note
    )
    notification_id = _send_returned_email(session, settings, claim, body.note)
    session.commit()
    return ReturnOut(
        claim_id=claim.id, state=claim.state.value, notification_id=notification_id
    )


@router.post("/cases/{claim_id}/regenerate", dependencies=[Depends(enforce_origin)])
def regenerate_artifact(
    claim_id: int,
    body: RegenerateBody,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> RegenerateOut:
    """Re-run the latest FAILED artifact of a stage (role must match the stage)."""
    _require(user, *_REGENERATE_ROLES[body.stage])
    claim = _get_claim_or_404(session, claim_id)
    artifact: DiagnosticReport | RecommendationNote | AdjudicationSummary | None
    if body.stage == "imaging":
        artifact = _latest_report(session, claim.id)
    elif body.stage == "recommendation":
        artifact = _latest_note(session, claim.id)
    else:
        artifact = _latest_summary(session, claim.id)
    if artifact is None or artifact.status is not ArtifactStatus.FAILED:
        status = artifact.status.value if artifact is not None else "missing"
        raise HTTPException(
            status_code=409,
            detail=f"latest {body.stage} artifact is {status}; only failed runs regenerate",
        )
    artifact.status = ArtifactStatus.PENDING
    artifact.error = None
    session.commit()
    if body.stage == "imaging":
        inference_runner.schedule_stage1(background_tasks, settings, artifact.id)
    elif body.stage == "recommendation":
        inference_runner.schedule_stage2(background_tasks, settings, artifact.id)
    else:
        inference_runner.schedule_stage3(background_tasks, settings, artifact.id)
    return RegenerateOut(
        claim_id=claim.id,
        stage=body.stage,
        artifact_id=artifact.id,
        status=artifact.status.value,
    )
