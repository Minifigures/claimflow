from pathlib import Path

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import db
from app.claimguard import audit
from app.models import AuditEvent, AuditEventType


def _build_chain(session: Session) -> list[AuditEvent]:
    events = [
        audit.append(
            session,
            AuditEventType.CLAIM_SUBMIT,
            claim_id=1,
            actor_user_id=10,
            actor_role="claimant",
            payload={"amount": 250.0, "claim_type": "imaging"},
        ),
        audit.append(
            session,
            "custom.review",
            claim_id=1,
            actor_user_id=11,
            actor_role="imaging_specialist",
            payload={"note": "scan looks consistent"},
        ),
        audit.append(
            session,
            AuditEventType.WORKFLOW_TRANSITION,
            claim_id=2,
            payload={"from": "SUBMITTED", "to": "IMAGING_REVIEW"},
        ),
        audit.append(
            session,
            AuditEventType.DECISION_APPROVE,
            claim_id=2,
            actor_user_id=12,
            actor_role="insurance_agent",
        ),
        audit.append(session, "auth.failed", payload={"email": "intruder@demo.ca"}),
        audit.append(
            session,
            AuditEventType.LLM_CALL,
            claim_id=3,
            actor_user_id=13,
            actor_role="system",
            payload={"model": "stub", "tokens": 42},
        ),
    ]
    session.commit()
    return events


def test_append_and_verify_mixed_events(session: Session) -> None:
    events = _build_chain(session)
    assert len(events) == 6
    assert events[0].event_type == "claim.submit"
    assert events[1].event_type == "custom.review"
    assert audit.verify_chain(session) == (True, 6)


def test_empty_chain_verifies(session: Session) -> None:
    assert audit.verify_chain(session) == (True, 0)


def test_first_event_prev_hash_is_genesis(session: Session) -> None:
    event = audit.append(
        session, AuditEventType.AUTH_LOGIN, actor_user_id=1, actor_role="claimant"
    )
    session.commit()
    assert audit.GENESIS == "GENESIS"
    assert event.prev_hash == audit.GENESIS


def test_each_prev_hash_links_to_previous_record_hash(session: Session) -> None:
    _build_chain(session)
    session.expire_all()
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.id.asc())).all()
    assert rows[0].prev_hash == audit.GENESIS
    for prev, curr in zip(rows, rows[1:], strict=False):
        assert curr.prev_hash == prev.record_hash


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("payload_json", '{"note":"totally different"}'),
        ("event_type", "decision.reject"),
        ("claim_id", 777),
        ("actor_user_id", 999),
        ("actor_role", "insurance_agent"),
    ],
)
def test_tampering_middle_event_breaks_chain(
    session: Session, column: str, value: str | int
) -> None:
    events = _build_chain(session)
    assert audit.verify_chain(session) == (True, len(events))
    target = events[1]
    assert getattr(target, column) != value
    stmt = update(AuditEvent).where(AuditEvent.id == target.id).values(**{column: value})
    session.execute(stmt)
    session.commit()
    session.expire_all()
    assert audit.verify_chain(session) == (False, len(events))


def test_tampering_actor_attribution_breaks_chain(session: Session) -> None:
    events = _build_chain(session)
    target = events[3]
    stmt = (
        update(AuditEvent)
        .where(AuditEvent.id == target.id)
        .values(actor_user_id=10, actor_role="claimant")
    )
    session.execute(stmt)
    session.commit()
    session.expire_all()
    assert audit.verify_chain(session) == (False, len(events))


def test_deleting_middle_row_breaks_chain(session: Session) -> None:
    events = _build_chain(session)
    session.execute(delete(AuditEvent).where(AuditEvent.id == events[2].id))
    session.commit()
    session.expire_all()
    assert audit.verify_chain(session) == (False, len(events) - 1)


def test_record_hash_recomputation_is_deterministic(session: Session) -> None:
    event = audit.append(
        session,
        AuditEventType.DOCUMENT_UPLOAD,
        claim_id=4,
        actor_user_id=10,
        actor_role="claimant",
        payload={"filename": "xray.dcm", "size_bytes": 1024},
    )
    session.commit()
    recomputed = [
        audit._record_hash(
            event_type=event.event_type,
            claim_id=event.claim_id,
            actor_user_id=event.actor_user_id,
            actor_role=event.actor_role,
            payload_json=event.payload_json,
            created_at=event.created_at,
            prev_hash=event.prev_hash,
        )
        for _ in range(2)
    ]
    assert recomputed[0] == recomputed[1] == event.record_hash


def test_payload_key_order_is_irrelevant(session: Session, tmp_path: Path) -> None:
    engine = db.make_engine(f"sqlite:///{tmp_path}/second-chain.sqlite")
    db.Base.metadata.create_all(engine)
    other = Session(engine)
    try:
        first = audit.append(session, "order.test", payload={"a": 1, "b": 2})
        second = audit.append(other, "order.test", payload={"b": 2, "a": 1})
        session.commit()
        other.commit()
        assert first.prev_hash == second.prev_hash == audit.GENESIS
        assert first.payload_json == second.payload_json == '{"a":1,"b":2}'
    finally:
        other.close()
        engine.dispose()
