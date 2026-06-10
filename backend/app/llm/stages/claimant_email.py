"""Claimant decision email drafting.

This is the ONLY LLM route that ever sees a claimant's name, and it sees nothing else:
the user turn is the minimal field set (decision, first_name, language, tone, claim_ref,
claim_type) — never history, scores, or medical findings. Keyless (or any LLM failure):
one of the eight static templates in ``fallbacks.fallback_claimant_email``.
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm import client as llm_client
from app.llm import fallbacks
from app.llm.prompts.loader import load_prompt
from app.llm.schemas import ClaimantEmailLLM, StageResult

ROUTE_NAME = "claimant_email"

_LANGUAGE_NAMES = {"en": "English", "fr": "French (Canadian French)"}

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


def draft_claimant_email(
    settings: Settings,
    session: Session,
    *,
    claim_id: int,
    decision: Literal["APPROVED", "REJECTED"],
    first_name: str,
    language: Literal["en", "fr"],
    tone: Literal["formal", "plain_language"],
    claim_ref: str,
    claim_type: str,
) -> StageResult:
    """Produce the claimant-email StageResult; never raises on LLM problems."""
    prompt = load_prompt(ROUTE_NAME)
    fallback_reason: str | None = None
    result: llm_client.LLMResult | None = None

    try:
        system = prompt.text.format(language=_LANGUAGE_NAMES.get(language, language))
        user_text = "\n".join(
            [
                f"decision: {decision}",
                f"first_name: {first_name}",
                f"language: {language}",
                f"tone: {tone}",
                f"claim_ref: {claim_ref}",
                f"claim_type: {claim_type}",
            ]
        )
        result = llm_client.generate(
            settings,
            session,
            route_name=ROUTE_NAME,
            system=system,
            user_content=[{"type": "text", "text": user_text}],
            schema=ClaimantEmailLLM,
            claim_id=claim_id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
        )
        parsed = cast(ClaimantEmailLLM, result.parsed)
    except _LLM_ERRORS as exc:
        fallback_reason = _fallback_reason(exc)
        parsed = fallbacks.fallback_claimant_email(
            decision=decision,
            first_name=first_name,
            language=language,
            tone=tone,
            claim_ref=claim_ref,
            claim_type=claim_type,
        )

    payload = parsed.model_dump()
    payload["decision"] = decision
    payload["language"] = language
    payload["tone"] = tone

    requires_review = fallback_reason in _MANDATORY_FALLBACKS
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
