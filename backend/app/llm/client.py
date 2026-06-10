"""Keyed-path LLM client: routed `messages.parse()` calls with audit, retry, and cost.

Every attempt (including refusals and truncations) is appended to the hash-chained
audit log; the caller owns the transaction and commits. Raw prompt/response text is
never stored in audit payloads — only sha256 digests and token/cost accounting.
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
    """No API key configured or the anthropic SDK is not importable."""


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


def llm_available(settings: Settings) -> bool:
    """True iff an API key is configured and the anthropic SDK is importable."""
    if not settings.anthropic_api_key:
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


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
    if not llm_available(settings):
        raise LLMUnavailableError("anthropic API key missing or SDK not installed")

    estimated = _estimated_input_tokens(user_content)
    if estimated > MAX_INPUT_EST_TOKENS:
        raise InputTooLargeError(
            f"estimated {estimated} input tokens exceeds gate of {MAX_INPUT_EST_TOKENS}"
        )

    import anthropic

    route = routing.get_route(route_name)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key).with_options(
        timeout=90.0, max_retries=2
    )
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
                "model": route.model,
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
