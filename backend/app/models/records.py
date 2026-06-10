from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.entities import utcnow
from app.models.enums import ClaimAction, ClaimState, NotificationStatus, Role, db_enum


class Decision(Base):
    """One row per human/system workflow action — drives the claimant timeline UI."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    actor_role: Mapped[str] = mapped_column(String(32))  # Role value or "system"
    action: Mapped[ClaimAction] = mapped_column(db_enum(ClaimAction, 32))
    from_state: Mapped[ClaimState | None] = mapped_column(
        db_enum(ClaimState, 32), default=None
    )
    to_state: Mapped[ClaimState] = mapped_column(db_enum(ClaimState, 32))
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(16), default="email")
    subject: Mapped[str] = mapped_column(String(255))
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text, default=None)
    provider: Mapped[str] = mapped_column(String(32), default="console")
    status: Mapped[NotificationStatus] = mapped_column(
        db_enum(NotificationStatus, 16), default=NotificationStatus.LOGGED
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class ClaimHistory(Base):
    """PII-free historical claims per member (seeded; queried by stage 3)."""

    __tablename__ = "claim_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    member_id: Mapped[str] = mapped_column(String(32), index=True)
    claim_type: Mapped[str] = mapped_column(String(64))
    procedure_code: Mapped[str] = mapped_column(String(16))
    diagnosis_code: Mapped[str] = mapped_column(String(16))
    modality: Mapped[str | None] = mapped_column(String(16), default=None)
    billed_amount: Mapped[float] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(16))  # approved | rejected
    date_of_service: Mapped[date | None] = mapped_column(Date, default=None)
    decided_at: Mapped[date | None] = mapped_column(Date, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)


class AuditEvent(Base):
    """Hash-chained, actor-aware audit log (pattern vendored from the ClaimGuard POC,
    extended: actor fields are part of the record hash)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(48), index=True)
    claim_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, default=None)
    actor_role: Mapped[str | None] = mapped_column(String(32), default=None)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    prev_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64), unique=True)
