"""Unit tests for the Gemini provider lane in app.llm.client — no network.

httpx.Client is monkeypatched with a fake that returns canned generateContent
payloads; the assertions cover provider resolution, structured parsing, refusal
and truncation mapping, markdown-fence salvage, transport degradation, and the
audit trail rows the shared record() path writes.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.client import (
    LLMRefusalError,
    LLMUnavailableError,
    generate,
    llm_available,
    resolve_provider,
)
from app.llm.schemas import ClaimantEmailLLM
from app.models import AuditEvent

PARSED = ClaimantEmailLLM(
    subject="Claim update", greeting="Hi Casey,", body_paragraphs=["All good."], closing="ClaimFlow"
)
PARSED_JSON = PARSED.model_dump_json()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.sqlite",
        upload_dir=tmp_path / "uploads",
        anthropic_api_key="",
        gemini_api_key="test-gemini-key",
        chroma_dir=tmp_path / "chroma",
    )


def _gemini_ok(text: str = PARSED_JSON, finish: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 40},
    }


def _gemini_blocked() -> dict[str, Any]:
    return {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


class _FakeClient:
    """Stands in for httpx.Client; pops one canned response per post call."""

    queue: list[Any] = []
    calls: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> None: ...

    def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        _FakeClient.calls.append({"url": url, "body": json, "headers": headers})
        item = _FakeClient.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch) -> type[_FakeClient]:
    _FakeClient.queue = []
    _FakeClient.calls = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    return _FakeClient


def test_provider_resolution(settings: Settings, tmp_path: Path) -> None:
    assert resolve_provider(settings) == "gemini"
    assert llm_available(settings) is True
    both = settings.model_copy(update={"anthropic_api_key": "sk-ant-x"})
    assert resolve_provider(both) == "anthropic"
    neither = settings.model_copy(update={"gemini_api_key": ""})
    assert resolve_provider(neither) is None
    assert llm_available(neither) is False


def _generate(settings: Settings, session: Session):
    return generate(
        settings,
        session,
        route_name="claimant_email",
        system="You draft emails.",
        user_content=[{"type": "text", "text": "Draft the decision email."}],
        schema=ClaimantEmailLLM,
        claim_id=None,
        prompt_version="v1",
        prompt_sha256="0" * 64,
    )


def test_gemini_happy_path_audits_and_parses(
    settings: Settings, session: Session, fake_http: type[_FakeClient]
) -> None:
    fake_http.queue = [_FakeResponse(_gemini_ok())]
    result = _generate(settings, session)
    session.commit()

    assert result.parsed == PARSED
    assert result.model == "gemini-2.5-flash"
    assert result.stop_reason == "end_turn"
    assert result.cost_usd == 0.0  # free tier; unknown model prices as zero
    assert result.input_tokens == 120 and result.output_tokens == 40

    sent = fake_http.calls[0]
    assert "gemini-2.5-flash:generateContent" in sent["url"]
    assert sent["headers"]["x-goog-api-key"] == "test-gemini-key"
    assert sent["body"]["generationConfig"]["responseMimeType"] == "application/json"

    events = session.scalars(select(AuditEvent)).all()
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["model"] == "gemini-2.5-flash"
    assert payload["stop_reason"] == "end_turn"


def test_gemini_refusal_maps_to_llm_refusal(
    settings: Settings, session: Session, fake_http: type[_FakeClient]
) -> None:
    fake_http.queue = [_FakeResponse(_gemini_blocked())]
    with pytest.raises(LLMRefusalError):
        _generate(settings, session)
    session.commit()
    payload = json.loads(session.scalars(select(AuditEvent)).one().payload_json)
    assert payload["stop_reason"] == "refusal"


def test_gemini_truncation_retries_with_doubled_budget(
    settings: Settings, session: Session, fake_http: type[_FakeClient]
) -> None:
    fake_http.queue = [
        _FakeResponse(_gemini_ok(finish="MAX_TOKENS")),
        _FakeResponse(_gemini_ok()),
    ]
    result = _generate(settings, session)
    session.commit()

    assert result.retried is True
    assert result.parsed == PARSED
    first, second = (c["body"]["generationConfig"]["maxOutputTokens"] for c in fake_http.calls)
    assert second == first * 2
    stops = [
        json.loads(e.payload_json)["stop_reason"] for e in session.scalars(select(AuditEvent))
    ]
    assert stops == ["max_tokens", "end_turn"]


def test_gemini_bad_json_raises_validation_error_after_audit(
    settings: Settings, session: Session, fake_http: type[_FakeClient]
) -> None:
    fake_http.queue = [_FakeResponse(_gemini_ok(text='{"subject": "missing fields"}'))]
    with pytest.raises(ValidationError):
        _generate(settings, session)
    session.commit()
    payload = json.loads(session.scalars(select(AuditEvent)).one().payload_json)
    assert payload["stop_reason"] == "parse_error"


def test_gemini_markdown_fenced_json_is_salvaged(
    settings: Settings, session: Session, fake_http: type[_FakeClient]
) -> None:
    fenced = f"```json\n{PARSED_JSON}\n```"
    fake_http.queue = [_FakeResponse(_gemini_ok(text=fenced))]
    result = _generate(settings, session)
    assert result.parsed == PARSED


def test_gemini_transport_error_degrades_to_unavailable(
    settings: Settings, session: Session, fake_http: type[_FakeClient]
) -> None:
    fake_http.queue = [httpx.ConnectError("boom")]
    with pytest.raises(LLMUnavailableError):
        _generate(settings, session)
