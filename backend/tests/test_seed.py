from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db
from app.claimguard.audit import verify_chain
from app.config import Settings
from app.main import create_app
from app.models import (
    AuditEvent,
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
from app.rag.store import get_adjudicated_cases_collection, get_client
from scripts.seed import (
    FIXTURE_STATES,
    REF_ADJUDICATION_FR,
    REF_APPROVED,
    REF_IMAGING_TAMPERED,
    REF_SPECIALIST_REVIEW,
    SeedSummary,
    seed_database,
)

ALL_ROLES = {
    Role.CLAIMANT,
    Role.IMAGING_SPECIALIST,
    Role.MEDICAL_SPECIALIST,
    Role.INSURANCE_AGENT,
}


def _user_count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User)) or 0


def _history_count(session: Session, member_id: str | None = None) -> int:
    stmt = select(func.count()).select_from(ClaimHistory)
    if member_id is not None:
        stmt = stmt.where(ClaimHistory.member_id == member_id)
    return session.scalar(stmt) or 0


def _history_tuples(session: Session) -> list[tuple]:
    return [
        tuple(row)
        for row in session.execute(
            select(
                ClaimHistory.member_id,
                ClaimHistory.claim_type,
                ClaimHistory.procedure_code,
                ClaimHistory.diagnosis_code,
                ClaimHistory.modality,
                ClaimHistory.billed_amount,
                ClaimHistory.outcome,
                ClaimHistory.date_of_service,
                ClaimHistory.decided_at,
                ClaimHistory.notes,
            ).order_by(ClaimHistory.id)
        ).all()
    ]


def _fresh_session(db_path: Path) -> Session:
    engine = db.make_engine(f"sqlite:///{db_path}")
    db.Base.metadata.create_all(engine)
    return Session(engine)


def test_seed_is_idempotent(session: Session) -> None:
    first = seed_database(session, fixtures=False)
    assert len(first.users_created) == 5
    assert first.history_inserted == 40

    users_after_first = _user_count(session)
    history_after_first = _history_count(session)

    second = seed_database(session, fixtures=False)
    assert second.users_created == []
    assert len(second.users_skipped) == 5
    assert second.history_inserted == 0
    assert _user_count(session) == users_after_first
    assert _history_count(session) == history_after_first


def test_seed_creates_all_roles_and_history(session: Session) -> None:
    seed_database(session, fixtures=False)
    roles = set(session.scalars(select(User.role)).all())
    assert ALL_ROLES <= roles
    assert _history_count(session, "MBR-1001") == 25
    assert _history_count(session, "MBR-1002") == 15


def test_seed_history_is_deterministic(tmp_path: Path) -> None:
    one = _fresh_session(tmp_path / "one.sqlite")
    two = _fresh_session(tmp_path / "two.sqlite")
    try:
        seed_database(one, fixtures=False)
        seed_database(two, fixtures=False)
        rows_one = _history_tuples(one)
        rows_two = _history_tuples(two)
        assert len(rows_one) == 40
        assert rows_one == rows_two
    finally:
        one.close()
        two.close()


def test_no_fixtures_leaves_zero_claims(session: Session, settings: Settings) -> None:
    seed_database(session, settings, fixtures=False)
    assert (session.scalar(select(func.count()).select_from(Claim)) or 0) == 0


# ------------------------------------------------------------------ demo fixture claims
#
# The full fixture seed walks seven claims through the real state machine and the
# stage-1/2/3 runners, so it is seeded once per module and shared by the assertions.


@pytest.fixture(scope="module")
def seeded(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Session, Settings, SeedSummary]]:
    tmp = tmp_path_factory.mktemp("seed-fixtures")
    settings = Settings(
        database_url=f"sqlite:///{tmp}/seed.sqlite",
        upload_dir=tmp / "uploads",
        jwt_secret="test-secret-0123456789abcdef-0123456789",
        cookie_secure=False,
        email_provider="console",
        model_backend="stub",
        anthropic_api_key="",
        chroma_dir=tmp / "chroma",
    )
    create_app(settings)
    factory = db.get_session_factory()
    session = factory()
    try:
        summary = seed_database(session, settings)
        yield session, settings, summary
    finally:
        session.close()


def _claim_by_ref(session: Session, claim_ref: str) -> Claim:
    claim = session.scalar(select(Claim).where(Claim.claim_ref == claim_ref))
    assert claim is not None, f"fixture claim {claim_ref} missing"
    return claim


def test_fixtures_create_exactly_seven_claims_in_the_right_states(
    seeded: tuple[Session, Settings, SeedSummary],
) -> None:
    session, _, summary = seeded
    assert summary.fixture_claims == list(FIXTURE_STATES)
    assert (session.scalar(select(func.count()).select_from(Claim)) or 0) == 7
    actual = {
        ref: state for ref, state in session.execute(select(Claim.claim_ref, Claim.state)).all()
    }
    assert actual == FIXTURE_STATES


def test_tampered_claim_report_is_flagged(
    seeded: tuple[Session, Settings, SeedSummary],
) -> None:
    """Non-authentic either way: the stub's filename hook says likely_fraudulent,
    the real fusion lands suspicious via CNN + metadata hard-override."""
    session, _, _ = seeded
    claim = _claim_by_ref(session, REF_IMAGING_TAMPERED)
    report = session.scalar(
        select(DiagnosticReport)
        .where(DiagnosticReport.claim_id == claim.id)
        .order_by(DiagnosticReport.id.desc())
        .limit(1)
    )
    assert report is not None
    assert report.authenticity_verdict in ("suspicious", "likely_fraudulent")
    assert report.requires_mandatory_review is True
    document = session.scalar(select(Document).where(Document.claim_id == claim.id))
    assert document is not None and "tampered" in document.filename
    assert document.mime == "application/dicom"
    assert document.dicom_meta_json is not None  # metadata signal feeds on this


def test_specialist_and_adjudication_artifacts_are_complete(
    seeded: tuple[Session, Settings, SeedSummary],
) -> None:
    session, _, _ = seeded
    claim_c = _claim_by_ref(session, REF_SPECIALIST_REVIEW)
    note = session.scalar(
        select(RecommendationNote).where(RecommendationNote.claim_id == claim_c.id)
    )
    assert note is not None and note.status.value == "complete"

    claim_d = _claim_by_ref(session, REF_ADJUDICATION_FR)
    assert claim_d.claimant.preferred_language == "fr"
    assert claim_d.claimant.member_id == "MBR-1002"
    summary_row = session.execute(
        select(func.count()).select_from(Decision).where(Decision.claim_id == claim_d.id)
    ).scalar_one()
    assert summary_row >= 3  # submit, system imaging_complete, forward, send_to_insurer


def test_approved_claim_has_notification_and_agent_decision(
    seeded: tuple[Session, Settings, SeedSummary],
) -> None:
    session, _, _ = seeded
    claim = _claim_by_ref(session, REF_APPROVED)
    assert claim.state is ClaimState.APPROVED

    notifications = session.scalars(
        select(Notification).where(Notification.claim_id == claim.id)
    ).all()
    assert len(notifications) >= 1
    assert any(claim.claim_ref in n.subject for n in notifications)
    assert any("approved" in n.body_text.lower() for n in notifications)

    approve = session.scalar(
        select(Decision).where(
            Decision.claim_id == claim.id, Decision.action == ClaimAction.APPROVE
        )
    )
    assert approve is not None
    assert approve.actor_role == Role.INSURANCE_AGENT.value


def test_audit_chain_verifies_after_full_seed(
    seeded: tuple[Session, Settings, SeedSummary],
) -> None:
    session, _, _ = seeded
    valid, checked = verify_chain(session)
    assert valid is True
    assert checked > 0


def test_precedent_collection_holds_at_least_twelve_cases(
    seeded: tuple[Session, Settings, SeedSummary],
) -> None:
    _, settings, summary = seeded
    assert summary.precedents_indexed == 12
    collection = get_adjudicated_cases_collection(get_client(settings))
    assert collection.count() >= 12


def test_second_seed_run_adds_nothing(
    seeded: tuple[Session, Settings, SeedSummary],
) -> None:
    session, settings, _ = seeded
    before = {
        model: session.scalar(select(func.count()).select_from(model)) or 0
        for model in (Claim, Document, Decision, Notification, AuditEvent)
    }
    second = seed_database(session, settings)
    assert second.fixture_claims == []
    assert second.precedents_indexed == 0
    after = {
        model: session.scalar(select(func.count()).select_from(model)) or 0
        for model in (Claim, Document, Decision, Notification, AuditEvent)
    }
    assert after == before
