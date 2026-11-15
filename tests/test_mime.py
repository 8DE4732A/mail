from imap_tools import MailMessage

from mailtui.config import Account, ImapConfig, SmtpConfig
from mailtui.mail.mime import (
    build_message,
    envelope_recipients,
    parse_body,
    parse_headers,
    raw_bytes,
    reply_addresses,
)


def account() -> Account:
    return Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        display_name="Me",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
    )


def test_build_message_hides_bcc_and_sets_thread_headers() -> None:
    message = build_message(
        account(),
        to=["Alice <alice@example.com>"],
        cc=["bob@example.com"],
        bcc=["secret@example.com"],
        subject="Re: 测试",
        body="你好",
        attachments=[("a.txt", "text/plain", b"hello")],
        in_reply_to="<parent@example.com>",
        references=["<root@example.com>"],
    )
    assert message["Bcc"] is None
    assert message["In-Reply-To"] == "<parent@example.com>"
    assert "<parent@example.com>" in message["References"]
    assert message["Message-ID"]
    raw = raw_bytes(message)
    assert b"Bcc:" not in raw
    assert b"secret@example.com" not in raw


def test_envelope_recipients_includes_bcc() -> None:
    assert envelope_recipients(
        ["Alice <alice@example.com>"],
        ["bob@example.com"],
        ["secret@example.com"],
    ) == ["alice@example.com", "bob@example.com", "secret@example.com"]


def test_parse_headers_and_body() -> None:
    raw = (
        b"From: Alice <alice@example.com>\r\n"
        b"To: me@example.com\r\n"
        b"Subject: =?utf-8?b?5rWL6K+V6YKu5Lu2?=\r\n"
        b"Message-ID: <abc@example.com>\r\n"
        b"Date: Thu, 13 Nov 2026 10:00:00 +0800\r\n"
        b"MIME-Version: 1.0\r\n"
        b"Content-Type: multipart/alternative; boundary=xyz\r\n"
        b"\r\n"
        b"--xyz\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"plain body\r\n"
        b"--xyz\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n"
        b"\r\n"
        b"<h1>HTML title</h1><p>html body</p>\r\n"
        b"--xyz--\r\n"
    )
    message = MailMessage.from_bytes(raw)
    headers = parse_headers(message)
    text, html_text, raw_eml, attachments = parse_body(message)

    assert headers["subject"] == "测试邮件"
    assert headers["message_id"] == "<abc@example.com>"
    assert headers["date_utc"].startswith("2026-11-13T02:00:00")
    assert text == "plain body"
    assert "HTML title" in html_text
    assert raw_eml
    assert attachments == []


def test_reply_addresses_prefers_reply_to_and_removes_self() -> None:
    row = {
        "from_json": '[{"name":"Alice","email":"alice@example.com"}]',
        "reply_to_json": '[{"name":"Team","email":"team@example.com"}]',
        "to_json": '[{"name":"","email":"me@example.com"}]',
        "cc_json": '[{"name":"","email":"bob@example.com"}]',
    }
    to, cc = reply_addresses(row, account())
    assert to == ["team@example.com"]
    assert cc == ["bob@example.com"]
