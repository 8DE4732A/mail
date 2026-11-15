from pathlib import Path

from mailtui.db.store import Store


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "mail.db")


def header(uid: int, subject: str = "测试邮件") -> dict:
    return {
        "uid": uid,
        "message_id": f"<{uid}@example.com>",
        "in_reply_to": None,
        "references_json": None,
        "subject": subject,
        "from_values": [{"name": "Alice", "email": "alice@example.com"}],
        "to_values": [{"name": "", "email": "me@example.com"}],
        "cc_values": [],
        "reply_to_values": [],
        "date_utc": "2026-11-13T00:00:00+00:00",
        "size": 1000,
        "flags": [],
        "is_seen": False,
        "is_flagged": False,
        "has_attachments": False,
        "snippet": "",
    }


def test_folder_message_and_search(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        folder_id = store.upsert_folder("work", "INBOX", role="inbox", uidvalidity=1)
        message_ids = store.upsert_headers("work", folder_id, [header(1), header(2, "周会纪要")])
        assert len(message_ids) == 2
        assert store.list_folders("work")[0]["unread"] == 2

        store.save_body(
            message_ids[0],
            text_body="支付链路灰度完成",
            html_text="",
            raw_eml=None,
            attachments=[],
        )
        assert store.search("支付链路")[0]["id"] == message_ids[0]
        assert store.search("支付")[0]["id"] == message_ids[0]
        assert store.get_message(message_ids[1])["subject"] == "周会纪要"
    finally:
        store.close()


def test_operations_and_flags(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        folder_id = store.upsert_folder("work", "INBOX", role="inbox")
        [message_id] = store.upsert_headers("work", folder_id, [header(1)])
        store.update_flags(message_id, ["\\Seen", "\\Flagged"], seen=True, flagged=True)
        row = store.get_message(message_id)
        assert row["is_seen"] == 1
        assert row["is_flagged"] == 1

        operation_id = store.enqueue_operation(
            "work",
            "flag",
            message_id=message_id,
            payload={"folder": "INBOX", "uid": 1, "flag": "\\Seen", "value": True},
        )
        claimed = store.claim_operation("work")
        assert claimed["id"] == operation_id
        store.finish_operation(operation_id, "done")
        assert store.claim_operation("work") is None
    finally:
        store.close()


def test_pending_flag_keeps_local_state(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        folder_id = store.upsert_folder("work", "INBOX", role="inbox")
        [message_id] = store.upsert_headers("work", folder_id, [header(1)])
        store.update_flags(message_id, ["\\Seen"], seen=True, flagged=False)
        store.enqueue_operation(
            "work",
            "flag",
            message_id=message_id,
            payload={"folder": "INBOX", "uid": 1, "flag": "\\Seen", "value": True},
        )
        assert store.has_pending_flag_operation(message_id)
        store.upsert_headers("work", folder_id, [header(1)])
        assert store.get_message(message_id)["is_seen"] == 1
    finally:
        store.close()


def test_reconcile_deletions_and_pending_mutations(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        folder_id = store.upsert_folder("work", "INBOX", role="inbox")
        first, second = store.upsert_headers(
            "work", folder_id, [header(1), header(2)]
        )
        store.mark_deleted(second)
        store.reconcile_deletions(folder_id, {1, 2})
        assert store.get_message(second)["is_deleted"] == 0

        store.mark_deleted(first)
        store.enqueue_operation(
            "work",
            "move",
            message_id=first,
            payload={"folder": "INBOX", "uid": 1, "destination": "Trash"},
        )
        store.reconcile_deletions(folder_id, {1, 2})
        assert store.get_message(first)["is_deleted"] == 1
        store.reconcile_deletions(folder_id, {2})
        assert store.get_message(first)["is_deleted"] == 1
    finally:
        store.close()


def test_cancel_folder_operations_and_remove_folders(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        folder_id = store.upsert_folder("work", "INBOX", role="inbox")
        [message_id] = store.upsert_headers("work", folder_id, [header(1)])
        operation_id = store.enqueue_operation(
            "work",
            "flag",
            message_id=message_id,
            payload={"folder": "INBOX", "uid": 1, "flag": "\\Seen", "value": True},
        )
        assert store.cancel_queued_operations_for_folder("work", "INBOX") == 1
        row = store._conn.execute(
            "SELECT status FROM operation WHERE id = ?", (operation_id,)
        ).fetchone()
        assert row["status"] == "failed"

        store.upsert_folder("work", "Old")
        store.remove_folders_not_in("work", {"INBOX"})
        assert {folder["name"] for folder in store.list_folders("work")} == {
            "INBOX"
        }
    finally:
        store.close()


def test_references_are_persisted(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        folder_id = store.upsert_folder("work", "INBOX", role="inbox")
        row = header(1)
        row["references"] = ["<root@example.com>", "<parent@example.com>"]
        [message_id] = store.upsert_headers("work", folder_id, [row])
        persisted = store.get_message(message_id)["references_json"]
        assert "root@example.com" in persisted
        assert "parent@example.com" in persisted
    finally:
        store.close()
