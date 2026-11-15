"""Extract readable plain text from an HTML email body.

This is intentionally not an HTML renderer. It keeps paragraphs, lists,
table rows and image alt text, and discards everything that could execute
or fetch remote resources.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Comment

SKIP_TAGS = (
    "script",
    "style",
    "head",
    "title",
    "meta",
    "link",
    "noscript",
    "template",
    "iframe",
    "object",
    "embed",
    "svg",
    "form",
    "input",
    "button",
)

BLOCK_TAGS = (
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "dl",
    "dt",
    "dd",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "ul",
)

SAFE_LINK_PREFIXES = ("http://", "https://", "mailto:")


def html_to_text(html: str) -> str:
    """Return a readable plain-text representation of *html*."""

    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(SKIP_TAGS):
        tag.decompose()

    for comment in soup.find_all(string=lambda value: isinstance(value, Comment)):
        comment.extract()

    for node in soup.find_all(string=True):
        parent = node.parent
        if parent is not None and parent.name in ("pre", "code"):
            continue
        if not node.strip():
            node.extract()

    for br in soup.find_all("br"):
        br.replace_with("\n")

    for image in soup.find_all("img"):
        alt = (image.get("alt") or "").strip()
        image.replace_with(f"🖼 {alt}" if alt else "[图片]")

    for link in soup.find_all("a"):
        href = (link.get("href") or "").strip()
        text = link.get_text(" ", strip=True)
        if not href.startswith(SAFE_LINK_PREFIXES):
            link.unwrap()
        elif not text:
            link.replace_with(href)
        elif href.startswith(("http://", "https://")) and href not in text:
            link.append(f" <{href}>")

    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    for item in soup.find_all("li"):
        item.insert_before("\n• ")

    for cell in soup.find_all(("td", "th")):
        cell.insert_after("  ")

    for row in soup.find_all("tr"):
        row.insert_after("\n")

    text = soup.get_text(" ", strip=False)
    text = text.replace("\xa0", " ").replace("\u200b", "")

    lines: list[str] = []
    previous_blank = False
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        line = re.sub(r" ([，。！？；：、])", r"\1", line)
        if line:
            lines.append(line)
            previous_blank = False
        elif not previous_blank:
            lines.append("")
            previous_blank = True

    return "\n".join(lines).strip()
