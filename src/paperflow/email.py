from __future__ import annotations

import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage


_EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class EmailDeliveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class GmailSettings:
    address: str = field(repr=False)
    app_password: str = field(repr=False)
    mail_to: str = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.app_password, str) or not self.app_password.strip():
            raise ValueError("app_password must be a non-empty string")
        for field in ("address", "mail_to"):
            value = getattr(self, field)
            if (
                not isinstance(value, str)
                or not value.strip()
                or "\r" in value
                or "\n" in value
                or _EMAIL_PATTERN.fullmatch(value) is None
            ):
                raise ValueError(f"{field} must be a valid email address")


def send_daily_email(
    settings: GmailSettings,
    subject: str,
    plain: str,
    html: str,
) -> None:
    message = EmailMessage()
    message["From"] = settings.address
    message["To"] = settings.mail_to
    message["Subject"] = subject
    message.set_content(plain)
    message.add_alternative(html, subtype="html")

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(settings.address, settings.app_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailDeliveryError("email delivery failed") from exc
