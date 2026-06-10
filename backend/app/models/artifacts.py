from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.entities import utcnow
from app.models.enums import ArtifactStatus, Recommendation, db_enum


class ArtifactColumns:
    """Shared columns for the three per-stage ML artifacts."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[ArtifactStatus] = mapped_column(
        db_enum(ArtifactStatus, 16), default=ArtifactStatus.PENDING
    )
    payload_json: Mapped[str | None] = mapped_column(Text, default=None)
    generated_by: Mapped[str] = mapped_column(String(128), default="")
    prompt_version: Mapped[str] = mapped_column(String(64), default="")
    fallback_reason: Mapped[str | None] = mapped_column(String(64), default=None)
    requires_mandatory_review: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class DiagnosticReport(ArtifactColumns, Base):
    __tablename__ = "diagnostic_reports"

    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), default=None)
    modality: Mapped[str | None] = mapped_column(String(16), default=None)
    modality_confidence: Mapped[float | None] = mapped_column(Float, default=None)
    authenticity_verdict: Mapped[str | None] = mapped_column(String(32), default=None)
    authenticity_risk: Mapped[float | None] = mapped_column(Float, default=None)


class RecommendationNote(ArtifactColumns, Base):
    __tablename__ = "recommendation_notes"

    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    recommendation: Mapped[Recommendation | None] = mapped_column(
        db_enum(Recommendation, 32), default=None
    )
    confidence: Mapped[float | None] = mapped_column(Float, default=None)


class AdjudicationSummary(ArtifactColumns, Base):
    __tablename__ = "adjudication_summaries"

    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    recommendation_lean: Mapped[str | None] = mapped_column(String(32), default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
