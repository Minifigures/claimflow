"""Stage 1c: draft diagnostic report for a single medical image.

Keyed path: downscale + re-encode the image, fill the stage-1 system prompt with the
classifier/forensics context, and run a structured vision call. Keyless (or any LLM
failure): render the deterministic fallback. Either way the authenticity facts and the
classifier verdict are SYSTEM-injected post-parse — the model can never author them.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm import client as llm_client
from app.llm import fallbacks
from app.llm.prompts.loader import load_prompt
from app.llm.schemas import DIAGNOSTIC_DISCLAIMER, DiagnosticReportLLM, StageResult
from app.ml.base import ImagingAnalysis

ROUTE_NAME = "stage1_diagnostic"
MAX_IMAGE_LONG_EDGE = 1568
_JPEG_QUALITY = 90
_SIGNAL_FLAG_THRESHOLD = 0.5

_LLM_ERRORS = (
    llm_client.LLMUnavailableError,
    llm_client.LLMRefusalError,
    llm_client.LLMTruncatedError,
    llm_client.InputTooLargeError,
    ValidationError,
)
_MANDATORY_FALLBACKS = frozenset({"refusal", "llm_truncated"})


def _fallback_reason(exc: Exception) -> str:
    if isinstance(exc, llm_client.LLMUnavailableError):
        return "no_api_key"
    if isinstance(exc, llm_client.LLMRefusalError):
        return "refusal"
    if isinstance(exc, llm_client.LLMTruncatedError):
        return "llm_truncated"
    return "llm_error"


def _encode_image_b64(image_path: Path) -> str:
    """Downscale to <= MAX_IMAGE_LONG_EDGE on the long edge, re-encode JPEG in memory."""
    from PIL import Image

    with Image.open(image_path) as opened:
        image = opened.copy() if opened.mode in ("L", "RGB") else opened.convert("RGB")
    long_edge = max(image.size)
    if long_edge > MAX_IMAGE_LONG_EDGE:
        scale = MAX_IMAGE_LONG_EDGE / long_edge
        new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        image = image.resize(new_size, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii")


def _format_system(prompt_text: str, analysis: ImagingAnalysis) -> str:
    flags = [
        f"{signal.name}: {signal.finding}"
        for signal in analysis.signals
        if signal.score >= _SIGNAL_FLAG_THRESHOLD
    ]
    return prompt_text.format(
        modality=analysis.modality,
        modality_confidence=f"{analysis.modality_confidence:.2f}",
        authenticity_risk=f"{analysis.authenticity_risk:.2f}",
        authenticity_flags="; ".join(flags) if flags else "none",
    )


def generate_diagnostic_report(
    settings: Settings,
    session: Session,
    *,
    claim_id: int,
    image_path: Path,
    image_media_type: str,
    analysis: ImagingAnalysis,
    declared_modality: str | None,
) -> StageResult:
    """Produce the stage-1 diagnostic report StageResult; never raises on LLM problems.

    ``image_media_type`` records the upload's original type; the keyed path always
    re-encodes to JPEG before sending, so the request media type is ``image/jpeg``.
    """
    prompt = load_prompt(ROUTE_NAME)
    fallback_reason: str | None = None
    result: llm_client.LLMResult | None = None

    try:
        if not llm_client.llm_available(settings):
            raise llm_client.LLMUnavailableError("anthropic API key missing or SDK not installed")
        system = _format_system(prompt.text, analysis)
        image_b64 = _encode_image_b64(image_path)
        context = (
            f"Claim #{claim_id}. Declared modality: {declared_modality or 'not declared'}. "
            f"Original upload media type: {image_media_type}. "
            "Draft the structured preliminary diagnostic report for the attached image."
        )
        user_content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64},
            },
            {"type": "text", "text": context},
        ]
        result = llm_client.generate(
            settings,
            session,
            route_name=ROUTE_NAME,
            system=system,
            user_content=user_content,
            schema=DiagnosticReportLLM,
            claim_id=claim_id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
        )
        parsed = cast(DiagnosticReportLLM, result.parsed)
    except _LLM_ERRORS as exc:
        fallback_reason = _fallback_reason(exc)
        parsed = fallbacks.fallback_diagnostic_report(analysis, declared_modality=declared_modality)

    payload = parsed.model_dump()
    # SYSTEM-injected facts — never LLM-authored, always overwrite whatever came back.
    payload["authenticity"] = {
        "verdict": analysis.authenticity_verdict,
        "risk_score": analysis.authenticity_risk,
        "signals": [signal.model_dump() for signal in analysis.signals],
    }
    payload["classifier"] = {
        "modality": analysis.modality,
        "confidence": analysis.modality_confidence,
    }
    payload["disclaimer"] = DIAGNOSTIC_DISCLAIMER

    requires_review = (
        analysis.authenticity_verdict != "authentic"
        or parsed.confidence < 0.5
        or fallback_reason in _MANDATORY_FALLBACKS
    )
    return StageResult(
        payload=payload,
        generated_by=result.model if result is not None else "fallback_template",
        prompt_version=prompt.version,
        fallback_reason=fallback_reason,
        requires_mandatory_review=requires_review,
        input_tokens=result.input_tokens if result is not None else 0,
        output_tokens=result.output_tokens if result is not None else 0,
        cost_usd=result.cost_usd if result is not None else 0.0,
        latency_ms=result.latency_ms if result is not None else 0,
    )
