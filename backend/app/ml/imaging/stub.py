"""Deterministic stub analyzer — the assessment's allowed 'mock', made demo-coherent.

Outputs derive from the file hash (stable across runs). Demo hooks:
- a filename containing 'tampered' produces a suspicious/likely_fraudulent verdict
  with plausible per-signal findings;
- a DICOM Modality tag disagreeing with the declared modality trips the
  metadata hard-override, mirroring the real fusion's behavior.
"""

import hashlib
from pathlib import Path

from app.ml.base import ForensicSignal, ImagingAnalysis

_MODALITIES = ("xray", "ct", "mri")

_DICOM_MODALITY_MAP = {"CR": "xray", "DX": "xray", "CT": "ct", "MR": "mri"}


class StubAnalyzer:
    def analyze(
        self,
        image_path: Path,
        *,
        declared_modality: str | None,
        dicom_meta: dict | None,
    ) -> ImagingAnalysis:
        digest = hashlib.sha256(image_path.read_bytes()).digest()
        seed = digest[0]

        modality = declared_modality or _MODALITIES[seed % 3]
        confidence = 0.90 + (seed % 8) / 100.0
        probs = {m: round(0.02 + (1 - confidence) / 2, 4) for m in _MODALITIES}
        probs[modality] = round(confidence, 4)

        signals: list[ForensicSignal] = [
            ForensicSignal(
                name="metadata",
                score=0.05,
                finding="EXIF/DICOM metadata consistent with declared acquisition.",
            ),
            ForensicSignal(
                name="ela",
                score=0.08,
                finding="Error-level analysis residuals uniform across tiles.",
            ),
            ForensicSignal(
                name="fft",
                score=0.06,
                finding="No periodic spectral peaks indicative of resampling.",
            ),
        ]
        verdict, risk = "authentic", 0.07
        quality_flags: list[str] = []

        tampered = "tampered" in image_path.name.lower()
        dicom_modality = None
        if dicom_meta:
            dicom_modality = _DICOM_MODALITY_MAP.get(str(dicom_meta.get("Modality", "")).upper())

        if tampered:
            verdict, risk = "likely_fraudulent", 0.81
            signals = [
                ForensicSignal(
                    name="ela",
                    score=0.84,
                    finding="Localized high-residual region (lower quadrant) consistent "
                    "with splicing.",
                ),
                ForensicSignal(
                    name="copy_move",
                    score=0.71,
                    finding="Keypoint self-matches indicate a cloned patch.",
                ),
                ForensicSignal(
                    name="metadata",
                    score=0.55,
                    finding="Software tag present without scanner manufacturer tags.",
                ),
            ]
        elif dicom_modality is not None and declared_modality and dicom_modality != declared_modality:
            verdict, risk = "suspicious", 0.62
            signals.append(
                ForensicSignal(
                    name="metadata",
                    score=0.90,
                    finding=f"DICOM Modality tag ({dicom_modality}) disagrees with the "
                    f"declared modality ({declared_modality}) — metadata hard-override.",
                )
            )

        if image_path.stat().st_size < 50_000:
            quality_flags.append("low_resolution")
        if image_path.suffix.lower() not in (".dcm", ".dicom"):
            quality_flags.append("non_dicom_upload")

        return ImagingAnalysis(
            modality=modality,
            modality_confidence=round(confidence, 4),
            modality_probs=probs,
            authenticity_verdict=verdict,
            authenticity_risk=risk,
            signals=signals,
            quality_flags=quality_flags,
            backend="stub",
        )
