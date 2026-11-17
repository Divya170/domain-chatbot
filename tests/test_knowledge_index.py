from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.knowledge.index import KnowledgeIndex
from tests.conftest import WriteDoc, make_doc


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _bump_mtime(path: Path, seconds: int = 10) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + seconds * 1_000_000_000))


def _texts(index: KnowledgeIndex) -> list[str]:
    snap = index.snapshot()
    assert snap is not None
    return [c.text for c in snap.chunks]


def test_loads_once_then_serves_cached(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("a.md", make_doc("a"))
    clock = FakeClock()
    index = KnowledgeIndex(kb_dir, reload_check_seconds=5, clock=clock)
    first = index.snapshot()
    assert first is not None and first.version == 1
    clock.now += 1
    assert index.snapshot() is first


def test_edit_is_served_after_check_interval(kb_dir: Path, write_doc: WriteDoc) -> None:
    path = write_doc("a.md", make_doc("a"))
    clock = FakeClock()
    index = KnowledgeIndex(kb_dir, reload_check_seconds=5, clock=clock)
    assert any("a overview text." in t for t in _texts(index))

    path.write_text(
        make_doc("a").replace("a overview text.", "UPDATED overview."), encoding="utf-8"
    )
    _bump_mtime(path)
    clock.now += 2  # within the interval: not re-checked yet
    assert not any("UPDATED" in t for t in _texts(index))
    clock.now += 5
    assert any("UPDATED" in t for t in _texts(index))
    snap = index.snapshot()
    assert snap is not None and snap.version == 2


def test_new_and_deleted_files_detected(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("a.md", make_doc("a"))
    clock = FakeClock()
    index = KnowledgeIndex(kb_dir, reload_check_seconds=5, clock=clock)
    index.snapshot()
    b = write_doc("b.md", make_doc("b"))
    clock.now += 6
    snap = index.snapshot()
    assert snap is not None and {f.doc_id for f in snap.files} == {"a", "b"}
    b.unlink()
    clock.now += 6
    snap = index.snapshot()
    assert snap is not None and {f.doc_id for f in snap.files} == {"a"}


def test_broken_edit_keeps_last_good_version(kb_dir: Path, write_doc: WriteDoc) -> None:
    path = write_doc("a.md", make_doc("a"))
    write_doc("b.md", make_doc("b"))
    clock = FakeClock()
    index = KnowledgeIndex(kb_dir, reload_check_seconds=5, clock=clock)
    good = _texts(index)

    path.write_text("---\nid: [broken\n---\n", encoding="utf-8")
    _bump_mtime(path)
    clock.now += 6
    snap = index.snapshot()
    assert snap is not None
    assert sorted(c.text for c in snap.chunks) == sorted(good)
    messages = [(p.file, p.severity) for p in snap.problems]
    assert ("a.md", "error") in messages
    assert ("a.md", "warning") in messages  # "serving the last good version"


def test_whole_load_failure_keeps_previous_snapshot(kb_dir: Path, write_doc: WriteDoc) -> None:
    write_doc("a.md", make_doc("a"))
    clock = FakeClock()
    index = KnowledgeIndex(kb_dir, reload_check_seconds=5, clock=clock)
    first = index.snapshot()
    shutil.rmtree(kb_dir)
    clock.now += 6
    assert index.snapshot() is first
    assert index.last_error is not None


def test_zero_interval_disables_auto_reload(kb_dir: Path, write_doc: WriteDoc) -> None:
    path = write_doc("a.md", make_doc("a"))
    clock = FakeClock()
    index = KnowledgeIndex(kb_dir, reload_check_seconds=0, clock=clock)
    first = index.snapshot()
    path.write_text(make_doc("a").replace("overview text", "changed"), encoding="utf-8")
    _bump_mtime(path)
    clock.now += 10_000
    assert index.snapshot() is first
    forced = index.reload()  # admin reload still works
    assert forced is not None and forced.version == 2


def test_missing_directory_at_startup(tmp_path: Path) -> None:
    index = KnowledgeIndex(tmp_path / "missing", reload_check_seconds=5)
    assert index.snapshot() is None
    assert not index.loaded
    assert index.last_error is not None
