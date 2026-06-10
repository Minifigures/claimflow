"""Stage 2: recommendation note over the assembled evidence bundle.

Keyed path: PII-allowlisted bundle via ``assemble_stage2_bundle`` into a structured
opus call. Keyless (or any LLM failure): the deterministic rule engine in
``fallbacks.fallback_recommendation``. The payload always carries the advisory notice
and a documents-reviewed manifest (filenames + sha256 digests, never content).
"""

from __future__ import annotations

import hashlib
from typing import cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm import client as llm_client
from app.llm import fallbacks
from app.llm.documents import CLAIM_FORM_ALLOWLIST, assemble_stage2_bundle
from app.llm.prompts.loader import load_prompt
from app.llm.schemas import ADVISORY_NOTICE, RecommendationNoteLLM, StageResult

ROUTE_NAME = "stage2_recommendation"

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


def _flatten_report(report: dict) -> dict:
    """Map a stage-1 payload (flat or with nested system-injected sections) onto the
    flat keys the rule engine documents in ``fallback_recommendation``."""
    authenticity = report.get("authenticity") or {}
    classifier = report.get("classifier") or {}
    risk = report.get("authenticity_risk")
    return {
        "modality": (
            report.get("modality")
            or classifier.get("modality")
            or report.get("modality_assessment")
        ),
        "authenticity_verdict": report.get("authenticity_verdict") or authenticity.get("verdict"),
        "authenticity_risk": risk if risk is not None else authenticity.get("risk_score"),
        "requires_mandatory_review": bool(report.get("requires_mandatory_review")),
        "impression": report.get("impression"),
    }


def _rule_bundle(
    claim_fields: dict,
    diagnostic_report: dict,
    uploads: list[tuple[str, str]],
    modality_for_procedure: str | None,
) -> dict:
    return {
        "claim": {key: claim_fields.get(key) for key in CLAIM_FORM_ALLOWLIST},
        "diagnostic_report": _flatten_report(diagnostic_report) if diagnostic_report else None,
        "uploads": [
            {"filename": name, "kind": "document", "text_extract_ok": bool(text.strip())}
            for name, text in uploads
        ],
        "modality_for_procedure": modality_for_procedure,
    }


def generate_recommendation(
    settings: Settings,
    session: Session,
    *,
    claim_id: int,
    claim_fields: dict,
    diagnostic_report: dict,
    uploads: list[tuple[str, str]],
    modality_for_procedure: str | None,
) -> StageResult:
    """Produce the stage-2 recommendation StageResult; never raises on LLM problems."""
    prompt = load_prompt(ROUTE_NAME)
    fallback_reason: str | None = None
    result: llm_client.LLMResult | None = None

    try:
        bundle_text, truncation_notes = assemble_stage2_bundle(
            claim_fields, diagnostic_report, uploads
        )
        sections = [
            bundle_text,
            "<expected_modality>\n"
            + (modality_for_procedure or "(no expected modality on file for this procedure)")
            + "\n</expected_modality>",
        ]
        if truncation_notes:
            sections.append(
                "<truncation_notes>\n" + "\n".join(truncation_notes) + "\n</truncation_notes>"
            )
        result = llm_client.generate(
            settings,
            session,
            route_name=ROUTE_NAME,
            system=prompt.text,
            user_content=[{"type": "text", "text": "\n\n".join(sections)}],
            schema=RecommendationNoteLLM,
            claim_id=claim_id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
        )
        parsed = cast(RecommendationNoteLLM, result.parsed)
    except _LLM_ERRORS as exc:
        fallback_reason = _fallback_reason(exc)
        parsed = fallbacks.fallback_recommendation(
            _rule_bundle(claim_fields, diagnostic_report, uploads, modality_for_procedure)
        )

    payload = parsed.model_dump()
    payload["advisory_notice"] = ADVISORY_NOTICE
    payload["documents_reviewed"] = [
        {"filename": name, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        for name, text in uploads
    ]

    # A non-supporting recommendation below the confidence bar, or any low-confidence
    # result, needs a human; refusal/truncation fallbacks always do.
    requires_review = parsed.confidence < 0.6 or fallback_reason in _MANDATORY_FALLBACKS
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
