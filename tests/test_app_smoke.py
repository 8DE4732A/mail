import asyncio

from textual.widgets import ListView

from mailtui.ui.demo_app import DemoApp, ComposeScreen
from mailtui.ui.help import HelpScreen


def test_demo_app_renders_html_body() -> None:
    async def run() -> None:
        app = DemoApp(demo=True)
        async with app.run_test(size=(110, 32)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "项目进度" in app.current_body_text
            assert "支付链路灰度" in app.current_body_text
            assert "🖼 吞吐趋势图" in app.current_body_text

            messages = app.query_one("#message-list", ListView)
            await pilot.press("j")
            await pilot.pause()
            assert messages.index == 1
            assert "本周结论" in app.current_body_text

            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, ComposeScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ComposeScreen)
            await pilot.press("question_mark")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(run())
