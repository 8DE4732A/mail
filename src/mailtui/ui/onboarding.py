"""First-run onboarding screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from mailtui.config import load_config
from mailtui.ui.app import AddAccountScreen


class OnboardingApp(App[bool]):
    TITLE = "MailTUI · 首次设置"
    CSS_PATH = "app.tcss"

    BINDINGS = [
        Binding("n", "add_account", "添加账号"),
        Binding("escape", "quit", "退出"),
        Binding("q", "quit", "退出"),
    ]

    def __init__(self, config_file: Path) -> None:
        super().__init__()
        self.config_file = config_file
        self.added = False

    def compose(self) -> ComposeResult:
        yield Static("  MailTUI  ·  首次设置", id="topbar")
        with Vertical(id="onboarding-wrap"):
            yield Vertical(
                Static("欢迎使用 MailTUI", id="onboarding-title"),
                Static(
                    "还没有配置邮箱账号。\n\n"
                    "1. 准备邮箱的 IMAP / SMTP 地址和应用专用密码\n"
                    "2. 点击“添加账号”\n"
                    "3. 保存后会自动进入收件箱\n\n"
                    "密码会优先保存到系统钥匙串；如果不可用，"
                    "可以配置 password_command 或环境变量。",
                    id="onboarding-body",
                ),
                Horizontal(
                    Button("添加账号", variant="primary", id="onboarding-add"),
                    Button("退出", id="onboarding-quit"),
                    id="onboarding-actions",
                ),
                id="onboarding",
            )
        yield Static(" n 添加账号   q 退出   ·   MailTUI", id="status")

    def on_mount(self) -> None:
        self.query_one("#onboarding-add", Button).focus()

    def action_add_account(self) -> None:
        self.push_screen(
            AddAccountScreen(self.config_file),
            self._after_add,
        )

    def _after_add(self, result: bool | None) -> None:
        if not result:
            return
        if load_config(self.config_file).accounts:
            self.added = True
            self.exit(True)

    def action_quit(self) -> None:
        self.exit(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "onboarding-add":
            self.action_add_account()
        elif event.button.id == "onboarding-quit":
            self.action_quit()
