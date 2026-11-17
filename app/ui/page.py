"""The chat page (NiceGUI), mounted on the FastAPI app at ``/``.

It consumes the public API through ``AsgiApiClient`` (no private shortcuts into the
services): GET /api/config, POST /api/chat/stream, POST /api/chat/regenerate,
GET/DELETE /api/sessions/{id}, POST /api/messages/{id}/feedback.

States: empty (welcome + starters), typing (dots before the first token), streaming
(caret), error (translated per code; the typed message is restored into the
composer), rate-limited (with wait time), offline (NiceGUI's reconnect overlay).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from typing import Any

from fastapi import FastAPI, Request
from nicegui import Client, background_tasks, ui
from nicegui import app as nicegui_app

from app.container import Container
from app.ui.client import AsgiApiClient, parse_sse
from app.ui.components import (
    ENTER_TO_SEND_JS,
    HEAD_HTML,
    Bubble,
    BubbleActions,
)
from app.ui.strings import error_text, t

logger = logging.getLogger(__name__)

STREAM_FLUSH_SECONDS = 0.08
COUNTER_THRESHOLD = 0.8
STARTER_STYLES = (
    ("payments", "linear-gradient(135deg,#0f766e,#14b8a6)"),
    ("description", "linear-gradient(135deg,#f59e0b,#fbbf24)"),
    ("event_available", "linear-gradient(135deg,#fb7185,#f472b6)"),
    ("local_hospital", "linear-gradient(135deg,#6366f1,#818cf8)"),
    ("volunteer_activism", "linear-gradient(135deg,#0ea5e9,#38bdf8)"),
    ("support_agent", "linear-gradient(135deg,#10b981,#34d399)"),
)


class ChatPage:
    def __init__(self, api: AsgiApiClient, ttl_days: int) -> None:
        self.api = api
        self.ttl_days = ttl_days
        self.store: Any = nicegui_app.storage.user
        self.config: dict[str, Any] = {}
        self.lang = "en"
        self.session_id: str | None = None
        self.busy = False
        self.task: asyncio.Task[None] | None = None
        self.last_answer: Bubble | None = None
        self.welcome: ui.element | None = None

    # ------------------------------------------------------------------ build
    async def build(self) -> None:
        response = await self.api.request("GET", "/api/config")
        self.config = response.json() or {}
        codes = {lang["code"] for lang in self.config.get("languages", [])}
        stored_lang = self.store.get("language")
        self.lang = (
            stored_lang if stored_lang in codes else self.config.get("defaultLanguage", "en")
        )
        self.session_id = self.store.get("session_id")
        self.actions = BubbleActions(self.copy, self.feedback, self.regenerate)
        lang = self.lang

        ui.colors(primary="#0f766e")
        ui.add_head_html(HEAD_HTML)
        with ui.header().classes("cb-header items-center no-wrap"):
            with ui.element("div").classes("cb-logo").props('aria-hidden="true"'):
                ui.icon("spa", size="24px")
            with ui.column().classes("gap-0 grow"):
                ui.label(self.config.get("title", "")).classes("cb-title")
                ui.label(self.config.get("subtitle", "")).classes("cb-subtitle")
            self.language_select = ui.select(
                {item["code"]: item["label"] for item in self.config.get("languages", [])},
                value=self.lang,
                on_change=self.change_language,
            ).props(f'dense outlined options-dense aria-label="{t(lang, "language")}"')
            ui.button(icon="add_comment", on_click=self.new_chat).props(
                f'flat round dense aria-label="{t(lang, "new_chat")}"'
            ).tooltip(t(lang, "new_chat"))
            ui.button(icon="delete_outline", on_click=self.confirm_delete).props(
                f'flat round dense aria-label="{t(lang, "delete")}"'
            ).tooltip(t(lang, "delete"))

        self.live = ui.element("div").classes("sr-only").props('aria-live="polite" role="status"')
        self.messages = ui.column().classes("cb-list w-full")

        max_len = int(self.config.get("maxMessageLength", 4000))
        self.max_len = max_len
        with ui.footer().classes("cb-footer"), ui.column().classes("cb-footer-inner gap-1"):
            self.error_banner = ui.label("").classes("cb-error w-full").props('role="alert"')
            self.error_banner.set_visibility(False)
            with ui.row().classes("cb-composer w-full items-end no-wrap gap-2"):
                self.input = (
                    ui.textarea(placeholder=t(lang, "placeholder"))
                    .props(
                        f"autogrow borderless dense maxlength={max_len} "
                        f'aria-label="{t(lang, "placeholder")}"'
                    )
                    .classes("grow")
                )
                self.input.on("keydown", lambda: self.send(), js_handler=ENTER_TO_SEND_JS)
                self.input.on_value_change(self.update_counter)
                self.send_button = (
                    ui.button(icon="send", on_click=lambda: self.send())
                    .props(f'round unelevated aria-label="{t(lang, "send")}"')
                    .classes("cb-send")
                )
                self.stop_button = ui.button(icon="stop", on_click=self.stop).props(
                    f'round color=negative aria-label="{t(lang, "stop")}"'
                )
                self.stop_button.set_visibility(False)
            self.counter = ui.label("").classes("cb-counter")
            self.counter.set_visibility(False)
            disclaimer = self.config.get("disclaimer", {}).get(lang, "")
            emergency = self.config.get("emergency", {}).get(lang, "")
            ui.label(f"{disclaimer} {emergency}").classes("cb-disclaimer")
            if self.config.get("persistentHistory"):
                ui.label(t(lang, "saved_hint", days=self.ttl_days)).classes("cb-disclaimer")

        # A reload/tab close deletes this client's elements; stop any in-flight stream so
        # it doesn't keep writing into deleted elements.
        self.client = ui.context.client
        self.client.on_delete(self.stop)
        await self.rehydrate()

    # ------------------------------------------------------------------ state helpers
    def remember_session(self, session_id: str) -> None:
        self.session_id = session_id
        self.store["session_id"] = session_id

    def forget_session(self) -> None:
        self.session_id = None
        self.store.pop("session_id", None)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.send_button.set_visibility(not busy)
        self.stop_button.set_visibility(busy)
        self.language_select.set_enabled(not busy)
        if busy:
            self.input.disable()
        else:
            self.input.enable()
            self.input.run_method("focus")

    def announce(self, text: str) -> None:
        self.live.clear()
        with self.live:
            ui.label(text[:600])

    def show_error(self, code: str, retry_after: int | None = None) -> None:
        if code == "RATE_LIMIT" and retry_after:
            message = t(self.lang, "rate_limited", seconds=retry_after)
        else:
            message = error_text(self.lang, code)
        self.error_banner.set_text(message)
        self.error_banner.set_visibility(True)
        self.announce(message)

    def hide_error(self) -> None:
        self.error_banner.set_visibility(False)

    def update_counter(self) -> None:
        length = len(self.input.value or "")
        if length >= self.max_len * COUNTER_THRESHOLD:
            self.counter.set_text(t(self.lang, "chars_left", n=max(self.max_len - length, 0)))
            self.counter.set_visibility(True)
        else:
            self.counter.set_visibility(False)

    def set_last_answer(self, bubble: Bubble | None) -> None:
        if self.last_answer is not None and self.last_answer is not bubble:
            self.last_answer.set_regenerate(False)
        self.last_answer = bubble
        if bubble is not None:
            bubble.set_regenerate(bubble.message_id is not None)

    # ------------------------------------------------------------------ welcome / history
    def show_welcome(self) -> None:
        if self.welcome is not None:
            return
        with self.messages:
            self.welcome = ui.column().classes("cb-hero w-full")
        with self.welcome:
            with ui.element("div").classes("cb-hero-badge").props('aria-hidden="true"'):
                ui.icon("spa", size="40px")
            ui.label(self.config.get("title", "")).classes("cb-hero-title")
            ui.label(self.config.get("welcome", {}).get(self.lang, "")).classes("cb-hero-text")
            with ui.element("div").classes("cb-starters"):
                for index, question in enumerate(
                    self.config.get("starters", {}).get(self.lang, [])
                ):
                    icon, color = STARTER_STYLES[index % len(STARTER_STYLES)]
                    card = (
                        ui.element("div")
                        .classes("cb-starter")
                        .props(f'role="button" tabindex="0" aria-label="{question}"')
                    )
                    with card:
                        with (
                            ui.element("div")
                            .classes("cb-starter-icon")
                            .style(f"background:{color}")
                        ):
                            ui.icon(icon, size="20px")
                        ui.label(question).classes("cb-starter-text")
                    card.on("click", lambda q=question: self.send(q))
                    card.on("keydown.enter", lambda q=question: self.send(q))

    def hide_welcome(self) -> None:
        if self.welcome is not None:
            self.welcome.delete()
            self.welcome = None

    async def rehydrate(self) -> None:
        if self.session_id:
            response = await self.api.request("GET", f"/api/sessions/{self.session_id}/messages")
            if response.status == 200:
                last: Bubble | None = None
                for m in response.json() or []:
                    if m["role"] == "user":
                        Bubble(self.messages, "user", m["content"], language=self.lang)
                    else:
                        last = Bubble(
                            self.messages,
                            "assistant",
                            m["content"],
                            language=self.lang,
                            actions=self.actions,
                            message_id=m["id"],
                            sources=m["sources"],
                            error=m.get("errorCode") is not None,
                        )
                        if m.get("feedback"):
                            last.show_feedback(m["feedback"])
                self.set_last_answer(last)
                if last is not None:
                    background_tasks.create(self._scroll_after_connect())
                    return
            else:
                self.forget_session()
        self.show_welcome()

    def scroll(self, force: bool = False) -> None:
        # Via the stored client, not the ambient slot: the slot of the handler that
        # triggered us (e.g. a starter button) may already be deleted.
        self.client.run_javascript(
            f"window.cbScroll && window.cbScroll({'true' if force else 'false'})"
        )

    async def _scroll_after_connect(self) -> None:
        # No ui.timer: its cleanup raises "parent element ... has been deleted" when the
        # page was already replaced (reload / second connection) before it fired.
        with contextlib.suppress(Exception):
            await self.client.connected()
            if self.client.id in Client.instances:
                self.scroll(force=True)

    # ------------------------------------------------------------------ actions
    async def send(self, text: str | None = None) -> None:
        if self.busy:
            return
        message = (text if text is not None else (self.input.value or "")).strip()
        if not message:
            return
        if len(message) > self.max_len:
            self.show_error("INVALID_INPUT")
            return
        self.hide_error()
        self.input.value = ""
        self.hide_welcome()
        user_bubble = Bubble(self.messages, "user", message, language=self.lang)
        self.scroll(force=True)
        payload = {"message": message, "language": self.lang, "sessionId": self.session_id}
        self.start("/api/chat/stream", payload, restore=message, user_bubble=user_bubble)

    async def regenerate(self, bubble: Bubble) -> None:
        if self.busy or not self.session_id or not bubble.message_id:
            return
        self.hide_error()
        bubble.delete()
        self.last_answer = None
        payload = {"sessionId": self.session_id, "messageId": bubble.message_id}
        self.start("/api/chat/regenerate", payload, restore=None, user_bubble=None)

    def start(
        self, path: str, payload: dict[str, Any], *, restore: str | None, user_bubble: Bubble | None
    ) -> None:
        self.set_busy(True)
        self.task = asyncio.create_task(
            self.stream(path, payload, restore=restore, user_bubble=user_bubble)
        )

    def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()

    async def stream(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        restore: str | None,
        user_bubble: Bubble | None,
        retried: bool = False,
    ) -> None:
        bubble = Bubble(
            self.messages, "assistant", "", language=self.lang, actions=self.actions, typing=True
        )
        self.announce(t(self.lang, "typing"))
        self.scroll()
        text = ""
        last_flush = 0.0
        try:
            async with self.api.stream("POST", path, payload) as response:
                if response.status != 200:
                    body = await response.read()
                    bubble.delete()
                    code, retry_after = _error_of(body, response.headers)
                    if (
                        code == "SESSION_NOT_FOUND"
                        and not retried
                        and path.endswith("/stream")
                        and payload.get("sessionId")
                    ):
                        self.forget_session()
                        await self.stream(
                            path,
                            {**payload, "sessionId": None},
                            restore=restore,
                            user_bubble=user_bubble,
                            retried=True,
                        )
                        return
                    if user_bubble is not None:
                        user_bubble.delete()
                    if restore:
                        self.input.value = restore
                    self.show_error(code, retry_after)
                    return
                async for frame in parse_sse(response.chunks):
                    kind = frame.get("type")
                    if kind == "start":
                        self.remember_session(frame["sessionId"])
                        bubble.message_id = frame["messageId"]
                    elif kind == "chunk":
                        text += frame.get("text", "")
                        now = time.monotonic()
                        if now - last_flush >= STREAM_FLUSH_SECONDS:
                            bubble.set_text(text, streaming=True)
                            self.scroll()
                            last_flush = now
                    elif kind == "sources":
                        bubble.set_sources(frame.get("sources", []))
                    elif kind == "done":
                        bubble.set_text(text)
                        bubble.finish(frame.get("messageId"))
                        self.set_last_answer(bubble)
                        self.announce(text)
                    elif kind == "error":
                        bubble.set_text(text)
                        bubble.mark_error()
                        bubble.finish(None)
                        self.set_last_answer(bubble)
                        if restore:
                            self.input.value = restore
                        self.show_error(frame.get("code", "INTERNAL"), frame.get("retryAfter"))
        except asyncio.CancelledError:
            # The bubble may already be gone (New chat clears the list): best effort.
            with contextlib.suppress(Exception):
                bubble.set_text(f"{text}\n\n{t(self.lang, 'stopped')}".strip())
                bubble.finish(None)
                self.set_last_answer(bubble)
            raise
        except Exception:
            if self.client.id not in Client.instances:  # client already gone
                logger.info("ui stream aborted: client disconnected")
                return
            logger.exception("ui stream failed")
            with contextlib.suppress(Exception):
                bubble.delete()
                if restore:
                    self.input.value = restore
                self.show_error("INTERNAL")
        finally:
            with contextlib.suppress(Exception):  # elements may be deleted (page reloaded)
                self.set_busy(False)
                self.scroll()

    async def copy(self, bubble: Bubble) -> None:
        ui.clipboard.write(bubble.text)
        ui.notify(t(self.lang, "copied"), position="bottom", timeout=1500)

    async def feedback(self, bubble: Bubble, rating: str) -> None:
        if not bubble.message_id:
            return
        response = await self.api.request(
            "POST", f"/api/messages/{bubble.message_id}/feedback", {"rating": rating}
        )
        if response.status == 204:
            bubble.show_feedback(rating)
            ui.notify(t(self.lang, "thanks"), position="bottom", timeout=1500)
        else:
            code, _ = _error_of(response.body, response.headers)
            self.show_error(code)

    async def new_chat(self) -> None:
        self.stop()
        self.forget_session()
        self.messages.clear()
        self.welcome = None
        self.last_answer = None
        self.hide_error()
        self.show_welcome()
        self.input.run_method("focus")

    async def confirm_delete(self) -> None:
        with ui.dialog() as dialog, ui.card():
            ui.label(t(self.lang, "delete_confirm"))
            with ui.row().classes("justify-end w-full"):
                ui.button(t(self.lang, "cancel"), on_click=lambda: dialog.submit(False)).props(
                    "flat"
                )
                ui.button(t(self.lang, "delete"), on_click=lambda: dialog.submit(True)).props(
                    "color=negative"
                )
        confirmed = await dialog
        if not confirmed:
            return
        if self.session_id:
            await self.api.request("DELETE", f"/api/sessions/{self.session_id}")
        await self.new_chat()

    async def change_language(self, event: Any) -> None:
        if event.value and event.value != self.lang:
            self.store["language"] = event.value
            ui.navigate.reload()


def _error_of(body: bytes, headers: dict[str, str]) -> tuple[str, int | None]:
    try:
        error = json.loads(body or b"{}").get("error", {})
    except (ValueError, AttributeError):
        error = {}
    retry = headers.get("retry-after")
    return str(error.get("code", "INTERNAL")), int(retry) if retry and retry.isdigit() else None


def mount_ui(fastapi_app: FastAPI, container: Container) -> None:
    settings = container.settings
    storage_secret = (
        settings.session_secret.get_secret_value()
        if settings.session_secret
        else secrets.token_urlsafe(32)
    )

    @ui.page("/")
    async def index(request: Request) -> None:
        state = request.scope.get("state", {})
        api = AsgiApiClient(
            fastapi_app,
            client_ip=request.client.host if request.client else "unknown",
            cookie=state.get("client_cookie"),
        )
        await ChatPage(api, settings.session_ttl_days).build()

    ui.run_with(
        fastapi_app,
        mount_path="/",
        title=container.domain.title,
        storage_secret=storage_secret,
        dark=None,
    )
