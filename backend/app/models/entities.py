from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.enums import ClaimState, DocumentKind, Modality, Role, db_enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(db_enum(Role, 32))
    full_name: Mapped[str] = mapped_column(String(255))
    member_id: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    preferred_language: Mapped[str] = mapped_column(String(8), default="en")
    preferred_tone: Mapped[str] = mapped_column(String(32), default="plain_language")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_ref: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    claimant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    claim_type: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    procedure_code: Mapped[str] = mapped_column(String(16), default="")
    diagnosis_code: Mapped[str] = mapped_column(String(16), default="")
    incident_date: Mapped[date | None] = mapped_column(Date, default=None)
    amount_claimed: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[ClaimState] = mapped_column(
        db_enum(ClaimState, 32), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    claimant: Mapped[User] = relationship(lazy="joined")
    documents: Mapped[list["Document"]] = relationship(back_populates="claim", lazy="selectin")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[DocumentKind] = mapped_column(db_enum(DocumentKind, 32))
    modality: Mapped[Modality | None] = mapped_column(
        db_enum(Modality, 16), default=None
    )
    filename: Mapped[str] = mapped_column(String(255))
    mime: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_path: Mapped[str] = mapped_column(String(512))
    preview_path: Mapped[str | None] = mapped_column(String(512), default=None)
    dicom_meta_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped[Claim] = relationship(back_populates="documents")
