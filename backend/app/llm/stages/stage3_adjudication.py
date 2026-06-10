"""Stage 3: adjudication summary for the insurance agent.

Keyed path: ``assemble_stage3_context`` into a structured opus call. Keyless (or any
LLM failure): the deterministic ``fallbacks.fallback_adjudication``. On both paths the
similar-case facts (case_ref / similarity / outcome) are SYSTEM-copied from retrieval —
the model only ever authors the per-case relevance note, so precedents cannot be
fabricated.
"""

from __future__ import annotations

from typing import cast

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm import client as llm_client
from app.llm import fallbacks
from app.llm.documents import assemble_stage3_context
from app.llm.prompts.loader import load_prompt
from app.llm.schemas import (
    ADJUDICATION_NOTICE,
    AdjudicationSummaryLLM,
    SimilarCaseOutcome,
    StageResult,
)

ROUTE_NAME = "stage3_adjudication"
_NO_NOTE = "(no note)"

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


def _authenticity_verdict(diagnostic_report: dict) -> str | None:
    verdict = diagnostic_report.get("authenticity_verdict")
    if verdict is None:
        verdict = (diagnostic_report.get("authenticity") or {}).get("verdict")
    return verdict


def _similar_case_outcomes(similar_cases: list[dict], notes: list[str]) -> list[dict]:
    """SYSTEM-copy retrieval facts; the model contributes only the relevance note."""
    outcomes: list[dict] = []
    for index, case in enumerate(similar_cases):
        outcomes.append(
            SimilarCaseOutcome(
                case_ref=str(case.get("case_ref", "")),
                similarity=float(case.get("similarity", 0.0)),
                outcome=str(case.get("outcome", "")),
                relevance_note=notes[index] if index < len(notes) else _NO_NOTE,
            ).model_dump()
        )
    return outcomes


def generate_adjudication(
    settings: Settings,
    session: Session,
    *,
    claim_id: int,
    specialist_note: dict,
    diagnostic_report: dict,
    history_rows: list[dict],
    history_stats: dict,
    similar_cases: list[dict],
    claimant_docs: list[tuple[str, str]],
) -> StageResult:
    """Produce the stage-3 adjudication StageResult; never raises on LLM problems."""
    prompt = load_prompt(ROUTE_NAME)
    fallback_reason: str | None = None
    result: llm_client.LLMResult | None = None

    try:
        context = assemble_stage3_context(
            specialist_note, diagnostic_report, history_rows, similar_cases, claimant_docs
        )
        result = llm_client.generate(
            settings,
            session,
            route_name=ROUTE_NAME,
            system=prompt.text,
            user_content=[{"type": "text", "text": context}],
            schema=AdjudicationSummaryLLM,
            claim_id=claim_id,
            prompt_version=prompt.version,
            prompt_sha256=prompt.sha256,
        )
        parsed = cast(AdjudicationSummaryLLM, result.parsed)
    except _LLM_ERRORS as exc:
        fallback_reason = _fallback_reason(exc)
        parsed = fallbacks.fallback_adjudication(
            specialist_note.get("recommendation"),
            history_stats,
            similar_cases,
            _authenticity_verdict(diagnostic_report),
        )

    payload = parsed.model_dump()
    payload["similar_case_outcomes"] = _similar_case_outcomes(
        similar_cases, parsed.similar_case_relevance_notes
    )
    payload["advisory_notice"] = ADJUDICATION_NOTICE

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
