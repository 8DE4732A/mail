"""Small built-in demo dataset for the UI shell."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Folder:
    label: str
    unread: int = 0
    path: str | None = None


@dataclass(frozen=True)
class Message:
    sender: str
    subject: str
    time: str
    body: str
    body_html: str | None = None
    unread: bool = False
    flagged: bool = False
    attachment: bool = False
    meta: dict[str, str] = field(default_factory=dict)


FOLDERS = [
    Folder("工作", 3, "工作"),
    Folder("收件箱", 3, "工作/收件箱"),
    Folder("项目", 0, "工作/项目"),
    Folder("Alpha", 5, "工作/项目/Alpha"),
    Folder("Beta", 2, "工作/项目/Beta"),
    Folder("已发送", 0, "已发送"),
    Folder("草稿", 2, "草稿"),
    Folder("回收站", 0, "回收站"),
]

MESSAGES = [
    Message(
        sender="Alice Chen",
        subject="Q4 项目进度同步",
        time="10:24",
        unread=True,
        flagged=True,
        attachment=True,
        meta={"email": "alice@example.com", "to": "work@example.com"},
        body="",
        body_html="""
        <html>
          <head><style>.x { color: red; }</style></head>
          <body>
            <h2>Q4 项目进度</h2>
            <p>本周完成情况如下：</p>
            <ul>
              <li>支付链路灰度已完成 <b>80%</b>，预计周五全量。</li>
              <li>风控规则迁移延后两天，需要确认白名单。</li>
              <li>下周一开始压测，峰值目标 3k TPS。</li>
            </ul>
            <table>
              <tr><th>模块</th><th>状态</th></tr>
              <tr><td>网关</td><td>进行中</td></tr>
              <tr><td>风控</td><td>已完成</td></tr>
            </table>
            <p>详细数据见 <a href="https://example.com/report">周报</a>。</p>
            <p><img src="https://example.com/chart.png" alt="吞吐趋势图"></p>
          </body>
        </html>
        """,
    ),
    Message(
        sender="Bob Li",
        subject="周会纪要：11 月第 2 周",
        time="09:10",
        unread=True,
        meta={"email": "bob@example.com", "to": "work@example.com"},
        body="""本周结论：

- 支付网关拆分按计划推进。
- 监控告警误报率下降到 4%。
- 需要补充一份回滚演练记录。

完整纪要见共享文档。""",
    ),
    Message(
        sender="Carol Wang",
        subject="合同修订版请确认",
        time="昨天",
        attachment=True,
        meta={"email": "carol@example.com", "to": "work@example.com"},
        body="""Hi,

法务已更新第 4.2 条和附件 A，请重点关注付款周期。

如果没问题，我明天走盖章流程。""",
    ),
    Message(
        sender="Dave Zhang",
        subject="告警恢复：支付超时率",
        time="昨天",
        flagged=True,
        meta={"email": "dave@example.com", "to": "work@example.com"},
        body="""23:12 支付超时率升至 7.8%，已自动扩容并恢复。

根因是上游缓存击穿，已加限流和预热。
后续动作：补充容量基线、更新值班手册。""",
    ),
    Message(
        sender="Eve Liu",
        subject="设计评审邀请",
        time="周二",
        attachment=True,
        meta={"email": "eve@example.com", "to": "work@example.com"},
        body="""邀请你参加周四 15:00 的终端邮箱客户端设计评审。

议题：
1. 三栏布局与快捷键
2. 离线发送队列
3. 大附件处理""",
    ),
]
