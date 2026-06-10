from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import db
from app.models import ClaimHistory, Role, User
from scripts.seed import seed_database

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
    first = seed_database(session)
    assert len(first.users_created) == 5
    assert first.history_inserted == 40

    users_after_first = _user_count(session)
    history_after_first = _history_count(session)

    second = seed_database(session)
    assert second.users_created == []
    assert len(second.users_skipped) == 5
    assert second.history_inserted == 0
    assert _user_count(session) == users_after_first
    assert _history_count(session) == history_after_first


def test_seed_creates_all_roles_and_history(session: Session) -> None:
    seed_database(session)
    roles = set(session.scalars(select(User.role)).all())
    assert ALL_ROLES <= roles
    assert _history_count(session, "MBR-1001") == 25
    assert _history_count(session, "MBR-1002") == 15


def test_seed_history_is_deterministic(tmp_path: Path) -> None:
    one = _fresh_session(tmp_path / "one.sqlite")
    two = _fresh_session(tmp_path / "two.sqlite")
    try:
        seed_database(one)
        seed_database(two)
        rows_one = _history_tuples(one)
        rows_two = _history_tuples(two)
        assert len(rows_one) == 40
        assert rows_one == rows_two
    finally:
        one.close()
        two.close()
