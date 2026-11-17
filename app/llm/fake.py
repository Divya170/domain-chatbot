"""Deterministic provider for tests and offline development."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence

from app.llm.base import LlmChunk, LlmRequest, Usage
from app.services.tokens import estimate_tokens


class FakeLlmProvider:
    """Streams scripted text word by word.

    ``fail_with`` is raised after ``fail_after_chunks`` chunks. ``closed_early`` turns
    true when the consumer stops iterating before the end (client disconnect).
    """

    def __init__(
        self,
        reply: str = "This is a fake answer.",
        *,
        fail_with: BaseException | None = None,
        fail_after_chunks: int = 0,
        delay_seconds: float = 0.0,
        configured: bool = True,
    ) -> None:
        self.reply = reply
        self.fail_with = fail_with
        self.fail_after_chunks = fail_after_chunks
        self.delay_seconds = delay_seconds
        self._configured = configured
        self.requests: list[LlmRequest] = []
        self.closed_early = False

    @property
    def name(self) -> str:
        return "fake"

    @property
    def configured(self) -> bool:
        return self._configured

    def _pieces(self) -> Sequence[str]:
        words = self.reply.split(" ")
        return [word if i == 0 else " " + word for i, word in enumerate(words)]

    async def stream(self, request: LlmRequest) -> AsyncGenerator[LlmChunk, None]:
        self.requests.append(request)
        pieces = self._pieces()
        finished = False
        raised = False
        try:
            for index, piece in enumerate(pieces):
                if self.fail_with is not None and index == self.fail_after_chunks:
                    raised = True
                    raise self.fail_with
                if self.delay_seconds:
                    await asyncio.sleep(self.delay_seconds)
                yield LlmChunk(text=piece)
            if self.fail_with is not None and self.fail_after_chunks >= len(pieces):
                raised = True
                raise self.fail_with
            input_text = request.system_instruction + "".join(t.text for t in request.contents)
            yield LlmChunk(
                usage=Usage(
                    input_tokens=estimate_tokens(input_text),
                    output_tokens=estimate_tokens(self.reply),
                )
            )
            finished = True
        finally:
            if not finished and not raised:
                self.closed_early = True
