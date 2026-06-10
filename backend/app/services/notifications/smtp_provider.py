"""Real SMTP delivery over SSL.

This provider never raises: any failure (connect, auth, send) is reported as
``SendResult(ok=False, ...)`` so the caller can persist a FAILED notification
instead of breaking the workflow transition that triggered the email.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import Settings
from app.services.notifications.base import SendResult


class SmtpProvider:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> SendResult:
        settings = self._settings
        try:
            message = MIMEMultipart("alternative")
            message["From"] = settings.email_from
            message["To"] = to
            message["Subject"] = subject
            message.attach(MIMEText(body_text, "plain", "utf-8"))
            if body_html is not None:
                message.attach(MIMEText(body_html, "html", "utf-8"))
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(message)
        except Exception as exc:  # provider contract: report failures, never raise
            return SendResult(ok=False, provider="smtp", detail=str(exc))
        return SendResult(ok=True, provider="smtp")
