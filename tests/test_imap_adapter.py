import asyncio
from unittest.mock import patch

from imap_tools import FolderInfo, MailMessage

from mailtui.config import Account, ImapConfig, SmtpConfig
from mailtui.mail.imap import ImapClient

RAW = (
    b"From: Alice <alice@example.com>\r\n"
    b"To: me@example.com\r\n"
    b"Subject: hello\r\n"
    b"Message-ID: <1@example.com>\r\n"
    b"Date: Thu, 13 Nov 2026 10:00:00 +0800\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"hello body\r\n"
)


class FakeFolderManager:
    def list(self, *args, **kwargs):
        return [FolderInfo("INBOX", "/", ())]  # type: ignore[arg-type]

    def set(self, folder, readonly=False):
        return ("OK", [])

    def status(self, folder=None, options=None):
        return {"UIDVALIDITY": 1, "UIDNEXT": 2, "MESSAGES": 1}


class FakeMailBox:
    def __init__(self, **kwargs):
        self.folder = FakeFolderManager()
        self.flag_calls = []
        self.append_calls = []
        self.move_calls = []
        self.delete_calls = []

    def login(self, username, password):
        return self

    def logout(self):
        return ("BYE", [])

    def fetch(self, *args, **kwargs):
        return [MailMessage.from_bytes(RAW)]

    def flag(self, uid_list, flag_set, value, chunks=None):
        self.flag_calls.append((uid_list, flag_set, value))
        return []

    def move(self, uid_list, destination_folder, chunks=None):
        self.move_calls.append((uid_list, destination_folder))
        return []

    def delete(self, uid_list, chunks=None):
        self.delete_calls.append(uid_list)
        return []

    def expunge(self):
        return ("OK", [])

    def append(self, message, folder="INBOX", dt=None, flag_set=None):
        self.append_calls.append((message, folder, flag_set))
        return ("OK", [])


def account() -> Account:
    return Account(
        key="work",
        email="me@example.com",
        username="me@example.com",
        imap=ImapConfig("imap.example.com"),
        smtp=SmtpConfig("smtp.example.com"),
    )


def test_adapter_fetch_flag_move_append() -> None:
    async def run() -> None:
        with patch("mailtui.mail.imap.MailBox", FakeMailBox):
            client = ImapClient(account(), "secret")
            try:
                folders = await client.list_folders()
                assert folders[0]["name"] == "INBOX"
                result = await client.fetch_headers("INBOX", limit=20)
                assert result["rows"][0]["subject"] == "hello"
                body = await client.fetch_body("INBOX", 1)
                assert body["text"] == "hello body"
                await client.set_flag("INBOX", 1, "\\Seen", True)
                await client.move("INBOX", 1, "Trash")
                await client.append("Sent", b"raw", {"\\Seen"})
                mailbox = client._mailbox
                assert mailbox.flag_calls == [(["1"], "\\Seen", True)]
                assert mailbox.move_calls == [(["1"], "Trash")]
                assert mailbox.append_calls[0][1] == "Sent"
            finally:
                await client.close()

    asyncio.run(run())
