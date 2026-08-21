from email.message import EmailMessage
import smtplib
import ssl

import pytest

from paperflow.email import EmailDeliveryError, GmailSettings, send_daily_email


def test_gmail_settings_repr_redacts_all_fields():
    settings = GmailSettings(
        address="PRIVATE_ADDRESS_SENTINEL@example.com",
        app_password="PRIVATE_PASSWORD_SENTINEL",
        mail_to="PRIVATE_MAIL_TO_SENTINEL@example.com",
    )

    rendered = repr(settings)
    assert "PRIVATE_ADDRESS_SENTINEL" not in rendered
    assert "PRIVATE_PASSWORD_SENTINEL" not in rendered
    assert "PRIVATE_MAIL_TO_SENTINEL" not in rendered


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

        def starttls(self, *, context):
            calls.append(("starttls", context))

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

    tls_context = calls[2][1]
    assert calls == [
        ("connect", "smtp.gmail.com", 587, 30),
        ("enter",),
        ("starttls", tls_context),
        ("login", "sender@example.com", "PRIVATE_PASSWORD"),
        ("send_message",),
        ("exit", None, None, None),
    ]
    assert isinstance(tls_context, ssl.SSLContext)
    assert tls_context.verify_mode == ssl.CERT_REQUIRED
    assert tls_context.check_hostname is True
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


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPException("PRIVATE_SMTP_SENTINEL"),
        OSError("PRIVATE_OS_SENTINEL"),
    ],
)
def test_send_daily_email_wraps_only_delivery_errors(monkeypatch, error):
    class FakeSMTP:
        def __init__(self, _host, _port, *, timeout):
            assert timeout == 30

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self, *, context):
            assert context.check_hostname is True
            raise error

    monkeypatch.setattr("paperflow.email.smtplib.SMTP", FakeSMTP)
    settings = GmailSettings(
        address="sender@example.com",
        app_password="PRIVATE_PASSWORD",
        mail_to="reader@example.com",
    )

    with pytest.raises(EmailDeliveryError, match="^email delivery failed$") as exc_info:
        send_daily_email(settings, "PaperFlow 2026-08-20", "plain", "<p>html</p>")

    assert exc_info.value.__cause__ is error
    assert "PRIVATE" not in str(exc_info.value)


def test_send_daily_email_does_not_wrap_message_programming_errors(monkeypatch):
    class BrokenMessage:
        def __setitem__(self, _name, _value):
            raise RuntimeError("PRIVATE_PROGRAMMING_SENTINEL")

    monkeypatch.setattr("paperflow.email.EmailMessage", BrokenMessage)
    monkeypatch.setattr(
        "paperflow.email.smtplib.SMTP",
        lambda *_args, **_kwargs: pytest.fail("SMTP must not start"),
    )
    settings = GmailSettings(
        address="sender@example.com",
        app_password="PRIVATE_PASSWORD",
        mail_to="reader@example.com",
    )

    with pytest.raises(RuntimeError, match="PRIVATE_PROGRAMMING_SENTINEL"):
        send_daily_email(settings, "PaperFlow 2026-08-20", "plain", "<p>html</p>")
