"""Keyed-path LLM client: routed structured calls with audit, retry, and cost.

Provider-pluggable: Anthropic (primary, Claude routing per stage) with a Gemini
free-tier lane when only GEMINI_API_KEY is configured. Both providers share the
same audit, truncation-retry, refusal, and schema-validation semantics, so the
stages cannot tell them apart. Every attempt (including refusals and truncations)
is appended to the hash-chained audit log; the caller owns the transaction and
commits. Raw prompt/response text is never stored in audit payloads — only
sha256 digests and token/cost accounting.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.claimguard import audit
from app.config import Settings
from app.llm import routing
from app.models.enums import AuditEventType

MAX_TOKENS_CAP = 16_000
MAX_INPUT_EST_TOKENS = 150_000
_CHARS_PER_TOKEN = 4


class LLMUnavailableError(Exception):
    """No usable provider (missing keys/SDK) or the provider transport failed."""


class LLMRefusalError(Exception):
    """The model refused (stop_reason == 'refusal')."""

    def __init__(self, category: str | None = None, explanation: str | None = None) -> None:
        self.category = category
        self.explanation = explanation
        super().__init__(f"LLM refused (category={category}): {explanation or 'no explanation'}")


class LLMTruncatedError(Exception):
    """Output hit max_tokens at the cap (or again after the single retry)."""


class InputTooLargeError(Exception):
    """Estimated input tokens exceed the crude pre-flight gate."""


def resolve_provider(settings: Settings) -> str | None:
    """Pick the live provider: Anthropic when its key is usable, else Gemini, else None."""
    if settings.anthropic_api_key:
        try:
            import anthropic  # noqa: F401

            return "anthropic"
        except ImportError:
            pass
    if settings.gemini_api_key:
        try:
            import httpx  # noqa: F401

            return "gemini"
        except ImportError:
            pass
    return None


def llm_available(settings: Settings) -> bool:
    """True iff some provider has a configured key and an importable transport."""
    return resolve_provider(settings) is not None


@dataclass(frozen=True)
class LLMResult:
    parsed: BaseModel
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    stop_reason: str
    retried: bool


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_user_content(user_content: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """Digest text blocks into one input hash; hash image base64 payloads separately."""
    text_parts: list[str] = []
    image_hashes: list[str] = []
    for block in user_content:
        if block.get("type") == "text":
            text_parts.append(str(block.get("text", "")))
        elif block.get("type") == "image":
            data = block.get("source", {}).get("data", "")
            image_hashes.append(_sha256(str(data)))
    return _sha256("\n".join(text_parts)), image_hashes


def _estimated_input_tokens(user_content: list[dict[str, Any]]) -> int:
    total_chars = sum(
        len(str(block.get("text", ""))) for block in user_content if block.get("type") == "text"
    )
    return total_chars // _CHARS_PER_TOKEN


def generate(
    settings: Settings,
    session: Session,
    *,
    route_name: str,
    system: str,
    user_content: list[dict[str, Any]],
    schema: type[BaseModel],
    claim_id: int | None,
    prompt_version: str,
    prompt_sha256: str,
) -> LLMResult:
    """Run one routed, structured LLM call; audit every attempt (caller commits)."""
    provider = resolve_provider(settings)
    if provider is None:
        raise LLMUnavailableError("no LLM provider configured (anthropic or gemini key)")

    estimated = _estimated_input_tokens(user_content)
    if estimated > MAX_INPUT_EST_TOKENS:
        raise InputTooLargeError(
            f"estimated {estimated} input tokens exceeds gate of {MAX_INPUT_EST_TOKENS}"
        )

    route = routing.get_route(route_name)
    model_name = route.model if provider == "anthropic" else settings.gemini_model
    input_sha256, image_sha256 = _hash_user_content(user_content)

    def record(
        *,
        stop_reason: str,
        retried: bool,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
        response_sha256: str | None,
    ) -> None:
        audit.append(
            session,
            AuditEventType.LLM_CALL,
            claim_id=claim_id,
            actor_role="system",
            payload={
                "route": route_name,
                "model": model_name,
                "prompt_version": prompt_version,
                "prompt_sha256": prompt_sha256,
                "input_sha256": input_sha256,
                "image_sha256": image_sha256,
                "response_sha256": response_sha256,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "latency_ms": latency_ms,
                "stop_reason": stop_reason,
                "retried": retried,
            },
        )

    max_tokens = route.max_tokens
    retried = False
    total_input = 0
    total_output = 0
    total_cost = 0.0
    total_latency = 0

    if provider == "gemini":
        from app.llm import gemini as gemini_provider

        response_schema = schema.model_json_schema()
        while True:
            start = time.perf_counter()
            try:
                outcome = gemini_provider.call_gemini(
                    api_key=settings.gemini_api_key,
                    model=model_name,
                    system=system,
                    user_content=user_content,
                    response_schema=response_schema,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                # Transport/HTTP failures degrade to the deterministic fallback
                # (stages catch LLMUnavailableError), never fail the stage.
                raise LLMUnavailableError(f"gemini call failed: {exc}") from exc
            latency_ms = int((time.perf_counter() - start) * 1000)

            cost_usd = routing.estimate_cost(model_name, outcome.input_tokens, outcome.output_tokens)
            total_input += outcome.input_tokens
            total_output += outcome.output_tokens
            total_cost += cost_usd
            total_latency += latency_ms

            if outcome.stop_reason == "refusal":
                record(
                    stop_reason="refusal",
                    retried=retried,
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    response_sha256=None,
                )
                raise LLMRefusalError(category=outcome.refusal_detail, explanation=None)

            if outcome.stop_reason == "max_tokens":
                record(
                    stop_reason="max_tokens",
                    retried=retried,
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    response_sha256=None,
                )
                if retried or max_tokens >= MAX_TOKENS_CAP:
                    raise LLMTruncatedError(
                        f"route {route_name!r} truncated at max_tokens={max_tokens}"
                    )
                retried = True
                max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)
                continue

            try:
                parsed = gemini_provider.parse_or_raise(outcome.text, schema)
            except Exception:
                record(
                    stop_reason="parse_error",
                    retried=retried,
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    response_sha256=_sha256(outcome.text),
                )
                raise  # ValidationError: stages catch it into their fallback

            record(
                stop_reason="end_turn",
                retried=retried,
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                response_sha256=_sha256(parsed.model_dump_json()),
            )
            return LLMResult(
                parsed=parsed,
                model=model_name,
                input_tokens=total_input,
                output_tokens=total_output,
                cost_usd=total_cost,
                latency_ms=total_latency,
                stop_reason="end_turn",
                retried=retried,
            )

    import anthropic

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key).with_options(
        timeout=90.0, max_retries=2
    )

    while True:
        kwargs: dict[str, Any] = {
            "model": route.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "output_format": schema,
        }
        if route.adaptive_thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        if route.effort:
            kwargs["output_config"] = {"effort": route.effort}

        start = time.perf_counter()
        response = client.messages.parse(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)

        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        cost_usd = routing.estimate_cost(route.model, input_tokens, output_tokens)
        total_input += input_tokens
        total_output += output_tokens
        total_cost += cost_usd
        total_latency += latency_ms

        stop_reason = str(response.stop_reason)

        if stop_reason == "refusal":
            record(
                stop_reason=stop_reason,
                retried=retried,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                response_sha256=None,
            )
            details = getattr(response, "stop_details", None)
            raise LLMRefusalError(
                category=getattr(details, "category", None),
                explanation=getattr(details, "explanation", None),
            )

        if stop_reason == "max_tokens":
            record(
                stop_reason=stop_reason,
                retried=retried,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                response_sha256=None,
            )
            if retried or max_tokens >= MAX_TOKENS_CAP:
                raise LLMTruncatedError(
                    f"route {route_name!r} truncated at max_tokens={max_tokens}"
                )
            retried = True
            max_tokens = min(max_tokens * 2, MAX_TOKENS_CAP)
            continue

        parsed: BaseModel = response.parsed_output
        record(
            stop_reason=stop_reason,
            retried=retried,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            response_sha256=_sha256(parsed.model_dump_json()),
        )
        return LLMResult(
            parsed=parsed,
            model=route.model,
            input_tokens=total_input,
            output_tokens=total_output,
            cost_usd=total_cost,
            latency_ms=total_latency,
            stop_reason=stop_reason,
            retried=retried,
        )
