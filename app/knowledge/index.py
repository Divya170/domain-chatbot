"""In-memory knowledge index with change detection and last-good protection.

* Loaded once at startup.
* ``snapshot()`` re-checks file mtimes/sizes at most every ``reload_check_seconds``
  (0 disables automatic checks) and rebuilds on change.
* Rebuilds swap the snapshot atomically. If a previously valid file becomes invalid,
  its last good chunks are kept and the problem is reported. If the whole load fails
  (directory gone, permission error), the previous snapshot stays in place.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.knowledge.base import FileProblem, KnowledgeChunk, KnowledgeSnapshot, LoadedFile
from app.knowledge.loader import (
    DEFAULT_MAX_CHUNK_TOKENS,
    iter_markdown_files,
    load_knowledge,
)

logger = logging.getLogger(__name__)

Fingerprint = tuple[tuple[str, int, int], ...]


def fingerprint(root: Path) -> Fingerprint:
    entries: list[tuple[str, int, int]] = []
    for path in iter_markdown_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(entries)


class KnowledgeIndex:
    def __init__(
        self,
        root: Path,
        *,
        reload_check_seconds: float = 5.0,
        max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = root
        self.reload_check_seconds = reload_check_seconds
        self.max_chunk_tokens = max_chunk_tokens
        self._clock = clock
        self._lock = threading.Lock()
        self._snapshot: KnowledgeSnapshot | None = None
        self._last_check = 0.0
        self._last_error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._snapshot is not None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def snapshot(self) -> KnowledgeSnapshot | None:
        """Current snapshot, refreshed if the check interval elapsed and files changed."""
        if self._snapshot is None:
            return self.reload()
        if self.reload_check_seconds > 0:
            now = self._clock()
            if now - self._last_check >= self.reload_check_seconds:
                self._last_check = now
                try:
                    changed = fingerprint(self.root) != self._snapshot.fingerprint
                except OSError:
                    changed = True
                if changed:
                    return self.reload()
        return self._snapshot

    def reload(self) -> KnowledgeSnapshot | None:
        """Rebuild now. Returns the active snapshot (new, or last good on failure)."""
        with self._lock:
            previous = self._snapshot
            try:
                fp = fingerprint(self.root)
                result = load_knowledge(self.root, max_chunk_tokens=self.max_chunk_tokens)
            except (OSError, ValueError) as exc:
                self._last_error = f"{type(exc).__name__}: knowledge load failed"
                logger.error(
                    "knowledge reload failed; keeping last good index",
                    extra={"error": type(exc).__name__},
                )
                self._last_check = self._clock()
                return previous

            chunks: list[KnowledgeChunk] = list(result.chunks)
            files: list[LoadedFile] = list(result.files)
            problems: list[FileProblem] = list(result.problems)
            if previous is not None:
                loaded_now = {f.file for f in files}
                failed_now = {p.file for p in result.problems}
                for old in previous.files:
                    if old.file in failed_now and old.file not in loaded_now:
                        chunks.extend(c for c in previous.chunks if c.file == old.file)
                        files.append(old)
                        problems.append(
                            FileProblem(
                                old.file, "serving the last good version of this file", "warning"
                            )
                        )
            version = (previous.version + 1) if previous else 1
            snapshot = KnowledgeSnapshot(
                version=version,
                chunks=tuple(chunks),
                files=tuple(sorted(files, key=lambda f: f.file)),
                problems=tuple(problems),
                loaded_at=datetime.now(UTC),
                fingerprint=fp,
            )
            self._snapshot = snapshot
            self._last_error = None
            self._last_check = self._clock()
            logger.info(
                "knowledge loaded",
                extra={
                    "version": version,
                    "files": len(files),
                    "chunks": len(chunks),
                    "problems": len(problems),
                },
            )
            return snapshot
