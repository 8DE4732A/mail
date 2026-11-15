import asyncio
from pathlib import Path

from mailtui.config import Account, AppConfig, ImapConfig, SmtpConfig
from mailtui.db.store import Store
from mailtui.services.sync import SyncService


class FakeImap:
    def __init__(
        self,
        rows: list[dict],
        uidvalidity: int = 1,
        *,
        folder_names: list[str] | None = None,
        uids: list[int] | None = None,
        message_count: int | None = None,
        all_uids: set[int] | None = None,
    ) -> None:
        self.rows = rows
        self.uidvalidity = uidvalidity
        self.closed = False
        self.folder_names = folder_names or ["INBOX"]
        self.uids = uids
        self.message_count = message_count
        self.all_uids = all_uids

    async def list_folders(self):
        return [
            {
                "name": name,
                "delimiter": "/",
                "flags": [],
                "role": "inbox" if name == "INBOX" else None,
            }
            for name in self.folder_names
        ]

    async def fetch_headers(self, folder: str, *, limit: int):
        return {
            "rows": self.rows,
            "uidvalidity": self.uidvalidity,
            "uidnext": len(self.rows) + 1,
            "messages": (
                self.message_count
                if self.message_count is not None
                else len(self.rows)
            ),
            "uids": self.uids or [row["uid"] for row in self.rows],
        }

    async def fetch_uids(self, folder: str):
        return self.all_uids

    async def close(self):
        self.closed = True


def account() -> Account:
    return Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
    )


def header(uid: int) -> dict:
    return {
        "uid": uid,
        "message_id": f"<{uid}@example.com>",
        "in_reply_to": None,
        "references_json": None,
        "subject": f"邮件 {uid}",
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


def test_sync_startup_upserts_folders_and_messages(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        config = AppConfig(accounts=[account()], initial_fetch=20)
        sync = SyncService(store, account(), "secret", config)
        sync.imap = FakeImap([header(1), header(2)])  # type: ignore[assignment]
        try:
            await sync.sync_startup()
            folders = store.list_folders("work")
            assert folders[0]["name"] == "INBOX"
            assert folders[0]["total"] == 2
        finally:
            await sync.close()
            store.close()

    asyncio.run(run())


def test_uidvalidity_change_clears_folder(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        config = AppConfig(accounts=[account()], initial_fetch=20)
        sync = SyncService(store, account(), "secret", config)
        fake = FakeImap([header(1)])
        sync.imap = fake  # type: ignore[assignment]
        try:
            await sync.sync_startup()
            assert store.list_folders("work")[0]["total"] == 1
            fake.rows = [header(3)]
            fake.uidvalidity = 2
            await sync.sync_folder("INBOX")
            messages = store.list_messages(store.list_folders("work")[0]["id"])
            assert [message["uid"] for message in messages] == [3]
        finally:
            await sync.close()
            store.close()

    asyncio.run(run())


def test_uidvalidity_change_invalidates_queued_operations(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        config = AppConfig(accounts=[account()], initial_fetch=20)
        sync = SyncService(store, account(), "secret", config)
        fake = FakeImap([header(1)])
        sync.imap = fake  # type: ignore[assignment]
        try:
            await sync.sync_startup()
            [message_id] = [
                row["id"]
                for row in store.list_messages(
                    store.list_folders("work")[0]["id"]
                )
            ]
            operation_id = store.enqueue_operation(
                "work",
                "flag",
                message_id=message_id,
                payload={
                    "folder": "INBOX",
                    "uid": 1,
                    "flag": "\\Seen",
                    "value": True,
                },
            )
            fake.uidvalidity = 2
            await sync.sync_folder("INBOX")
            row = store._conn.execute(
                "SELECT status, last_error FROM operation WHERE id = ?",
                (operation_id,),
            ).fetchone()
            assert row["status"] == "failed"
            assert "UIDVALIDITY" in row["last_error"]
        finally:
            await sync.close()
            store.close()

    asyncio.run(run())


def test_parse_failure_is_not_treated_as_deletion(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        config = AppConfig(accounts=[account()], initial_fetch=20)
        sync = SyncService(store, account(), "secret", config)
        fake = FakeImap([header(1)])
        sync.imap = fake  # type: ignore[assignment]
        try:
            await sync.sync_startup()
            folder_id = store.list_folders("work")[0]["id"]
            [message_id] = [row["id"] for row in store.list_messages(folder_id)]
            # The UID was fetched, but header parsing failed.
            fake.rows = []
            fake.uids = [1]
            await sync.sync_folder("INBOX")
            assert store.get_message(message_id)["is_deleted"] == 0
        finally:
            await sync.close()
            store.close()

    asyncio.run(run())


def test_server_deletion_detected_when_folder_exceeds_limit(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        config = AppConfig(accounts=[account()], initial_fetch=1)
        sync = SyncService(store, account(), "secret", config)
        fake = FakeImap(
            [header(2)],
            uids=[2],
            message_count=5,
            all_uids={2, 3, 4, 5, 6},
        )
        sync.imap = fake  # type: ignore[assignment]
        try:
            await sync.sync_startup()
            folder_id = store.list_folders("work")[0]["id"]
            # Seed a cached UID that the server no longer has.
            store.upsert_headers("work", folder_id, [header(1)])
            await sync.sync_folder("INBOX")
            row = store.get_message_by_uid(folder_id, 1)
            assert row["is_deleted"] == 1
        finally:
            await sync.close()
            store.close()

    asyncio.run(run())


def test_removed_server_folder_is_removed_locally(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        config = AppConfig(accounts=[account()], initial_fetch=20)
        sync = SyncService(store, account(), "secret", config)
        fake = FakeImap([header(1)], folder_names=["INBOX", "Old"])
        sync.imap = fake  # type: ignore[assignment]
        try:
            await sync.list_and_store_folders()
            assert {f["name"] for f in store.list_folders("work")} == {
                "INBOX",
                "Old",
            }
            fake.folder_names = ["INBOX"]
            folders = await sync.list_and_store_folders()
            assert {f["name"] for f in folders} == {"INBOX"}
        finally:
            await sync.close()
            store.close()

    asyncio.run(run())
