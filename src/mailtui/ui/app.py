"""Live Textual application."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import mimetypes
from dataclasses import dataclass, field
from email.utils import getaddresses
from pathlib import Path
from typing import Any

from PIL import Image as PILImage
from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
    TextArea,
    Tree,
)

from mailtui.config import (
    Account,
    AppConfig,
    ConfigError,
    add_account,
    load_config,
    remove_account,
)
from mailtui.credentials import delete_password, store_password
from mailtui.db.store import Store
from mailtui.mail.mime import (
    build_message,
    date_text,
    envelope_recipients,
    forward_subject,
    quote_reply,
    raw_bytes,
    reply_addresses,
    reply_subject,
)
from mailtui.paths import config_path as default_config_path
from mailtui.paths import files_dir

try:
    from textual_image.renderable import (
        HalfcellImage as _HalfcellRenderable,
        Image as _AutoImageRenderable,
        UnicodeImage as _UnicodeRenderable,
    )
    from textual_image.widget import Image as TerminalImage

    IMAGE_PROTOCOL_SUPPORTED = _AutoImageRenderable not in (
        _HalfcellRenderable,
        _UnicodeRenderable,
    )
except Exception:
    TerminalImage = None
    IMAGE_PROTOCOL_SUPPORTED = False
from mailtui.services.operations import OperationWorker
from mailtui.services.sync import SyncService
from mailtui.ui.folder_tree import build_folder_tree
from mailtui.ui.help import HelpScreen

logger = logging.getLogger(__name__)


def _address_list(value: str) -> list[str]:
    return [
        f"{name} <{email}>" if name else email
        for name, email in getaddresses([value])
        if email
    ]


def _sender(row: dict[str, Any]) -> str:
    try:
        values = json.loads(row.get("from_json") or "[]")
    except json.JSONDecodeError:
        return ""
    if not values:
        return ""
    item = values[0]
    return (
        f"{item.get('name', '')} <{item.get('email', '')}>"
        if item.get("name")
        else str(item.get("email", ""))
    )


@dataclass
class ComposeData:
    to: str = ""
    cc: str = ""
    bcc: str = ""
    subject: str = ""
    body: str = ""
    attachment_paths: str = ""
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)


class ComposeScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("ctrl+enter", "send", "发送"),
    ]

    def __init__(self, data: ComposeData) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("撰写新邮件", id="compose-title"),
            Input(value=self.data.to, placeholder="收件人，逗号分隔", id="to"),
            Input(value=self.data.cc, placeholder="抄送（可选）", id="cc"),
            Input(value=self.data.bcc, placeholder="密送（可选）", id="bcc"),
            Input(value=self.data.subject, placeholder="主题", id="subject"),
            Input(
                value=self.data.attachment_paths,
                placeholder="附件路径，多个用分号分隔（可选）",
                id="attachments",
            ),
            TextArea(self.data.body, id="compose-body"),
            Horizontal(
                Button("发送", variant="primary", id="send"),
                Button("取消", id="cancel"),
                id="compose-actions",
            ),
            id="compose",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            self.action_send()
        elif event.button.id == "cancel":
            self.dismiss()

    def action_send(self) -> None:
        data = ComposeData(
            to=self.query_one("#to", Input).value,
            cc=self.query_one("#cc", Input).value,
            bcc=self.query_one("#bcc", Input).value,
            subject=self.query_one("#subject", Input).value,
            body=self.query_one("#compose-body", TextArea).text,
            attachment_paths=self.query_one("#attachments", Input).value,
            in_reply_to=self.data.in_reply_to,
            references=self.data.references,
        )
        if self.app.submit_compose(data):
            self.dismiss()

    def action_dismiss(self) -> None:
        self.dismiss()


def human_size(value: int | None) -> str:
    size = float(value or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def attachment_export_dir(message_id: int) -> Path:
    return files_dir() / "exports" / str(message_id)


def unique_save_path(
    directory: Path, filename: str, used: set[str]
) -> Path:
    name = Path(filename).name
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate = name
    counter = 2
    while candidate in used or (directory / candidate).exists():
        candidate = f"{stem} ({counter}){suffix}"
        counter += 1
    used.add(candidate)
    return directory / candidate


def widget_inside(widget: Any, container_id: str) -> bool:
    current = widget
    while current is not None:
        if getattr(current, "id", None) == container_id:
            return True
        current = getattr(current, "parent", None)
    return False


def sniff_image_mime(content: bytes | None) -> str | None:
    """Detect an image from its bytes, regardless of the declared MIME type."""

    if not content:
        return None
    try:
        with PILImage.open(io.BytesIO(content)) as image:
            image.verify()
            image_format = image.format
    except Exception:
        return None
    if not image_format:
        return None
    return PILImage.MIME.get(image_format, f"image/{image_format.lower()}")


class AttachmentScreen(ModalScreen[None]):
    """Single attachment preview and save confirmation."""

    BINDINGS = [Binding("escape", "dismiss", "关闭")]

    def __init__(self, attachment: dict[str, Any], message_id: int) -> None:
        super().__init__()
        self.attachment = attachment
        self.message_id = message_id

    def compose(self) -> ComposeResult:
        filename = Path(
            self.attachment.get("filename") or "attachment.bin"
        ).name
        content_type = str(
            self.attachment.get("content_type") or "未知类型"
        )
        size = human_size(self.attachment.get("size"))
        content = self.attachment.get("content")
        detected_type = (
            sniff_image_mime(content)
            if content and not content_type.lower().startswith("image/")
            else None
        )
        is_image = bool(
            content
            and (
                content_type.lower().startswith("image/")
                or detected_type is not None
            )
        )
        type_text = (
            f"{content_type}（检测为 {detected_type}）"
            if detected_type and detected_type != content_type
            else content_type
        )
        target = attachment_export_dir(self.message_id) / filename

        yield Vertical(
            Static("附件", id="attachment-title"),
            Static(
                f"[bold]{escape(filename)}[/]\n"
                f"[#94a3b8]{escape(type_text)}  ·  {size}[/]",
                id="attachment-meta",
            ),
            *(
                [
                    TerminalImage(
                        io.BytesIO(content),
                        id="attachment-image",
                        on_error=lambda exc: Static(
                            f"图片预览失败: {exc}",
                            id="attachment-image-error",
                        ),
                    ),
                    Static(
                        "终端图片协议可用，按 Enter 聚焦后可查看大图；保存仍使用下方目录。",
                        id="attachment-image-hint",
                    ),
                ]
                if is_image and IMAGE_PROTOCOL_SUPPORTED and TerminalImage is not None
                else [
                    Static(
                        "当前终端不支持 Kitty/Sixel 图片协议，图片将以普通附件方式处理。"
                        if is_image
                        else "该附件不是图片，无法内嵌预览。",
                        id="attachment-image-hint",
                    )
                ]
            ),
            Static("保存到", id="attachment-save-label"),
            Input(value=str(target.parent), id="attachment-dir"),
            Static(
                f"最终文件: {target}",
                id="attachment-path",
            ),
            Horizontal(
                Button("保存到此目录", variant="primary", id="attachment-save"),
                Button("关闭", id="attachment-close"),
                id="attachment-actions",
            ),
            id="attachment-box",
        )

    def action_save(self) -> None:
        content = self.attachment.get("content")
        if not content:
            self.app.notify("附件内容尚未下载", severity="warning")
            return
        raw_directory = self.query_one("#attachment-dir", Input).value.strip()
        if not raw_directory:
            self.app.notify("请输入保存目录", severity="warning")
            return
        directory = Path(raw_directory).expanduser()
        filename = Path(
            self.attachment.get("filename") or "attachment.bin"
        ).name
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / filename
            target.write_bytes(content)
        except OSError as exc:
            self.app.notify(str(exc), title="附件保存失败", severity="error")
            return
        self.app.notify(f"已保存到 {target}", title="附件已保存", timeout=8)
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "attachment-save":
            self.action_save()
        elif event.button.id == "attachment-close":
            self.dismiss()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "attachment-dir":
            return
        filename = Path(
            self.attachment.get("filename") or "attachment.bin"
        ).name
        directory = Path(event.value).expanduser()
        self.query_one("#attachment-path", Static).update(
            f"最终文件: {directory / filename}"
        )


class SaveAllAttachmentsScreen(ModalScreen[None]):
    """Explicit save-all confirmation with a visible destination."""

    BINDINGS = [Binding("escape", "dismiss", "关闭")]

    def __init__(
        self, attachments: list[dict[str, Any]], message_id: int
    ) -> None:
        super().__init__()
        self.attachments = attachments
        self.message_id = message_id

    def compose(self) -> ComposeResult:
        names = "\n".join(
            f"▣ {Path(item.get('filename') or 'attachment.bin').name}"
            for item in self.attachments
        )
        target = attachment_export_dir(self.message_id)
        yield Vertical(
            Static("保存全部附件", id="attachment-title"),
            Static(
                f"共 {len(self.attachments)} 个附件：\n{names}\n\n"
                f"目标目录: {target}",
                id="attachment-meta",
            ),
            Input(value=str(target), id="attachment-dir"),
            Horizontal(
                Button("确认保存全部", variant="primary", id="attachment-save-all"),
                Button("取消", id="attachment-cancel"),
                id="attachment-actions",
            ),
            id="attachment-box",
        )

    def action_save_all(self) -> None:
        raw_directory = self.query_one("#attachment-dir", Input).value.strip()
        if not raw_directory:
            self.app.notify("请输入保存目录", severity="warning")
            return
        directory = Path(raw_directory).expanduser()
        saved = 0
        used_names: set[str] = set()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for item in self.attachments:
                content = item.get("content")
                if not content:
                    continue
                filename = Path(
                    item.get("filename") or "attachment.bin"
                ).name
                unique_save_path(directory, filename, used_names).write_bytes(
                    content
                )
                saved += 1
        except OSError as exc:
            self.app.notify(str(exc), title="附件保存失败", severity="error")
            return
        self.app.notify(
            f"已保存 {saved} 个附件到 {directory}",
            title="附件已保存",
            timeout=8,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "attachment-save-all":
            self.action_save_all()
        elif event.button.id == "attachment-cancel":
            self.dismiss()


class ConfirmRemoveScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "cancel", "取消")]

    def __init__(self, key: str) -> None:
        super().__init__()
        self.key = key

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("删除账号", id="compose-title"),
            Static(
                f"确定删除账号 “{self.key}” 吗？本地邮件缓存会保留。",
                id="confirm-message",
            ),
            Horizontal(
                Button("删除", variant="error", id="confirm-remove"),
                Button("取消", id="confirm-cancel"),
                id="confirm-actions",
            ),
            id="confirm-box",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-remove":
            self.dismiss(True)
        elif event.button.id == "confirm-cancel":
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class AddAccountScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "取消"),
        Binding("ctrl+enter", "save", "保存"),
    ]

    def __init__(self, config_file: Path) -> None:
        super().__init__()
        self.config_file = config_file

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("添加账号", id="compose-title"),
            Static(
                "密码留空时，可使用 password_command 或环境变量。",
                id="form-hint",
            ),
            VerticalScroll(
                Vertical(
                    Input(placeholder="账号 key，例如 work", id="acct-key"),
                    Input(placeholder="邮箱地址", id="acct-email"),
                    Input(placeholder="显示名称（可选）", id="acct-name"),
                    Input(placeholder="登录用户名，默认同邮箱", id="acct-username"),
                    Input(placeholder="IMAP host", id="acct-imap-host"),
                    Input(value="993", placeholder="IMAP port", id="acct-imap-port"),
                    Input(value="ssl", placeholder="IMAP security: ssl / starttls", id="acct-imap-security"),
                    Input(placeholder="SMTP host", id="acct-smtp-host"),
                    Input(value="465", placeholder="SMTP port", id="acct-smtp-port"),
                    Input(value="ssl", placeholder="SMTP security: ssl / starttls", id="acct-smtp-security"),
                    Input(password=True, placeholder="密码/应用专用密码（可选）", id="acct-password"),
                    Input(placeholder="password_command（可选）", id="acct-password-command"),
                    Static("", id="form-error"),
                    id="account-form",
                ),
                id="account-form-scroll",
            ),
            Horizontal(
                Button("保存", variant="primary", id="acct-save"),
                Button("取消", id="acct-cancel"),
                id="compose-actions",
            ),
            id="compose",
        )

    def _value(self, widget_id: str) -> str:
        return self.query_one(f"#{widget_id}", Input).value.strip()

    def _error(self, message: str) -> None:
        self.query_one("#form-error", Static).update(f"[#f87171]{message}[/]")

    def action_save(self) -> None:
        key = self._value("acct-key")
        email = self._value("acct-email")
        username = self._value("acct-username") or email
        display_name = self._value("acct-name")
        imap_host = self._value("acct-imap-host")
        smtp_host = self._value("acct-smtp-host")
        try:
            imap_port = int(self._value("acct-imap-port"))
            smtp_port = int(self._value("acct-smtp-port"))
        except ValueError:
            self._error("端口必须是整数")
            return
        imap_security = self._value("acct-imap-security").lower() or "ssl"
        smtp_security = self._value("acct-smtp-security").lower() or "ssl"
        if imap_security not in ("ssl", "starttls") or smtp_security not in (
            "ssl",
            "starttls",
        ):
            self._error("security 只能是 ssl 或 starttls")
            return
        password = self._value("acct-password")
        password_command = self._value("acct-password-command") or None
        try:
            add_account(
                key=key,
                email=email,
                username=username,
                display_name=display_name,
                imap_host=imap_host,
                imap_port=imap_port,
                imap_security=imap_security,
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_security=smtp_security,
                password_command=password_command,
                path=self.config_file,
            )
        except ConfigError as exc:
            self._error(str(exc))
            return
        if password:
            if not store_password(key, password):
                self.app.notify(
                    "系统钥匙串不可用，密码未保存；请使用 password_command 或环境变量。",
                    severity="warning",
                )
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "acct-save":
            self.action_save()
        elif event.button.id == "acct-cancel":
            self.action_cancel()


class AccountsScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("n", "add", "添加"),
        Binding("d", "remove", "删除"),
    ]

    def __init__(self, config_file: Path, current_key: str) -> None:
        super().__init__()
        self.config_file = config_file
        self.current_key = current_key
        self.accounts: list[Account] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("账号管理", id="compose-title"),
            Static(
                "添加或删除账号后，重启 MailTUI 生效。",
                id="accounts-hint",
            ),
            ListView(id="account-list"),
            Horizontal(
                Button("添加账号", variant="primary", id="accounts-add"),
                Button("删除选中", variant="error", id="accounts-remove"),
                Button("关闭", id="accounts-close"),
                id="accounts-actions",
            ),
            Static("", id="accounts-status"),
            id="accounts",
        )

    async def on_mount(self) -> None:
        await self.reload()

    async def reload(self) -> None:
        try:
            self.accounts = load_config(self.config_file).accounts
        except ConfigError as exc:
            self.accounts = []
            self.query_one("#accounts-status", Static).update(
                f"[#f87171]{exc}[/]"
            )
            return
        view = self.query_one("#account-list", ListView)
        await view.clear()
        for account in self.accounts:
            marker = "●" if account.key == self.current_key else " "
            await view.append(
                ListItem(
                    Label(
                        f"{marker} [bold]{account.key:<10}[/] "
                        f"{account.email:<28} "
                        f"[dim]imap={account.imap.host}:{account.imap.port} "
                        f"smtp={account.smtp.host}:{account.smtp.port}[/]"
                    )
                )
            )
        if self.accounts:
            view.index = 0
        self.query_one("#accounts-status", Static).update(
            f"{len(self.accounts)} 个账号"
        )

    def _selected(self) -> Account | None:
        index = self.query_one("#account-list", ListView).index
        if index is None or index < 0 or index >= len(self.accounts):
            return None
        return self.accounts[index]

    def action_add(self) -> None:
        self.app.push_screen(
            AddAccountScreen(self.config_file),
            self._after_add,
        )

    def _after_add(self, result: bool | None) -> None:
        if result:
            self.app.notify("账号已添加，重启 MailTUI 后生效", title="账号管理")
            asyncio.create_task(self.reload())

    def action_remove(self) -> None:
        account = self._selected()
        if account is None:
            return
        self.app.push_screen(
            ConfirmRemoveScreen(account.key),
            lambda result: self._after_remove(account.key, result),
        )

    def _after_remove(self, key: str, result: bool | None) -> None:
        if not result:
            return
        try:
            remove_account(key, self.config_file)
        except ConfigError as exc:
            self.app.notify(str(exc), title="账号管理", severity="error")
            return
        delete_password(key)
        self.app.notify(f"已删除账号 {key}，重启 MailTUI 后生效", title="账号管理")
        asyncio.create_task(self.reload())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "accounts-add":
            self.action_add()
        elif event.button.id == "accounts-remove":
            self.action_remove()
        elif event.button.id == "accounts-close":
            self.dismiss()


class MailApp(App[None]):
    TITLE = "MailTUI"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("j", "cursor_down", "下移"),
        Binding("k", "cursor_up", "上移"),
        Binding("enter", "open_message", "打开/标记已读"),
        Binding("c", "compose", "撰写"),
        Binding("a", "accounts", "账号"),
        Binding("question_mark", "help", "帮助"),
        Binding("f1", "help", "帮助"),
        Binding("r", "reply", "回复"),
        Binding("R", "reply_all", "回复全部"),
        Binding("f", "forward", "转发"),
        Binding("s", "toggle_flag", "星标"),
        Binding("o", "save_attachments", "保存附件"),
        Binding("u", "mark_unread", "标记未读"),
        Binding("d", "delete", "移到回收站"),
        Binding("g", "refresh", "同步"),
        Binding("slash", "search", "搜索"),
        Binding("escape", "escape", "返回"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(
        self,
        account: Account,
        password: str,
        config: AppConfig,
        *,
        store: Store | None = None,
        start_workers: bool = True,
        config_file: Path | None = None,
    ) -> None:
        super().__init__()
        self.account = account
        self.password = password
        self.config = config
        self.store = store or Store()
        self.start_workers = start_workers
        self.config_file = config_file or default_config_path()

        self.folders: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.current_folder: dict[str, Any] | None = None
        self.current_row: dict[str, Any] | None = None
        self.current_body_text = ""
        self.preview_open = False
        self.search_query = ""

        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task[Any]] = []
        self._syncing: set[str] = set()
        self._fetching: set[int] = set()
        self._loading_folders = False
        self._refresh_pending = False
        self._pending_refresh_folders: set[str] = set()
        self._refresh_current_requested = False
        self._folder_nodes: dict[str, Any] = {}
        self._folder_signature: tuple[Any, ...] | None = None
        self._message_signature: tuple[Any, ...] | None = None

        self.sync = SyncService(
            self.store,
            account,
            password,
            config,
            on_event=self._on_sync_event,
        )
        self.worker = OperationWorker(
            self.store,
            account,
            password,
            self.sync.imap,
            on_event=self._on_worker_event,
        )

    # --------------------------------------------------------------- lifecycle

    def compose(self) -> ComposeResult:
        yield Static(
            f"  MailTUI  ·  {self.account.email}  ·  {self.account.key}",
            id="topbar",
        )
        with Horizontal(id="main"):
            with Vertical(id="folders"):
                yield Static("邮箱", classes="pane-title")
                yield Tree("邮箱", id="folder-tree")
            with Vertical(id="messages"):
                yield Static("收件箱", classes="pane-title", id="message-title")
                yield Input(placeholder="搜索主题、发件人、正文…", id="search")
                yield ListView(id="message-list")
            with Vertical(id="preview"):
                with Horizontal(id="preview-titlebar"):
                    yield Static("阅读", classes="pane-title")
                    yield Button("×", id="preview-close", classes="preview-close")
                with VerticalScroll(id="preview-scroll"):
                    yield Static(id="preview-meta")
                    yield ListView(id="attachment-list")
                    yield Static(Text(""), id="preview-body", markup=False)
        with Horizontal(id="bottombar"):
            yield Static(
                " j/k 选择  Enter 阅读  c 撰写  r 回复  ? 帮助",
                id="keybar",
            )
            yield Static(" 正在初始化…", id="status")

    async def on_mount(self) -> None:
        self.store.reset_running_operations()
        self.query_one("#search", Input).display = False
        await self._load_folders()

        if not self.start_workers:
            if self.folders:
                await self._select_folder(0)
            else:
                self._show_empty_state("没有本地邮件缓存")
            return

        self._tasks = [
            asyncio.create_task(self._startup()),
            asyncio.create_task(self.sync.poll_loop(self._stop)),
            asyncio.create_task(self.worker.run(self._stop)),
        ]

    async def on_unmount(self) -> None:
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.sync.close()
        self.store.close()

    async def _startup(self) -> None:
        try:
            await self.sync.list_and_store_folders()
            await self._load_folders()
            if self.folders:
                await self._select_folder(0)
            await self.sync.sync_startup()
            await self._load_folders()
            if self.current_folder and not self.search_query:
                await self._refresh_messages()
            self._set_status("同步完成")
        except Exception as exc:
            logger.exception("startup failed")
            self.notify(str(exc), title="同步失败", severity="error")
            self._set_status(f"同步失败: {exc}")

    # ------------------------------------------------------------------ events

    def _on_sync_event(self, name: str, payload: dict[str, Any]) -> None:
        if name == "sync":
            folder = payload.get("folder", "")
            state = payload.get("state")
            if state == "running":
                self._set_status(f"正在同步 {folder}…")
            elif state == "done":
                self._set_status(f"{folder} 同步完成")
        elif name == "folder" and payload.get("state") == "done":
            self._schedule_refresh(payload.get("folder"))
        elif name == "error":
            self._set_status(f"同步错误: {payload.get('error')}")
            self.notify(str(payload.get("error")), title="同步错误", severity="error")

    def _on_worker_event(self, name: str, payload: dict[str, Any]) -> None:
        if name == "operation" and payload.get("state") == "done":
            if payload.get("warning"):
                self.notify(str(payload["warning"]), title="发送提醒", severity="warning")
            elif payload.get("kind") == "send":
                self.notify("邮件已发送", title="发送成功")
                sent = self.store.folder_by_role(self.account.key, "sent")
                if sent and self.start_workers:
                    self._start_folder_sync(sent["name"])
            if payload.get("kind") in ("send", "move", "delete"):
                self._schedule_refresh()
        elif name == "operation" and payload.get("state") == "failed":
            self.notify(str(payload.get("error")), title="操作失败", severity="error")
        elif name == "operation" and payload.get("state") == "unknown":
            self.notify(
                str(payload.get("error") or "发送结果不确定，可能已送达"),
                title="发送结果不确定",
                severity="warning",
                timeout=10,
            )

    def _schedule_refresh(self, folder: str | None = None) -> None:
        if folder:
            self._pending_refresh_folders.add(str(folder))
        else:
            self._refresh_current_requested = True
        if self._refresh_pending:
            return
        self._refresh_pending = True
        asyncio.create_task(self._refresh_after_sync())

    async def _refresh_after_sync(self) -> None:
        try:
            folders = set(self._pending_refresh_folders)
            self._pending_refresh_folders.clear()
            current_requested = self._refresh_current_requested
            self._refresh_current_requested = False
            await self._load_folders()
            if (
                self.current_folder
                and not self.search_query
                and (
                    current_requested
                    or not folders
                    or self.current_folder["name"] in folders
                )
            ):
                await self._refresh_messages()
        finally:
            self._refresh_pending = False
            if self._pending_refresh_folders or self._refresh_current_requested:
                self._schedule_refresh()

    # ----------------------------------------------------------------- folders

    async def _load_folders(self) -> None:
        if self._loading_folders:
            return
        self._loading_folders = True
        try:
            self.folders = self.store.list_folders(self.account.key)
            folder_signature = tuple(
                (
                    folder["id"],
                    folder["name"],
                    folder.get("role"),
                    folder.get("total", 0),
                    folder.get("unread", 0),
                    folder.get("uidvalidity"),
                    folder.get("delimiter"),
                )
                for folder in self.folders
            )
            if folder_signature == self._folder_signature:
                current_name = (
                    self.current_folder["name"] if self.current_folder else None
                )
                if current_name:
                    current = next(
                        (
                            folder
                            for folder in self.folders
                            if folder["name"] == current_name
                        ),
                        None,
                    )
                    if current:
                        self.current_folder = current
                return
            self._folder_signature = folder_signature
            if not self.folders:
                self.current_folder = None
                self.messages = []
                self._folder_nodes = {}
                self._message_signature = None
                build_folder_tree(
                    self.query_one("#folder-tree", Tree),
                    [],
                    root_label=self.account.email,
                )
                await self._render_messages()
                return
            current_name = self.current_folder["name"] if self.current_folder else None
            self._folder_nodes = build_folder_tree(
                self.query_one("#folder-tree", Tree),
                self.folders,
                root_label=self.account.email,
                current_name=current_name,
            )
            if current_name and current_name in self._folder_nodes:
                self.current_folder = next(
                    folder
                    for folder in self.folders
                    if folder["name"] == current_name
                )
                tree = self.query_one("#folder-tree", Tree)
                node = self._folder_nodes[current_name]
                tree.call_after_refresh(lambda: tree.select_node(node))
            elif self._folder_nodes and current_name:
                first_name = next(iter(self._folder_nodes))
                self.current_folder = next(
                    folder
                    for folder in self.folders
                    if folder["name"] == first_name
                )
                self._message_signature = None
                first = self._folder_nodes[first_name]
                tree = self.query_one("#folder-tree", Tree)
                tree.call_after_refresh(lambda: tree.select_node(first))
                await self._refresh_messages()
        finally:
            self._loading_folders = False

    async def _select_folder(self, index: int) -> None:
        if index < 0 or index >= len(self.folders):
            return
        await self._select_folder_by_name(self.folders[index]["name"])

    async def _select_folder_by_name(self, name: str) -> None:
        folder = next((item for item in self.folders if item["name"] == name), None)
        if folder is None:
            return
        if self.current_folder and self.current_folder["id"] == folder["id"]:
            return
        self.current_folder = folder
        self.search_query = ""
        search = self.query_one("#search", Input)
        search.value = ""
        search.display = False
        self.query_one("#message-title", Static).update(
            f"{escape(folder['name'])}  ·  {folder.get('total', 0)} 封"
        )
        self._set_status(f"已切换到 {folder['name']}")
        await self._refresh_messages(focus=True)
        if self.start_workers:
            self._start_folder_sync(folder["name"])

    def _start_folder_sync(self, folder_name: str) -> None:
        if folder_name in self._syncing:
            return
        self._syncing.add(folder_name)

        async def run() -> None:
            try:
                await self.sync.sync_folder(folder_name)
            except Exception as exc:
                logger.exception("folder sync failed: %s", folder_name)
                self.notify(str(exc), title=f"{folder_name} 同步失败", severity="error")
            finally:
                self._syncing.discard(folder_name)

        self._tasks.append(asyncio.create_task(run()))

    # ---------------------------------------------------------------- messages

    async def _refresh_messages(self, *, focus: bool = False) -> None:
        selected_id = int(self.current_row["id"]) if self.current_row else None
        old_index = self.query_one("#message-list", ListView).index or 0
        if self.current_folder is None:
            new_messages: list[dict[str, Any]] = []
            message_signature: tuple[Any, ...] = (None,)
        else:
            new_messages = self.store.list_messages(
                int(self.current_folder["id"]), limit=self.config.page_size
            )
            message_signature = (
                int(self.current_folder["id"]),
                tuple(
                    (
                        row["id"],
                        row["is_seen"],
                        row["is_flagged"],
                        row["subject"],
                        row["from_json"],
                        row["date_utc"],
                        row["has_attachments"],
                    )
                    for row in new_messages
                ),
            )
        if message_signature == self._message_signature:
            if focus and not self.search_query:
                self.query_one("#message-list", ListView).focus()
            return
        self.messages = new_messages
        self._message_signature = message_signature
        await self._render_messages()
        if self.messages:
            new_index = next(
                (
                    index
                    for index, row in enumerate(self.messages)
                    if selected_id is not None and int(row["id"]) == selected_id
                ),
                min(old_index, len(self.messages) - 1),
            )
            view = self.query_one("#message-list", ListView)
            view.index = new_index
            if self.preview_open:
                self._show_message(new_index)
            else:
                full = (
                    self.store.get_message(int(self.messages[new_index]["id"]))
                    or self.messages[new_index]
                )
                self.current_row = full
                self.current_body_text = (
                    full.get("text_body") or full.get("html_text") or ""
                )
            if focus and not self.search_query:
                view.focus()
        else:
            self._show_empty_state("这个文件夹没有邮件")

    async def _render_messages(self, *, select_first: bool = False) -> None:
        view = self.query_one("#message-list", ListView)
        await view.clear()
        for row in self.messages:
            await view.append(ListItem(Label(self._row_markup(row))))
        if select_first and self.messages:
            view.index = 0

    def _row_markup(self, row: dict[str, Any]) -> str:
        dot = "[#22d3ee]●[/]" if not row.get("is_seen") else " "
        flag = "[#f59e0b]★[/]" if row.get("is_flagged") else " "
        clip = "[#94a3b8]▣[/]" if row.get("has_attachments") else " "
        sender = escape((_sender(row) or "未知发件人")[:18])
        subject = escape(row.get("subject") or "(无主题)")
        folder = ""
        if self.search_query and row.get("folder_name"):
            folder = f" [dim #64748b]{escape(row['folder_name'])}[/]"
        return (
            f"{dot} {flag}{clip} [bold]{sender:<18}[/] "
            f"{subject}{folder}  [dim]{date_text(row.get('date_utc'))}[/]"
        )

    def _show_empty_state(self, message: str) -> None:
        self.current_row = None
        self.current_body_text = ""
        self.query_one("#preview-meta", Static).update(
            "[bold #7dd3fc]MailTUI[/]\n[#94a3b8]选择一封邮件开始阅读[/]"
        )
        self.query_one("#preview-body", Static).update(Text(message))

    def _show_preview(self) -> None:
        if self.preview_open:
            return
        self.preview_open = True
        self.query_one("#preview").display = True

    def _hide_preview(self) -> None:
        if not self.preview_open:
            return
        self.preview_open = False
        self.query_one("#preview").display = False
        self.query_one("#message-list", ListView).focus()

    def _show_message(self, index: int) -> None:
        if not self.preview_open:
            return
        if index < 0 or index >= len(self.messages):
            return
        row = self.messages[index]
        full = self.store.get_message(int(row["id"])) or row
        self.current_row = full
        self.current_body_text = (
            full.get("text_body") or full.get("html_text") or ""
        )
        status = "已读" if full.get("is_seen") else "未读"
        attachments: list[dict[str, Any]] = []
        try:
            attachments = self.store.attachments(int(full["id"]))
        except Exception:
            pass
        attachment = f"  ·  {len(attachments)} 个附件" if attachments else ""
        self.query_one("#preview-meta", Static).update(
            f"[bold #7dd3fc]{escape(full.get('subject') or '(无主题)')}[/]\n"
            f"[#94a3b8]From:[/] {escape(_sender(full))}\n"
            f"[#94a3b8]To:[/] {escape(self._to_text(full))}\n"
            f"[#94a3b8]Date:[/] {date_text(full.get('date_utc'))}\n"
            f"[#94a3b8]状态:[/] {status}{attachment}"
        )
        self.query_one("#preview-body", Static).update(
            Text(self.current_body_text or "正在加载正文…")
        )
        asyncio.create_task(
            self._render_attachments(int(full["id"]), attachments)
        )
        if full.get("body_state") != "ready":
            asyncio.create_task(self._fetch_body(full))

    async def _render_attachments(
        self, message_id: int, attachments: list[dict[str, Any]]
    ) -> None:
        view = self.query_one("#attachment-list", ListView)
        await view.clear()
        if not self.current_row or int(self.current_row["id"]) != message_id:
            return
        for item in attachments:
            filename = Path(item.get("filename") or "未命名附件").name
            label = (
                f"▣ {filename}  ({human_size(item.get('size'))})"
                "  ·  Enter 查看/保存"
            )
            await view.append(ListItem(Label(label)))
        if attachments:
            view.index = 0

    def _to_text(self, row: dict[str, Any]) -> str:
        try:
            values = json.loads(row.get("to_json") or "[]")
        except json.JSONDecodeError:
            return ""
        return ", ".join(
            item.get("email", "") for item in values if item.get("email")
        )

    async def _fetch_body(self, row: dict[str, Any]) -> None:
        message_id = int(row["id"])
        if message_id in self._fetching:
            return
        self._fetching.add(message_id)
        self.store.set_body_state(message_id, "fetching")
        try:
            result = await self.sync.imap.fetch_body(
                row["folder_name"], int(row["uid"])
            )
            self.store.save_body(
                message_id,
                text_body=result["text"],
                html_text=result["html_text"],
                raw_eml=result["raw"],
                attachments=result["attachments"],
            )
            flags = result.get("flags", [])
            if not self.store.has_pending_flag_operation(message_id):
                self.store.update_flags(
                    message_id,
                    flags,
                    seen="\\Seen" in flags,
                    flagged="\\Flagged" in flags,
                )
            if (
                self.preview_open
                and self.current_row
                and int(self.current_row["id"]) == message_id
            ):
                for index, item in enumerate(self.messages):
                    if int(item["id"]) == message_id:
                        self._show_message(index)
                        break
        except Exception as exc:
            logger.exception("body fetch failed: %s", message_id)
            self.store.set_body_state(message_id, "error")
            self.notify(str(exc), title="正文加载失败", severity="error")
            if (
                self.preview_open
                and self.current_row
                and int(self.current_row["id"]) == message_id
            ):
                self.query_one("#preview-body", Static).update(
                    Text("正文加载失败，可稍后重试或查看原文。")
                )
        finally:
            self._fetching.discard(message_id)

    # ---------------------------------------------------------------- actions

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Tree):
            self.focused.action_cursor_down()
        else:
            self.query_one("#message-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Tree):
            self.focused.action_cursor_up()
        else:
            self.query_one("#message-list", ListView).action_cursor_up()

    def action_open_message(self) -> None:
        self._show_preview()
        self._mark_seen()
        index = self.query_one("#message-list", ListView).index
        if index is not None:
            self._show_message(index)

    def _mark_seen(self) -> None:
        row = self.current_row
        if not row or row.get("is_seen"):
            return
        try:
            flags = json.loads(row.get("flags_json") or "[]")
        except json.JSONDecodeError:
            flags = []
        if "\\Seen" not in flags:
            flags.append("\\Seen")
        self.store.update_flags(
            int(row["id"]),
            flags,
            seen=True,
            flagged=bool(row.get("is_flagged")),
        )
        self.store.enqueue_operation(
            self.account.key,
            "flag",
            message_id=int(row["id"]),
            payload={
                "folder": row["folder_name"],
                "uid": int(row["uid"]),
                "flag": "\\Seen",
                "value": True,
            },
        )
        row["is_seen"] = True
        row["flags_json"] = json.dumps(flags)
        index = self.query_one("#message-list", ListView).index or 0
        self._sync_list_row(index)
        if self.preview_open:
            self._show_message(index)

    def action_toggle_flag(self) -> None:
        row = self.current_row
        if not row:
            return
        flagged = not bool(row.get("is_flagged"))
        try:
            flags = json.loads(row.get("flags_json") or "[]")
        except json.JSONDecodeError:
            flags = []
        if flagged and "\\Flagged" not in flags:
            flags.append("\\Flagged")
        if not flagged:
            flags = [flag for flag in flags if flag != "\\Flagged"]
        self.store.update_flags(
            int(row["id"]),
            flags,
            seen=bool(row.get("is_seen")),
            flagged=flagged,
        )
        self.store.enqueue_operation(
            self.account.key,
            "flag",
            message_id=int(row["id"]),
            payload={
                "folder": row["folder_name"],
                "uid": int(row["uid"]),
                "flag": "\\Flagged",
                "value": flagged,
            },
        )
        row["is_flagged"] = flagged
        row["flags_json"] = json.dumps(flags)
        index = self.query_one("#message-list", ListView).index or 0
        self._sync_list_row(index)
        if self.preview_open:
            self._show_message(index)

    def action_mark_unread(self) -> None:
        row = self.current_row
        if not row or not row.get("is_seen"):
            return
        try:
            flags = [
                flag
                for flag in json.loads(row.get("flags_json") or "[]")
                if flag != "\\Seen"
            ]
        except json.JSONDecodeError:
            flags = []
        self.store.update_flags(
            int(row["id"]),
            flags,
            seen=False,
            flagged=bool(row.get("is_flagged")),
        )
        self.store.enqueue_operation(
            self.account.key,
            "flag",
            message_id=int(row["id"]),
            payload={
                "folder": row["folder_name"],
                "uid": int(row["uid"]),
                "flag": "\\Seen",
                "value": False,
            },
        )
        row["is_seen"] = False
        row["flags_json"] = json.dumps(flags)
        index = self.query_one("#message-list", ListView).index or 0
        self._sync_list_row(index)
        if self.preview_open:
            self._show_message(index)

    def _sync_list_row(self, index: int) -> None:
        if index < 0 or index >= len(self.messages):
            return
        row = self.messages[index]
        if self.current_row and int(row["id"]) == int(self.current_row["id"]):
            row["is_seen"] = self.current_row.get("is_seen", 0)
            row["is_flagged"] = self.current_row.get("is_flagged", 0)
            row["flags_json"] = self.current_row.get("flags_json", "[]")
        view = self.query_one("#message-list", ListView)
        children = list(view.children)
        if index < len(children):
            child = children[index]
            if isinstance(child, ListItem):
                child.query_one(Label).update(self._row_markup(row))

    def action_save_attachments(self) -> None:
        row = self.current_row
        if not row:
            return
        attachments = self.store.attachments(int(row["id"]))
        if not attachments:
            self.notify("这封邮件没有附件", severity="warning")
            return
        self.push_screen(
            SaveAllAttachmentsScreen(attachments, int(row["id"]))
        )

    def action_delete(self) -> None:
        row = self.current_row
        if not row:
            return
        trash = self.store.folder_by_role(self.account.key, "trash")
        if trash is None:
            self.notify("没有找到回收站文件夹，未执行删除", severity="warning")
            return
        if int(trash["id"]) == int(row["folder_id"]):
            self.notify("邮件已经在回收站", severity="warning")
            return
        self.store.enqueue_operation(
            self.account.key,
            "move",
            message_id=int(row["id"]),
            payload={
                "folder": row["folder_name"],
                "uid": int(row["uid"]),
                "destination": trash["name"],
            },
        )
        self.store.mark_deleted(int(row["id"]))
        if self.query_one("#message-list", ListView).index is not None:
            self.messages.pop(self.query_one("#message-list", ListView).index or 0)
        self._message_signature = None
        asyncio.create_task(self._render_messages(select_first=True))
        self._show_empty_state("已移到回收站")
        self.notify("已移到回收站", title="删除")
        self._schedule_refresh()

    def action_compose(self) -> None:
        self.push_screen(ComposeScreen(ComposeData()))

    def action_accounts(self) -> None:
        self.push_screen(AccountsScreen(self.config_file, self.account.key))

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_reply(self) -> None:
        self._open_reply(reply_all=False, forward=False)

    def action_reply_all(self) -> None:
        self._open_reply(reply_all=True, forward=False)

    def action_forward(self) -> None:
        self._open_reply(reply_all=False, forward=True)

    def _open_reply(self, *, reply_all: bool, forward: bool) -> None:
        row = self.current_row
        if not row:
            return
        if forward:
            data = ComposeData(
                subject=forward_subject(str(row.get("subject") or "")),
                body=(
                    "---------- Forwarded message ----------\n"
                    f"From: {_sender(row)}\n"
                    f"Date: {date_text(row.get('date_utc'))}\n"
                    f"Subject: {row.get('subject')}\n\n"
                    f"{self.current_body_text}\n"
                ),
            )
        else:
            to, cc = reply_addresses(row, self.account)
            data = ComposeData(
                to=", ".join(to if reply_all else to[:1]),
                cc=", ".join(cc) if reply_all else "",
                subject=reply_subject(str(row.get("subject") or "")),
                body=quote_reply(
                    _sender(row),
                    date_text(row.get("date_utc")),
                    self.current_body_text,
                ),
                in_reply_to=row.get("message_id"),
                references=json.loads(row.get("references_json") or "[]"),
            )
        self.push_screen(ComposeScreen(data))

    def submit_compose(self, data: ComposeData) -> bool:
        to = _address_list(data.to)
        cc = _address_list(data.cc)
        bcc = _address_list(data.bcc)
        recipients = envelope_recipients(to, cc, bcc)
        if not recipients:
            self.notify("至少需要一个收件人", severity="warning")
            return False
        attachments: list[tuple[str, str, bytes]] = []
        for raw_path in [item.strip() for item in data.attachment_paths.split(";") if item.strip()]:
            path = Path(raw_path).expanduser()
            if not path.is_file():
                self.notify(f"附件不存在: {path}", severity="error")
                return False
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            attachments.append((path.name, content_type, path.read_bytes()))
        message = build_message(
            self.account,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=data.subject,
            body=data.body,
            attachments=attachments,
            in_reply_to=data.in_reply_to,
            references=data.references,
        )
        self.store.enqueue_operation(
            self.account.key,
            "send",
            payload={"recipients": recipients},
            mime_blob=raw_bytes(message),
        )
        self.notify("已加入发送队列", title="发送")
        return True

    def action_refresh(self) -> None:
        if self.current_folder:
            self._start_folder_sync(self.current_folder["name"])

    def action_search(self) -> None:
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_escape(self) -> None:
        search = self.query_one("#search", Input)
        if search.display:
            search.value = ""
            search.display = False
            self.search_query = ""
            self._show_empty_state("选择一封邮件开始阅读")
            if self.current_folder:
                asyncio.create_task(self._refresh_messages(focus=True))
            return
        if self.preview_open:
            self._hide_preview()

    def action_quit(self) -> None:
        self.exit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "search":
            return
        query = event.value.strip()
        if not query:
            self.search_query = ""
            if self.current_folder:
                asyncio.create_task(self._refresh_messages(focus=True))
            return
        self.search_query = query
        self._message_signature = None
        self.messages = self.store.search(
            query, account_key=self.account.key, limit=self.config.page_size
        )
        asyncio.create_task(self._render_messages())
        if not self.messages:
            self._show_empty_state("没有搜索结果")
        else:
            self.query_one("#message-list", ListView).focus()
        self.query_one("#message-title", Static).update(
            f"搜索 “{query}”  ·  {len(self.messages)} 条结果"
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.list_view.index is None:
            return
        if event.list_view.id == "message-list":
            if self.preview_open:
                self._show_message(event.list_view.index)
                return
            row = self.messages[event.list_view.index]
            full = (
                self.store.get_message(int(row["id"])) or row
            )
            self.current_row = full
            self.current_body_text = (
                full.get("text_body") or full.get("html_text") or ""
            )

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if isinstance(data, dict) and data.get("name"):
            asyncio.create_task(self._select_folder_by_name(str(data["name"])))
        elif event.node.children:
            if event.node.is_expanded:
                event.node.collapse()
            else:
                event.node.expand()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "message-list":
            self.action_open_message()
        elif event.list_view.id == "attachment-list":
            self._open_attachment(event.list_view.index or 0)

    def _open_attachment(self, index: int) -> None:
        row = self.current_row
        if not row:
            return
        attachments = self.store.attachments(int(row["id"]))
        if index < 0 or index >= len(attachments):
            return
        self.push_screen(
            AttachmentScreen(attachments[index], int(row["id"]))
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preview-close":
            self._hide_preview()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if not self.preview_open or event.widget is None:
            return
        if widget_inside(event.widget, "preview") or widget_inside(
            event.widget, "message-list"
        ):
            return
        self._hide_preview()

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(Text(f" {text}"))
