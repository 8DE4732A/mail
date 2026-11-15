"""MIME parsing and message construction."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, formatdate, getaddresses, make_msgid
from pathlib import Path
from typing import Any, Iterable

from imap_tools import MailMessage

from mailtui.config import Account
from mailtui.domain.html_text import html_to_text


def address_dicts(value: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not value:
        return result
    for name, email in getaddresses([str(value)]):
        if email:
            result.append({"name": name, "email": email})
    return result


def address_strings(values: Iterable[dict[str, str]]) -> list[str]:
    return [
        formataddr((item.get("name", ""), item.get("email", "")))
        for item in values
        if item.get("email")
    ]


def _safe_references(raw: str | None) -> list[str]:
    if not raw:
        return []
    return re.findall(r"<[^<>\s]+>", raw)


def parse_headers(message: MailMessage) -> dict[str, Any]:
    flags = list(message.flags)
    try:
        raw_date = message.date
    except Exception:
        raw_date = datetime.now(timezone.utc)
    if raw_date.tzinfo is None:
        date_utc = raw_date.replace(tzinfo=timezone.utc)
    else:
        date_utc = raw_date.astimezone(timezone.utc)
    size = message.size_rfc822 or message.size

    def values(items: Any) -> list[dict[str, str]]:
        return [
            {"name": item.name or "", "email": item.email or ""}
            for item in items
            if item.email
        ]

    def one(item: Any) -> list[dict[str, str]]:
        if item is None or not item.email:
            return []
        return [{"name": item.name or "", "email": item.email}]

    return {
        "uid": int(message.uid or 0),
        "message_id": (message.obj.get("Message-ID") or "").strip() or None,
        "in_reply_to": (message.obj.get("In-Reply-To") or "").strip() or None,
        "references": _safe_references(message.obj.get("References")),
        "subject": message.subject,
        "from_values": one(message.from_values),
        "to_values": values(message.to_values),
        "cc_values": values(message.cc_values),
        "reply_to_values": values(message.reply_to_values),
        "date_utc": date_utc.isoformat(timespec="seconds"),
        "size": size,
        "flags": flags,
        "is_seen": "\\Seen" in flags,
        "is_flagged": "\\Flagged" in flags,
        "has_attachments": bool(message.attachments),
        "snippet": "",
    }


def parse_body(message: MailMessage) -> tuple[str, str, bytes, list[dict[str, Any]]]:
    text = (message.text or "").replace("\r\n", "\n").replace("\r", "\n")
    html = message.html or ""
    html_text = html_to_text(html) if html else ""
    if not text and html_text:
        text = html_text
    text = text.strip("\n")

    attachments: list[dict[str, Any]] = []
    for index, attachment in enumerate(message.attachments):
        filename = Path(attachment.filename or f"attachment-{index + 1}").name
        filename = re.sub(r"[\x00-\x1f\x7f]", "_", filename)
        content = attachment.payload
        attachments.append(
            {
                "part_id": f"{index}",
                "filename": filename,
                "content_type": attachment.content_type
                or "application/octet-stream",
                "size": attachment.size,
                "content_id": attachment.content_id,
                "content": content,
                "state": "downloaded",
            }
        )
    return text, html_text, bytes(message.obj), attachments


def build_message(
    account: Account,
    *,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str,
    body: str,
    attachments: Iterable[tuple[str, str, bytes]] = (),
    in_reply_to: str | None = None,
    references: Iterable[str] = (),
    message_id: str | None = None,
) -> EmailMessage:
    cc = cc or []
    bcc = bcc or []
    message = EmailMessage()
    message["From"] = account.from_header
    if to:
        message["To"] = ", ".join(to)
    if cc:
        message["Cc"] = ", ".join(cc)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = message_id or make_msgid(
        domain=account.email.split("@")[-1] if "@" in account.email else None
    )
    message["User-Agent"] = "MailTUI/0.1"
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    reference_list = [item for item in references if item]
    if in_reply_to and in_reply_to not in reference_list:
        reference_list.append(in_reply_to)
    if reference_list:
        message["References"] = " ".join(reference_list)

    message.set_content(body, subtype="plain", charset="utf-8")
    for filename, content_type, content in attachments:
        maintype, _, subtype = content_type.partition("/")
        message.add_attachment(
            content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=Path(filename).name,
        )
    return message


def raw_bytes(message: EmailMessage) -> bytes:
    return message.as_bytes(policy=SMTP)


def envelope_recipients(
    to: Iterable[str], cc: Iterable[str] = (), bcc: Iterable[str] = ()
) -> list[str]:
    result: list[str] = []
    for value in [*to, *cc, *bcc]:
        for _, email in getaddresses([value]):
            if email and email not in result:
                result.append(email)
    return result


def reply_subject(subject: str) -> str:
    subject = subject.strip()
    return subject if re.match(r"(?i)^re:", subject) else f"Re: {subject}"


def forward_subject(subject: str) -> str:
    subject = subject.strip()
    return subject if re.match(r"(?i)^fwd?:", subject) else f"Fwd: {subject}"


def quote_reply(sender: str, date_text: str, body: str) -> str:
    quoted = "\n".join(f"> {line}" if line else ">" for line in body.splitlines())
    return f"On {date_text}, {sender} wrote:\n{quoted}\n\n"


def reply_addresses(row: dict[str, Any], account: Account) -> tuple[list[str], list[str]]:
    from_value = _first_email(row.get("from_json"))
    reply_value = _first_email(row.get("reply_to_json"))
    to = [reply_value or from_value] if (reply_value or from_value) else []
    to = [value for value in to if value.lower() != account.email.lower()]

    cc: list[str] = []
    for key in ("to_json", "cc_json"):
        try:
            values = json.loads(row.get(key) or "[]")
        except json.JSONDecodeError:
            values = []
        for item in values:
            email = str(item.get("email", ""))
            if (
                email
                and email.lower() != account.email.lower()
                and email not in to
                and email not in cc
            ):
                cc.append(email)
    return to, cc


def _first_email(raw: str | None) -> str:
    try:
        values = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return ""
    if values and isinstance(values, list):
        return str(values[0].get("email", ""))
    return ""


def date_text(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value
