"""Keyboard shortcut help screen."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static

HELP_TEXT = """# 快捷键帮助

## 通用

| 按键 | 动作 |
| --- | --- |
| `?` / `F1` | 打开/关闭帮助 |
| `q` | 返回/退出 |
| `Esc` | 关闭阅读区/弹窗/退出搜索 |

## 邮件列表

| 按键 | 动作 |
| --- | --- |
| `j` / `k` | 上下移动 |
| `Enter` | 打开邮件并标记已读 |
| `c` | 撰写新邮件 |
| `r` / `R` / `f` | 回复 / 回复全部 / 转发 |
| `s` | 星标/取消星标 |
| `u` | 标记未读 |
| `d` | 移到回收站 |
| `o` | 打开附件保存面板 |
| `g` | 同步当前文件夹 |
| `/` | 搜索 |

## 文件夹树

| 按键 | 动作 |
| --- | --- |
| `Tab` | 在文件夹树和邮件列表间切换 |
| `j` / `k` | 在同一层级上下移动 |
| `←` / `→` | 收起/展开节点 |
| `Enter` | 展开/收起，或打开文件夹 |

## 阅读区

| 操作 | 动作 |
| --- | --- |
| 点击右上角 `×` | 关闭阅读区 |
| 点击阅读区/邮件列表以外的区域 | 自动关闭阅读区 |
| `Esc` | 关闭阅读区 |

## 撰写邮件

| 按键 | 动作 |
| --- | --- |
| `c` | 新建邮件 |
| `r` | 回复 |
| `R` | 回复全部 |
| `f` | 转发 |
| `Ctrl+Enter` | 发送 |
| `Esc` | 取消撰写 |

附件在“附件路径”字段填写，多个路径用 `;` 分隔。

## 账号与维护

| 按键/命令 | 动作 |
| --- | --- |
| `a` | 打开账号管理 |
| `n` | 添加账号 |
| `d` | 删除选中账号 |
| `mail doctor` | 检查配置、keyring 和文件夹 |
| `mail db rebuild-fts` | 重建搜索索引 |
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "关闭"),
        Binding("q", "dismiss", "关闭"),
        Binding("question_mark", "dismiss", "关闭"),
        Binding("f1", "dismiss", "关闭"),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("快捷键帮助", id="help-title"),
            VerticalScroll(
                Markdown(HELP_TEXT, id="help-markdown"),
                id="help-scroll",
            ),
            Static(" Esc / q 关闭   ·   可滚动查看", id="help-footer"),
            id="help",
        )

    def action_dismiss(self) -> None:
        self.dismiss()
