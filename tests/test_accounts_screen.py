import asyncio
from pathlib import Path

from textual.widgets import Input

from mailtui.config import (
    Account,
    AppConfig,
    ImapConfig,
    SmtpConfig,
    add_account,
    load_config,
)
from mailtui.ui.app import AccountsScreen, AddAccountScreen, MailApp


def test_accounts_screen_adds_account(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    add_account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        display_name="Me",
        imap_host="imap.example.com",
        imap_port=993,
        imap_security="ssl",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_security="ssl",
        path=path,
    )
    account = load_config(path).accounts[0]
    config = AppConfig(accounts=[account])

    async def run() -> None:
        app = MailApp(
            account,
            "secret",
            config,
            start_workers=False,
            config_file=path,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, AccountsScreen)

            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, AddAccountScreen)
            values = {
                "acct-key": "personal",
                "acct-email": "me@personal.com",
                "acct-username": "me@personal.com",
                "acct-imap-host": "imap.personal.com",
                "acct-smtp-host": "smtp.personal.com",
            }
            for widget_id, value in values.items():
                app.screen.query_one(f"#{widget_id}", Input).value = value

            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert isinstance(app.screen, AccountsScreen)
            assert len(load_config(path).accounts) == 2

            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(run())
