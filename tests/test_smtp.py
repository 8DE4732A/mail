import asyncio
from unittest.mock import patch

import aiosmtplib
import pytest

from mailtui.config import Account, ImapConfig, SmtpConfig
from mailtui.mail.smtp import AmbiguousSmtpError, send_message


class FakeSMTP:
    send_error: Exception | None = None

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def connect(self):
        return None

    async def login(self, username, password):
        return None

    async def send_message(self, message, sender=None, recipients=None):
        if self.send_error:
            raise self.send_error
        return {}, ""

    async def quit(self):
        return None


def account() -> Account:
    return Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
    )


def test_disconnect_during_send_is_ambiguous() -> None:
    FakeSMTP.send_error = aiosmtplib.SMTPServerDisconnected("connection dropped")
    raw = (
        b"From: me@example.com\r\n"
        b"To: alice@example.com\r\n"
        b"Subject: test\r\n"
        b"\r\n"
        b"hello"
    )
    with patch("mailtui.mail.smtp.aiosmtplib.SMTP", FakeSMTP):
        with pytest.raises(AmbiguousSmtpError):
            asyncio.run(
                send_message(
                    account(),
                    "secret",
                    raw,
                    ["alice@example.com"],
                )
            )
