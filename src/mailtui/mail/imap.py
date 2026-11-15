"""imap-tools adapter.

Each account owns a single-thread executor, so the IMAP connection stays on
one thread and commands are serialized.
"""

from __future__ import annotations

import asyncio
import ssl
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

from imap_tools import MailBox, MailBoxStartTls, MailMessage

from mailtui.config import Account
from mailtui.mail.mime import parse_body, parse_headers

T = TypeVar("T")

SPECIAL_ROLES = {
    "\\inbox": "inbox",
    "\\sent": "sent",
    "\\drafts": "drafts",
    "\\trash": "trash",
    "\\junk": "junk",
    "\\archive": "archive",
}

NAME_ROLES = [
    ("inbox", ("inbox", "收件箱")),
    ("sent", ("sent", "sent items", "已发送")),
    ("drafts", ("draft", "drafts", "草稿")),
    ("trash", ("trash", "deleted items", "已删除", "回收站")),
    ("junk", ("junk", "spam", "垃圾邮件")),
    ("archive", ("archive", "归档")),
]


def folder_role(name: str, flags: tuple[str, ...] | list[str]) -> str | None:
    lowered_flags = {str(flag).lower() for flag in flags}
    for flag, role in SPECIAL_ROLES.items():
        if flag in lowered_flags:
            return role
    lowered_name = name.lower()
    for role, needles in NAME_ROLES:
        if any(needle in lowered_name for needle in needles):
            return role
    return None


class ImapError(RuntimeError):
    """Raised when an IMAP operation fails."""


class ImapClient:
    def __init__(
        self,
        account: Account,
        password: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.account = account
        self.password = password
        self._ssl_context = ssl_context
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"imap-{account.key}"
        )
        self._mailbox: MailBox | MailBoxStartTls | None = None
        self._closed = False

    async def _call(self, fn: Callable[[MailBox | MailBoxStartTls], T]) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._run, fn)

    def _run(self, fn: Callable[[MailBox | MailBoxStartTls], T]) -> T:
        try:
            mailbox = self._ensure_sync()
            return fn(mailbox)
        except Exception:
            self._reset_sync()
            raise

    def _ensure_sync(self) -> MailBox | MailBoxStartTls:
        if self._mailbox is not None:
            return self._mailbox
        context = self._ssl_context or ssl.create_default_context()
        imap = self.account.imap
        if imap.security == "starttls":
            mailbox = MailBoxStartTls(
                host=imap.host, port=imap.port, timeout=30, ssl_context=context
            )
        else:
            mailbox = MailBox(
                host=imap.host, port=imap.port, timeout=30, ssl_context=context
            )
        try:
            mailbox.login(self.account.username, self.password)
        except Exception as exc:
            try:
                mailbox.logout()
            except Exception:
                pass
            raise ImapError(f"IMAP 登录失败: {exc}") from exc
        self._mailbox = mailbox
        return mailbox

    def _reset_sync(self) -> None:
        mailbox = self._mailbox
        self._mailbox = None
        if mailbox is not None:
            try:
                mailbox.logout()
            except Exception:
                pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._close_sync)
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _close_sync(self) -> None:
        self._reset_sync()

    async def list_folders(self) -> list[dict[str, Any]]:
        def operation(mailbox: MailBox | MailBoxStartTls) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for item in mailbox.folder.list():
                flags = list(item.flags)
                if any(str(flag).lower() == "\\noselect" for flag in flags):
                    continue
                result.append(
                    {
                        "name": item.name,
                        "delimiter": item.delim,
                        "flags": flags,
                        "role": folder_role(item.name, flags),
                    }
                )
            return result

        return await self._call(operation)

    async def folder_status(self, folder: str) -> dict[str, int]:
        return await self._call(lambda mailbox: mailbox.folder.status(folder))

    async def fetch_headers(
        self, folder: str, *, limit: int
    ) -> dict[str, Any]:
        def operation(mailbox: MailBox | MailBoxStartTls) -> dict[str, Any]:
            mailbox.folder.set(folder)
            status = mailbox.folder.status(folder)
            uidnext = int(status.get("UIDNEXT") or 0)
            if uidnext > 0:
                start = max(1, uidnext - limit)
                uid_list = [str(uid) for uid in range(start, uidnext)]
                messages = (
                    mailbox.fetch(
                        uid_list=uid_list,
                        headers_only=True,
                        mark_seen=False,
                    )
                    if uid_list
                    else []
                )
            else:
                messages = mailbox.fetch(
                    reverse=True,
                    limit=limit,
                    headers_only=True,
                    mark_seen=False,
                )
            rows = []
            uids: list[int] = []
            for message in messages:
                try:
                    uid = int(message.uid or 0)
                except (TypeError, ValueError):
                    uid = 0
                if uid:
                    uids.append(uid)
                try:
                    rows.append(parse_headers(message))
                except Exception:
                    continue
            return {
                "rows": rows,
                "uidvalidity": status.get("UIDVALIDITY"),
                "uidnext": status.get("UIDNEXT"),
                "messages": status.get("MESSAGES"),
                "uids": uids,
            }

        return await self._call(operation)

    async def fetch_uids(self, folder: str) -> set[int] | None:
        """Return the complete UID set when the server supports UID SEARCH."""

        def operation(mailbox: MailBox | MailBoxStartTls) -> set[int] | None:
            mailbox.folder.set(folder)
            try:
                return {int(uid) for uid in mailbox.uids("ALL")}
            except Exception:
                return None

        return await self._call(operation)

    async def fetch_body(self, folder: str, uid: int) -> dict[str, Any]:
        def operation(mailbox: MailBox | MailBoxStartTls) -> dict[str, Any]:
            mailbox.folder.set(folder)
            messages = mailbox.fetch(uid_list=[str(uid)], mark_seen=False)
            message: MailMessage | None = next(iter(messages), None)
            if message is None:
                raise ImapError(f"邮件不存在: {folder}/{uid}")
            text, html_text, raw, attachments = parse_body(message)
            return {
                "text": text,
                "html_text": html_text,
                "raw": raw,
                "attachments": attachments,
                "flags": list(message.flags),
            }

        return await self._call(operation)

    async def set_flag(
        self, folder: str, uid: int, flag: str, value: bool
    ) -> None:
        def operation(mailbox: MailBox | MailBoxStartTls) -> None:
            mailbox.folder.set(folder)
            mailbox.flag([str(uid)], flag, value)

        await self._call(operation)

    async def move(self, folder: str, uid: int, destination: str) -> None:
        def operation(mailbox: MailBox | MailBoxStartTls) -> None:
            mailbox.folder.set(folder)
            mailbox.move([str(uid)], destination)

        await self._call(operation)

    async def delete(self, folder: str, uid: int) -> None:
        def operation(mailbox: MailBox | MailBoxStartTls) -> None:
            mailbox.folder.set(folder)
            mailbox.delete([str(uid)])
            mailbox.expunge()

        await self._call(operation)

    async def append(self, folder: str, raw: bytes, flags: set[str]) -> None:
        def operation(mailbox: MailBox | MailBoxStartTls) -> None:
            mailbox.append(raw, folder=folder, flag_set=flags)

        await self._call(operation)
