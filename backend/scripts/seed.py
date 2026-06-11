"""Idempotent demo seeder: demo users, PII-free claim history, and demo fixture claims.

Run with `uv run python -m scripts.seed` (add `--no-fixtures` to skip the demo claims)
or import `seed_database` for tests/startup.

Fixture claims are driven through the REAL service layer — the workflow state machine,
the stage-1/2/3 inference runners, the notification service, and the hash-chained audit
log — exactly as the routers do it, so every portal queue shows coherent, replayable
data on first login. Never raw state stuffing. Everything works keyless: the stages
self-fall-back to deterministic templates and the stub analyzer.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import random
import shutil
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db
from app.auth.passwords import hash_password
from app.claimguard import audit
from app.config import Settings
from app.llm.stages.claimant_email import draft_claimant_email
from app.main import create_app
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
    Modality,
    RecommendationNote,
    Role,
    User,
)
from app.rag.indexer import index_closed_case
from app.services.dicom_preview import process_dicom, sniff_kind
from app.services.inference_runner import run_stage1, run_stage2, run_stage3
from app.services.notifications.service import render_claim_returned, send_claim_email
from app.workflow import state_machine

logger = logging.getLogger("claimflow.seed")

DEMO_PASSWORD = "demo1234"

HISTORY_SEED = 42
HISTORY_COUNTS = {"MBR-1001": 25, "MBR-1002": 15}
SERVICE_WINDOW = (date(2023, 1, 1), date(2025, 12, 1))

CLAIM_TYPES = ("imaging", "physio", "dental", "prescription")
MODALITIES = tuple(m.value for m in Modality)
PROCEDURE_CODES = {
    "imaging": ("IMG-201", "IMG-205", "IMG-310"),
    "physio": ("PHY-110", "PHY-204", "PHY-330"),
    "dental": ("DEN-303", "DEN-115", "DEN-220"),
    "prescription": ("RX-405", "RX-112", "RX-518"),
}
DIAGNOSIS_CODES = ("M54.5", "S82.1", "M25.51", "K08.9", "J45.9", "G43.0")
NOTE_LINES = (
    "Receipt resubmitted after the first copy was illegible.",
    "Provider invoice matched the plan fee schedule.",
    "Pre-authorization was on file before the date of service.",
    "Amount exceeded the annual category maximum.",
    "Duplicate of an earlier submission; original was paid.",
)


@dataclass(frozen=True)
class DemoUser:
    email: str
    role: Role
    full_name: str
    member_id: str | None = None
    preferred_language: str = "en"


DEMO_USERS: tuple[DemoUser, ...] = (
    DemoUser("claimant@demo.ca", Role.CLAIMANT, "Casey Claimant", "MBR-1001"),
    DemoUser("claimant2@demo.ca", Role.CLAIMANT, "Camille Tremblay", "MBR-1002", "fr"),
    DemoUser("imaging@demo.ca", Role.IMAGING_SPECIALIST, "Iris Imaging"),
    DemoUser("specialist@demo.ca", Role.MEDICAL_SPECIALIST, "Sam Specialist"),
    DemoUser("agent@demo.ca", Role.INSURANCE_AGENT, "Avery Agent"),
)


@dataclass
class SeedSummary:
    users_created: list[str] = field(default_factory=list)
    users_skipped: list[str] = field(default_factory=list)
    history_inserted: int = 0
    history_counts: dict[str, int] = field(default_factory=dict)
    precedents_indexed: int = 0
    fixture_claims: list[str] = field(default_factory=list)


def _history_rows(member_id: str, count: int, rng: random.Random) -> list[ClaimHistory]:
    start, end = SERVICE_WINDOW
    span_days = (end - start).days
    rows: list[ClaimHistory] = []
    for _ in range(count):
        claim_type = rng.choice(CLAIM_TYPES)
        modality = rng.choice(MODALITIES) if claim_type == "imaging" else None
        date_of_service = start + timedelta(days=rng.randrange(span_days + 1))
        rows.append(
            ClaimHistory(
                member_id=member_id,
                claim_type=claim_type,
                procedure_code=rng.choice(PROCEDURE_CODES[claim_type]),
                diagnosis_code=rng.choice(DIAGNOSIS_CODES),
                modality=modality,
                billed_amount=round(rng.uniform(80.0, 4500.0), 2),
                outcome="approved" if rng.random() < 0.75 else "rejected",
                date_of_service=date_of_service,
                decided_at=date_of_service + timedelta(days=rng.randint(7, 45)),
                notes=rng.choice(NOTE_LINES) if rng.random() < 0.25 else None,
            )
        )
    return rows


# ------------------------------------------------------------------ demo fixture claims

SEED_ASSETS_DIR = Path(__file__).resolve().parents[1] / "seed-assets"

REF_IMAGING_CLEAN = "CLM-DEMO-0001"
REF_IMAGING_TAMPERED = "CLM-DEMO-0002"
REF_SPECIALIST_REVIEW = "CLM-DEMO-0003"
REF_ADJUDICATION_FR = "CLM-DEMO-0004"
REF_RETURNED = "CLM-DEMO-0005"
REF_FURTHER_TESTING = "CLM-DEMO-0006"
REF_APPROVED = "CLM-DEMO-0007"

FIXTURE_STATES: dict[str, ClaimState] = {
    REF_IMAGING_CLEAN: ClaimState.IMAGING_REVIEW,
    REF_IMAGING_TAMPERED: ClaimState.IMAGING_REVIEW,
    REF_SPECIALIST_REVIEW: ClaimState.SPECIALIST_REVIEW,
    REF_ADJUDICATION_FR: ClaimState.ADJUDICATION,
    REF_RETURNED: ClaimState.RETURNED_TO_CLAIMANT,
    REF_FURTHER_TESTING: ClaimState.PENDING_FURTHER_TESTING,
    REF_APPROVED: ClaimState.APPROVED,
}

RETURN_NOTE = "Image is a photocopy; please upload the original DICOM export"
FURTHER_TESTING_NOTE = (
    "Initial CT is inconclusive for the reported symptoms; "
    "please obtain a contrast-enhanced follow-up series"
)
APPROVE_NOTE = "Imaging authentic, specialist supports the claim, member history is consistent."


@dataclass(frozen=True)
class PrecedentSpec:
    """One anonymized closed case for the precedent index (not tied to any Claim row)."""

    claim_ref: str
    modality: str
    procedure_code: str
    diagnosis_code: str
    recommendation: str
    key_finding: str
    decision: str


# Three clusters so similar-case retrieval returns genuine precedents for the demo
# claims: knee x-ray (IMG-201, mostly approved), brain MRI (IMG-401, mixed outcomes),
# chest CT (IMG-301, approved).
PRECEDENTS: tuple[PrecedentSpec, ...] = (
    PrecedentSpec(
        "SEED-PREC-001", "xray", "IMG-201", "M25.51", "SUPPORTS_CLAIM",
        "Moderate suprapatellar joint effusion following acute knee trauma; no fracture "
        "line visible; lateral soft-tissue swelling.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-002", "xray", "IMG-201", "S82.1", "SUPPORTS_CLAIM",
        "Nondisplaced avulsion fragment at the lateral tibial plateau of the knee with "
        "intact trabecular pattern.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-003", "xray", "IMG-201", "M25.51", "SUPPORTS_CLAIM",
        "Degenerative medial compartment joint-space narrowing of the knee with marginal "
        "osteophytes; findings match the reported symptoms.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-004", "xray", "IMG-201", "S82.1", "SUPPORTS_CLAIM",
        "Transverse patellar fracture with 3 mm displacement and overlying soft-tissue "
        "swelling after a fall onto the knee.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-005", "xray", "IMG-201", "M25.51", "INSUFFICIENT_EVIDENCE",
        "Single low-resolution knee view; joint margins not assessable; the requested "
        "repeat study was never supplied.",
        "REJECTED",
    ),
    PrecedentSpec(
        "SEED-PREC-006", "mri", "IMG-401", "G43.0", "SUPPORTS_CLAIM",
        "Scattered white-matter hyperintensities on brain MRI consistent with chronic "
        "migraine; no mass effect or midline shift.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-007", "mri", "IMG-401", "G43.0", "SUPPORTS_CLAIM",
        "Unremarkable contrast brain MRI supporting a migraine workup; ventricles and "
        "sulci within normal limits.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-008", "mri", "IMG-401", "G43.0", "INSUFFICIENT_EVIDENCE",
        "Brain MRI sequences incomplete; axial FLAIR series missing and not provided "
        "after follow-up request.",
        "REJECTED",
    ),
    PrecedentSpec(
        "SEED-PREC-009", "mri", "IMG-401", "G43.0", "REQUIRES_FURTHER_TESTING",
        "Nonspecific T2 signal in the right frontal lobe on brain MRI; recommended "
        "repeat study with contrast was declined.",
        "REJECTED",
    ),
    PrecedentSpec(
        "SEED-PREC-010", "ct", "IMG-301", "J45.9", "SUPPORTS_CLAIM",
        "Chest CT shows bronchial wall thickening with mucus plugging consistent with "
        "the reported asthma exacerbation.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-011", "ct", "IMG-301", "J45.9", "SUPPORTS_CLAIM",
        "Small calcified granuloma in the right middle lobe on chest CT; otherwise "
        "clear lung fields.",
        "APPROVED",
    ),
    PrecedentSpec(
        "SEED-PREC-012", "ct", "IMG-301", "J45.9", "SUPPORTS_CLAIM",
        "Mild air trapping on expiratory chest CT views; no consolidation or pleural "
        "effusion.",
        "APPROVED",
    ),
)


class SeedError(RuntimeError):
    """A fixture claim did not reach the expected state/artifact status."""


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise SeedError(message)


def seed_precedents(settings: Settings) -> int:
    """Index the anonymized closed-case precedents into Chroma (idempotent upserts)."""
    for case in PRECEDENTS:
        index_closed_case(
            settings,
            claim_ref=case.claim_ref,
            modality=case.modality,
            claim_type="imaging",
            procedure_code=case.procedure_code,
            diagnosis_code=case.diagnosis_code,
            recommendation=case.recommendation,
            key_findings=[case.key_finding],
            decision=case.decision,
        )
    return len(PRECEDENTS)


def _first_name(full_name: str) -> str:
    parts = full_name.split()
    return parts[0] if parts else full_name


def _key_findings(note: RecommendationNote | None) -> list[str]:
    """PII-free excerpt of the specialist note summary (mirrors the agent router)."""
    if note is None or not note.payload_json:
        return []
    payload = json.loads(note.payload_json)
    summary = str(payload.get("summary") or payload.get("impression") or "").strip()
    return [summary[:300]] if summary else []


def _attach_seed_asset(
    session: Session,
    settings: Settings,
    claim: Claim,
    claimant: User,
    asset_name: str,
    modality: Modality,
) -> Document:
    """Copy a seed asset into the claim's upload dir and persist the Document row,
    mirroring the upload router (real sha256/size, DOCUMENT_UPLOAD audit event)."""
    source = SEED_ASSETS_DIR / asset_name
    target_dir = settings.upload_dir / str(claim.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / asset_name  # keep the filename ('tampered' drives the demo)
    shutil.copyfile(source, target)

    # Mirror the upload router's DICOM branch: de-identify in place, extract the
    # safe metadata dict (the analyzer's metadata signal reads it), render preview.
    sniffed = sniff_kind(target, "")
    dicom_meta_json: str | None = None
    preview_path: str | None = None
    if sniffed == "dicom":
        meta, preview_path = process_dicom(target)
        dicom_meta_json = json.dumps(meta)
    mime = {"dicom": "application/dicom", "png": "image/png", "jpeg": "image/jpeg"}[sniffed]
    data = target.read_bytes()

    document = Document(
        claim_id=claim.id,
        uploader_id=claimant.id,
        kind=DocumentKind.IMAGING,
        modality=modality,
        filename=asset_name,
        mime=mime,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_path=str(target),
        preview_path=preview_path,
        dicom_meta_json=dicom_meta_json,
    )
    session.add(document)
    session.flush()
    audit.append(
        session,
        AuditEventType.DOCUMENT_UPLOAD,
        claim_id=claim.id,
        actor_user_id=claimant.id,
        actor_role=claimant.role.value,
        payload={
            "filename": document.filename,
            "kind": document.kind.value,
            "sha256": document.sha256,
        },
    )
    session.commit()
    return document


def _submit_with_imaging(
    session: Session,
    settings: Settings,
    *,
    claimant: User,
    claim_ref: str,
    asset_name: str,
    modality: Modality,
    procedure_code: str,
    diagnosis_code: str,
    description: str,
    amount_claimed: float,
    incident_date: date,
) -> Claim:
    """Create + submit a claim, attach the imaging asset, and run stage 1 — exactly the
    claimant flow (create_claim -> upload_document -> analyze_claim)."""
    claim = Claim(
        claim_ref=claim_ref,
        claimant_id=claimant.id,
        claim_type="imaging",
        description=description,
        procedure_code=procedure_code,
        diagnosis_code=diagnosis_code,
        incident_date=incident_date,
        amount_claimed=amount_claimed,
        state=ClaimState.SUBMITTED,
    )
    session.add(claim)
    session.flush()
    state_machine.record_initial_submit(session, claim, claimant)
    audit.append(
        session,
        AuditEventType.CLAIM_SUBMIT,
        claim_id=claim.id,
        actor_user_id=claimant.id,
        actor_role=claimant.role.value,
        payload={
            "claim_ref": claim.claim_ref,
            "claim_type": claim.claim_type,
            "amount_claimed": claim.amount_claimed,
        },
    )
    session.commit()

    document = _attach_seed_asset(session, settings, claim, claimant, asset_name, modality)

    report = DiagnosticReport(
        claim_id=claim.id, document_id=document.id, status=ArtifactStatus.PENDING
    )
    session.add(report)
    session.flush()
    session.commit()
    asyncio.run(run_stage1(settings, report.id))
    session.refresh(report)
    session.refresh(claim)
    _expect(
        report.status is ArtifactStatus.COMPLETE,
        f"{claim_ref}: stage-1 report ended {report.status.value} ({report.error})",
    )
    _expect(
        claim.state is ClaimState.IMAGING_REVIEW,
        f"{claim_ref}: expected IMAGING_REVIEW after stage 1, got {claim.state.value}",
    )
    return claim


def _forward_to_specialist(
    session: Session, settings: Settings, claim: Claim, imaging_user: User
) -> RecommendationNote:
    """Imaging specialist forwards the case; stage-2 note runs (mirrors forward_case)."""
    state_machine.apply_transition(session, claim, ClaimAction.FORWARD, actor=imaging_user)
    note = RecommendationNote(claim_id=claim.id, status=ArtifactStatus.PENDING)
    session.add(note)
    session.flush()
    session.commit()
    asyncio.run(run_stage2(settings, note.id))
    session.refresh(note)
    session.refresh(claim)
    _expect(
        note.status is ArtifactStatus.COMPLETE,
        f"{claim.claim_ref}: stage-2 note ended {note.status.value} ({note.error})",
    )
    return note


def _send_to_insurer(
    session: Session, settings: Settings, claim: Claim, specialist_user: User
) -> AdjudicationSummary:
    """Medical specialist sends to insurer; stage-3 summary runs (mirrors send_to_insurer)."""
    state_machine.apply_transition(
        session, claim, ClaimAction.SEND_TO_INSURER, actor=specialist_user
    )
    summary = AdjudicationSummary(claim_id=claim.id, status=ArtifactStatus.PENDING)
    session.add(summary)
    session.flush()
    session.commit()
    asyncio.run(run_stage3(settings, summary.id))
    session.refresh(summary)
    session.refresh(claim)
    _expect(
        summary.status is ArtifactStatus.COMPLETE,
        f"{claim.claim_ref}: stage-3 summary ended {summary.status.value} ({summary.error})",
    )
    return summary


def _notify_claim_returned(
    session: Session, settings: Settings, claim: Claim, reason: str
) -> None:
    """Render + persist + 'send' the claim-returned email (mirrors the specialist router)."""
    subject, body_text = render_claim_returned(
        claim.claim_ref, reason=reason, first_name=_first_name(claim.claimant.full_name)
    )
    send_claim_email(
        session,
        settings,
        claim=claim,
        recipient=claim.claimant,
        subject=subject,
        body_text=body_text,
    )


def _return_to_claimant(
    session: Session, settings: Settings, claim: Claim, imaging_user: User, note: str
) -> None:
    state_machine.apply_transition(
        session, claim, ClaimAction.RETURN_TO_CLAIMANT, actor=imaging_user, note=note
    )
    _notify_claim_returned(session, settings, claim, note)
    session.commit()


def _request_further_testing(
    session: Session, settings: Settings, claim: Claim, specialist_user: User, note: str
) -> None:
    state_machine.apply_transition(
        session, claim, ClaimAction.REQUEST_FURTHER_TESTING, actor=specialist_user, note=note
    )
    _notify_claim_returned(session, settings, claim, note)
    session.commit()


def _latest_complete_report(session: Session, claim_id: int) -> DiagnosticReport | None:
    return session.scalar(
        select(DiagnosticReport)
        .where(
            DiagnosticReport.claim_id == claim_id,
            DiagnosticReport.status == ArtifactStatus.COMPLETE,
        )
        .order_by(DiagnosticReport.id.desc())
        .limit(1)
    )


def _latest_complete_note(session: Session, claim_id: int) -> RecommendationNote | None:
    return session.scalar(
        select(RecommendationNote)
        .where(
            RecommendationNote.claim_id == claim_id,
            RecommendationNote.status == ArtifactStatus.COMPLETE,
        )
        .order_by(RecommendationNote.id.desc())
        .limit(1)
    )


def _approve_with_email(
    session: Session, settings: Settings, claim: Claim, agent_user: User, note: str
) -> None:
    """Agent approval mirroring draft_decision_email + decide_claim: draft (keyless
    fallback template), transition + Decision + audit + Notification in one commit,
    then best-effort precedent indexing."""
    claimant = claim.claimant
    language: Literal["en", "fr"] = "fr" if claimant.preferred_language == "fr" else "en"
    tone: Literal["formal", "plain_language"] = (
        "formal" if claimant.preferred_tone == "formal" else "plain_language"
    )
    draft = draft_claimant_email(
        settings,
        session,
        claim_id=claim.id,
        decision="APPROVED",
        first_name=_first_name(claimant.full_name),
        language=language,
        tone=tone,
        claim_ref=claim.claim_ref,
        claim_type=claim.claim_type,
    )
    audit.append(
        session,
        AuditEventType.EMAIL_DRAFTED,
        claim_id=claim.id,
        actor_user_id=agent_user.id,
        actor_role=agent_user.role.value,
        payload={
            "decision": "APPROVED",
            "generated_by": draft.generated_by,
            "fallback_reason": draft.fallback_reason,
        },
    )
    session.commit()

    state_machine.apply_transition(session, claim, ClaimAction.APPROVE, actor=agent_user, note=note)
    audit.append(
        session,
        AuditEventType.DECISION_APPROVE,
        claim_id=claim.id,
        actor_user_id=agent_user.id,
        actor_role=agent_user.role.value,
        payload={"note_present": bool(note)},
    )
    payload = draft.payload
    body_text = "\n\n".join([payload["greeting"], *payload["body_paragraphs"], payload["closing"]])
    send_claim_email(
        session,
        settings,
        claim=claim,
        recipient=claimant,
        subject=payload["subject"],
        body_text=body_text,
    )

    try:
        report = _latest_complete_report(session, claim.id)
        rec_note = _latest_complete_note(session, claim.id)
        index_closed_case(
            settings,
            claim_ref=claim.claim_ref,
            modality=report.modality if report is not None else None,
            claim_type=claim.claim_type,
            procedure_code=claim.procedure_code,
            diagnosis_code=claim.diagnosis_code,
            recommendation=(
                rec_note.recommendation.value
                if rec_note is not None and rec_note.recommendation is not None
                else None
            ),
            key_findings=_key_findings(rec_note),
            decision="APPROVED",
        )
    except Exception:  # noqa: BLE001 — precedent indexing must never fail the decision
        logger.exception("precedent indexing failed for claim %s; decision proceeds", claim.id)

    session.commit()


def seed_fixtures(session: Session, settings: Settings) -> list[str]:
    """Create the seven demo claims through the real service layer.

    The caller guarantees the Claim table is empty (idempotency gate lives in
    seed_database). Returns the created claim refs in creation order.
    """
    users = {user.email: user for user in session.scalars(select(User)).all()}
    claimant = users["claimant@demo.ca"]
    claimant_fr = users["claimant2@demo.ca"]
    imaging = users["imaging@demo.ca"]
    specialist = users["specialist@demo.ca"]
    agent = users["agent@demo.ca"]

    # a. Parked in IMAGING_REVIEW with a clean x-ray.
    _submit_with_imaging(
        session,
        settings,
        claimant=claimant,
        claim_ref=REF_IMAGING_CLEAN,
        asset_name="clean_xray.png",
        modality=Modality.XRAY,
        procedure_code="IMG-201",
        diagnosis_code="M25.51",
        description="Left knee X-ray after fall",
        amount_claimed=420.0,
        incident_date=date(2026, 5, 12),
    )

    # b. THE DEMO STAR: a tampered study (copy-move + splice on a real x-ray, wrapped
    # in a DICOM whose Modality tag says CT) flagged non-authentic in IMAGING_REVIEW.
    # stub: filename hook -> likely_fraudulent; real: CNN + metadata override -> suspicious.
    tampered = _submit_with_imaging(
        session,
        settings,
        claimant=claimant,
        claim_ref=REF_IMAGING_TAMPERED,
        asset_name="tampered_xray.dcm",
        modality=Modality.XRAY,
        procedure_code="IMG-201",
        diagnosis_code="S82.1",
        description="Left knee X-ray series, follow-up after cast removal",
        amount_claimed=510.0,
        incident_date=date(2026, 5, 19),
    )
    tampered_report = _latest_complete_report(session, tampered.id)
    _expect(
        tampered_report is not None
        and tampered_report.authenticity_verdict in ("suspicious", "likely_fraudulent"),
        f"{REF_IMAGING_TAMPERED}: expected a non-authentic verdict, got "
        f"{tampered_report.authenticity_verdict if tampered_report else 'no report'}",
    )

    # c. SPECIALIST_REVIEW: clean CT forwarded by the imaging specialist.
    claim_c = _submit_with_imaging(
        session,
        settings,
        claimant=claimant,
        claim_ref=REF_SPECIALIST_REVIEW,
        asset_name="clean_ct.png",
        modality=Modality.CT,
        procedure_code="IMG-301",
        diagnosis_code="J45.9",
        description="Chest CT for persistent cough and wheeze",
        amount_claimed=1480.0,
        incident_date=date(2026, 4, 30),
    )
    _forward_to_specialist(session, settings, claim_c, imaging)

    # d. ADJUDICATION for the French-preference claimant: the decision-modal demo
    # drafts a FRENCH email live against this claim.
    claim_d = _submit_with_imaging(
        session,
        settings,
        claimant=claimant_fr,
        claim_ref=REF_ADJUDICATION_FR,
        asset_name="clean_mri.png",
        modality=Modality.MRI,
        procedure_code="IMG-401",
        diagnosis_code="G43.0",
        description="IRM cérébrale pour migraines récurrentes",
        amount_claimed=2150.0,
        incident_date=date(2026, 5, 5),
    )
    _forward_to_specialist(session, settings, claim_d, imaging)
    _send_to_insurer(session, settings, claim_d, specialist)

    # e. RETURNED_TO_CLAIMANT via the real return flow (notification email included).
    claim_e = _submit_with_imaging(
        session,
        settings,
        claimant=claimant,
        claim_ref=REF_RETURNED,
        asset_name="clean_xray.png",
        modality=Modality.XRAY,
        procedure_code="IMG-201",
        diagnosis_code="S62.1",
        description="Right wrist X-ray after cycling fall",
        amount_claimed=365.0,
        incident_date=date(2026, 5, 22),
    )
    _return_to_claimant(session, settings, claim_e, imaging, RETURN_NOTE)

    # f. PENDING_FURTHER_TESTING via the full path: forward, then request more evidence.
    claim_f = _submit_with_imaging(
        session,
        settings,
        claimant=claimant,
        claim_ref=REF_FURTHER_TESTING,
        asset_name="clean_ct.png",
        modality=Modality.CT,
        procedure_code="IMG-301",
        diagnosis_code="R10.9",
        description="Abdominal CT for recurring pain",
        amount_claimed=1320.0,
        incident_date=date(2026, 4, 18),
    )
    _forward_to_specialist(session, settings, claim_f, imaging)
    _request_further_testing(session, settings, claim_f, specialist, FURTHER_TESTING_NOTE)

    # g. APPROVED: the full lifecycle, ending with the agent's atomic decide-and-notify.
    claim_g = _submit_with_imaging(
        session,
        settings,
        claimant=claimant,
        claim_ref=REF_APPROVED,
        asset_name="clean_xray.png",
        modality=Modality.XRAY,
        procedure_code="IMG-201",
        diagnosis_code="S93.4",
        description="Left ankle X-ray after basketball injury",
        amount_claimed=395.0,
        incident_date=date(2026, 4, 6),
    )
    _forward_to_specialist(session, settings, claim_g, imaging)
    _send_to_insurer(session, settings, claim_g, specialist)
    _approve_with_email(session, settings, claim_g, agent, APPROVE_NOTE)

    for claim_ref, expected_state in FIXTURE_STATES.items():
        state = session.scalar(select(Claim.state).where(Claim.claim_ref == claim_ref))
        _expect(
            state is expected_state,
            f"{claim_ref}: expected {expected_state.value}, got {state}",
        )
    return list(FIXTURE_STATES)


def seed_database(
    session: Session,
    settings: Settings | None = None,
    *,
    fixtures: bool = True,
) -> SeedSummary:
    summary = SeedSummary()
    for spec in DEMO_USERS:
        if session.scalar(select(User.id).where(User.email == spec.email)) is not None:
            summary.users_skipped.append(spec.email)
            continue
        session.add(
            User(
                email=spec.email,
                password_hash=hash_password(DEMO_PASSWORD),
                role=spec.role,
                full_name=spec.full_name,
                member_id=spec.member_id,
                preferred_language=spec.preferred_language,
            )
        )
        summary.users_created.append(spec.email)

    existing = session.scalar(select(func.count()).select_from(ClaimHistory)) or 0
    if existing == 0:
        rng = random.Random(HISTORY_SEED)
        rows: list[ClaimHistory] = []
        for member_id, count in HISTORY_COUNTS.items():
            rows.extend(_history_rows(member_id, count, rng))
        session.add_all(rows)
        summary.history_inserted = len(rows)

    session.commit()
    summary.history_counts = {
        member_id: count
        for member_id, count in session.execute(
            select(ClaimHistory.member_id, func.count())
            .group_by(ClaimHistory.member_id)
            .order_by(ClaimHistory.member_id)
        ).all()
    }

    if fixtures:
        existing_claims = session.scalar(select(func.count()).select_from(Claim)) or 0
        if existing_claims == 0:
            settings = settings or Settings()
            summary.precedents_indexed = seed_precedents(settings)
            summary.fixture_claims = seed_fixtures(session, settings)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the ClaimFlow demo database.")
    parser.add_argument(
        "--no-fixtures",
        action="store_true",
        help="seed only users and claim history; skip the demo claims and precedents",
    )
    args = parser.parse_args()

    settings = Settings()
    create_app(settings)
    factory = db.get_session_factory()
    with factory() as session:
        summary = seed_database(session, settings, fixtures=not args.no_fixtures)

    print("ClaimFlow demo seed")
    print(f"{'user':<24}status")
    for email in summary.users_created:
        print(f"{email:<24}created")
    for email in summary.users_skipped:
        print(f"{email:<24}skipped (already exists)")
    counts = ", ".join(f"{member}={count}" for member, count in summary.history_counts.items())
    print(f"claim_history rows: {counts} (inserted this run: {summary.history_inserted})")
    if summary.fixture_claims:
        print(f"precedent cases indexed: {summary.precedents_indexed}")
        print("fixture claims:")
        for claim_ref in summary.fixture_claims:
            print(f"  {claim_ref:<16}{FIXTURE_STATES[claim_ref].value}")
    elif not args.no_fixtures:
        print("fixture claims: skipped (claims already exist)")
    print(f"all demo users: {DEMO_PASSWORD}")


if __name__ == "__main__":
    main()
