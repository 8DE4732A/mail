import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image as PILImage
from textual.widgets import Input, ListView

from mailtui.config import Account, AppConfig, ImapConfig, SmtpConfig
from mailtui.db.store import Store
from mailtui.ui.app import (
    AttachmentScreen,
    MailApp,
    sniff_image_mime,
    unique_save_path,
)


def test_sniff_image_mime_ignores_declared_type() -> None:
    for image_format in ("JPEG", "PNG", "WEBP"):
        buffer = BytesIO()
        PILImage.new("RGB", (2, 2), "red").save(buffer, format=image_format)
        detected = sniff_image_mime(buffer.getvalue())
        assert detected == f"image/{image_format.lower()}"
    assert sniff_image_mime(b"not an image") is None


def test_unique_save_path_handles_duplicate_names(tmp_path: Path) -> None:
    used: set[str] = set()
    first = unique_save_path(tmp_path, "report.txt", used)
    (tmp_path / "report.txt").write_bytes(b"existing")
    second = unique_save_path(tmp_path, "report.txt", used)
    third = unique_save_path(tmp_path, "report.txt", used)
    assert first.name == "report.txt"
    assert second.name == "report (2).txt"
    assert third.name == "report (3).txt"


def test_attachment_row_opens_save_dialog(tmp_path: Path) -> None:
    store = Store(tmp_path / "mail.db")
    folder_id = store.upsert_folder("work", "INBOX", role="inbox")
    [message_id] = store.upsert_headers(
        "work",
        folder_id,
        [
            {
                "uid": 1,
                "message_id": "<1@example.com>",
                "in_reply_to": None,
                "references_json": None,
                "subject": "附件测试",
                "from_values": [{"name": "Alice", "email": "alice@example.com"}],
                "to_values": [],
                "cc_values": [],
                "reply_to_values": [],
                "date_utc": "2026-11-14T00:00:00+00:00",
                "size": 100,
                "flags": [],
                "is_seen": True,
                "is_flagged": False,
                "has_attachments": True,
                "snippet": "",
            }
        ],
    )
    store.save_body(
        message_id,
        text_body="正文",
        html_text="",
        raw_eml=None,
        attachments=[
            {
                "part_id": "0",
                "filename": "report.txt",
                "content_type": "text/plain",
                "size": 5,
                "content": b"hello",
                "state": "downloaded",
            }
        ],
    )
    account = Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
    )
    config = AppConfig(accounts=[account])

    async def run() -> None:
        app = MailApp(
            account,
            "secret",
            config,
            store=store,
            start_workers=False,
            config_file=tmp_path / "config.toml",
        )
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            rows = app.query_one("#attachment-list", ListView)
            assert len(rows.children) == 1
            rows.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, AttachmentScreen)
            app.screen.query_one("#attachment-dir", Input).value = str(
                tmp_path / "saved"
            )
            app.screen.action_save()
            await pilot.pause()
            assert (tmp_path / "saved" / "report.txt").read_bytes() == b"hello"
            await pilot.press("q")

    asyncio.run(run())
