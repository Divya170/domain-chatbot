"""NiceGUI building blocks: message bubble, welcome block, error banner, head assets."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from app.ui.markdown import render_markdown
from app.ui.strings import t

TYPING_HTML = '<span class="cb-dots" aria-hidden="true"><i></i><i></i><i></i></span>'

HEAD_HTML = """
<meta name="theme-color" content="#0f766e">
<style>
  :root {
    --cb-primary:#0f766e; --cb-primary-2:#14b8a6; --cb-accent:#f59e0b; --cb-rose:#fb7185;
    --cb-bg:#f4faf8; --cb-surface:#ffffff; --cb-ink:#14302c; --cb-muted:#5c7873;
    --cb-line:rgba(15,118,110,.14); --cb-danger:#c0392b;
    --cb-shadow:0 1px 2px rgba(16,60,54,.06), 0 8px 24px -8px rgba(16,60,54,.14);
    --cb-grad:linear-gradient(135deg,#0f766e 0%,#14b8a6 100%);
  }
  body.body--dark {
    --cb-bg:#0b1513; --cb-surface:#13211f; --cb-ink:#e6f2ef; --cb-muted:#8fb0aa;
    --cb-line:rgba(94,234,212,.14); --cb-shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 24px -8px rgba(0,0,0,.5);
  }
  body, .q-field__native, .q-btn {
    font-family: "Inter", "Segoe UI", "Noto Sans", "Noto Sans Tamil", system-ui, -apple-system, sans-serif;
  }
  body { background: var(--cb-bg); color: var(--cb-ink); }
  body::before { content:""; position:fixed; inset:0; z-index:-1; pointer-events:none;
    background:
      radial-gradient(520px 320px at 8% -4%, rgba(20,184,166,.22), transparent 70%),
      radial-gradient(480px 360px at 100% 8%, rgba(251,113,133,.14), transparent 70%),
      radial-gradient(520px 380px at 50% 110%, rgba(245,158,11,.12), transparent 70%); }
  .q-page-container { background: transparent; }

  /* header */
  .cb-header { background: rgba(255,255,255,.72) !important; color: var(--cb-ink);
    backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--cb-line); gap: 12px; padding: 10px 16px; }
  body.body--dark .cb-header { background: rgba(11,21,19,.72) !important; }
  .cb-logo { width: 42px; height: 42px; border-radius: 14px; background: var(--cb-grad); color:#fff;
    display:flex; align-items:center; justify-content:center; box-shadow: 0 6px 16px -4px rgba(15,118,110,.55); }
  .cb-title { font-weight: 750; font-size: 1.05rem; line-height: 1.2; letter-spacing: -.01em; }
  .cb-subtitle { font-size: .76rem; color: var(--cb-muted); display:flex; align-items:center; gap:6px; }
  .cb-subtitle::before { content:""; width:7px; height:7px; border-radius:50%; background:#22c55e;
    box-shadow:0 0 0 3px rgba(34,197,94,.2); }
  .cb-header .q-field--outlined .q-field__control { border-radius: 999px; background: var(--cb-surface);
    box-shadow: var(--cb-shadow); }
  .cb-header .q-field__native, .cb-header .q-field__append { color: var(--cb-ink) !important; }
  .cb-header .q-btn { color: var(--cb-primary) !important; background: var(--cb-surface);
    box-shadow: var(--cb-shadow); }

  /* conversation */
  .cb-list { max-width: 860px; margin: 0 auto; padding: 22px 14px 10px; gap: 18px; }
  .cb-row { width: 100%; align-items: flex-end; gap: 10px; animation: cb-in .28s ease-out both; }
  .cb-row.cb-user { justify-content: flex-end; }
  .cb-wrap { max-width: min(86%, 700px); }
  .cb-avatar { flex: 0 0 auto; width: 34px; height: 34px; border-radius: 12px; background: var(--cb-grad);
    color:#fff; display:flex; align-items:center; justify-content:center;
    box-shadow: 0 4px 12px -3px rgba(15,118,110,.5); }
  .cb-bubble { padding: 12px 16px; border-radius: 6px 20px 20px 20px; background: var(--cb-surface);
    border: 1px solid var(--cb-line); box-shadow: var(--cb-shadow); color: var(--cb-ink);
    line-height: 1.65; overflow-wrap: anywhere; font-size: .95rem; }
  .cb-user .cb-bubble { background: var(--cb-grad); color:#fff; border: 0; white-space: pre-wrap;
    border-radius: 20px 20px 6px 20px; box-shadow: 0 10px 22px -10px rgba(15,118,110,.7); }
  .cb-bubble p { margin: 0 0 .55em; } .cb-bubble p:last-child { margin-bottom: 0; }
  .cb-bubble ul, .cb-bubble ol { margin: .3em 0 .55em 1.2em; }
  .cb-bubble strong { color: var(--cb-primary); } body.body--dark .cb-bubble strong { color: #5eead4; }
  .cb-bubble table { border-collapse: collapse; }
  .cb-bubble td, .cb-bubble th { border: 1px solid var(--cb-line); padding: 4px 8px; }
  .cb-bubble a { color: var(--cb-primary); text-decoration: underline; text-underline-offset: 2px; }
  .cb-error-bubble { border: 1px solid var(--cb-danger) !important; }
  .cb-streaming::after { content: "▍"; color: var(--cb-primary-2); animation: cb-blink 1s steps(1) infinite; }
  @keyframes cb-blink { 50% { opacity: 0; } }
  @keyframes cb-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  .cb-dots i { display:inline-block; width:7px; height:7px; margin:0 2px; border-radius:50%;
    background: var(--cb-primary-2); animation: cb-pulse 1s infinite ease-in-out; }
  .cb-dots i:nth-child(2){animation-delay:.15s} .cb-dots i:nth-child(3){animation-delay:.3s}
  @keyframes cb-pulse { 0%,80%,100%{transform:translateY(0);opacity:.4} 40%{transform:translateY(-5px);opacity:1} }
  .cb-actions { opacity:.85; } .cb-actions .q-btn { min-height: 30px; color: var(--cb-muted); }
  .cb-sources { border: 1px solid var(--cb-line); border-radius: 12px; background: var(--cb-surface);
    overflow: hidden; }
  .cb-sources .q-item { min-height: 36px; color: var(--cb-primary); }

  /* welcome hero */
  .cb-hero { align-items: center; text-align: center; padding: 28px 4px 6px; gap: 12px; animation: cb-in .4s ease-out both; }
  .cb-hero-badge { width: 76px; height: 76px; border-radius: 26px; background: var(--cb-grad); color:#fff;
    display:flex; align-items:center; justify-content:center;
    box-shadow: 0 18px 36px -12px rgba(15,118,110,.65), inset 0 1px 0 rgba(255,255,255,.35); }
  .cb-hero-title { font-size: clamp(1.35rem, 4vw, 1.9rem); font-weight: 800; letter-spacing: -.02em; line-height: 1.2;
    background: var(--cb-grad); -webkit-background-clip: text; background-clip: text; color: transparent; }
  .cb-hero-text { max-width: 560px; color: var(--cb-muted); line-height: 1.6; font-size: .98rem; }
  .cb-starters { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px;
    width: 100%; margin-top: 10px; }
  .cb-starter { display:flex; align-items:center; gap: 12px; text-align:left; padding: 14px 16px;
    border-radius: 18px; background: var(--cb-surface); border: 1px solid var(--cb-line);
    box-shadow: var(--cb-shadow); cursor: pointer; transition: transform .15s, box-shadow .15s, border-color .15s; }
  .cb-starter:hover { transform: translateY(-2px); border-color: var(--cb-primary-2);
    box-shadow: 0 14px 28px -12px rgba(15,118,110,.35); }
  .cb-starter-icon { flex:0 0 auto; width:38px; height:38px; border-radius:12px; display:flex;
    align-items:center; justify-content:center; color:#fff; }
  .cb-starter-text { font-size: .92rem; font-weight: 600; line-height: 1.35; color: var(--cb-ink); }

  /* composer */
  .cb-footer { background: linear-gradient(to top, var(--cb-bg) 72%, transparent) !important; border: 0 !important; box-shadow: none !important; color: inherit; }
  .cb-footer-inner { max-width: 860px; margin: 0 auto; width: 100%; padding: 6px 14px 10px; }
  .cb-composer { background: var(--cb-surface); border: 1px solid var(--cb-line); border-radius: 26px;
    box-shadow: 0 14px 34px -12px rgba(16,60,54,.28); padding: 6px 8px 6px 14px; }
  .cb-composer:focus-within { border-color: var(--cb-primary-2); box-shadow: 0 0 0 4px rgba(20,184,166,.16),
    0 14px 34px -12px rgba(16,60,54,.28); }
  .cb-composer .q-field__control:before, .cb-composer .q-field__control:after { display:none; }
  .cb-composer .q-field__control { padding: 0; }
  .cb-send { background: var(--cb-grad) !important; color:#fff !important;
    box-shadow: 0 8px 18px -6px rgba(15,118,110,.7); }
  .cb-error { color: #fff; background: var(--cb-danger); border-radius: 14px; padding: 10px 14px; }
  .cb-counter { font-size: .75rem; color: var(--cb-muted); text-align: right; }
  .cb-disclaimer { font-size: .7rem; color: var(--cb-muted); line-height: 1.45; text-align: center; }
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden;
    clip:rect(0,0,0,0); white-space:nowrap; border:0; }
  @media (max-width: 560px) { .cb-starters { grid-template-columns: 1fr; } }
  @media (max-width: 480px) { .cb-wrap { max-width: 92%; } .cb-subtitle { display:none; }
    .cb-avatar { width: 30px; height: 30px; } }
  @media (prefers-reduced-motion: reduce) { * { animation: none !important; transition: none !important; } }
</style>
<script>
  window.cbFollow = true;
  window.addEventListener('scroll', function () {
    var d = document.documentElement;
    window.cbFollow = (window.innerHeight + window.scrollY) >= (d.scrollHeight - 160);
  }, {passive: true});
  window.cbScroll = function (force) {
    if (force || window.cbFollow) {
      window.scrollTo({top: document.documentElement.scrollHeight});
      window.cbFollow = true;
    }
  };
</script>
"""

ENTER_TO_SEND_JS = (
    "(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) "
    "{ e.preventDefault(); emit(); } }"
)


def html_block(content: str) -> ui.html:
    """``ui.html`` with our own sanitising (NiceGUI 3 requires the sanitize argument)."""
    try:
        return ui.html(content, sanitize=False)
    except TypeError:  # older NiceGUI without the argument
        return ui.html(content)


def scroll(force: bool = False) -> None:
    ui.run_javascript(f"window.cbScroll && window.cbScroll({'true' if force else 'false'})")


Handler = Callable[["Bubble"], Awaitable[None]]


@dataclass
class BubbleActions:
    on_copy: Handler
    on_feedback: Callable[[Bubble, str], Awaitable[None]]
    on_regenerate: Handler


class Bubble:
    """One message. Assistant bubbles render sanitised Markdown and carry actions."""

    def __init__(
        self,
        parent: ui.element,
        role: str,
        text: str,
        *,
        language: str,
        actions: BubbleActions | None = None,
        message_id: str | None = None,
        sources: Sequence[dict[str, Any]] = (),
        typing: bool = False,
        error: bool = False,
    ) -> None:
        self.role = role
        self.text = text
        self.language = language
        self.message_id = message_id
        self._actions = actions
        with parent:
            self.row = ui.row().classes(f"cb-row cb-{role} no-wrap")
        if role != "user":
            with self.row, ui.element("div").classes("cb-avatar").props('aria-hidden="true"'):
                ui.icon("spa", size="18px")
        with self.row, ui.column().classes("cb-wrap gap-1"):
            if role == "user":
                self.body: ui.element = ui.label(text).classes("cb-bubble")
                self.sources_box: ui.element | None = None
                self.action_row: ui.element | None = None
                return
            self.body = html_block("").classes("cb-bubble")
            self.sources_box = ui.column().classes("gap-0 w-full")
            self.action_row = ui.row().classes("cb-actions gap-1 items-center")
        self._build_actions()
        self.set_text(text, streaming=False, typing=typing)
        if sources:
            self.set_sources(sources)
        if error:
            self.mark_error()
        if self.action_row is not None:
            self.action_row.set_visibility(not typing and message_id is not None)

    # ------------------------------------------------------------------ content
    def set_text(self, text: str, *, streaming: bool = False, typing: bool = False) -> None:
        self.text = text
        if isinstance(self.body, ui.label):
            self.body.set_text(text)
            return
        if not isinstance(self.body, ui.html):
            return
        self.body.content = TYPING_HTML if typing and not text else render_markdown(text)
        if streaming:
            self.body.classes(add="cb-streaming")
        else:
            self.body.classes(remove="cb-streaming")

    def set_sources(self, sources: Sequence[dict[str, Any]]) -> None:
        if self.sources_box is None or not sources:
            return
        self.sources_box.clear()
        with (
            self.sources_box,
            ui.expansion(t(self.language, "sources"), icon="menu_book")
            .classes("cb-sources text-sm w-full")
            .props("dense"),
        ):
            for s in sources:
                ui.label(f"{s.get('title', '')} — {s.get('heading', '')}").classes("text-xs")

    def mark_error(self) -> None:
        self.body.classes(add="cb-error-bubble")

    def finish(self, message_id: str | None) -> None:
        if message_id:
            self.message_id = message_id
        self.body.classes(remove="cb-streaming")
        if self.action_row is not None:
            self.action_row.set_visibility(self.message_id is not None)

    def set_regenerate(self, visible: bool) -> None:
        if hasattr(self, "_regen_button"):
            self._regen_button.set_visibility(visible)

    def delete(self) -> None:
        self.row.delete()

    # ------------------------------------------------------------------ actions
    def _build_actions(self) -> None:
        if self.action_row is None or self._actions is None:
            return
        actions = self._actions
        lang = self.language
        with self.action_row:
            ui.button(icon="content_copy", on_click=lambda: actions.on_copy(self)).props(
                f'flat round dense size=sm aria-label="{t(lang, "copy")}"'
            ).tooltip(t(lang, "copy"))
            self._up = ui.button(
                icon="thumb_up", on_click=lambda: actions.on_feedback(self, "up")
            ).props(f'flat round dense size=sm aria-label="{t(lang, "helpful")}"')
            self._down = ui.button(
                icon="thumb_down", on_click=lambda: actions.on_feedback(self, "down")
            ).props(f'flat round dense size=sm aria-label="{t(lang, "not_helpful")}"')
            self._regen_button = ui.button(
                t(lang, "regenerate"), icon="refresh", on_click=lambda: actions.on_regenerate(self)
            ).props("flat dense no-caps size=sm")
            self._regen_button.set_visibility(False)

    def show_feedback(self, rating: str) -> None:
        self._up.props(f"color={'primary' if rating == 'up' else 'grey'}")
        self._down.props(f"color={'primary' if rating == 'down' else 'grey'}")
