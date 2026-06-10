"""Specialist work queues. Day 2 scope: the imaging-review queue only."""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import require_role
from app.db import get_db
from app.models import Claim, ClaimState, DiagnosticReport, Role

router = APIRouter()


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


@router.get("/queue", dependencies=[Depends(require_role(Role.IMAGING_SPECIALIST))])
def imaging_queue(
    stage: Literal["imaging"] = "imaging",
    session: Session = Depends(get_db),
) -> list[QueueItem]:
    claims = session.scalars(
        select(Claim)
        .where(Claim.state == ClaimState.IMAGING_REVIEW)
        .order_by(Claim.updated_at.asc(), Claim.id.asc())
    ).all()
    items: list[QueueItem] = []
    for claim in claims:
        report = session.scalar(
            select(DiagnosticReport)
            .where(DiagnosticReport.claim_id == claim.id)
            .order_by(DiagnosticReport.id.desc())
            .limit(1)
        )
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
                authenticity_verdict=report.authenticity_verdict if report is not None else None,
                authenticity_risk=report.authenticity_risk if report is not None else None,
                requires_mandatory_review=(
                    report.requires_mandatory_review if report is not None else None
                ),
            )
        )
    return items
