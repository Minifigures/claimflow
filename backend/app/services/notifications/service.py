"""Claim email orchestration: render -> persist Notification -> provider send -> audit.

Runs inside the caller's transaction; the caller commits.
"""

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy.orm import Session

from app.claimguard import audit
from app.config import Settings
from app.models import AuditEventType, Claim, Notification, NotificationStatus, User
from app.services.notifications.base import get_provider

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


@lru_cache(maxsize=None)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        undefined=StrictUndefined,
        autoescape=False,  # plain-text templates only
        keep_trailing_newline=True,
    )


def render_claim_returned(claim_ref: str, reason: str, first_name: str) -> tuple[str, str]:
    """Render the plain-text "claim returned" email. Returns ``(subject, body_text)``."""
    subject = f"Action needed on your claim {claim_ref}"
    template = _env().get_template("claim-returned.txt.j2")
    body_text = template.render(claim_ref=claim_ref, reason=reason, first_name=first_name)
    return subject, body_text


def send_claim_email(
    session: Session,
    settings: Settings,
    *,
    claim: Claim,
    recipient: User,
    subject: str,
    body_text: str,
    body_html: str | None = None,
) -> Notification:
    """Persist a Notification, send via the configured provider, and audit the result.

    Status semantics: console delivery stays LOGGED (delivered-to-log is the terminal
    state of the keyless demo path), a real provider success becomes SENT with
    ``sent_at`` stamped, and any provider failure becomes FAILED (the rendered body is
    kept intact on the row). Never raises on delivery failure. Caller commits.
    """
    provider = get_provider(settings)
    notification = Notification(
        claim_id=claim.id,
        recipient_id=recipient.id,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        provider=settings.email_provider,
        status=NotificationStatus.LOGGED,
    )
    session.add(notification)
    session.flush()

    result = provider.send(
        to=recipient.email, subject=subject, body_text=body_text, body_html=body_html
    )
    if not result.ok:
        notification.status = NotificationStatus.FAILED
    elif result.provider != "console":
        notification.status = NotificationStatus.SENT
        notification.sent_at = datetime.now(timezone.utc)

    # PII note: the audit payload deliberately carries NO email body — the body may
    # contain the claimant's first name, and the audit log must stay PII-free. The
    # full body lives only on the Notification row.
    audit.append(
        session,
        AuditEventType.EMAIL_SENT,
        claim_id=claim.id,
        actor_role="system",
        payload={
            "recipient_id": recipient.id,
            "subject": subject,
            "provider": result.provider,
            "status": notification.status.value,
        },
    )
    return notification
