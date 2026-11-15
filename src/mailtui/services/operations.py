"""Durable operation worker for sends and mailbox mutations."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from mailtui.config import Account
from mailtui.db.store import Store
from mailtui.mail.imap import ImapClient
from mailtui.mail.smtp import (
    AmbiguousSmtpError,
    PermanentSmtpError,
    TransientSmtpError,
    send_message,
)

logger = logging.getLogger(__name__)

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


def _backoff(attempts: int) -> str:
    seconds = min(600, 5 * (2 ** max(0, attempts - 1)))
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat(timespec="seconds")


class OperationWorker:
    def __init__(
        self,
        store: Store,
        account: Account,
        password: str,
        imap: ImapClient,
        *,
        on_event: EventCallback | None = None,
    ) -> None:
        self.store = store
        self.account = account
        self.password = password
        self.imap = imap
        self.on_event = on_event

    async def emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_event:
            result = self.on_event(name, payload)
            if asyncio.iscoroutine(result):
                await result

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            operation = self.store.claim_operation(self.account.key)
            if operation is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._execute(operation)

    async def _execute(self, operation: dict[str, Any]) -> None:
        payload = json.loads(operation.get("payload_json") or "{}")
        kind = operation["kind"]
        try:
            if kind == "send":
                await self._send(operation, payload)
            elif kind == "flag":
                await self._flag(operation, payload)
            elif kind == "move":
                await self._move(operation, payload)
            elif kind == "delete":
                await self._delete(operation, payload)
            else:
                raise RuntimeError(f"未知操作: {kind}")
        except TransientSmtpError as exc:
            self._retry(operation, str(exc))
        except AmbiguousSmtpError as exc:
            self.store.finish_operation(
                operation["id"], "unknown", error=str(exc)
            )
            await self.emit(
                "operation",
                {
                    "id": operation["id"],
                    "state": "unknown",
                    "kind": "send",
                    "error": str(exc),
                },
            )
        except PermanentSmtpError as exc:
            self.store.finish_operation(operation["id"], "failed", error=str(exc))
            await self.emit("operation", {"id": operation["id"], "state": "failed", "error": str(exc)})
        except Exception as exc:
            logger.exception("operation %s failed", operation.get("id"))
            self._retry(operation, str(exc))

    def _retry(self, operation: dict[str, Any], error: str) -> None:
        attempts = int(operation.get("attempts") or 0) + 1
        status = "failed" if attempts >= 10 else "queued"
        self.store.finish_operation(
            operation["id"],
            status,
            error=error,
            attempts=attempts,
            next_attempt_at=_backoff(attempts) if status == "queued" else None,
        )
        if (
            status == "failed"
            and operation.get("kind") in ("move", "delete")
            and operation.get("message_id")
        ):
            self.store.restore_message(int(operation["message_id"]))

    async def _send(
        self, operation: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        raw = operation.get("mime_blob")
        if not raw:
            raise RuntimeError("发送操作缺少 MIME 数据")
        recipients = payload.get("recipients") or []
        if not recipients:
            raise RuntimeError("发送操作缺少收件人")
        await send_message(self.account, self.password, raw, recipients)
        sent_folder = self.store.folder_by_role(self.account.key, "sent")
        if self.account.sent_copy == "append" and sent_folder:
            try:
                await self.imap.append(sent_folder["name"], raw, {"\\Seen"})
            except Exception as exc:
                self.store.finish_operation(
                    operation["id"],
                    "done",
                    error=f"已发送，但保存到已发送失败: {exc}",
                )
                await self.emit(
                    "operation",
                    {
                        "id": operation["id"],
                        "state": "done",
                        "warning": f"已发送，但保存到已发送失败: {exc}",
                    },
                )
                return
        self.store.finish_operation(operation["id"], "done")
        await self.emit(
            "operation", {"id": operation["id"], "state": "done", "kind": "send"}
        )

    async def _flag(
        self, operation: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        await self.imap.set_flag(
            payload["folder"],
            int(payload["uid"]),
            payload["flag"],
            bool(payload["value"]),
        )
        self.store.finish_operation(operation["id"], "done")
        await self.emit(
            "operation", {"id": operation["id"], "state": "done", "kind": "flag"}
        )

    async def _move(
        self, operation: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        await self.imap.move(
            payload["folder"], int(payload["uid"]), payload["destination"]
        )
        if operation.get("message_id"):
            self.store.mark_deleted(int(operation["message_id"]))
        self.store.finish_operation(operation["id"], "done")
        await self.emit(
            "operation", {"id": operation["id"], "state": "done", "kind": "move"}
        )

    async def _delete(
        self, operation: dict[str, Any], payload: dict[str, Any]
    ) -> None:
        await self.imap.delete(payload["folder"], int(payload["uid"]))
        if operation.get("message_id"):
            self.store.mark_deleted(int(operation["message_id"]))
        self.store.finish_operation(operation["id"], "done")
        await self.emit(
            "operation", {"id": operation["id"], "state": "done", "kind": "delete"}
        )
