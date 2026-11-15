import asyncio
from pathlib import Path

from textual.widgets import Input

from mailtui.config import load_config
from mailtui.ui.app import AddAccountScreen
from mailtui.ui.onboarding import OnboardingApp


def test_onboarding_starts_without_account_and_adds_one(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    async def run() -> None:
        app = OnboardingApp(path)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, AddAccountScreen)
            values = {
                "acct-key": "work",
                "acct-email": "me@example.com",
                "acct-username": "me@example.com",
                "acct-imap-host": "imap.example.com",
                "acct-smtp-host": "smtp.example.com",
            }
            for widget_id, value in values.items():
                app.screen.query_one(f"#{widget_id}", Input).value = value
            await pilot.press("ctrl+enter")
            await pilot.pause()
        assert app.added is True
        assert load_config(path).accounts[0].key == "work"

    asyncio.run(run())
