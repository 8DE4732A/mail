import asyncio
from pathlib import Path

from textual.widgets import Tree

from mailtui.config import Account, AppConfig, ImapConfig, SmtpConfig
from mailtui.db.store import Store
from mailtui.ui.app import MailApp


def test_live_folder_tree_builds_hierarchy(tmp_path: Path) -> None:
    store = Store(tmp_path / "mail.db")
    store.upsert_folder("work", "INBOX", role="inbox")
    store.upsert_folder("work", "Work", role=None)
    store.upsert_folder("work", "Work/Projects", role=None)
    store.upsert_folder("work", "Work/Projects/Alpha", role=None)
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
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            tree = app.query_one("#folder-tree", Tree)
            labels = [str(node.label) for node in tree.root.children]
            assert any("INBOX" in label for label in labels)
            work = next(
                node for node in tree.root.children if "Work" in str(node.label)
            )
            assert work.is_expanded
            assert any("Projects" in str(node.label) for node in work.children)
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("j")
            await pilot.pause()
            assert tree.cursor_node is not None
            await pilot.press("q")

    asyncio.run(run())
