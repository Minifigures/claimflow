"""Email provider abstraction.

Providers are deliberately tiny: one ``send`` method returning a ``SendResult``.
``get_provider`` is the only place that knows which concrete provider backs a
``Settings.email_provider`` value (console is the keyless default).
"""

from dataclasses import dataclass
from typing import Protocol

from app.config import Settings


@dataclass
class SendResult:
    ok: bool
    provider: str
    detail: str = ""


class EmailProvider(Protocol):
    def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> SendResult: ...


def get_provider(settings: Settings) -> EmailProvider:
    """Select the email provider from settings (console unless explicitly smtp)."""
    if settings.email_provider == "smtp":
        from app.services.notifications.smtp_provider import SmtpProvider

        return SmtpProvider(settings)
    from app.services.notifications.console_provider import ConsoleProvider

    return ConsoleProvider()
