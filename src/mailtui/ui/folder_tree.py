"""Build a cascading folder tree from flat IMAP folder names."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rich.text import Text
from textual.widgets import Tree


@dataclass
class _Trie:
    children: dict[str, "_Trie"] = field(default_factory=dict)
    folder: Any | None = None


def _path(folder: Any) -> str:
    if isinstance(folder, dict):
        return str(folder.get("name") or "")
    return str(getattr(folder, "path", None) or getattr(folder, "label", ""))


def _label(folder: Any) -> str:
    if isinstance(folder, dict):
        return str(folder.get("name") or "")
    return str(getattr(folder, "label", "") or getattr(folder, "path", ""))


def _unread(folder: Any) -> int:
    if isinstance(folder, dict):
        return int(folder.get("unread") or 0)
    return int(getattr(folder, "unread", 0) or 0)


def _delimiter(folder: Any) -> str:
    if isinstance(folder, dict):
        return str(folder.get("delimiter") or "/")
    return "/"


def folder_label(folder: Any, fallback: str = "") -> Text:
    name = _label(folder) or fallback
    unread = _unread(folder)
    text = Text()
    text.append(name, style="bold")
    if unread:
        text.append(f"  {unread}", style="#22d3ee")
    return text


def build_folder_tree(
    tree: Tree,
    folders: list[Any],
    *,
    root_label: str,
    current_name: str | None = None,
    expand_depth: int = 0,
) -> dict[str, Any]:
    """Populate *tree* and return a mapping of folder name to node."""

    tree.auto_expand = False
    tree.clear()
    tree.root.set_label(Text(root_label, style="bold #7dd3fc"))
    tree.root.expand()

    trie = _Trie()
    for folder in folders:
        path = _path(folder)
        if not path:
            continue
        parts = [part for part in path.split(_delimiter(folder)) if part]
        if not parts:
            continue
        node = trie
        for part in parts:
            node = node.children.setdefault(part, _Trie())
        node.folder = folder

    current_delim = "/"
    if current_name:
        for folder in folders:
            if _path(folder) == current_name:
                current_delim = _delimiter(folder)
                break
    current_parts = current_name.split(current_delim) if current_name else []
    mapping: dict[str, Any] = {}

    def add_nodes(node: _Trie, parent: Any, prefix: list[str], depth: int) -> None:
        for part, child in node.children.items():
            parts = [*prefix, part]
            folder = child.folder
            should_expand = depth <= expand_depth
            if current_parts and current_parts[: len(parts)] == parts:
                should_expand = True
            if folder is not None:
                label = folder_label(folder, part)
                data = folder
                mapping[_path(folder)] = None
            else:
                label = Text(part, style="bold")
                data = None
            tree_node = parent.add(
                label,
                data=data,
                expand=should_expand and bool(child.children),
            )
            if folder is not None:
                mapping[_path(folder)] = tree_node
            add_nodes(child, tree_node, parts, depth + 1)

    add_nodes(trie, tree.root, [], 0)
    tree.root.expand()
    return mapping
