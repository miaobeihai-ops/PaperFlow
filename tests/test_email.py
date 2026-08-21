from email.message import EmailMessage

import pytest

from paperflow.email import GmailSettings, send_daily_email


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("address", ""),
        ("address", "not-an-email"),
        ("address", "sender@example.com\r\nBcc: victim@example.com"),
        ("app_password", ""),
        ("mail_to", ""),
        ("mail_to", "not-an-email"),
        ("mail_to", "reader@example.com\nBcc: victim@example.com"),
    ],
)
def test_gmail_settings_rejects_empty_invalid_and_injected_fields(field, value):
    values = {
        "address": "sender@example.com",
        "app_password": "PRIVATE_PASSWORD",
        "mail_to": "reader@example.com",
    }
    values[field] = value

    with pytest.raises(ValueError) as exc_info:
        GmailSettings(**values)

    assert "PRIVATE_PASSWORD" not in str(exc_info.value)


def test_send_daily_email_uses_gmail_smtp_in_order_with_plain_and_html(
    monkeypatch,
):
    calls = []
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, *, timeout):
            calls.append(("connect", host, port, timeout))

        def __enter__(self):
            calls.append(("enter",))
            return self

        def __exit__(self, exc_type, exc, traceback):
            calls.append(("exit", exc_type, exc, traceback))

        def starttls(self):
            calls.append(("starttls",))

        def login(self, address, password):
            calls.append(("login", address, password))

        def send_message(self, message):
            calls.append(("send_message",))
            sent.append(message)

    monkeypatch.setattr("paperflow.email.smtplib.SMTP", FakeSMTP)
    settings = GmailSettings(
        address="sender@example.com",
        app_password="PRIVATE_PASSWORD",
        mail_to="reader@example.com",
    )

    send_daily_email(settings, "PaperFlow 2026-08-20", "plain body", "<b>html body</b>")

    assert calls == [
        ("connect", "smtp.gmail.com", 587, 30),
        ("enter",),
        ("starttls",),
        ("login", "sender@example.com", "PRIVATE_PASSWORD"),
        ("send_message",),
        ("exit", None, None, None),
    ]
    assert len(sent) == 1
    message = sent[0]
    assert isinstance(message, EmailMessage)
    assert message["From"] == "sender@example.com"
    assert message["To"] == "reader@example.com"
    assert message["Subject"] == "PaperFlow 2026-08-20"
    assert message.is_multipart()
    assert [part.get_content_type() for part in message.iter_parts()] == [
        "text/plain",
        "text/html",
    ]
    assert message.get_body(preferencelist=("plain",)).get_content() == "plain body\n"
    assert message.get_body(preferencelist=("html",)).get_content() == "<b>html body</b>\n"
