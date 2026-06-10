import json
import logging
import smtplib

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.claimguard import audit
from app.config import Settings
from app.models import (
    AuditEvent,
    Claim,
    ClaimState,
    Notification,
    NotificationStatus,
    Role,
    User,
)
from app.services.notifications.base import SendResult, get_provider
from app.services.notifications.console_provider import ConsoleProvider
from app.services.notifications.service import render_claim_returned, send_claim_email
from app.services.notifications.smtp_provider import SmtpProvider

CLAIM_REF = "CLM-NOTIF-00001"
REASON = "The uploaded imaging file could not be opened."


@pytest.fixture()
def claim(session: Session, users: dict[str, User]) -> Claim:
    c = Claim(
        claim_ref=CLAIM_REF,
        claimant_id=users[Role.CLAIMANT.value].id,
        claim_type="imaging",
        state=ClaimState.RETURNED_TO_CLAIMANT,
    )
    session.add(c)
    session.commit()
    return c


def smtp_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "email_provider": "smtp",
            "smtp_host": "smtp.example.test",
            "smtp_user": "mailer",
            "smtp_password": "secret",
        }
    )


class FakeSMTPSSL:
    """Stands in for smtplib.SMTP_SSL: records the login and sent messages."""

    instances: list["FakeSMTPSSL"] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.logged_in: tuple[str, str] | None = None
        self.sent: list = []
        FakeSMTPSSL.instances.append(self)

    def __enter__(self) -> "FakeSMTPSSL":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, message) -> None:
        self.sent.append(message)


class ExplodingSMTPSSL:
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise ConnectionRefusedError("connection refused by smtp.example.test")


# --- console path -------------------------------------------------------------------


def test_console_path_logs_notification_and_audits(
    session: Session, settings: Settings, users: dict[str, User], claim: Claim
) -> None:
    claimant = users[Role.CLAIMANT.value]
    subject, body_text = render_claim_returned(CLAIM_REF, REASON, "Casey")

    notification = send_claim_email(
        session,
        settings,
        claim=claim,
        recipient=claimant,
        subject=subject,
        body_text=body_text,
    )
    session.commit()

    row = session.scalar(select(Notification).where(Notification.id == notification.id))
    assert row is not None
    assert row.status is NotificationStatus.LOGGED
    assert row.provider == "console"
    assert row.sent_at is None
    assert row.claim_id == claim.id
    assert row.recipient_id == claimant.id
    assert row.body_text == body_text

    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "email.sent"))
    assert event is not None
    assert event.claim_id == claim.id
    assert event.actor_role == "system"
    payload = json.loads(event.payload_json)
    assert payload == {
        "recipient_id": claimant.id,
        "subject": subject,
        "provider": "console",
        "status": "logged",
    }
    # The audit log stays PII-free: no email body (it carries the first name).
    assert body_text not in event.payload_json
    assert "Casey" not in event.payload_json
    assert audit.verify_chain(session) == (True, 1)


def test_console_provider_logs_full_email(caplog: pytest.LogCaptureFixture) -> None:
    provider = ConsoleProvider()
    with caplog.at_level(logging.INFO, logger="claimflow.email"):
        result = provider.send(
            to="claimant@demo.ca",
            subject="Action needed",
            body_text="Body line one.",
            body_html="<p>Body line one.</p>",
        )
    assert result == SendResult(ok=True, provider="console")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "claimant@demo.ca" in logged
    assert "Action needed" in logged
    assert "Body line one." in logged
    assert "<p>Body line one.</p>" in logged


# --- smtp path ----------------------------------------------------------------------


def test_smtp_success_marks_sent(
    session: Session,
    settings: Settings,
    users: dict[str, User],
    claim: Claim,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeSMTPSSL.instances.clear()
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    cfg = smtp_settings(settings)
    claimant = users[Role.CLAIMANT.value]

    notification = send_claim_email(
        session,
        cfg,
        claim=claim,
        recipient=claimant,
        subject="Action needed on your claim",
        body_text="Please re-upload your documents.",
        body_html="<p>Please re-upload your documents.</p>",
    )
    session.commit()

    assert notification.status is NotificationStatus.SENT
    assert notification.sent_at is not None
    assert notification.provider == "smtp"

    [fake] = FakeSMTPSSL.instances
    assert (fake.host, fake.port) == ("smtp.example.test", 465)
    assert fake.timeout == 15
    assert fake.logged_in == ("mailer", "secret")
    [message] = fake.sent
    assert message["From"] == cfg.email_from
    assert message["To"] == claimant.email
    assert message["Subject"] == "Action needed on your claim"

    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "email.sent"))
    assert event is not None
    payload = json.loads(event.payload_json)
    assert payload["provider"] == "smtp"
    assert payload["status"] == "sent"
    assert audit.verify_chain(session) == (True, 1)


def test_smtp_failure_marks_failed_without_raising(
    session: Session,
    settings: Settings,
    users: dict[str, User],
    claim: Claim,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP_SSL", ExplodingSMTPSSL)
    claimant = users[Role.CLAIMANT.value]

    notification = send_claim_email(  # must not raise
        session,
        smtp_settings(settings),
        claim=claim,
        recipient=claimant,
        subject="Action needed on your claim",
        body_text="Please re-upload your documents.",
    )
    session.commit()

    assert notification.status is NotificationStatus.FAILED
    assert notification.sent_at is None
    assert notification.body_text == "Please re-upload your documents."  # body kept intact

    event = session.scalar(select(AuditEvent).where(AuditEvent.event_type == "email.sent"))
    assert event is not None
    payload = json.loads(event.payload_json)
    assert payload["status"] == "failed"
    assert payload["provider"] == "smtp"
    assert audit.verify_chain(session) == (True, 1)


def test_smtp_provider_send_reports_exception_detail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(smtplib, "SMTP_SSL", ExplodingSMTPSSL)
    result = SmtpProvider(smtp_settings(settings)).send(
        to="claimant@demo.ca", subject="s", body_text="b"
    )
    assert result.ok is False
    assert result.provider == "smtp"
    assert "connection refused" in result.detail


# --- rendering ----------------------------------------------------------------------


def test_render_claim_returned_renders_subject_and_body() -> None:
    subject, body_text = render_claim_returned(CLAIM_REF, REASON, "Casey")

    assert CLAIM_REF in subject
    assert "Casey" in body_text
    assert REASON in body_text
    assert CLAIM_REF in body_text
    assert "re-upload" in body_text.lower()
    # Claimant-facing copy carries no model scores or fraud language.
    for banned in ("fraud", "score", "risk", "model"):
        assert banned not in body_text.lower()
        assert banned not in subject.lower()


# --- provider selection -------------------------------------------------------------


def test_provider_selection_from_settings(settings: Settings) -> None:
    assert isinstance(get_provider(settings), ConsoleProvider)
    assert isinstance(get_provider(smtp_settings(settings)), SmtpProvider)
    # Anything that is not explicitly smtp falls back to the keyless console provider.
    other = settings.model_copy(update={"email_provider": "sendgrid"})
    assert isinstance(get_provider(other), ConsoleProvider)
