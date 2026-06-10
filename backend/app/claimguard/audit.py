"""Hash-chained, actor-aware audit log.

Pattern adapted from the ClaimGuard POC audit log (hash-chained SQLite event log);
reimplemented on SQLAlchemy and extended so the actor fields are part of the record
hash — actor attribution cannot be edited without breaking the chain.
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, AuditEventType

GENESIS = "GENESIS"


def _canonical(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


def _record_hash(
    *,
    event_type: str,
    claim_id: int | None,
    actor_user_id: int | None,
    actor_role: str | None,
    payload_json: str,
    created_at: datetime,
    prev_hash: str,
) -> str:
    body = _canonical(
        {
            "event_type": event_type,
            "claim_id": claim_id,
            "actor_user_id": actor_user_id,
            "actor_role": actor_role,
            "payload": payload_json,
            "created_at": created_at.isoformat(),
            "prev_hash": prev_hash,
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def append(
    session: Session,
    event_type: AuditEventType | str,
    *,
    claim_id: int | None = None,
    actor_user_id: int | None = None,
    actor_role: str | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    """Append an event to the chain inside the caller's transaction (caller commits)."""
    last = session.scalar(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    prev_hash = last.record_hash if last is not None else GENESIS
    created_at = datetime.now(timezone.utc)
    payload_json = _canonical(payload or {})
    event = AuditEvent(
        event_type=str(event_type),
        claim_id=claim_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        payload_json=payload_json,
        created_at=created_at,
        prev_hash=prev_hash,
        record_hash=_record_hash(
            event_type=str(event_type),
            claim_id=claim_id,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            payload_json=payload_json,
            created_at=created_at,
            prev_hash=prev_hash,
        ),
    )
    session.add(event)
    session.flush()
    return event


def verify_chain(session: Session) -> tuple[bool, int]:
    """Recompute every hash in id order. Returns (chain_valid, events_checked)."""
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.id.asc())).all()
    prev_hash = GENESIS
    for event in events:
        if event.prev_hash != prev_hash:
            return False, len(events)
        created = event.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        expected = _record_hash(
            event_type=event.event_type,
            claim_id=event.claim_id,
            actor_user_id=event.actor_user_id,
            actor_role=event.actor_role,
            payload_json=event.payload_json,
            created_at=created,
            prev_hash=event.prev_hash,
        )
        if expected != event.record_hash:
            return False, len(events)
        prev_hash = event.record_hash
    return True, len(events)
