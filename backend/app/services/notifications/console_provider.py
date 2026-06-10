"""Keyless demo provider: the email is "delivered" to the application log.

Delivery is proven by the Notification DB row plus the ``claimflow.email`` log
line — no SMTP credentials are needed to demo the notification flow end to end.
"""

import logging

from app.services.notifications.base import SendResult

logger = logging.getLogger("claimflow.email")


class ConsoleProvider:
    def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> SendResult:
        html_part = f"\n\n--- html alternative ---\n{body_html}" if body_html else ""
        logger.info(
            "EMAIL (console provider)\nTo: %s\nSubject: %s\n\n%s%s",
            to,
            subject,
            body_text,
            html_part,
        )
        return SendResult(ok=True, provider="console")
