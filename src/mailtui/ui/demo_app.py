"""Textual application shell."""

from __future__ import annotations

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

from mailtui.demo import FOLDERS, MESSAGES, Folder, Message
from mailtui.domain.html_text import html_to_text
from mailtui.ui.folder_tree import build_folder_tree
from mailtui.ui.help import HelpScreen

DEFAULT_FOLDERS = [Folder(name) for name in ["收件箱", "已发送", "草稿", "回收站"]]


def message_row(message: Message, read: bool) -> str:
    dot = "[#22d3ee]●[/]" if message.unread and not read else " "
    flag = "[#f59e0b]★[/]" if message.flagged else " "
    clip = "[#94a3b8]▣[/]" if message.attachment else " "
    return (
        f"{dot} {flag}{clip} [bold]{message.sender:<12}[/] "
        f"{message.subject}  [dim]{message.time}[/]"
    )


class ComposeScreen(ModalScreen[None]):
    """Minimal composer shell."""

    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("ctrl+enter", "send", "发送"),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("撰写新邮件", id="compose-title"),
            Input(placeholder="收件人", id="to"),
            Input(placeholder="主题", id="subject"),
            TextArea(id="compose-body"),
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
        self.dismiss()
        self.app.notify("已加入发送队列（界面骨架，尚未接入 SMTP）", title="发送")

    def action_dismiss(self) -> None:
        self.dismiss()


class DemoApp(App[None]):
    """The mail client shell."""

    TITLE = "MailTUI"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("j", "cursor_down", "下移"),
        Binding("k", "cursor_up", "上移"),
        Binding("enter", "open_message", "打开"),
        Binding("c", "compose", "撰写"),
        Binding("escape", "escape", "返回"),
        Binding("question_mark", "help", "帮助"),
        Binding("f1", "help", "帮助"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(self, *, demo: bool = False) -> None:
        super().__init__()
        self.demo = demo
        self.folders = FOLDERS if demo else DEFAULT_FOLDERS
        self.current_folder: Folder | None = None
        self.read: set[int] = set()
        self.current_body_text = ""
        self.preview_open = False
        self._folder_nodes: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield Static(
            "  MailTUI  ·  work@example.com  ·  12 unread"
            if self.demo
            else "  MailTUI  ·  尚未配置账号  ·  使用 --demo 查看界面",
            id="topbar",
        )
        with Horizontal(id="main"):
            with Vertical(id="folders"):
                yield Static("邮箱", classes="pane-title")
                yield Tree("邮箱", id="folder-tree")
            with Vertical(id="messages"):
                yield Static(
                    "收件箱  ·  最近邮件",
                    classes="pane-title",
                    id="message-title",
                )
                items = (
                    [
                        ListItem(Label(message_row(message, read=False)))
                        for message in MESSAGES
                    ]
                    if self.demo
                    else []
                )
                yield ListView(*items, initial_index=0 if items else None, id="message-list")
            with Vertical(id="preview"):
                with Horizontal(id="preview-titlebar"):
                    yield Static("阅读", classes="pane-title")
                    yield Button("×", id="preview-close", classes="preview-close")
                with VerticalScroll(id="preview-scroll"):
                    yield Static(id="preview-meta")
                    yield Static(Text(""), id="preview-body", markup=False)
        with Horizontal(id="bottombar"):
            yield Static(
                " j/k 选择  Enter 阅读  c 撰写  r 回复  ? 帮助",
                id="keybar",
            )
            yield Static(
                " Textual · demo"
                if self.demo
                else " 尚未配置账号 · 使用 `mail --demo`",
                id="status",
            )

    async def on_mount(self) -> None:
        await self._load_folders()
        if self.demo:
            self.query_one("#message-list", ListView).focus()
        else:
            self._show_empty_state()
            self.query_one("#folder-tree", Tree).focus()

    async def _load_folders(self) -> None:
        self._folder_nodes = build_folder_tree(
            self.query_one("#folder-tree", Tree),
            self.folders,
            root_label="work@example.com" if self.demo else "MailTUI",
            expand_depth=1 if self.demo else 0,
        )
        if self._folder_nodes:
            tree = self.query_one("#folder-tree", Tree)
            node = next(iter(self._folder_nodes.values()))
            tree.call_after_refresh(lambda: tree.select_node(node))

    def _select_folder(self, folder: Folder) -> None:
        self.current_folder = folder
        unread = f"  ·  {folder.unread} 未读" if folder.unread else ""
        self.query_one("#message-title", Static).update(
            f"{folder.label}{unread}"
        )
        self.query_one("#status", Static).update(
            f" 当前文件夹: {folder.path or folder.label}   ·   j/k 消息   Tab 树"
        )

    def _show_empty_state(self) -> None:
        self.current_body_text = ""
        self.query_one("#preview-meta", Static).update(
            "[bold #7dd3fc]欢迎使用 MailTUI[/]\n"
            "[#94a3b8]还没有配置邮箱账号。[/]"
        )
        self.query_one("#preview-body", Static).update(
            Text(
                "运行 `mail --demo` 可以先查看界面。\n\n"
                "后续接入账号配置后，这里会显示同步下来的邮件。"
            )
        )

    def _show_message(self, index: int) -> None:
        message = MESSAGES[index]
        body = message.body or html_to_text(message.body_html or "")
        self.current_body_text = body
        status = "已读" if index in self.read else "未读"
        attachment = "  ·  1 个附件" if message.attachment else ""
        email = message.meta.get("email", "")
        to = message.meta.get("to", "")
        self.query_one("#preview-meta", Static).update(
            f"[bold #7dd3fc]{message.subject}[/]\n"
            f"[#94a3b8]From:[/] {message.sender} <{email}>\n"
            f"[#94a3b8]To:[/] {to}\n"
            f"[#94a3b8]状态:[/] {status}{attachment}"
        )
        self.query_one("#preview-body", Static).update(Text(body))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if (
            self.demo
            and self.preview_open
            and event.list_view.id == "message-list"
            and event.list_view.index is not None
        ):
            self._show_message(event.list_view.index)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not self.demo or event.list_view.id != "message-list":
            return
        self._open_message()

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

    def _open_message(self) -> None:
        index = self.query_one("#message-list", ListView).index
        if index is None:
            return
        self._show_preview()
        self.read.add(index)
        item = self.query_one("#message-list", ListView).children[index]
        if isinstance(item, ListItem):
            item.query_one(Label).update(message_row(MESSAGES[index], read=True))
        self._show_message(index)

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if isinstance(data, Folder):
            self._select_folder(data)
        elif event.node.children:
            if event.node.is_expanded:
                event.node.collapse()
            else:
                event.node.expand()

    def action_cursor_down(self) -> None:
        if isinstance(self.focused, Tree):
            self.focused.action_cursor_down()
        elif self.demo:
            self.query_one("#message-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        if isinstance(self.focused, Tree):
            self.focused.action_cursor_up()
        elif self.demo:
            self.query_one("#message-list", ListView).action_cursor_up()

    def action_open_message(self) -> None:
        if self.demo:
            self._open_message()

    def action_compose(self) -> None:
        self.push_screen(ComposeScreen())

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_escape(self) -> None:
        if self.preview_open:
            self._hide_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "preview-close":
            self._hide_preview()

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if not self.preview_open or event.widget is None:
            return
        current = event.widget
        while current is not None:
            if getattr(current, "id", None) in ("preview", "message-list"):
                return
            current = getattr(current, "parent", None)
        self._hide_preview()


if __name__ == "__main__":
    DemoApp(demo=True).run()
