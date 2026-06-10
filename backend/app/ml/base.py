"""Stage-1 imaging analysis contract — the seam between the workflow and any backend.

`MODEL_BACKEND=stub` (default) serves deterministic results so the demo runs with no
weights and no API key; `real` loads the trained CNNs + forensics (Day 7-8).
"""

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from app.config import Settings


class ForensicSignal(BaseModel):
    name: str
    score: float = Field(ge=0.0, le=1.0)
    finding: str


class ImagingAnalysis(BaseModel):
    modality: str  # xray | ct | mri
    modality_confidence: float = Field(ge=0.0, le=1.0)
    modality_probs: dict[str, float]
    authenticity_verdict: str  # authentic | suspicious | likely_fraudulent
    authenticity_risk: float = Field(ge=0.0, le=1.0)
    signals: list[ForensicSignal]
    quality_flags: list[str]
    backend: str  # "stub" | "real"


class ImagingAnalyzer(Protocol):
    def analyze(
        self,
        image_path: Path,
        *,
        declared_modality: str | None,
        dicom_meta: dict | None,
    ) -> ImagingAnalysis: ...


def get_analyzer(settings: Settings) -> ImagingAnalyzer:
    if settings.model_backend == "real":
        try:
            from app.ml.imaging.real import RealAnalyzer

            return RealAnalyzer(settings)
        except Exception:  # missing weights/deps — degrade loudly, never crash
            import logging

            logging.getLogger("claimflow.ml").exception(
                "real backend unavailable; degrading to stub analyzer"
            )
    from app.ml.imaging.stub import StubAnalyzer

    return StubAnalyzer()
