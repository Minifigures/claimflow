"""Claimant-facing claim lifecycle: create, analyze, resubmit, list, detail, timeline."""

import json
import uuid
from datetime import date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import enforce_origin, get_current_user, get_settings_dep, require_role
from app.claimguard import audit
from app.config import Settings
from app.db import get_db
from app.models import (
    ArtifactStatus,
    AuditEventType,
    Claim,
    ClaimAction,
    ClaimState,
    Decision,
    DiagnosticReport,
    Document,
    DocumentKind,
    Role,
    User,
)
from app.services import inference_runner
from app.workflow import state_machine
from app.workflow.state_machine import TransitionError

router = APIRouter()


class ClaimCreate(BaseModel):
    claim_type: str = Field(min_length=1, max_length=64)
    description: str
    procedure_code: str = Field(default="", max_length=16)
    diagnosis_code: str = Field(default="", max_length=16)
    incident_date: date | None = None
    amount_claimed: float = Field(ge=0)


class ClaimOut(BaseModel):
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


class DocumentSummary(BaseModel):
    id: int
    filename: str
    kind: str
    modality: str | None
    has_preview: bool


class ReportOut(BaseModel):
    id: int
    status: str
    modality: str | None
    authenticity_verdict: str | None
    authenticity_risk: float | None
    requires_mandatory_review: bool
    payload: dict | None
    error: str | None


class ClaimDetail(ClaimOut):
    documents: list[DocumentSummary]
    diagnostic_report: ReportOut | None


class TimelineEntry(BaseModel):
    action: str
    from_state: str | None
    to_state: str
    actor_role: str
    note: str | None
    created_at: datetime


class AnalyzeOut(BaseModel):
    report_id: int
    status: str


class ResubmitRequest(BaseModel):
    note: str


class ResubmitOut(BaseModel):
    state: str
    report_id: int | None
    report_status: str | None


def _claim_out(claim: Claim) -> ClaimOut:
    return ClaimOut(
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
    )


def _report_out(report: DiagnosticReport) -> ReportOut:
    return ReportOut(
        id=report.id,
        status=report.status.value,
        modality=report.modality,
        authenticity_verdict=report.authenticity_verdict,
        authenticity_risk=report.authenticity_risk,
        requires_mandatory_review=report.requires_mandatory_review,
        payload=json.loads(report.payload_json) if report.payload_json else None,
        error=report.error,
    )


def _get_owned_claim(session: Session, claim_id: int, user: User) -> Claim:
    """Claimant-only access: 404 (not 403) so foreign claim ids are not discoverable."""
    claim = session.get(Claim, claim_id)
    if claim is None or claim.claimant_id != user.id:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def _get_viewable_claim(session: Session, claim_id: int, user: User) -> Claim:
    """Owner or any staff role may view; other claimants get 404."""
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    if user.role is Role.CLAIMANT and claim.claimant_id != user.id:
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim


def _transition_or_409(
    session: Session,
    claim: Claim,
    action: ClaimAction,
    *,
    actor: User | None,
    note: str | None = None,
) -> Decision:
    try:
        return state_machine.apply_transition(session, claim, action, actor=actor, note=note)
    except TransitionError as exc:
        raise HTTPException(status_code=409, detail=exc.reason) from exc


def _latest_imaging_document(session: Session, claim_id: int) -> Document | None:
    return session.scalar(
        select(Document)
        .where(Document.claim_id == claim_id, Document.kind == DocumentKind.IMAGING)
        .order_by(Document.id.desc())
        .limit(1)
    )


def _ensure_no_active_report(session: Session, claim_id: int) -> None:
    active = session.scalar(
        select(DiagnosticReport)
        .where(
            DiagnosticReport.claim_id == claim_id,
            DiagnosticReport.status.in_([ArtifactStatus.PENDING, ArtifactStatus.RUNNING]),
        )
        .limit(1)
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"a diagnostic report is already {active.status.value} for this claim",
        )


def _create_report(session: Session, claim: Claim, document: Document) -> DiagnosticReport:
    report = DiagnosticReport(
        claim_id=claim.id,
        document_id=document.id,
        status=ArtifactStatus.PENDING,
    )
    session.add(report)
    session.flush()
    return report


@router.post("", status_code=201, dependencies=[Depends(enforce_origin)])
def create_claim(
    body: ClaimCreate,
    user: User = Depends(require_role(Role.CLAIMANT)),
    session: Session = Depends(get_db),
) -> ClaimOut:
    claim = Claim(
        claim_ref=f"CLM-{uuid.uuid4().hex[:8].upper()}",
        claimant_id=user.id,
        claim_type=body.claim_type,
        description=body.description,
        procedure_code=body.procedure_code,
        diagnosis_code=body.diagnosis_code,
        incident_date=body.incident_date,
        amount_claimed=body.amount_claimed,
        state=ClaimState.SUBMITTED,
    )
    session.add(claim)
    session.flush()
    state_machine.record_initial_submit(session, claim, user)
    audit.append(
        session,
        AuditEventType.CLAIM_SUBMIT,
        claim_id=claim.id,
        actor_user_id=user.id,
        actor_role=user.role.value,
        payload={
            "claim_ref": claim.claim_ref,
            "claim_type": claim.claim_type,
            "amount_claimed": claim.amount_claimed,
        },
    )
    session.commit()
    return _claim_out(claim)


@router.post("/{claim_id}/analyze", dependencies=[Depends(enforce_origin)])
def analyze_claim(
    claim_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_role(Role.CLAIMANT)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> AnalyzeOut:
    claim = _get_owned_claim(session, claim_id, user)
    if claim.state is not ClaimState.SUBMITTED:
        raise HTTPException(
            status_code=409,
            detail=f"analysis requires state SUBMITTED; claim is {claim.state.value}",
        )
    _ensure_no_active_report(session, claim.id)
    document = _latest_imaging_document(session, claim.id)
    if document is None:
        raise HTTPException(
            status_code=422, detail="claim has no imaging document; upload one before analyzing"
        )
    report = _create_report(session, claim, document)
    session.commit()
    inference_runner.schedule_stage1(background_tasks, settings, report.id)
    return AnalyzeOut(report_id=report.id, status=report.status.value)


@router.post("/{claim_id}/resubmit", dependencies=[Depends(enforce_origin)])
def resubmit_claim(
    claim_id: int,
    body: ResubmitRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_role(Role.CLAIMANT)),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ResubmitOut:
    claim = _get_owned_claim(session, claim_id, user)
    _transition_or_409(session, claim, ClaimAction.RESUBMIT, actor=user, note=body.note)
    report: DiagnosticReport | None = None
    document = _latest_imaging_document(session, claim.id)
    if document is not None:
        _ensure_no_active_report(session, claim.id)
        report = _create_report(session, claim, document)
    session.commit()
    if report is not None:
        inference_runner.schedule_stage1(background_tasks, settings, report.id)
    return ResubmitOut(
        state=claim.state.value,
        report_id=report.id if report is not None else None,
        report_status=report.status.value if report is not None else None,
    )


@router.get("")
def list_claims(
    user: User = Depends(require_role(Role.CLAIMANT)),
    session: Session = Depends(get_db),
) -> list[ClaimOut]:
    claims = session.scalars(
        select(Claim)
        .where(Claim.claimant_id == user.id)
        .order_by(Claim.created_at.desc(), Claim.id.desc())
    ).all()
    return [_claim_out(c) for c in claims]


@router.get("/{claim_id}")
def get_claim(
    claim_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ClaimDetail:
    claim = _get_viewable_claim(session, claim_id, user)
    documents = session.scalars(
        select(Document).where(Document.claim_id == claim.id).order_by(Document.id.asc())
    ).all()
    report = session.scalar(
        select(DiagnosticReport)
        .where(DiagnosticReport.claim_id == claim.id)
        .order_by(DiagnosticReport.id.desc())
        .limit(1)
    )
    return ClaimDetail(
        **_claim_out(claim).model_dump(),
        documents=[
            DocumentSummary(
                id=d.id,
                filename=d.filename,
                kind=d.kind.value,
                modality=d.modality.value if d.modality else None,
                has_preview=d.preview_path is not None,
            )
            for d in documents
        ],
        diagnostic_report=_report_out(report) if report is not None else None,
    )


@router.get("/{claim_id}/timeline")
def get_timeline(
    claim_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> list[TimelineEntry]:
    claim = _get_viewable_claim(session, claim_id, user)
    decisions = session.scalars(
        select(Decision).where(Decision.claim_id == claim.id).order_by(Decision.id.asc())
    ).all()
    return [
        TimelineEntry(
            action=d.action.value,
            from_state=d.from_state.value if d.from_state else None,
            to_state=d.to_state.value,
            actor_role=d.actor_role,
            note=d.note,
            created_at=d.created_at,
        )
        for d in decisions
    ]
