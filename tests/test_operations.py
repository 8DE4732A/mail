import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from mailtui.config import Account, ImapConfig, SmtpConfig
from mailtui.db.store import Store
from mailtui.mail.smtp import AmbiguousSmtpError, TransientSmtpError
from mailtui.services.operations import OperationWorker


class FakeImap:
    def __init__(self) -> None:
        self.flags: list[tuple] = []
        self.appended: list[tuple] = []
        self.moved: list[tuple] = []
        self.deleted: list[tuple] = []

    async def set_flag(self, folder, uid, flag, value):
        self.flags.append((folder, uid, flag, value))

    async def append(self, folder, raw, flags):
        self.appended.append((folder, raw, flags))

    async def move(self, folder, uid, destination):
        self.moved.append((folder, uid, destination))

    async def delete(self, folder, uid):
        self.deleted.append((folder, uid))


def account() -> Account:
    return Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
        sent_copy="append",
    )


def test_send_operation_calls_smtp_and_appends_sent(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        store.upsert_folder("work", "INBOX", role="inbox")
        store.upsert_folder("work", "Sent", role="sent")
        store.enqueue_operation(
            "work",
            "send",
            payload={"recipients": ["alice@example.com"]},
            mime_blob=b"raw message",
        )
        operation = store.claim_operation("work")
        assert operation is not None
        imap = FakeImap()
        worker = OperationWorker(store, account(), "secret", imap)  # type: ignore[arg-type]
        with patch(
            "mailtui.services.operations.send_message", new=AsyncMock()
        ) as send:
            await worker._execute(operation)
            send.assert_awaited_once()
        assert imap.appended[0][0] == "Sent"
        assert store.get_message(1) is None
        store.close()

    asyncio.run(run())


def test_transient_send_failure_is_requeued(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        store.enqueue_operation(
            "work",
            "send",
            payload={"recipients": ["alice@example.com"]},
            mime_blob=b"raw message",
        )
        operation = store.claim_operation("work")
        assert operation is not None
        worker = OperationWorker(store, account(), "secret", FakeImap())  # type: ignore[arg-type]
        with patch(
            "mailtui.services.operations.send_message",
            new=AsyncMock(side_effect=TransientSmtpError("temporary")),
        ):
            await worker._execute(operation)
        row = store._conn.execute(
            "SELECT status, attempts, next_attempt_at FROM operation WHERE id = ?",
            (operation["id"],),
        ).fetchone()
        assert row["status"] == "queued"
        assert row["attempts"] == 1
        assert row["next_attempt_at"]
        store.close()

    asyncio.run(run())


def test_ambiguous_send_is_marked_unknown(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        store.enqueue_operation(
            "work",
            "send",
            payload={"recipients": ["alice@example.com"]},
            mime_blob=b"raw message",
        )
        operation = store.claim_operation("work")
        assert operation is not None
        worker = OperationWorker(store, account(), "secret", FakeImap())  # type: ignore[arg-type]
        with patch(
            "mailtui.services.operations.send_message",
            new=AsyncMock(side_effect=AmbiguousSmtpError("disconnect")),
        ):
            await worker._execute(operation)
        row = store._conn.execute(
            "SELECT status FROM operation WHERE id = ?",
            (operation["id"],),
        ).fetchone()
        assert row["status"] == "unknown"
        store.close()

    asyncio.run(run())


def test_terminal_move_failure_restores_message(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        folder_id = store.upsert_folder("work", "INBOX", role="inbox")
        [message_id] = store.upsert_headers("work", folder_id, [_header(1)])
        store.mark_deleted(message_id)
        operation_id = store.enqueue_operation(
            "work",
            "move",
            message_id=message_id,
            payload={"folder": "INBOX", "uid": 1, "destination": "Trash"},
        )
        operation = store.claim_operation("work")
        assert operation is not None
        operation["attempts"] = 9
        worker = OperationWorker(store, account(), "secret", FakeImap())  # type: ignore[arg-type]
        worker._retry(operation, "permanent failure")
        assert store.get_message(message_id)["is_deleted"] == 0
        row = store._conn.execute(
            "SELECT status FROM operation WHERE id = ?", (operation_id,)
        ).fetchone()
        assert row["status"] == "failed"
        store.close()

    asyncio.run(run())


def test_flag_and_move_operations(tmp_path: Path) -> None:
    async def run() -> None:
        store = Store(tmp_path / "mail.db")
        folder_id = store.upsert_folder("work", "INBOX", role="inbox")
        [message_id] = store.upsert_headers("work", folder_id, [_header(1)])
        imap = FakeImap()
        worker = OperationWorker(store, account(), "secret", imap)  # type: ignore[arg-type]

        store.enqueue_operation(
            "work",
            "flag",
            message_id=message_id,
            payload={"folder": "INBOX", "uid": 1, "flag": "\\Seen", "value": True},
        )
        await worker._execute(store.claim_operation("work"))
        assert imap.flags == [("INBOX", 1, "\\Seen", True)]

        store.enqueue_operation(
            "work",
            "move",
            message_id=message_id,
            payload={"folder": "INBOX", "uid": 1, "destination": "Trash"},
        )
        await worker._execute(store.claim_operation("work"))
        assert imap.moved == [("INBOX", 1, "Trash")]
        assert store.get_message(message_id)["is_deleted"] == 1
        store.close()

    asyncio.run(run())


def _header(uid: int) -> dict:
    return {
        "uid": uid,
        "message_id": f"<{uid}@example.com>",
        "in_reply_to": None,
        "references_json": None,
        "subject": "测试",
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
