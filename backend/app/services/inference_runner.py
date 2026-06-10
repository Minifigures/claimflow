"""Stage-1 background inference: pending -> running -> complete|failed.

Runs after the HTTP response via FastAPI BackgroundTasks, in its own DB session.
On success it applies the system `imaging_complete` transition (which itself
audits). On any failure the artifact is marked failed with the error preserved —
the UI offers regenerate. Orphaned `running` artifacts are failed at startup.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI

from app import db
from app.claimguard import audit
from app.config import Settings
from app.ml.base import get_analyzer
from app.models import (
    ArtifactStatus,
    AuditEventType,
    Claim,
    ClaimAction,
    ClaimState,
    DiagnosticReport,
    Document,
)
from app.workflow.state_machine import apply_transition

logger = logging.getLogger("claimflow.inference")

ANALYZE_TIMEOUT_S = 30.0


def _build_report_payload(analysis, document: Document) -> dict:
    """Template diagnostic report from analyzer output (stage-1c VLM enriches this
    on the keyed path — Day 3)."""
    return {
        "modality_assessment": analysis.modality,
        "anatomical_region": "as submitted",
        "image_quality": "degraded" if analysis.quality_flags else "adequate",
        "quality_issues": analysis.quality_flags,
        "findings": [],
        "impression": (
            f"Automated draft: {analysis.modality.upper()} study classified with "
            f"{analysis.modality_confidence:.0%} confidence; authenticity verdict "
            f"'{analysis.authenticity_verdict}' (risk {analysis.authenticity_risk:.2f}). "
            "Specialist must perform full read."
        ),
        "authenticity": {
            "verdict": analysis.authenticity_verdict,
            "risk_score": analysis.authenticity_risk,
            "signals": [s.model_dump() for s in analysis.signals],
        },
        "source_document": document.filename,
        "disclaimer": (
            "Draft generated for specialist review. Not a diagnosis. A licensed imaging "
            "specialist must review and approve before this report is used."
        ),
    }


async def run_stage1(settings: Settings, report_id: int) -> None:
    factory = db.get_session_factory()
    session = factory()
    try:
        report = session.get(DiagnosticReport, report_id)
        if report is None or report.status not in (ArtifactStatus.PENDING, ArtifactStatus.FAILED):
            return
        claim = session.get(Claim, report.claim_id)
        document = session.get(Document, report.document_id) if report.document_id else None
        if claim is None or document is None:
            return

        report.status = ArtifactStatus.RUNNING
        session.commit()

        try:
            analyzer = get_analyzer(settings)
            dicom_meta = json.loads(document.dicom_meta_json) if document.dicom_meta_json else None
            from pathlib import Path

            analysis = await asyncio.wait_for(
                asyncio.to_thread(
                    analyzer.analyze,
                    Path(document.storage_path),
                    declared_modality=document.modality.value if document.modality else None,
                    dicom_meta=dicom_meta,
                ),
                timeout=ANALYZE_TIMEOUT_S,
            )
        except Exception as exc:
            report.status = ArtifactStatus.FAILED
            report.error = f"{type(exc).__name__}: {exc}"
            session.commit()
            logger.exception("stage-1 inference failed for report %s", report_id)
            return

        report.payload_json = json.dumps(_build_report_payload(analysis, document))
        report.modality = analysis.modality
        report.modality_confidence = analysis.modality_confidence
        report.authenticity_verdict = analysis.authenticity_verdict
        report.authenticity_risk = analysis.authenticity_risk
        report.generated_by = f"{analysis.backend}-analyzer"
        report.requires_mandatory_review = analysis.authenticity_verdict != "authentic"
        report.status = ArtifactStatus.COMPLETE
        report.completed_at = datetime.now(timezone.utc)

        audit.append(
            session,
            AuditEventType.LLM_CALL,
            claim_id=claim.id,
            actor_role="system",
            payload={
                "stage": "imaging_analysis",
                "backend": analysis.backend,
                "modality": analysis.modality,
                "authenticity_verdict": analysis.authenticity_verdict,
            },
        )
        if claim.state == ClaimState.SUBMITTED:
            apply_transition(session, claim, ClaimAction.IMAGING_COMPLETE, actor=None)
        session.commit()
    finally:
        session.close()


def schedule_stage1(background_tasks, settings: Settings, report_id: int) -> None:
    background_tasks.add_task(run_stage1, settings, report_id)


def recover_orphans(app: FastAPI) -> int:
    """Mark artifacts stuck in `running` (crash mid-task) as failed; called at startup."""
    factory = db.get_session_factory()
    session = factory()
    try:
        from sqlalchemy import select

        orphans = session.scalars(
            select(DiagnosticReport).where(DiagnosticReport.status == ArtifactStatus.RUNNING)
        ).all()
        for report in orphans:
            report.status = ArtifactStatus.FAILED
            report.error = "orphaned at startup (process restarted mid-run)"
        session.commit()
        return len(orphans)
    finally:
        session.close()
