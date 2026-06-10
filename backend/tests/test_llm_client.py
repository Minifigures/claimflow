"""Unit tests for app.llm.client / app.llm.routing — no network.

The anthropic SDK is replaced via sys.modules with a fake whose `messages.parse`
returns canned SimpleNamespace responses and records the kwargs of every call.
"""

import hashlib
import json
import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from app.claimguard import audit
from app.config import Settings
from app.llm import routing
from app.llm.client import (
    InputTooLargeError,
    LLMRefusalError,
    LLMTruncatedError,
    LLMUnavailableError,
    generate,
)
from app.llm.schemas import ClaimantEmailLLM, DiagnosticReportLLM, RecommendationNoteLLM
from app.models import AuditEvent

PARSED = ClaimantEmailLLM(
    subject="Claim update", greeting="Hi Casey,", body_paragraphs=["All good."], closing="ClaimFlow"
)


def _usage(input_tokens: int = 1000, output_tokens: int = 200) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


def _response(
    parsed: Any = None,
    stop_reason: str = "end_turn",
    usage: SimpleNamespace | None = None,
    stop_details: SimpleNamespace | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        parsed_output=parsed,
        stop_reason=stop_reason,
        usage=usage or _usage(),
        stop_details=stop_details,
    )


def install_fake_anthropic(monkeypatch: pytest.MonkeyPatch, responses: list[Any]) -> list[dict]:
    """Install a fake `anthropic` module; returns the list of captured parse() kwargs."""
    calls: list[dict] = []

    class FakeMessages:
        def parse(self, **kwargs: Any) -> SimpleNamespace:
            index = min(len(calls), len(responses) - 1)
            calls.append(kwargs)
            return responses[index]

    class FakeAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            self.messages = FakeMessages()

        def with_options(self, **kwargs: Any) -> "FakeAnthropic":
            return self

    module = types.ModuleType("anthropic")
    module.Anthropic = FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return calls


@pytest.fixture()
def keyed_settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"anthropic_api_key": "sk-test"})


def _generate(settings: Settings, session, **overrides: Any):
    kwargs: dict[str, Any] = {
        "route_name": "claimant_email",
        "system": "You draft claimant status emails.",
        "user_content": [{"type": "text", "text": "hello"}],
        "schema": ClaimantEmailLLM,
        "claim_id": None,
        "prompt_version": "v1",
        "prompt_sha256": "deadbeef",
    }
    kwargs.update(overrides)
    return generate(settings, session, **kwargs)


def _llm_events(session) -> list[AuditEvent]:
    return list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "llm_call")
            .order_by(AuditEvent.id.asc())
        )
    )


# ---------------------------------------------------------------- availability gate


def test_unavailable_without_api_key(settings: Settings, session) -> None:
    with pytest.raises(LLMUnavailableError):
        _generate(settings, session)  # conftest settings have anthropic_api_key=""
    assert _llm_events(session) == []


# ---------------------------------------------------------------- success path


def test_success_returns_parsed_and_exact_cost(keyed_settings, session, monkeypatch) -> None:
    calls = install_fake_anthropic(monkeypatch, [_response(parsed=PARSED)])
    result = _generate(keyed_settings, session)

    assert result.parsed is PARSED
    assert result.model == "claude-haiku-4-5"
    assert result.stop_reason == "end_turn"
    assert result.retried is False
    assert result.input_tokens == 1000
    assert result.output_tokens == 200
    assert result.cost_usd == (1000 * 1.0 + 200 * 5.0) / 1_000_000
    assert len(calls) == 1

    events = _llm_events(session)
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["route"] == "claimant_email"
    assert payload["model"] == "claude-haiku-4-5"
    assert payload["stop_reason"] == "end_turn"
    assert payload["retried"] is False
    assert payload["cost_usd"] == result.cost_usd
    assert payload["prompt_version"] == "v1"
    assert payload["prompt_sha256"] == "deadbeef"
    assert (
        payload["response_sha256"]
        == hashlib.sha256(PARSED.model_dump_json().encode("utf-8")).hexdigest()
    )


# ---------------------------------------------------------------- refusal


def test_refusal_raises_and_audits(keyed_settings, session, monkeypatch) -> None:
    install_fake_anthropic(
        monkeypatch,
        [
            _response(
                stop_reason="refusal",
                stop_details=SimpleNamespace(category="cyber", explanation="not allowed"),
            )
        ],
    )
    with pytest.raises(LLMRefusalError) as excinfo:
        _generate(keyed_settings, session)
    assert excinfo.value.category == "cyber"
    assert excinfo.value.explanation == "not allowed"

    events = _llm_events(session)
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["stop_reason"] == "refusal"
    assert payload["response_sha256"] is None


# ---------------------------------------------------------------- max_tokens retry


def test_max_tokens_retries_once_with_doubled_budget(keyed_settings, session, monkeypatch) -> None:
    calls = install_fake_anthropic(
        monkeypatch, [_response(stop_reason="max_tokens"), _response(parsed=PARSED)]
    )
    result = _generate(keyed_settings, session)  # claimant_email starts at 1024

    assert result.retried is True
    assert result.parsed is PARSED
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 1024
    assert calls[1]["max_tokens"] == 2048

    events = _llm_events(session)
    assert len(events) == 2
    first, second = (json.loads(e.payload_json) for e in events)
    assert first["stop_reason"] == "max_tokens"
    assert first["retried"] is False
    assert second["stop_reason"] == "end_turn"
    assert second["retried"] is True


def test_max_tokens_at_cap_raises_truncated(keyed_settings, session, monkeypatch) -> None:
    calls = install_fake_anthropic(monkeypatch, [_response(stop_reason="max_tokens")])
    with pytest.raises(LLMTruncatedError):
        _generate(
            keyed_settings,
            session,
            route_name="stage2_recommendation",  # already at the 16000 cap
            schema=RecommendationNoteLLM,
        )
    assert len(calls) == 1  # no retry possible at the cap

    events = _llm_events(session)
    assert len(events) == 1
    assert json.loads(events[0].payload_json)["stop_reason"] == "max_tokens"


# ---------------------------------------------------------------- per-route kwargs


def test_stage2_kwargs_include_thinking_and_effort(keyed_settings, session, monkeypatch) -> None:
    calls = install_fake_anthropic(monkeypatch, [_response(parsed=PARSED)])
    _generate(keyed_settings, session, route_name="stage2_recommendation")

    (kwargs,) = calls
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["max_tokens"] == 16000
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "medium"}
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs


def test_claimant_email_kwargs_omit_thinking_and_effort(
    keyed_settings, session, monkeypatch
) -> None:
    calls = install_fake_anthropic(monkeypatch, [_response(parsed=PARSED)])
    _generate(keyed_settings, session, route_name="claimant_email")

    (kwargs,) = calls
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["max_tokens"] == 1024
    assert "thinking" not in kwargs
    assert "output_config" not in kwargs
    assert "temperature" not in kwargs


# ---------------------------------------------------------------- input size gate


def test_input_size_gate_raises_before_any_call(keyed_settings, session, monkeypatch) -> None:
    calls = install_fake_anthropic(monkeypatch, [_response(parsed=PARSED)])
    huge = "x" * 700_000  # 700k chars / 4 = 175k estimated tokens > 150k gate
    with pytest.raises(InputTooLargeError):
        _generate(keyed_settings, session, user_content=[{"type": "text", "text": huge}])
    assert calls == []
    assert _llm_events(session) == []


# ---------------------------------------------------------------- env overrides


def test_env_override_changes_stage2_model(keyed_settings, session, monkeypatch) -> None:
    monkeypatch.setenv("CLAIMFLOW_MODEL_STAGE2_RECOMMENDATION", "claude-sonnet-4-6")
    calls = install_fake_anthropic(monkeypatch, [_response(parsed=PARSED)])
    result = _generate(keyed_settings, session, route_name="stage2_recommendation")

    assert calls[0]["model"] == "claude-sonnet-4-6"
    assert result.model == "claude-sonnet-4-6"


def test_get_route_env_overrides_via_mapping() -> None:
    route = routing.get_route(
        "claimant_email",
        settings_env={
            "CLAIMFLOW_MODEL_CLAIMANT_EMAIL": "claude-sonnet-4-6",
            "CLAIMFLOW_MAX_TOKENS_CLAIMANT_EMAIL": "2048",
            "CLAIMFLOW_EFFORT_CLAIMANT_EMAIL": "low",
        },
    )
    assert route.model == "claude-sonnet-4-6"
    assert route.max_tokens == 2048
    assert route.effort == "low"
    # base route untouched (frozen dataclass + replace)
    assert routing.ROUTES["claimant_email"].model == "claude-haiku-4-5"


def test_estimate_cost_unknown_model_is_zero() -> None:
    assert routing.estimate_cost("not-a-model", 10_000, 10_000) == 0.0


# ---------------------------------------------------------------- image hashing


def test_image_blocks_hashed_not_embedded(keyed_settings, session, monkeypatch) -> None:
    install_fake_anthropic(monkeypatch, [_response(parsed=PARSED)])
    b64 = "aGVsbG8taW1hZ2UtYnl0ZXM="
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
        {"type": "text", "text": "describe this image"},
    ]
    _generate(
        keyed_settings,
        session,
        route_name="stage1_diagnostic",
        schema=DiagnosticReportLLM,
        user_content=content,
    )

    events = _llm_events(session)
    payload = json.loads(events[0].payload_json)
    assert payload["image_sha256"] == [hashlib.sha256(b64.encode("utf-8")).hexdigest()]
    assert payload["input_sha256"] == hashlib.sha256(b"describe this image").hexdigest()
    assert b64 not in events[0].payload_json  # raw base64 never lands in the audit log


# ---------------------------------------------------------------- audit chain integrity


def test_audit_chain_verifies_after_llm_calls(keyed_settings, session, monkeypatch) -> None:
    install_fake_anthropic(
        monkeypatch, [_response(stop_reason="max_tokens"), _response(parsed=PARSED)]
    )
    _generate(keyed_settings, session)
    session.commit()

    ok, checked = audit.verify_chain(session)
    assert ok is True
    assert checked >= 2
