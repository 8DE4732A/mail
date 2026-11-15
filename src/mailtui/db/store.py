"""SQLite store.

The store is intentionally small and synchronous. Network work lives in the
mail adapters; SQLite operations are fast and are serialized with a lock so the
UI and background workers can share one connection safely.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from mailtui.paths import db_path

SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _addresses(value: str | None) -> list[dict[str, str]]:
    raw = _loads(value, [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _sender_text(from_json: str | None) -> str:
    addresses = _addresses(from_json)
    if not addresses:
        return ""
    first = addresses[0]
    name = first.get("name", "").strip()
    email = first.get("email", "").strip()
    return f"{name} <{email}>" if name else email


def _recipient_text(*values: str | None) -> str:
    result: list[str] = []
    for value in values:
        for address in _addresses(value):
            email = address.get("email", "").strip()
            if email:
                result.append(email)
    return " ".join(result)


def _references_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return _json(value)


def _payload_folder(raw: str | None) -> str | None:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return payload.get("folder")


def _invalidate_folder_operations(
    conn: sqlite3.Connection,
    account_key: str,
    folder_name: str,
    reason: str,
) -> int:
    rows = conn.execute(
        """
        SELECT id, payload_json FROM operation
         WHERE account_key = ? AND status = 'queued'
           AND kind IN ('flag', 'move', 'delete')
        """,
        (account_key,),
    ).fetchall()
    invalidated = 0
    for row in rows:
        if _payload_folder(row["payload_json"]) != folder_name:
            continue
        conn.execute(
            """
            UPDATE operation
               SET status = 'failed', last_error = ?, updated_at = ?
             WHERE id = ?
            """,
            (reason, utc_now(), row["id"]),
        )
        invalidated += 1
    return invalidated


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version >= SCHEMA_VERSION:
                self._ensure_columns()
                return
            self._conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            self._ensure_columns()
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _ensure_columns(self) -> None:
        """Add columns that may be missing in a database from an older build."""

        message_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(message)")
        }
        if "reply_to_json" not in message_columns:
            self._conn.execute(
                "ALTER TABLE message ADD COLUMN reply_to_json TEXT NOT NULL DEFAULT '[]'"
            )
        attachment_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(attachment)")
        }
        if "content" not in attachment_columns:
            self._conn.execute("ALTER TABLE attachment ADD COLUMN content BLOB")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    # ---------------------------------------------------------------- folders

    def upsert_folder(
        self,
        account_key: str,
        name: str,
        *,
        delimiter: str | None = None,
        role: str | None = None,
        uidvalidity: int | None = None,
        uidnext: int | None = None,
    ) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM folder WHERE account_key = ? AND name = ?",
                (account_key, name),
            ).fetchone()
            if row is None:
                cur = self._conn.execute(
                    """
                    INSERT INTO folder
                      (account_key, name, delimiter, role, uidvalidity, uidnext)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (account_key, name, delimiter, role, uidvalidity, uidnext),
                )
                return int(cur.lastrowid)
            self._conn.execute(
                """
                UPDATE folder
                   SET delimiter = COALESCE(?, delimiter),
                       role = COALESCE(?, role),
                       uidvalidity = COALESCE(?, uidvalidity),
                       uidnext = COALESCE(?, uidnext)
                 WHERE id = ?
                """,
                (delimiter, role, uidvalidity, uidnext, row["id"]),
            )
            return int(row["id"])

    def list_folders(self, account_key: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT f.*,
                       COUNT(m.id) FILTER (WHERE m.is_deleted = 0) AS total,
                       COUNT(m.id) FILTER (
                         WHERE m.is_deleted = 0 AND m.is_seen = 0
                       ) AS unread
                  FROM folder f
                  LEFT JOIN message m ON m.folder_id = f.id
                 WHERE f.account_key = ?
                 GROUP BY f.id
                 ORDER BY
                   CASE f.role
                     WHEN 'inbox' THEN 0
                     WHEN 'sent' THEN 1
                     WHEN 'drafts' THEN 2
                     WHEN 'trash' THEN 3
                     WHEN 'junk' THEN 4
                     WHEN 'archive' THEN 5
                     ELSE 9
                   END,
                   f.name
                """,
                (account_key,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_folder(self, folder_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM folder WHERE id = ?", (folder_id,)
            ).fetchone()
            return dict(row) if row else None

    def folder_by_role(
        self, account_key: str, role: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM folder WHERE account_key = ? AND role = ? LIMIT 1",
                (account_key, role),
            ).fetchone()
            return dict(row) if row else None

    def set_folder_synced(self, folder_id: int, uidvalidity: int | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE folder SET last_sync_at = ?, uidvalidity = COALESCE(?, uidvalidity) WHERE id = ?",
                (utc_now(), uidvalidity, folder_id),
            )

    # --------------------------------------------------------------- messages

    def upsert_headers(
        self,
        account_key: str,
        folder_id: int,
        rows: Iterable[dict[str, Any]],
    ) -> list[int]:
        ids: list[int] = []
        with self.transaction() as conn:
            for item in rows:
                existing = conn.execute(
                    "SELECT id FROM message WHERE folder_id = ? AND uid = ?",
                    (folder_id, item["uid"]),
                ).fetchone()
                values = (
                    item.get("message_id"),
                    item.get("in_reply_to"),
                    _references_value(
                        item.get("references_json") or item.get("references")
                    ),
                    item.get("subject", ""),
                    _json(item.get("from_values", [])),
                    _json(item.get("to_values", [])),
                    _json(item.get("cc_values", [])),
                    _json(item.get("reply_to_values", [])),
                    item["date_utc"],
                    item.get("size", 0),
                    _json(item.get("flags", [])),
                    int(bool(item.get("is_seen"))),
                    int(bool(item.get("is_flagged"))),
                    int(bool(item.get("has_attachments"))),
                    item.get("snippet", ""),
                )
                if existing is None:
                    cur = conn.execute(
                        """
                        INSERT INTO message (
                          account_key, folder_id, uid, message_id, in_reply_to,
                          references_json, subject, from_json, to_json, cc_json,
                          reply_to_json,
                          date_utc, size, flags_json, is_seen, is_flagged,
                          has_attachments, snippet, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            account_key,
                            folder_id,
                            item["uid"],
                            *values,
                            utc_now(),
                        ),
                    )
                    message_id = int(cur.lastrowid)
                    self._index_message(
                        conn,
                        message_id,
                        item.get("subject", ""),
                        _sender_text(_json(item.get("from_values", []))),
                        _recipient_text(
                            _json(item.get("to_values", [])),
                            _json(item.get("cc_values", [])),
                        ),
                        "",
                    )
                else:
                    message_id = int(existing["id"])
                    pending_flag = conn.execute(
                        """
                        SELECT 1 FROM operation
                         WHERE message_id = ? AND kind = 'flag'
                           AND status IN ('queued','running')
                         LIMIT 1
                        """,
                        (message_id,),
                    ).fetchone()
                    if pending_flag:
                        current = conn.execute(
                            "SELECT flags_json, is_seen, is_flagged FROM message WHERE id = ?",
                            (message_id,),
                        ).fetchone()
                        if current:
                            values = (
                                *values[:10],
                                current["flags_json"],
                                current["is_seen"],
                                current["is_flagged"],
                                *values[13:],
                            )
                    conn.execute(
                        """
                        UPDATE message
                           SET message_id = ?, in_reply_to = ?, references_json = ?,
                               subject = ?, from_json = ?, to_json = ?, cc_json = ?,
                               reply_to_json = ?, date_utc = ?, size = ?, flags_json = ?, is_seen = ?,
                               is_flagged = ?, has_attachments = MAX(has_attachments, ?), snippet = ?
                         WHERE id = ?
                        """,
                        (*values, message_id),
                    )
                    body_row = conn.execute(
                        "SELECT text_body FROM body WHERE message_id = ?",
                        (message_id,),
                    ).fetchone()
                    self._index_message(
                        conn,
                        message_id,
                        item.get("subject", ""),
                        _sender_text(_json(item.get("from_values", []))),
                        _recipient_text(
                            _json(item.get("to_values", [])),
                            _json(item.get("cc_values", [])),
                        ),
                        body_row["text_body"] if body_row else "",
                    )
                ids.append(message_id)
        return ids

    def _index_message(
        self,
        conn: sqlite3.Connection,
        message_id: int,
        subject: str,
        sender: str,
        recipients: str,
        body: str,
    ) -> None:
        conn.execute("DELETE FROM message_fts WHERE rowid = ?", (message_id,))
        conn.execute(
            "INSERT INTO message_fts(rowid, subject, sender, recipients, body) VALUES (?, ?, ?, ?, ?)",
            (message_id, subject, sender, recipients, body),
        )

    def list_messages(
        self,
        folder_id: int,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT m.*, f.name AS folder_name, f.role AS folder_role
                  FROM message m
                  JOIN folder f ON f.id = m.folder_id
                 WHERE m.folder_id = ? AND m.is_deleted = 0
                 ORDER BY m.date_utc DESC, m.id DESC
                 LIMIT ? OFFSET ?
                """,
                (folder_id, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def existing_uids(self, folder_id: int) -> set[int]:
        with self._lock:
            return {
                int(row["uid"])
                for row in self._conn.execute(
                    "SELECT uid FROM message WHERE folder_id = ?", (folder_id,)
                )
            }

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT m.*, f.name AS folder_name, f.role AS folder_role,
                       b.text_body, b.html_text
                  FROM message m
                  JOIN folder f ON f.id = m.folder_id
                  LEFT JOIN body b ON b.message_id = m.id
                 WHERE m.id = ?
                """,
                (message_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_message_by_uid(
        self, folder_id: int, uid: int
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM message WHERE folder_id = ? AND uid = ?",
                (folder_id, uid),
            ).fetchone()
            return dict(row) if row else None

    def set_body_state(self, message_id: int, state: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE message SET body_state = ? WHERE id = ?",
                (state, message_id),
            )

    def save_body(
        self,
        message_id: int,
        *,
        text_body: str,
        html_text: str,
        raw_eml: bytes | None,
        attachments: Iterable[dict[str, Any]],
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO body (message_id, text_body, html_text, raw_eml, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                  text_body = excluded.text_body,
                  html_text = excluded.html_text,
                  raw_eml = excluded.raw_eml,
                  fetched_at = excluded.fetched_at
                """,
                (message_id, text_body, html_text, raw_eml, utc_now()),
            )
            conn.execute("DELETE FROM attachment WHERE message_id = ?", (message_id,))
            attachment_list = list(attachments)
            for attachment in attachment_list:
                conn.execute(
                    """
                    INSERT INTO attachment
                      (message_id, part_id, filename, content_type, size,
                       content_id, local_path, content, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        attachment.get("part_id", ""),
                        attachment.get("filename", ""),
                        attachment.get("content_type", "application/octet-stream"),
                        attachment.get("size", 0),
                        attachment.get("content_id"),
                        attachment.get("local_path"),
                        attachment.get("content"),
                        attachment.get("state", "downloaded"),
                    ),
                )
            conn.execute(
                "UPDATE message SET body_state = 'ready', has_attachments = ? WHERE id = ?",
                (1 if attachment_list else 0, message_id),
            )
            row = conn.execute(
                "SELECT subject, from_json, to_json, cc_json FROM message WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row:
                self._index_message(
                    conn,
                    message_id,
                    row["subject"],
                    _sender_text(row["from_json"]),
                    _recipient_text(row["to_json"], row["cc_json"]),
                    text_body,
                )

    def attachments(self, message_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM attachment WHERE message_id = ? ORDER BY id",
                (message_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def update_flags(
        self, message_id: int, flags: Iterable[str], *, seen: bool, flagged: bool
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE message SET flags_json = ?, is_seen = ?, is_flagged = ? WHERE id = ?",
                (_json(list(flags)), int(seen), int(flagged), message_id),
            )

    def mark_deleted(self, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE message SET is_deleted = 1 WHERE id = ?", (message_id,)
            )

    def mark_deleted_by_uid(self, folder_id: int, uid: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE message SET is_deleted = 1 WHERE folder_id = ? AND uid = ?",
                (folder_id, uid),
            )

    def restore_message(self, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE message SET is_deleted = 0 WHERE id = ?", (message_id,)
            )

    def has_pending_flag_operation(self, message_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM operation
                 WHERE message_id = ? AND kind = 'flag'
                   AND status IN ('queued', 'running')
                 LIMIT 1
                """,
                (message_id,),
            ).fetchone()
            return row is not None

    def reconcile_deletions(
        self, folder_id: int, server_uids: set[int]
    ) -> None:
        """Reconcile local deletion flags with the authoritative server UID set."""

        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT id, uid, is_deleted FROM message WHERE folder_id = ?",
                (folder_id,),
            ).fetchall()
            for row in rows:
                uid = int(row["uid"])
                if uid not in server_uids:
                    conn.execute(
                        "UPDATE message SET is_deleted = 1 WHERE id = ?",
                        (row["id"],),
                    )
                elif row["is_deleted"]:
                    pending = conn.execute(
                        """
                        SELECT 1 FROM operation
                         WHERE message_id = ? AND kind IN ('move', 'delete')
                           AND status IN ('queued', 'running')
                         LIMIT 1
                        """,
                        (row["id"],),
                    ).fetchone()
                    if pending is None:
                        conn.execute(
                            "UPDATE message SET is_deleted = 0 WHERE id = ?",
                            (row["id"],),
                        )

    def clear_folder_messages(self, folder_id: int) -> None:
        with self.transaction() as conn:
            ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM message WHERE folder_id = ?", (folder_id,)
                )
            ]
            for message_id in ids:
                conn.execute("DELETE FROM message_fts WHERE rowid = ?", (message_id,))
            conn.execute("DELETE FROM message WHERE folder_id = ?", (folder_id,))

    def cancel_queued_operations_for_folder(
        self, account_key: str, folder_name: str
    ) -> int:
        """Invalidate queued UID-based operations tied to a folder generation."""

        with self.transaction() as conn:
            return _invalidate_folder_operations(
                conn,
                account_key,
                folder_name,
                "UIDVALIDITY changed; operation invalidated",
            )

    def remove_folders_not_in(
        self, account_key: str, keep_names: set[str]
    ) -> list[str]:
        """Remove cached folders absent from the server folder list."""

        removed: list[str] = []
        with self.transaction() as conn:
            folders = conn.execute(
                "SELECT id, name FROM folder WHERE account_key = ?",
                (account_key,),
            ).fetchall()
            for folder in folders:
                if folder["name"] in keep_names:
                    continue
                message_ids = [
                    row["id"]
                    for row in conn.execute(
                        "SELECT id FROM message WHERE folder_id = ?",
                        (folder["id"],),
                    )
                ]
                for message_id in message_ids:
                    conn.execute(
                        "DELETE FROM message_fts WHERE rowid = ?", (message_id,)
                    )
                _invalidate_folder_operations(
                    conn,
                    account_key,
                    folder["name"],
                    "folder no longer exists",
                )
                conn.execute("DELETE FROM folder WHERE id = ?", (folder["id"],))
                removed.append(folder["name"])
        return removed

    def search(
        self, query: str, *, account_key: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        terms = [term for term in re.split(r"\s+", query.strip()) if term]
        if not terms:
            return []
        with self._lock:
            if all(len(term) >= 3 for term in terms):
                escaped = [term.replace('"', '""') for term in terms]
                match = " AND ".join(f'"{term}"' for term in escaped)
                sql = """
                    SELECT m.*, f.name AS folder_name, f.role AS folder_role
                      FROM message_fts
                      JOIN message m ON m.id = message_fts.rowid
                      JOIN folder f ON f.id = m.folder_id
                     WHERE message_fts MATCH ? AND m.is_deleted = 0
                """
                params: list[Any] = [match]
                if account_key:
                    sql += " AND m.account_key = ?"
                    params.append(account_key)
                sql += " ORDER BY bm25(message_fts) LIMIT ?"
                params.append(limit)
                try:
                    return [dict(row) for row in self._conn.execute(sql, params)]
                except sqlite3.OperationalError:
                    pass

            like = f"%{query.strip()}%"
            sql = """
                SELECT m.*, f.name AS folder_name, f.role AS folder_role
                  FROM message m
                  JOIN folder f ON f.id = m.folder_id
                  LEFT JOIN body b ON b.message_id = m.id
                 WHERE m.is_deleted = 0
                   AND (m.subject LIKE ? OR m.from_json LIKE ? OR
                        m.to_json LIKE ? OR b.text_body LIKE ?)
            """
            params = [like, like, like, like]
            if account_key:
                sql += " AND m.account_key = ?"
                params.append(account_key)
            sql += " ORDER BY m.date_utc DESC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in self._conn.execute(sql, params)]

    # ------------------------------------------------------------- operations

    def enqueue_operation(
        self,
        account_key: str,
        kind: str,
        *,
        payload: dict[str, Any] | None = None,
        message_id: int | None = None,
        draft_id: int | None = None,
        mime_blob: bytes | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO operation
                  (account_key, kind, message_id, draft_id, payload_json, mime_blob,
                   created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_key,
                    kind,
                    message_id,
                    draft_id,
                    _json(payload or {}),
                    mime_blob,
                    utc_now(),
                    utc_now(),
                ),
            )
            return int(cur.lastrowid)

    def claim_operation(self, account_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM operation
                 WHERE account_key = ? AND status = 'queued'
                   AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                 ORDER BY id LIMIT 1
                """,
                (account_key, utc_now()),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE operation SET status = 'running', updated_at = ? WHERE id = ?",
                (utc_now(), row["id"]),
            )
            result = dict(row)
            result["status"] = "running"
            return result

    def finish_operation(
        self,
        operation_id: int,
        status: str,
        *,
        error: str | None = None,
        attempts: int | None = None,
        next_attempt_at: str | None = None,
    ) -> None:
        with self._lock:
            if attempts is None:
                self._conn.execute(
                    """
                    UPDATE operation
                       SET status = ?, last_error = ?, next_attempt_at = ?,
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (status, error, next_attempt_at, utc_now(), operation_id),
                )
            else:
                self._conn.execute(
                    """
                    UPDATE operation
                       SET status = ?, last_error = ?, attempts = ?,
                           next_attempt_at = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (status, error, attempts, next_attempt_at, utc_now(), operation_id),
                )

    def reset_running_operations(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE operation
                   SET status = CASE WHEN kind = 'send' THEN 'unknown' ELSE 'queued' END,
                       updated_at = ?
                 WHERE status = 'running'
                """,
                (utc_now(),),
            )
            self._conn.execute(
                "UPDATE message SET body_state = 'none' WHERE body_state = 'fetching'"
            )

    # ------------------------------------------------------------------ misc

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def purge_account(self, account_key: str) -> None:
        with self.transaction() as conn:
            ids = [
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM message WHERE account_key = ?", (account_key,)
                )
            ]
            for message_id in ids:
                conn.execute("DELETE FROM message_fts WHERE rowid = ?", (message_id,))
            conn.execute("DELETE FROM folder WHERE account_key = ?", (account_key,))
            conn.execute("DELETE FROM draft WHERE account_key = ?", (account_key,))
            conn.execute("DELETE FROM operation WHERE account_key = ?", (account_key,))

    def rebuild_fts(self) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM message_fts")
            rows = conn.execute(
                """
                SELECT m.id, m.subject, m.from_json, m.to_json, m.cc_json,
                       COALESCE(b.text_body, '') AS body
                  FROM message m
                  LEFT JOIN body b ON b.message_id = m.id
                 WHERE m.is_deleted = 0
                """
            )
            for row in rows:
                conn.execute(
                    "INSERT INTO message_fts(rowid, subject, sender, recipients, body) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        row["id"],
                        row["subject"],
                        _sender_text(row["from_json"]),
                        _recipient_text(row["to_json"], row["cc_json"]),
                        row["body"],
                    ),
                )

    def vacuum(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")
