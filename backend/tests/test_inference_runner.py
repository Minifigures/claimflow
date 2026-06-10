import asyncio
import json
import time
from itertools import count
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.ml.base as ml_base
from app.claimguard import audit
from app.config import Settings
from app.models import (
    ArtifactStatus,
    AuditEvent,
    Claim,
    ClaimState,
    Decision,
    DiagnosticReport,
    Document,
    DocumentKind,
    Modality,
    Role,
    User,
)
from app.services import inference_runner
from app.services.inference_runner import recover_orphans, run_stage1

_ref_counter = count(1)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 256


def write_png(tmp_path: Path, name: str = "knee_xray.png") -> Path:
    path = tmp_path / name
    path.write_bytes(PNG_BYTES)
    return path


def make_claim_chain(
    session: Session,
    users: dict[str, User],
    image_path: Path,
    *,
    report_status: ArtifactStatus = ArtifactStatus.PENDING,
) -> tuple[Claim, Document, DiagnosticReport]:
    """ORM-only setup: claim (SUBMITTED) + imaging document + diagnostic report."""
    claimant = users[Role.CLAIMANT.value]
    claim = Claim(
        claim_ref=f"CLM-INF-{next(_ref_counter):05d}",
        claimant_id=claimant.id,
        claim_type="imaging",
        state=ClaimState.SUBMITTED,
    )
    session.add(claim)
    session.flush()
    document = Document(
        claim_id=claim.id,
        uploader_id=claimant.id,
        kind=DocumentKind.IMAGING,
        modality=Modality.XRAY,
        filename=image_path.name,
        mime="image/png",
        size_bytes=image_path.stat().st_size if image_path.exists() else 0,
        sha256="0" * 64,
        storage_path=str(image_path),
    )
    session.add(document)
    session.flush()
    report = DiagnosticReport(claim_id=claim.id, document_id=document.id, status=report_status)
    session.add(report)
    session.commit()
    return claim, document, report


def test_run_stage1_happy_path(
    settings: Settings, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    image = write_png(tmp_path)
    claim, document, report = make_claim_chain(session, users, image)

    asyncio.run(run_stage1(settings, report.id))
    session.expire_all()

    report = session.get(DiagnosticReport, report.id)
    claim = session.get(Claim, claim.id)
    assert report is not None and claim is not None
    assert report.status == ArtifactStatus.COMPLETE
    assert report.error is None
    assert report.completed_at is not None
    assert report.modality == "xray"
    assert report.generated_by == "fallback_template"  # keyless stage-1c path
    assert report.fallback_reason == "no_api_key"
    assert report.prompt_version == "v1"
    assert report.authenticity_verdict == "authentic"
    # keyless fallback pins confidence to 0.0, which always flags mandatory review
    assert report.requires_mandatory_review is True

    assert report.payload_json is not None
    payload = json.loads(report.payload_json)
    assert payload["authenticity"]["verdict"] == "authentic"
    assert payload["classifier"]["modality"] == "xray"
    assert "disclaimer" in payload
    assert document.filename  # document row still linked to the report
    assert report.document_id == document.id

    assert claim.state == ClaimState.IMAGING_REVIEW

    llm_events = session.scalars(
        select(AuditEvent).where(
            AuditEvent.event_type == "llm_call", AuditEvent.claim_id == claim.id
        )
    ).all()
    assert len(llm_events) == 1
    assert json.loads(llm_events[0].payload_json)["stage"] == "imaging_analysis"

    ok, n = audit.verify_chain(session)
    assert ok is True
    assert n == 2  # llm_call + workflow.transition


def test_run_stage1_missing_file_marks_failed_without_transition(
    settings: Settings, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    missing = tmp_path / "never_written.png"
    claim, _, report = make_claim_chain(session, users, missing)

    asyncio.run(run_stage1(settings, report.id))
    session.expire_all()

    report = session.get(DiagnosticReport, report.id)
    claim = session.get(Claim, claim.id)
    assert report is not None and claim is not None
    assert report.status == ArtifactStatus.FAILED
    assert report.error is not None and "FileNotFoundError" in report.error
    assert claim.state == ClaimState.SUBMITTED
    assert session.scalars(select(Decision)).all() == []
    assert session.scalars(select(AuditEvent)).all() == []


class _SleepyAnalyzer:
    def analyze(self, image_path: Path, *, declared_modality: str | None, dicom_meta: dict | None):
        time.sleep(1.0)
        return None


def test_run_stage1_timeout_marks_failed(
    settings: Settings,
    session: Session,
    users: dict[str, User],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = write_png(tmp_path)
    claim, _, report = make_claim_chain(session, users, image)

    monkeypatch.setattr(inference_runner, "ANALYZE_TIMEOUT_S", 0.01)
    # Patch the registry and the runner's imported reference to it.
    monkeypatch.setattr(ml_base, "get_analyzer", lambda _settings: _SleepyAnalyzer())
    monkeypatch.setattr(inference_runner, "get_analyzer", lambda _settings: _SleepyAnalyzer())

    asyncio.run(run_stage1(settings, report.id))
    session.expire_all()

    report = session.get(DiagnosticReport, report.id)
    claim = session.get(Claim, claim.id)
    assert report is not None and claim is not None
    assert report.status == ArtifactStatus.FAILED
    assert report.error is not None and "TimeoutError" in report.error
    assert claim.state == ClaimState.SUBMITTED


def test_run_stage1_is_idempotent_on_complete_report(
    settings: Settings, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    image = write_png(tmp_path)
    claim, _, report = make_claim_chain(session, users, image)

    asyncio.run(run_stage1(settings, report.id))
    session.expire_all()
    report = session.get(DiagnosticReport, report.id)
    assert report is not None and report.status == ArtifactStatus.COMPLETE
    payload_before = report.payload_json
    completed_before = report.completed_at
    events_before = len(session.scalars(select(AuditEvent)).all())
    decisions_before = len(session.scalars(select(Decision)).all())

    asyncio.run(run_stage1(settings, report.id))
    session.expire_all()

    report = session.get(DiagnosticReport, report.id)
    claim = session.get(Claim, claim.id)
    assert report is not None and claim is not None
    assert report.status == ArtifactStatus.COMPLETE
    assert report.payload_json == payload_before
    assert report.completed_at == completed_before
    assert claim.state == ClaimState.IMAGING_REVIEW
    assert len(session.scalars(select(AuditEvent)).all()) == events_before
    assert len(session.scalars(select(Decision)).all()) == decisions_before


def test_recover_orphans_fails_running_reports(
    app, settings: Settings, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    image = write_png(tmp_path)
    _, _, running = make_claim_chain(session, users, image, report_status=ArtifactStatus.RUNNING)
    _, _, pending = make_claim_chain(session, users, image)

    recovered = recover_orphans(app)
    assert recovered == 1
    session.expire_all()

    running = session.get(DiagnosticReport, running.id)
    pending = session.get(DiagnosticReport, pending.id)
    assert running is not None and pending is not None
    assert running.status == ArtifactStatus.FAILED
    assert running.error is not None and "orphaned at startup" in running.error
    assert pending.status == ArtifactStatus.PENDING


def test_tampered_filename_demo_hook(
    settings: Settings, session: Session, users: dict[str, User], tmp_path: Path
) -> None:
    image = write_png(tmp_path, name="tampered_xray.png")
    claim, _, report = make_claim_chain(session, users, image)

    asyncio.run(run_stage1(settings, report.id))
    session.expire_all()

    report = session.get(DiagnosticReport, report.id)
    claim = session.get(Claim, claim.id)
    assert report is not None and claim is not None
    assert report.status == ArtifactStatus.COMPLETE
    assert report.authenticity_verdict == "likely_fraudulent"
    assert report.requires_mandatory_review is True
    assert report.payload_json is not None
    payload = json.loads(report.payload_json)
    assert payload["authenticity"]["verdict"] == "likely_fraudulent"
    assert claim.state == ClaimState.IMAGING_REVIEW
