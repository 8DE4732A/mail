"""Mailbox synchronization."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from mailtui.config import Account, AppConfig
from mailtui.db.store import Store
from mailtui.mail.imap import ImapClient

logger = logging.getLogger(__name__)

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class SyncService:
    def __init__(
        self,
        store: Store,
        account: Account,
        password: str,
        config: AppConfig,
        *,
        on_event: EventCallback | None = None,
    ) -> None:
        self.store = store
        self.account = account
        self.config = config
        self.imap = ImapClient(account, password)
        self.on_event = on_event
        self._folder_locks: dict[str, asyncio.Lock] = {}

    async def emit(self, name: str, payload: dict[str, Any]) -> None:
        if self.on_event:
            result = self.on_event(name, payload)
            if asyncio.iscoroutine(result):
                await result

    async def list_and_store_folders(self) -> list[dict[str, Any]]:
        folders = await self.imap.list_folders()
        for folder in folders:
            self.store.upsert_folder(
                self.account.key,
                folder["name"],
                delimiter=folder["delimiter"],
                role=folder["role"],
            )
        if folders:
            self.store.remove_folders_not_in(
                self.account.key, {folder["name"] for folder in folders}
            )
        return self.store.list_folders(self.account.key)

    async def sync_folder(self, folder_name: str) -> int:
        lock = self._folder_locks.setdefault(folder_name, asyncio.Lock())
        async with lock:
            folder = next(
                (
                    item
                    for item in self.store.list_folders(self.account.key)
                    if item["name"] == folder_name
                ),
                None,
            )
            if folder is None:
                self.store.upsert_folder(self.account.key, folder_name)
                folder = next(
                    item
                    for item in self.store.list_folders(self.account.key)
                    if item["name"] == folder_name
                )

            await self.emit("sync", {"folder": folder_name, "state": "running"})
            await self.emit(
                "folder", {"folder": folder_name, "state": "syncing"}
            )
            result = await self.imap.fetch_headers(
                folder_name, limit=self.config.initial_fetch
            )
            uidvalidity = result.get("uidvalidity")
            if (
                folder.get("uidvalidity")
                and uidvalidity
                and int(folder["uidvalidity"]) != int(uidvalidity)
            ):
                self.store.cancel_queued_operations_for_folder(
                    self.account.key, folder_name
                )
                self.store.clear_folder_messages(int(folder["id"]))

            rows = result.get("rows", [])
            fetched_uids = {int(row["uid"]) for row in rows}
            server_count = result.get("messages")
            if server_count is not None and int(server_count) <= self.config.initial_fetch:
                candidate = set(result.get("uids") or fetched_uids)
                server_uids: set[int] | None = (
                    candidate
                    if len(candidate) == int(server_count)
                    else None
                )
            else:
                candidate = await self.imap.fetch_uids(folder_name)
                server_uids = (
                    candidate
                    if candidate is not None
                    and (
                        server_count is None
                        or len(candidate) == int(server_count)
                    )
                    else None
                )
            if server_uids is not None:
                self.store.reconcile_deletions(
                    int(folder["id"]), server_uids
                )
            self.store.upsert_headers(self.account.key, int(folder["id"]), rows)
            self.store.upsert_folder(
                self.account.key,
                folder_name,
                uidvalidity=uidvalidity,
                uidnext=result.get("uidnext"),
            )
            self.store.set_folder_synced(int(folder["id"]), uidvalidity)
            await self.emit(
                "folder",
                {
                    "folder": folder_name,
                    "state": "done",
                    "new": len(rows),
                },
            )
            await self.emit("sync", {"folder": folder_name, "state": "done"})
            return len(rows)

    async def sync_startup(self) -> None:
        folders = await self.list_and_store_folders()
        await self.emit("folders", {"folders": folders})
        targets = [
            item
            for item in self.store.list_folders(self.account.key)
            if item["role"]
            in ("inbox", "sent", "drafts", "trash", "junk", "archive")
        ]
        if not targets:
            targets = [
                item
                for item in self.store.list_folders(self.account.key)
                if item["name"].upper() == "INBOX"
            ]
        for folder in targets:
            try:
                await self.sync_folder(folder["name"])
            except Exception as exc:
                logger.exception("startup sync failed: %s", folder["name"])
                await self.emit(
                    "error",
                    {
                        "folder": folder["name"],
                        "error": str(exc),
                    },
                )

    async def poll_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.poll_interval)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                break
            inbox = self.store.folder_by_role(self.account.key, "inbox")
            if inbox:
                try:
                    await self.sync_folder(inbox["name"])
                except Exception as exc:
                    await self.emit("error", {"error": str(exc)})

    async def close(self) -> None:
        await self.imap.close()
