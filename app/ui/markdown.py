"""Sanitised Markdown → HTML for assistant messages.

Raw HTML in the source is disabled (escaped), and markdown-it's link validation drops
``javascript:``, ``vbscript:``, ``file:`` and ``data:`` URLs. Links open in a new tab
with ``rel="noopener noreferrer nofollow"``. Images are not rendered.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token

_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "breaks": True})
_md.enable("table")
_md.enable("strikethrough")
_md.disable("image")


def _link_open(renderer: Any, tokens: Sequence[Token], idx: int, options: Any, env: Any) -> str:
    token = tokens[idx]
    token.attrSet("target", "_blank")
    token.attrSet("rel", "noopener noreferrer nofollow")
    return str(renderer.renderToken(tokens, idx, options, env))


_md.add_render_rule("link_open", _link_open)


def render_markdown(text: str) -> str:
    return str(_md.render(text))
