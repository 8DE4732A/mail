import asyncio
from pathlib import Path

from textual.widgets import Button, Input, ListView, Static

from mailtui.config import Account, AppConfig, ImapConfig, SmtpConfig
from mailtui.db.store import Store
from mailtui.ui.app import ComposeData, ComposeScreen, MailApp


def test_live_app_loads_cached_message_and_search(tmp_path: Path) -> None:
    account = Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
    )
    config = AppConfig(accounts=[account])
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
                "subject": "缓存邮件",
                "from_values": [{"name": "Alice", "email": "alice@example.com"}],
                "to_values": [{"name": "", "email": "me@example.com"}],
                "cc_values": [],
                "reply_to_values": [],
                "date_utc": "2026-11-13T00:00:00+00:00",
                "size": 100,
                "flags": [],
                "is_seen": False,
                "is_flagged": False,
                "has_attachments": False,
                "snippet": "",
            }
        ],
    )
    store.save_body(
        message_id,
        text_body="缓存正文",
        html_text="",
        raw_eml=None,
        attachments=[],
    )

    async def run() -> None:
        app = MailApp(
            account,
            "secret",
            config,
            store=store,
            start_workers=False,
                config_file=tmp_path / "config.toml",
        )
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            keybar = app.query_one("#keybar", Static)
            status = app.query_one("#status", Static)
            assert keybar.region.y == status.region.y
            assert status.region.x > keybar.region.x
            await pilot.press("enter")
            await pilot.pause()
            assert app.current_body_text == "缓存正文"
            assert app.current_row is not None
            assert app.current_row["is_seen"] == 1
            assert app.query_one("#preview").display is True
            await pilot.click("#topbar")
            await pilot.pause()
            assert app.preview_open is False
            await pilot.press("enter")
            await pilot.pause()
            assert app.preview_open is True
            app.query_one("#preview-close", Button).press()
            await pilot.pause()
            assert app.preview_open is False
            assert app.query_one("#preview").display is False
            await pilot.press("slash")
            await pilot.pause()
            search = app.query_one("#search", Input)
            assert search.display is True
            search.value = "缓存"
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.messages) == 1
            await pilot.press("escape")
            await pilot.pause()
            assert search.display is False
            assert app.submit_compose(
                ComposeData(
                    to="alice@example.com",
                    subject="发送测试",
                    body="hello",
                )
            )
            operations = [store.claim_operation("work") for _ in range(2)]
            assert any(
                operation is not None and operation["kind"] == "send"
                for operation in operations
            )
            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, ComposeScreen)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(run())


def test_refresh_preserves_selected_message(tmp_path: Path) -> None:
    account = Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
    )
    config = AppConfig(accounts=[account])
    store = Store(tmp_path / "mail.db")
    folder_id = store.upsert_folder("work", "INBOX", role="inbox")
    rows = []
    for uid in (1, 2):
        rows.append(
            {
                "uid": uid,
                "message_id": f"<{uid}@example.com>",
                "in_reply_to": None,
                "references_json": None,
                "subject": f"邮件 {uid}",
                "from_values": [{"name": "Alice", "email": "alice@example.com"}],
                "to_values": [],
                "cc_values": [],
                "reply_to_values": [],
                "date_utc": f"2026-11-14T0{uid}:00:00+00:00",
                "size": 100,
                "flags": [],
                "is_seen": True,
                "is_flagged": False,
                "has_attachments": False,
                "snippet": "",
            }
        )
    ids = store.upsert_headers("work", folder_id, rows)
    for message_id in ids:
        store.save_body(
            message_id,
            text_body="正文",
            html_text="",
            raw_eml=None,
            attachments=[],
        )

    async def run() -> None:
        app = MailApp(
            account,
            "secret",
            config,
            store=store,
            start_workers=False,
            config_file=tmp_path / "config.toml",
        )
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            view = app.query_one("#message-list", ListView)
            await pilot.press("j")
            await pilot.pause()
            assert view.index == 1
            selected_id = app.current_row["id"]
            first_item = view.children[0]
            await app._refresh_messages()
            await pilot.pause()
            assert view.index == 1
            assert app.current_row["id"] == selected_id
            assert view.children[0] is first_item
            await pilot.press("q")

    asyncio.run(run())
