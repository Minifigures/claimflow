"""Background inference runners for the three ML/LLM stages.

Each runner follows the same contract: it executes after the HTTP response via
FastAPI BackgroundTasks, opens its own DB session, walks the artifact through
pending -> running -> complete|failed, and never raises. The LLM stage functions
self-fall-back to deterministic templates when keyless, so a failed artifact
always means an unexpected error (preserved on the row for the regenerate UI).
Orphaned `running` artifacts are failed at startup.
"""

import asyncio
import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI
from sqlalchemy import select

from app import db
from app.claimguard import audit
from app.config import Settings
from app.llm.documents import extract_pdf_text
from app.llm.stages.stage1_diagnostic import generate_diagnostic_report
from app.llm.stages.stage2_recommendation import generate_recommendation
from app.llm.stages.stage3_adjudication import generate_adjudication
from app.ml.base import get_analyzer
from app.models import (
    AdjudicationSummary,
    ArtifactStatus,
    AuditEventType,
    Claim,
    ClaimAction,
    ClaimHistory,
    ClaimState,
    DiagnosticReport,
    Document,
    DocumentKind,
    Recommendation,
    RecommendationNote,
)
from app.rag.anonymizer import build_case_summary, make_case_ref
from app.rag.indexer import index_case_document
from app.rag.retriever import find_similar_cases, get_case_documents
from app.workflow.state_machine import apply_transition

logger = logging.getLogger("claimflow.inference")

ANALYZE_TIMEOUT_S = 30.0

_RUNNABLE = (ArtifactStatus.PENDING, ArtifactStatus.FAILED)

# Expected imaging modality by procedure-code prefix; None when no prefix matches
# (or the claim is not an imaging claim) — the stage-2 rule engine treats that as
# "no expected modality on file".
PROCEDURE_MODALITY: dict[str, str] = {"IMG-2": "xray", "IMG-3": "ct", "IMG-4": "mri"}

_IMPRESSION_EXCERPT_CHARS = 200
_HISTORY_LIMIT = 25
_RECENT_WINDOW_DAYS = 365


def _expected_modality(claim_type: str, procedure_code: str) -> str | None:
    if claim_type != "imaging":
        return None
    for prefix, modality in PROCEDURE_MODALITY.items():
        if procedure_code.startswith(prefix):
            return modality
    return None


def _latest_complete_report(session, claim_id: int) -> DiagnosticReport | None:
    return session.scalar(
        select(DiagnosticReport)
        .where(
            DiagnosticReport.claim_id == claim_id,
            DiagnosticReport.status == ArtifactStatus.COMPLETE,
        )
        .order_by(DiagnosticReport.id.desc())
        .limit(1)
    )


def _latest_complete_note(session, claim_id: int) -> RecommendationNote | None:
    return session.scalar(
        select(RecommendationNote)
        .where(
            RecommendationNote.claim_id == claim_id,
            RecommendationNote.status == ArtifactStatus.COMPLETE,
        )
        .order_by(RecommendationNote.id.desc())
        .limit(1)
    )


def _payload_or_empty(payload_json: str | None) -> dict:
    return json.loads(payload_json) if payload_json else {}


async def run_stage1(settings: Settings, report_id: int) -> None:
    factory = db.get_session_factory()
    session = factory()
    try:
        report = session.get(DiagnosticReport, report_id)
        if report is None or report.status not in _RUNNABLE:
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
            analysis = await asyncio.wait_for(
                asyncio.to_thread(
                    analyzer.analyze,
                    Path(document.storage_path),
                    declared_modality=document.modality.value if document.modality else None,
                    dicom_meta=dicom_meta,
                ),
                timeout=ANALYZE_TIMEOUT_S,
            )
            # Stage-1c report drafting: keyed path runs the vision LLM; keyless it
            # renders the deterministic fallback — never raises on LLM problems.
            stage = generate_diagnostic_report(
                settings,
                session,
                claim_id=claim.id,
                image_path=Path(document.storage_path),
                image_media_type=document.mime,
                analysis=analysis,
                declared_modality=document.modality.value if document.modality else None,
            )
        except Exception as exc:
            session.rollback()
            report.status = ArtifactStatus.FAILED
            report.error = f"{type(exc).__name__}: {exc}"
            session.commit()
            logger.exception("stage-1 inference failed for report %s", report_id)
            return

        report.payload_json = json.dumps(stage.payload)
        report.modality = analysis.modality
        report.modality_confidence = analysis.modality_confidence
        report.authenticity_verdict = analysis.authenticity_verdict
        report.authenticity_risk = analysis.authenticity_risk
        report.generated_by = stage.generated_by
        report.prompt_version = stage.prompt_version
        report.fallback_reason = stage.fallback_reason
        report.requires_mandatory_review = (
            stage.requires_mandatory_review or analysis.authenticity_verdict != "authentic"
        )
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


def _stage2_uploads(
    settings: Settings, session, claim: Claim
) -> list[tuple[str, str]]:
    """Extract text from the claim's non-imaging PDF uploads and index it into RAG.

    Extraction failures yield an empty text (the rule engine reports the gap);
    RAG indexing failures are logged and never fail the stage.
    """
    documents = session.scalars(
        select(Document)
        .where(Document.claim_id == claim.id, Document.kind != DocumentKind.IMAGING)
        .order_by(Document.id.asc())
    ).all()
    uploads: list[tuple[str, str]] = []
    for document in documents:
        if document.mime != "application/pdf":
            continue  # PDFs only; images and other binaries carry no extractable text
        try:
            text, _ok = extract_pdf_text(Path(document.storage_path))
        except Exception:
            logger.exception("pdf text extraction failed for document %s", document.id)
            text = ""
        uploads.append((document.filename, text))
        if not text:
            continue
        try:
            index_case_document(
                settings,
                claimant_id=claim.claimant_id,
                claim_id=claim.id,
                doc_type=document.kind.value,
                filename=document.filename,
                text=text,
            )
        except Exception:
            logger.exception("RAG indexing failed for document %s", document.id)
    return uploads


async def run_stage2(settings: Settings, note_id: int) -> None:
    """Stage-2 recommendation note. No claim transition: the human forwards."""
    factory = db.get_session_factory()
    session = factory()
    try:
        note = session.get(RecommendationNote, note_id)
        if note is None or note.status not in _RUNNABLE:
            return
        claim = session.get(Claim, note.claim_id)
        if claim is None:
            return

        note.status = ArtifactStatus.RUNNING
        note.error = None
        session.commit()

        try:
            claim_fields = {
                "claim_type": claim.claim_type,
                "procedure_code": claim.procedure_code,
                "diagnosis_code": claim.diagnosis_code,
                "incident_date": (
                    claim.incident_date.isoformat() if claim.incident_date else None
                ),
                "amount_claimed": claim.amount_claimed,
            }
            report = _latest_complete_report(session, claim.id)
            diagnostic_report = _payload_or_empty(report.payload_json) if report else {}
            uploads = _stage2_uploads(settings, session, claim)

            stage = generate_recommendation(
                settings,
                session,
                claim_id=claim.id,
                claim_fields=claim_fields,
                diagnostic_report=diagnostic_report,
                uploads=uploads,
                modality_for_procedure=_expected_modality(
                    claim.claim_type, claim.procedure_code
                ),
            )

            note.payload_json = json.dumps(stage.payload)
            note.recommendation = Recommendation(stage.payload["recommendation"])
            note.confidence = stage.payload.get("confidence")
            note.generated_by = stage.generated_by
            note.prompt_version = stage.prompt_version
            note.fallback_reason = stage.fallback_reason
            note.requires_mandatory_review = stage.requires_mandatory_review
            note.status = ArtifactStatus.COMPLETE
            note.completed_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as exc:
            session.rollback()
            note.status = ArtifactStatus.FAILED
            note.error = f"{type(exc).__name__}: {exc}"
            session.commit()
            logger.exception("stage-2 inference failed for note %s", note_id)
    finally:
        session.close()


def _history_inputs(session, member_id: str | None) -> tuple[list[dict], dict]:
    rows = (
        session.scalars(
            select(ClaimHistory)
            .where(ClaimHistory.member_id == member_id)
            .order_by(ClaimHistory.date_of_service.desc())
            .limit(_HISTORY_LIMIT)
        ).all()
        if member_id
        else []
    )
    history_rows = [
        {
            "date_of_service": (
                row.date_of_service.isoformat() if row.date_of_service else None
            ),
            "claim_type": row.claim_type,
            "procedure_code": row.procedure_code,
            "billed_amount": row.billed_amount,
            "outcome": row.outcome,
        }
        for row in rows
    ]
    today = date.today()
    rejected = sum(1 for row in rows if row.outcome == "rejected")
    history_stats = {
        "total": len(rows),
        "approved": sum(1 for row in rows if row.outcome == "approved"),
        "rejected": rejected,
        "recent_12mo": sum(
            1
            for row in rows
            if row.date_of_service is not None
            and (today - row.date_of_service).days <= _RECENT_WINDOW_DAYS
        ),
        "prior_rejections": rejected,
    }
    return history_rows, history_stats


async def run_stage3(settings: Settings, summary_id: int) -> None:
    """Stage-3 adjudication summary for the insurance agent."""
    factory = db.get_session_factory()
    session = factory()
    try:
        summary = session.get(AdjudicationSummary, summary_id)
        if summary is None or summary.status not in _RUNNABLE:
            return
        claim = session.get(Claim, summary.claim_id)
        if claim is None:
            return

        summary.status = ArtifactStatus.RUNNING
        summary.error = None
        session.commit()

        try:
            note = _latest_complete_note(session, claim.id)
            specialist_note = _payload_or_empty(note.payload_json) if note else {}
            report = _latest_complete_report(session, claim.id)
            diagnostic_report = _payload_or_empty(report.payload_json) if report else {}
            report_modality = report.modality if report else None

            history_rows, history_stats = _history_inputs(session, claim.claimant.member_id)

            impression = str(diagnostic_report.get("impression") or "")
            query = build_case_summary(
                modality=report_modality,
                claim_type=claim.claim_type,
                procedure_code=claim.procedure_code,
                diagnosis_code=claim.diagnosis_code,
                recommendation=specialist_note.get("recommendation"),
                key_findings=(
                    [impression[:_IMPRESSION_EXCERPT_CHARS]] if impression else []
                ),
                decision="PENDING",
            )
            similar_cases = find_similar_cases(
                settings,
                session,
                query=query,
                modality=report_modality,
                exclude_case_ref=make_case_ref(claim.claim_ref),
            )
            claimant_docs = [
                (row["filename"], row["text"])
                for row in get_case_documents(
                    settings,
                    session,
                    claimant_id=claim.claimant_id,
                    query=claim.description or claim.claim_type,
                )
            ]

            stage = generate_adjudication(
                settings,
                session,
                claim_id=claim.id,
                specialist_note=specialist_note,
                diagnostic_report=diagnostic_report,
                history_rows=history_rows,
                history_stats=history_stats,
                similar_cases=similar_cases,
                claimant_docs=claimant_docs,
            )

            summary.payload_json = json.dumps(stage.payload)
            summary.recommendation_lean = stage.payload.get("recommendation_lean")
            summary.confidence = stage.payload.get("confidence")
            summary.generated_by = stage.generated_by
            summary.prompt_version = stage.prompt_version
            summary.fallback_reason = stage.fallback_reason
            summary.requires_mandatory_review = stage.requires_mandatory_review
            summary.status = ArtifactStatus.COMPLETE
            summary.completed_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as exc:
            session.rollback()
            summary.status = ArtifactStatus.FAILED
            summary.error = f"{type(exc).__name__}: {exc}"
            session.commit()
            logger.exception("stage-3 inference failed for summary %s", summary_id)
    finally:
        session.close()


def schedule_stage1(
    background_tasks: BackgroundTasks, settings: Settings, report_id: int
) -> None:
    background_tasks.add_task(run_stage1, settings, report_id)


def schedule_stage2(
    background_tasks: BackgroundTasks, settings: Settings, note_id: int
) -> None:
    background_tasks.add_task(run_stage2, settings, note_id)


def schedule_stage3(
    background_tasks: BackgroundTasks, settings: Settings, summary_id: int
) -> None:
    background_tasks.add_task(run_stage3, settings, summary_id)


def recover_orphans(app: FastAPI) -> int:
    """Mark artifacts stuck in `running` (crash mid-task) as failed; called at startup."""
    factory = db.get_session_factory()
    session = factory()
    try:
        recovered = 0
        for model in (DiagnosticReport, RecommendationNote, AdjudicationSummary):
            orphans = session.scalars(
                select(model).where(model.status == ArtifactStatus.RUNNING)
            ).all()
            for artifact in orphans:
                artifact.status = ArtifactStatus.FAILED
                artifact.error = "orphaned at startup (process restarted mid-run)"
            recovered += len(orphans)
        session.commit()
        return recovered
    finally:
        session.close()
