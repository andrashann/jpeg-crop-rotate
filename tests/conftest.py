import itertools
import os
import sys
from pathlib import Path

# Must happen before PyQt6 is imported anywhere (app.py included) — Qt reads
# this when the platform plugin loads. Leave it alone if the environment
# already set something (e.g. a real display) so tests can still be watched
# interactively if desired.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PIL import Image
from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import QApplication

import app


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """A single QApplication for the whole test session (Qt only allows one
    per process); autouse so no test needs to request it explicitly."""
    existing = QApplication.instance()
    yield existing if existing is not None else QApplication([])


@pytest.fixture(scope="session")
def jpegtran_path():
    path = app.find_jpegtran()
    if path is None:
        pytest.skip("jpegtran not found on PATH")
    return path


def write_test_image(path: Path, size: tuple[int, int] = (200, 130)) -> Path:
    """A small JPEG with a distinguishable gradient, so crop regions and
    rotations can be verified by content/dimensions, not just 'it ran'."""
    img = Image.new("RGB", size, color=(255, 0, 0))
    for x in range(size[0]):
        for y in range(size[1]):
            img.putpixel((x, y), (x % 256, y % 256, 0))
    img.save(path, quality=90)
    return path


@pytest.fixture
def sample_jpeg(tmp_path) -> Path:
    """A single fresh 200x130 test JPEG, unique to this test's tmp_path —
    safe to mutate (rotate/crop/overwrite) without affecting other tests."""
    return write_test_image(tmp_path / "sample.jpg")


@pytest.fixture
def make_jpeg(tmp_path):
    """Factory for additional test JPEGs, e.g. for multi-file batch tests.
    Pass a bare name to place it in tmp_path, or a full path (parent dirs
    are created for you) to test files living in different folders."""
    counter = itertools.count()

    def _make(path: Path | str | None = None, size: tuple[int, int] = (200, 130)) -> Path:
        if path is None:
            path = tmp_path / f"extra{next(counter)}.jpg"
        else:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
        return write_test_image(path, size)

    return _make


@pytest.fixture
def command_log():
    return app.CommandLog()


@pytest.fixture
def session_state():
    return app.SessionState()


def synth_drop(widget, paths: list[Path]) -> None:
    """Simulate an OS-level file drag-and-drop of `paths` onto `widget`,
    exactly like a real drag from Finder/Explorer would deliver."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    pos = QPointF(10, 10)
    enter_event = QDragEnterEvent(
        pos.toPoint(), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
    )
    widget.dragEnterEvent(enter_event)
    drop_event = QDropEvent(
        pos, Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier
    )
    widget.dropEvent(drop_event)
