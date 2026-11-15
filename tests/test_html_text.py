from mailtui.domain.html_text import html_to_text


def test_extracts_readable_structure() -> None:
    html = """
    <html>
      <head><style>.x { color: red }</style><script>alert(1)</script></head>
      <body>
        <h2>项目进度</h2>
        <p>本周完成情况如下：</p>
        <ul>
          <li>支付链路灰度 <b>80%</b></li>
          <li>风控规则迁移</li>
        </ul>
        <table>
          <tr><th>模块</th><th>状态</th></tr>
          <tr><td>网关</td><td>进行中</td></tr>
        </table>
        <p>见 <a href="https://example.com/report">周报</a>。</p>
        <p><img src="https://example.com/a.png" alt="趋势图"></p>
      </body>
    </html>
    """

    text = html_to_text(html)

    assert "项目进度" in text
    assert "本周完成情况如下：" in text
    assert "• 支付链路灰度 80%" in text
    assert "• 风控规则迁移" in text
    assert "模块" in text and "进行中" in text
    assert "周报" in text and "https://example.com/report" in text
    assert "🖼 趋势图" in text
    assert "alert(1)" not in text
    assert "color: red" not in text


def test_drops_unsafe_links_and_empty_html() -> None:
    assert html_to_text("<p><a href='javascript:alert(1)'>点我</a></p>") == "点我"
    assert html_to_text("") == ""
    assert html_to_text("<script>alert(1)</script>") == ""


def test_handles_entities_and_line_breaks() -> None:
    text = html_to_text("<p>A&nbsp;B<br>C</p><p>D</p>")
    assert "A B" in text
    assert "C" in text
    assert "\nD" in text
