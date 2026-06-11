"""Gemini provider for the keyed LLM path (free-tier lane).

Speaks the Generative Language REST API directly over httpx (no extra SDK):
JSON-mode structured output validated against the same pydantic schemas the
Anthropic path uses, with content blocks translated from the Anthropic shape
(text / base64 image) to Gemini parts. `client.generate` owns routing, audit,
retries, and cost accounting; this module only makes one HTTP call and maps
the response into provider-neutral terms.

Free-tier limits (Flash): ~10 requests/min — fine for a demo, and the routing
env overrides (CLAIMFLOW_GEMINI_MODEL) allow model swaps without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# finishReason / blockReason values that mean the model declined the request.
_REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "IMAGE_SAFETY"}


@dataclass(frozen=True)
class GeminiOutcome:
    text: str
    stop_reason: str  # provider-neutral: end_turn | max_tokens | refusal
    input_tokens: int
    output_tokens: int
    refusal_detail: str | None = None


def _to_parts(user_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate Anthropic-shaped content blocks into Gemini parts."""
    parts: list[dict[str, Any]] = []
    for block in user_content:
        if block.get("type") == "text":
            parts.append({"text": str(block.get("text", ""))})
        elif block.get("type") == "image":
            source = block.get("source", {})
            parts.append(
                {
                    "inline_data": {
                        "mime_type": source.get("media_type", "image/png"),
                        "data": source.get("data", ""),
                    }
                }
            )
    return parts


def _map_stop(finish_reason: str | None, block_reason: str | None) -> str:
    if block_reason or (finish_reason or "").upper() in _REFUSAL_REASONS:
        return "refusal"
    if (finish_reason or "").upper() == "MAX_TOKENS":
        return "max_tokens"
    return "end_turn"


def call_gemini(
    *,
    api_key: str,
    model: str,
    system: str,
    user_content: list[dict[str, Any]],
    response_schema: dict[str, Any] | None,
    max_tokens: int,
    timeout: float = 90.0,
) -> GeminiOutcome:
    """One generateContent call; returns provider-neutral outcome.

    Sends the pydantic JSON schema as ``responseJsonSchema``; if this Gemini API
    version rejects that field, retries once in plain JSON mode (the caller still
    validates against the schema, so nothing is trusted either way).
    """
    import httpx

    generation_config: dict[str, Any] = {
        "maxOutputTokens": max_tokens,
        "responseMimeType": "application/json",
    }
    if response_schema is not None:
        generation_config["responseJsonSchema"] = response_schema

    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": _to_parts(user_content)}],
        "generationConfig": generation_config,
    }
    url = f"{GEMINI_BASE_URL}/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key}

    with httpx.Client(timeout=timeout) as http:
        response = http.post(url, json=body, headers=headers)
        if response.status_code == 400 and "responseJsonSchema" in response.text:
            generation_config.pop("responseJsonSchema", None)
            response = http.post(url, json=body, headers=headers)
        response.raise_for_status()
    payload = response.json()

    feedback = payload.get("promptFeedback", {}) or {}
    block_reason = feedback.get("blockReason")
    candidates = payload.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    finish_reason = candidate.get("finishReason")
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(str(p.get("text", "")) for p in parts)

    usage = payload.get("usageMetadata", {}) or {}
    return GeminiOutcome(
        text=text,
        stop_reason=_map_stop(finish_reason, block_reason),
        input_tokens=int(usage.get("promptTokenCount", 0)),
        output_tokens=int(usage.get("candidatesTokenCount", 0)),
        refusal_detail=block_reason or finish_reason,
    )


def parse_or_raise(text: str, schema: type) -> Any:
    """Validate the JSON-mode text against the route's pydantic schema.

    Raises pydantic ValidationError (which every stage already catches into its
    deterministic fallback) when the model returns malformed or off-schema JSON.
    """
    from pydantic import ValidationError

    try:
        return schema.model_validate_json(text)
    except ValidationError:
        # One lenient pass: Flash occasionally wraps JSON in markdown fences.
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            stripped = stripped[stripped.find("{") : stripped.rfind("}") + 1]
            return schema.model_validate_json(stripped)
        if (start := stripped.find("{")) != -1 and (end := stripped.rfind("}")) != -1:
            candidate = stripped[start : end + 1]
            if candidate != stripped:
                return schema.model_validate_json(candidate)
        raise
