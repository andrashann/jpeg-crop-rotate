# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "PyQt6",
#   "piexif",
# ]
# ///
"""Lossless JPEG rotate/crop GUI built on top of jpegtran."""

from __future__ import annotations

import atexit
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import piexif
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QImage,
    QImageReader,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_ICON_DIR = Path(__file__).resolve().parent / "icons"


def icon(name: str) -> QIcon:
    return QIcon(str(_ICON_DIR / name))


CROP_SNAP = 16  # px; a multiple of every jpegtran iMCU size (8, 16, 16x8, 8x16)
JPEG_EXTENSIONS = {".jpg", ".jpeg"}

ROTATE_TRANSFORMS: dict[str, list[str]] = {
    "Rotate 90° CW": ["-rotate", "90"],
    "Rotate 90° CCW": ["-rotate", "270"],
    "Rotate 180°": ["-rotate", "180"],
    "Flip horizontal": ["-flip", "horizontal"],
    "Flip vertical": ["-flip", "vertical"],
    "Transpose": ["-transpose"],
    "Transverse": ["-transverse"],
}


# --------------------------------------------------------------------------
# jpegtran plumbing
# --------------------------------------------------------------------------


def find_jpegtran() -> str | None:
    return shutil.which("jpegtran")


@dataclass
class JpegtranResult:
    ok: bool
    command: list[str]
    stderr: str = ""


def run_jpegtran(jpegtran_path: str, args: list[str], infile: Path, outfile: Path) -> JpegtranResult:
    command = [jpegtran_path, "-copy", "all", *args, "-outfile", str(outfile), str(infile)]
    proc = subprocess.run(command, capture_output=True, text=True)
    return JpegtranResult(ok=proc.returncode == 0, command=command, stderr=proc.stderr.strip())


def reset_exif_orientation(path: Path) -> None:
    """Reset EXIF Orientation to 1 (normal) so viewers don't double-rotate."""
    try:
        exif_dict = piexif.load(str(path))
        orientation = exif_dict.get("0th", {}).get(piexif.ImageIFD.Orientation)
        if orientation not in (None, 1):
            exif_dict["0th"][piexif.ImageIFD.Orientation] = 1
            piexif.insert(piexif.dump(exif_dict), str(path))
    except Exception:
        pass  # best-effort; missing/unreadable EXIF is not a failure


def build_postfix_path(infile: Path, postfix: str, target_dir: Path | None = None) -> Path:
    directory = target_dir if target_dir is not None else infile.parent
    return directory / (infile.stem + postfix + infile.suffix)


def is_jpeg(path: Path) -> bool:
    return path.suffix.lower() in JPEG_EXTENSIONS


_HOME = str(Path.home())
_HOME_PREFIX = _HOME + os.sep


def display_path(path: Path | str) -> str:
    """Shorten a path for display by replacing the user's home dir with ~."""
    s = str(path)
    if s == _HOME:
        return "~"
    if s.startswith(_HOME_PREFIX):
        return "~" + os.sep + s[len(_HOME_PREFIX):]
    return s


# --------------------------------------------------------------------------
# Shared log
# --------------------------------------------------------------------------


class CommandLog(QWidget):
    """Not a visible widget; just a QObject-ish signal hub for logged commands."""

    logged = pyqtSignal(str)

    def log(self, command: list[str], ok: bool, stderr: str = "") -> None:
        status = "OK" if ok else "FAILED"
        line = f"[{status}] {shlex.join(display_path(part) for part in command)}"
        if stderr:
            line += f"\n         stderr: {stderr}"
        self.logged.emit(line)


class SessionState:
    """Holds cross-tab state that only lives for the current app run."""

    def __init__(self) -> None:
        self.skip_overwrite_confirm = False


def confirm_overwrite(parent: QWidget, session_state: SessionState, message: str) -> bool:
    """Ask before an in-place overwrite, honoring a session-scoped "don't remind me" opt-out."""
    if session_state.skip_overwrite_confirm:
        return True

    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Overwrite original?")
    box.setText(message)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    checkbox = QCheckBox("Don't remind me again this session")
    box.setCheckBox(checkbox)

    result = box.exec()
    proceed = result == QMessageBox.StandardButton.Yes
    if proceed and checkbox.isChecked():
        session_state.skip_overwrite_confirm = True
    return proceed


def confirm_existing_target(parent: QWidget, target_path: Path) -> tuple[str, bool]:
    """Ask what to do when a batch output file already exists.

    Returns (action, apply_to_all) where action is "skip" or "overwrite".
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("File already exists")
    box.setText(f"{display_path(target_path)} already exists.\nWhat would you like to do?")
    skip_button = box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
    overwrite_button = box.addButton("Overwrite", QMessageBox.ButtonRole.DestructiveRole)
    box.setDefaultButton(skip_button)
    checkbox = QCheckBox("Apply to all remaining files in this batch")
    box.setCheckBox(checkbox)

    box.exec()
    action = "overwrite" if box.clickedButton() is overwrite_button else "skip"
    return action, checkbox.isChecked()


# --------------------------------------------------------------------------
# Reusable widgets
# --------------------------------------------------------------------------


class SaveModeWidget(QGroupBox):
    def __init__(self, default_postfix: str, parent: QWidget | None = None) -> None:
        super().__init__("Output settings", parent)
        self._target_folder: Path | None = None
        self._target_folder_user_set = False

        outer = QVBoxLayout(self)

        mode_row = QHBoxLayout()
        self.inplace_radio = QRadioButton("Save in place")
        self.postfix_radio = QRadioButton("Save with postfix:")
        self.postfix_radio.setChecked(True)
        self.postfix_field = QLineEdit(default_postfix)
        self.postfix_field.setFixedWidth(80)

        mode_row.addWidget(self.inplace_radio)
        mode_row.addWidget(self.postfix_radio)
        mode_row.addWidget(self.postfix_field)
        mode_row.addStretch(1)
        outer.addLayout(mode_row)

        # Only meaningful in postfix mode: each file can go back into its own
        # source folder (the default — important when the batch mixes files
        # from different folders) or all files can be redirected to one
        # chosen folder.
        folder_row = QHBoxLayout()
        self.source_folder_radio = QRadioButton("Save to source folder")
        self.source_folder_radio.setChecked(True)
        self.target_folder_radio = QRadioButton("Save to folder:")
        self.target_folder_field = QLineEdit()
        self.target_folder_field.setReadOnly(True)
        self.target_folder_browse_button = QPushButton("Browse…")
        self.target_folder_browse_button.clicked.connect(self._browse_target_folder)
        folder_row.addWidget(self.source_folder_radio)
        folder_row.addWidget(self.target_folder_radio)
        folder_row.addWidget(self.target_folder_field, 1)
        folder_row.addWidget(self.target_folder_browse_button)
        outer.addLayout(folder_row)

        # Explicit groups: all four radios share this widget as their parent,
        # and Qt auto-exclusive-groups same-parent radios together by default
        # — without this they'd all fight over a single selection instead of
        # forming two independent pairs.
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self.inplace_radio)
        self._mode_group.addButton(self.postfix_radio)
        self._folder_group = QButtonGroup(self)
        self._folder_group.addButton(self.source_folder_radio)
        self._folder_group.addButton(self.target_folder_radio)

        self.postfix_radio.toggled.connect(self._update_enabled_states)
        self.target_folder_radio.toggled.connect(self._on_target_folder_radio_toggled)
        self._update_enabled_states()

    def _update_enabled_states(self) -> None:
        postfix_active = self.postfix_radio.isChecked()
        self.postfix_field.setEnabled(postfix_active)
        self.source_folder_radio.setEnabled(postfix_active)
        self.target_folder_radio.setEnabled(postfix_active)
        target_folder_active = postfix_active and self.target_folder_radio.isChecked()
        self.target_folder_field.setEnabled(target_folder_active)
        self.target_folder_browse_button.setEnabled(target_folder_active)

    def _on_target_folder_radio_toggled(self, checked: bool) -> None:
        if checked:
            # Deliberately switching to a fixed folder is itself a choice;
            # stop silently overwriting it with later per-file defaults.
            self._target_folder_user_set = True
        self._update_enabled_states()

    def _browse_target_folder(self) -> None:
        start_dir = str(self._target_folder) if self._target_folder else str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Select target folder", start_dir)
        if chosen:
            self._target_folder = Path(chosen)
            self._target_folder_user_set = True
            self.target_folder_field.setText(display_path(self._target_folder))

    def set_default_target_folder(self, folder: Path) -> None:
        """Auto-suggest a target folder, unless the user already picked one explicitly."""
        if self._target_folder_user_set:
            return
        self._target_folder = folder
        self.target_folder_field.setText(display_path(folder))

    def target_folder(self) -> Path | None:
        return self._target_folder

    def is_inplace(self) -> bool:
        return self.inplace_radio.isChecked()

    def postfix(self) -> str:
        return self.postfix_field.text() or "_out"

    def output_path_for(self, infile: Path) -> Path:
        if self.is_inplace():
            return infile
        target = self._target_folder if self.target_folder_radio.isChecked() else None
        return build_postfix_path(infile, self.postfix(), target)


class DropZone(QFrame):
    """Drag-and-drop (and click-to-browse) target for JPEG files.

    filesDropped emits accepted paths, whether they arrived via a drag-and-drop
    or via the click-to-browse file dialog.
    """

    filesDropped = pyqtSignal(list)

    def __init__(self, hint: str, multi: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.multi = multi
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(70)
        self.setStyleSheet(
            "QFrame { border: 2px dashed #888; border-radius: 6px; }"
        )
        layout = QVBoxLayout(self)
        self.label = QLabel(f"{hint}\n(or click to browse)")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        self._accept(paths)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def _browse(self) -> None:
        if self.multi:
            paths, _ = QFileDialog.getOpenFileNames(self, "Select JPEG files", "", "JPEG files (*.jpg *.jpeg)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select a JPEG file", "", "JPEG files (*.jpg *.jpeg)")
            paths = [path] if path else []
        self._accept([Path(p) for p in paths])

    def _accept(self, paths: list[Path]) -> None:
        jpegs = [p for p in paths if is_jpeg(p)]
        rejected = len(paths) - len(jpegs)
        if jpegs:
            self.filesDropped.emit(jpegs)
        if rejected:
            QMessageBox.warning(
                self, "Unsupported files",
                f"Ignored {rejected} file(s) that aren't .jpg/.jpeg.",
            )


# --------------------------------------------------------------------------
# Rotate tab
# --------------------------------------------------------------------------


class RotateTab(QWidget):
    def __init__(
        self,
        jpegtran_path: str,
        command_log: CommandLog,
        session_state: SessionState,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.jpegtran_path = jpegtran_path
        self.command_log = command_log
        self.session_state = session_state
        self.files: list[Path] = []

        layout = QVBoxLayout(self)

        self.drop_zone = DropZone("Drop JPEG files here (adds to the batch below)", multi=True)
        self.drop_zone.filesDropped.connect(self.add_files)
        layout.addWidget(self.drop_zone)

        self.THUMBNAIL_SIZE = 60

        self.file_table = QTableWidget(0, 3)
        self.file_table.setIconSize(QSize(self.THUMBNAIL_SIZE, self.THUMBNAIL_SIZE))
        self.file_table.horizontalHeader().hide()
        self.file_table.verticalHeader().hide()
        self.file_table.verticalHeader().setDefaultSectionSize(self.THUMBNAIL_SIZE + 8)
        self.file_table.setShowGrid(False)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setColumnWidth(0, self.THUMBNAIL_SIZE + 16)
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.file_table.setColumnWidth(2, 36)
        layout.addWidget(self.file_table)

        list_buttons_row = QHBoxLayout()
        self.add_button = QPushButton("Add more files")
        self.add_button.clicked.connect(self.browse_files)
        self.remove_button = QPushButton("Remove selected")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("Clear list")
        self.clear_button.clicked.connect(self.clear_files)
        list_buttons_row.addStretch(1)
        list_buttons_row.addWidget(self.add_button)
        list_buttons_row.addWidget(self.remove_button)
        list_buttons_row.addWidget(self.clear_button)
        layout.addLayout(list_buttons_row)

        self.transform_combo = QComboBox()
        self.transform_combo.addItems(ROTATE_TRANSFORMS.keys())
        layout.addWidget(QLabel("Transformation:"))
        layout.addWidget(self.transform_combo)
        layout.addSpacing(12)

        self.save_mode = SaveModeWidget(default_postfix="_tr")
        layout.addWidget(self.save_mode)

        self.progress_label = QLabel("")
        layout.addWidget(self.progress_label)

        self.process_selected_button = QPushButton("Process selected files")
        self.process_selected_button.clicked.connect(self.process_selected_files)
        layout.addWidget(self.process_selected_button)

        self.process_button = QPushButton("Process all files")
        self.process_button.clicked.connect(self.process_files)
        layout.addWidget(self.process_button)

    def browse_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select JPEG files", "", "JPEG files (*.jpg *.jpeg)")
        if paths:
            self.add_files([Path(p) for p in paths])

    def add_files(self, paths: list[Path]) -> None:
        rejected = []
        for p in paths:
            if p in self.files:
                continue
            # The drop zone only filtered by .jpg/.jpeg extension, which
            # doesn't guarantee the content is actually a JPEG (e.g. a
            # renamed text file) -- decode it for real here, since we need
            # the thumbnail anyway, and skip files that fail.
            thumbnail = self._make_thumbnail(p, self.THUMBNAIL_SIZE)
            if thumbnail.isNull():
                rejected.append(p)
                continue
            self.files.append(p)
            self._add_row(p, thumbnail)
        self._update_default_target_folder()
        if rejected:
            names = "\n".join(display_path(p) for p in rejected)
            QMessageBox.warning(
                self,
                "Couldn't read file(s)",
                f"Ignored {len(rejected)} file(s) that aren't valid JPEG images:\n{names}",
            )

    def _add_row(self, path: Path, thumbnail: QPixmap) -> None:
        row = self.file_table.rowCount()
        self.file_table.insertRow(row)

        thumb_item = QTableWidgetItem()
        thumb_item.setIcon(QIcon(thumbnail))
        thumb_item.setFlags(thumb_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.file_table.setItem(row, 0, thumb_item)

        path_item = QTableWidgetItem(display_path(path))
        path_item.setFlags(path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.file_table.setItem(row, 1, path_item)

        delete_button = QToolButton()
        delete_button.setIcon(icon("lc_cancel.svg"))
        delete_button.setIconSize(QSize(14, 14))
        delete_button.setAutoRaise(True)
        delete_button.setToolTip("Remove from list")
        delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        delete_button.clicked.connect(lambda checked=False, b=delete_button: self._delete_row_for_button(b))
        self.file_table.setCellWidget(row, 2, delete_button)

    @staticmethod
    def _make_thumbnail(path: Path, size: int = 40) -> QPixmap:
        reader = QImageReader(str(path))
        orig_size = reader.size()
        if orig_size.isValid() and orig_size.width() > 0 and orig_size.height() > 0:
            reader.setScaledSize(orig_size.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        return QPixmap.fromImage(image) if not image.isNull() else QPixmap()

    def _delete_row_for_button(self, button: QToolButton) -> None:
        for row in range(self.file_table.rowCount()):
            if self.file_table.cellWidget(row, 2) is button:
                self.file_table.removeRow(row)
                if 0 <= row < len(self.files):
                    del self.files[row]
                break
        self._update_default_target_folder()

    def remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.file_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.file_table.removeRow(row)
            if 0 <= row < len(self.files):
                del self.files[row]
        self._update_default_target_folder()

    def _update_default_target_folder(self) -> None:
        if self.files:
            self.save_mode.set_default_target_folder(self.files[0].parent)

    def clear_files(self) -> None:
        self.files.clear()
        self.file_table.setRowCount(0)
        self.progress_label.setText("")

    def _set_list_controls_enabled(self, enabled: bool) -> None:
        self.add_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        for row in range(self.file_table.rowCount()):
            widget = self.file_table.cellWidget(row, 2)
            if widget is not None:
                widget.setEnabled(enabled)

    def process_files(self) -> None:
        if not self.files:
            QMessageBox.information(self, "No files", "Drop or browse for JPEG files first.")
            return
        self._run_batch(list(self.files))

    def process_selected_files(self) -> None:
        rows = sorted({idx.row() for idx in self.file_table.selectedIndexes()})
        selected = [self.files[row] for row in rows if 0 <= row < len(self.files)]
        if not selected:
            QMessageBox.information(self, "No selection", "Select one or more files in the list first.")
            return
        self._run_batch(selected)

    def _run_batch(self, files_to_process: list[Path]) -> None:
        if self.save_mode.is_inplace():
            if not confirm_overwrite(
                self,
                self.session_state,
                f"This will overwrite {len(files_to_process)} original file(s) in place. Continue?",
            ):
                return

        transform_args = ROTATE_TRANSFORMS[self.transform_combo.currentText()]
        total = len(files_to_process)
        self.process_button.setEnabled(False)
        self.process_selected_button.setEnabled(False)
        self._set_list_controls_enabled(False)

        # Remembers a "skip"/"overwrite" choice for the rest of this batch
        # once the user checks "Apply to all remaining files"; None means
        # ask again for the next file whose target already exists.
        remembered_existing_action: str | None = None

        # files_to_process is already a standalone list (not self.files itself),
        # so it's safe to iterate even though the controls that could mutate
        # self.files are disabled for the duration anyway.
        for i, infile in enumerate(files_to_process, start=1):
            self.progress_label.setText(f"{i}/{total} processed")
            QApplication.processEvents()

            if not self.save_mode.is_inplace():
                target = self.save_mode.output_path_for(infile)
                if target.exists():
                    if remembered_existing_action is not None:
                        action = remembered_existing_action
                    else:
                        action, apply_to_all = confirm_existing_target(self, target)
                        if apply_to_all:
                            remembered_existing_action = action
                    if action == "skip":
                        self._mark_skipped(infile)
                        continue

            self._process_one(infile, transform_args)

        self.progress_label.setText(f"{total}/{total} processed")
        self.process_button.setEnabled(True)
        self.process_selected_button.setEnabled(True)
        self._set_list_controls_enabled(True)

    def _process_one(self, infile: Path, transform_args: list[str]) -> None:
        final_outfile = self.save_mode.output_path_for(infile)
        write_target = final_outfile
        tmp_path: Path | None = None
        if self.save_mode.is_inplace():
            fd, tmp_name = tempfile.mkstemp(suffix=infile.suffix, dir=infile.parent)
            os.close(fd)
            tmp_path = Path(tmp_name)
            write_target = tmp_path

        result = run_jpegtran(self.jpegtran_path, transform_args, infile, write_target)

        if result.ok:
            reset_exif_orientation(write_target)
            if tmp_path is not None:
                tmp_path.replace(final_outfile)
                self._refresh_thumbnail(infile)  # in-place: infile's own content just changed
            self._mark_status(infile, True)
        else:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            self._mark_status(infile, False, result.stderr)

        self.command_log.log(result.command, result.ok, result.stderr)

    def _mark_status(self, infile: Path, ok: bool, stderr: str = "") -> None:
        try:
            row = self.files.index(infile)
        except ValueError:
            return  # row was removed (e.g. mid-batch) before we could update it
        item = self.file_table.item(row, 1)
        if item is None:
            return
        if ok:
            item.setText(f"{display_path(infile)}  —  OK")
            item.setForeground(QColor("#2e7d32"))
        else:
            item.setText(f"{display_path(infile)}  —  FAILED: {stderr}")
            item.setForeground(QColor("#c62828"))

    def _mark_skipped(self, infile: Path) -> None:
        try:
            row = self.files.index(infile)
        except ValueError:
            return
        item = self.file_table.item(row, 1)
        if item is None:
            return
        item.setText(f"{display_path(infile)}  —  Skipped (target exists)")
        item.setForeground(QColor("#757575"))

    def _refresh_thumbnail(self, path: Path) -> None:
        try:
            row = self.files.index(path)
        except ValueError:
            return
        item = self.file_table.item(row, 0)
        if item is None:
            return
        item.setIcon(QIcon(self._make_thumbnail(path, self.THUMBNAIL_SIZE)))


# --------------------------------------------------------------------------
# Crop tab
# --------------------------------------------------------------------------


class CropToolbar(QWidget):
    """Floating, icon-only overlay toolbar anchored to the image viewer's
    top-left corner: crop/move tools, clear selection, zoom, and rotate."""

    toolChanged = pyqtSignal(str)  # "crop" | "move"
    clearRequested = pyqtSignal()
    zoomInRequested = pyqtSignal()
    zoomOutRequested = pyqtSignal()
    resetZoomRequested = pyqtSignal()
    rotateCCWRequested = pyqtSignal()
    rotateCWRequested = pyqtSignal()

    ICON_SIZE = QSize(20, 20)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("cropToolbar")
        # Plain QWidgets don't paint their QSS background by default (only
        # QFrame/QLabel/etc. do) — without this the rgba background below is
        # silently ignored and the toolbar is invisible over dark images.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "#cropToolbar { background-color: rgba(255, 255, 255, 165); border-radius: 6px; }"
            "#cropToolbar QToolButton { border: none; padding: 3px; border-radius: 4px; }"
            "#cropToolbar QToolButton:checked { background-color: rgba(0, 0, 0, 45); }"
            "#cropToolbar QToolButton:hover:!disabled { background-color: rgba(0, 0, 0, 20); }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        self.crop_button = self._make_button("sc_crop.svg", "Crop (select area)", checkable=True)
        self.move_button = self._make_button("lc_arrowshapes.quad-arrow.svg", "Move (pan view)", checkable=True)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_group.addButton(self.crop_button)
        self._tool_group.addButton(self.move_button)
        self.crop_button.setChecked(True)
        self.crop_button.toggled.connect(lambda checked: checked and self.toolChanged.emit("crop"))
        self.move_button.toggled.connect(lambda checked: checked and self.toolChanged.emit("move"))

        self.clear_button = self._make_button("lc_cancel.svg", "Clear selection")
        self.clear_button.clicked.connect(self.clearRequested)

        self.zoom_in_button = self._make_button("lc_zoomin.svg", "Zoom in")
        self.zoom_in_button.clicked.connect(self.zoomInRequested)
        self.zoom_out_button = self._make_button("lc_zoomout.svg", "Zoom out")
        self.zoom_out_button.clicked.connect(self.zoomOutRequested)
        self.reset_zoom_button = self._make_button("lc_view100.svg", "Reset zoom (1:1)")
        self.reset_zoom_button.clicked.connect(self.resetZoomRequested)

        self.rotate_ccw_button = self._make_button("lc_rotateleft.svg", "Rotate left 90°")
        self.rotate_ccw_button.clicked.connect(self.rotateCCWRequested)
        self.rotate_cw_button = self._make_button("lc_rotateright.svg", "Rotate right 90°")
        self.rotate_cw_button.clicked.connect(self.rotateCWRequested)

        layout.addWidget(self.crop_button)
        layout.addWidget(self.clear_button)
        layout.addSpacing(10)
        layout.addWidget(self.zoom_in_button)
        layout.addWidget(self.zoom_out_button)
        layout.addWidget(self.move_button)
        layout.addWidget(self.reset_zoom_button)
        layout.addSpacing(10)
        layout.addWidget(self.rotate_ccw_button)
        layout.addWidget(self.rotate_cw_button)

        self.set_image_loaded(False)

    def _make_button(self, icon_name: str, tooltip: str, checkable: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setIcon(icon(icon_name))
        button.setIconSize(self.ICON_SIZE)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setToolTip(tooltip)
        button.setCheckable(checkable)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def set_zoom_state(self, zoom: int) -> None:
        self.zoom_in_button.setEnabled(zoom < 32)
        self.zoom_out_button.setEnabled(zoom > 1)
        self.reset_zoom_button.setEnabled(zoom > 1)
        self.move_button.setEnabled(zoom > 1)

    def set_has_selection(self, has_selection: bool) -> None:
        self.clear_button.setEnabled(has_selection)

    def set_tool(self, tool: str) -> None:
        (self.crop_button if tool == "crop" else self.move_button).setChecked(True)

    def set_image_loaded(self, loaded: bool) -> None:
        for button in (
            self.crop_button, self.move_button, self.clear_button,
            self.zoom_in_button, self.zoom_out_button, self.reset_zoom_button,
            self.rotate_ccw_button, self.rotate_cw_button,
        ):
            button.setEnabled(loaded)
        if loaded:
            self.set_zoom_state(1)
            self.set_has_selection(False)


class ImageCropView(QWidget):
    """Displays a JPEG and lets the user drag out a crop rectangle.

    The rectangle's origin is kept snapped to a CROP_SNAP px grid in
    original-image pixel coordinates, since jpegtran's -crop silently
    rounds the offset down to the nearest iMCU boundary.

    A floating CropToolbar overlay (top-left corner) provides a crop/move
    tool switch, selection clearing, zoom (as a multiplier on top of the
    normal fit-to-window scale, so the responsive base sizing is untouched),
    and rotate. Zoom+pan is a "virtual camera": the widget's own size never
    changes, only what portion of the image is sampled into it.
    """

    selectionChanged = pyqtSignal(object)  # QRect (image coords) or None

    HANDLE = 8
    MAX_ZOOM = 32

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._image: QImage | None = None
        self._crop: QRect | None = None  # image pixel coords
        self._scale = 1.0
        self._offset = QPointF(0.0, 0.0)
        self._zoom = 1  # 1..MAX_ZOOM, powers of two; multiplies the fit-to-window base scale
        self._pan = QPointF(0.0, 0.0)  # display-pixel scroll offset while zoomed in
        self._tool = "crop"  # "crop" | "move"
        self._drag_mode: str | None = None  # "new" | "move_rect" | "resize:*"
        self._drag_anchor_image = QPoint(0, 0)
        self._drag_start_crop: QRect | None = None
        self._pan_drag_start_mouse: QPoint | None = None
        self._pan_drag_start_pan: QPointF | None = None
        self._wheel_accum = 0  # accumulated angleDelta; smooths out trackpad scroll ticks
        self.rotation_degrees = 0  # cumulative CW rotation (0/90/180/270) applied since load

        self.toolbar = CropToolbar(self)
        self.toolbar.toolChanged.connect(self._set_tool)
        self.toolbar.clearRequested.connect(self.clear_selection)
        self.toolbar.zoomInRequested.connect(self._zoom_in)
        self.toolbar.zoomOutRequested.connect(self._zoom_out)
        self.toolbar.resetZoomRequested.connect(self._reset_zoom)
        self.toolbar.rotateCCWRequested.connect(lambda: self.rotate_image(clockwise=False))
        self.toolbar.rotateCWRequested.connect(lambda: self.rotate_image(clockwise=True))
        self.selectionChanged.connect(self._sync_toolbar)
        self._reposition_toolbar()

    def load_image(self, path: Path) -> bool:
        """Returns False if the file couldn't be decoded (corrupt, an
        unsupported format, or too large for Qt's image allocation limit)."""
        self._image = QImage(str(path))
        if self._image.isNull():
            self.toolbar.set_image_loaded(False)
            self.update()
            return False
        self._crop = None
        self.rotation_degrees = 0
        self._zoom = 1
        self._pan = QPointF(0.0, 0.0)
        self._wheel_accum = 0
        self._tool = "crop"
        self.toolbar.set_tool("crop")
        self.toolbar.set_image_loaded(True)
        self._recompute_view()
        self.update()
        self.selectionChanged.emit(None)
        return True

    def has_image(self) -> bool:
        return self._image is not None and not self._image.isNull()

    def crop_rect(self) -> QRect | None:
        return self._crop

    def clear(self) -> None:
        self._image = None
        self._crop = None
        self.rotation_degrees = 0
        self._zoom = 1
        self._pan = QPointF(0.0, 0.0)
        self._wheel_accum = 0
        self._tool = "crop"
        self.toolbar.set_tool("crop")
        self.toolbar.set_image_loaded(False)
        self.update()
        self.selectionChanged.emit(None)

    def clear_selection(self) -> None:
        """Drop the current crop rectangle only; keep the loaded image/rotation."""
        self._crop = None
        self.update()
        self.selectionChanged.emit(None)

    def rotate_image(self, clockwise: bool) -> None:
        """Rotate the preview (and any active selection) 90 deg; tracks the
        cumulative rotation so the same transform can be applied losslessly
        via jpegtran at save time."""
        if not self.has_image():
            return
        old_w, old_h = self._image.width(), self._image.height()
        self._image = self._image.transformed(QTransform().rotate(90 if clockwise else -90))
        if self._crop is not None:
            self._crop = self._rotate_rect(self._crop, clockwise, old_w, old_h)
        self.rotation_degrees = (self.rotation_degrees + (90 if clockwise else -90)) % 360
        # Zoom/pan don't carry a meaningful interpretation across a dimension
        # swap, so reset the camera along with the rotation.
        self._zoom = 1
        self._pan = QPointF(0.0, 0.0)
        self._recompute_view()
        self.update()
        self.selectionChanged.emit(self._crop)

    @staticmethod
    def _rotate_rect(r: QRect, clockwise: bool, old_w: int, old_h: int) -> QRect:
        x, y, w, h = r.x(), r.y(), r.width(), r.height()
        if clockwise:
            return QRect(old_h - (y + h), x, h, w)
        return QRect(y, old_w - (x + w), h, w)

    # -- tool / zoom / pan --------------------------------------------------

    def _set_tool(self, tool: str) -> None:
        self._tool = tool
        self._drag_mode = None
        self._pan_drag_start_mouse = None

    def _zoom_in(self) -> None:
        self._set_zoom(min(self._zoom * 2, self.MAX_ZOOM))

    def _zoom_out(self) -> None:
        self._set_zoom(max(self._zoom // 2, 1))

    def _reset_zoom(self) -> None:
        self._set_zoom(1)

    def _set_zoom(self, new_zoom: int, anchor: QPoint | None = None) -> None:
        """Change zoom, keeping the content under `anchor` (widget coords) in
        place — defaults to the viewport center (used by the toolbar
        buttons); wheelEvent passes the cursor position instead."""
        if not self.has_image() or new_zoom == self._zoom:
            return
        avail_w, avail_h = self.width(), self.height()
        if anchor is None:
            anchor = QPoint(avail_w // 2, avail_h // 2)
        focus = self._to_image(anchor)

        self._zoom = new_zoom
        iw, ih = self._image.width(), self._image.height()
        base_scale = min(avail_w / iw, avail_h / ih, 1.0) if avail_w and avail_h else 1.0
        effective_scale = base_scale * self._zoom
        self._pan = QPointF(
            focus.x() * effective_scale - anchor.x(),
            focus.y() * effective_scale - anchor.y(),
        )
        if self._zoom == 1 and self._tool == "move":
            self._set_tool("crop")
            self.toolbar.set_tool("crop")

        self._recompute_view()
        self.toolbar.set_zoom_state(self._zoom)
        self.update()

    def _sync_toolbar(self, *_args) -> None:
        self.toolbar.set_zoom_state(self._zoom)
        has_selection = bool(self._crop and self._crop.width() > 0 and self._crop.height() > 0)
        self.toolbar.set_has_selection(has_selection)

    def _reposition_toolbar(self) -> None:
        self.toolbar.adjustSize()
        margin = 8
        self.toolbar.move(margin, margin)

    # -- coordinate mapping -------------------------------------------------

    def _recompute_view(self) -> None:
        if not self.has_image():
            return
        iw, ih = self._image.width(), self._image.height()
        avail_w, avail_h = self.width(), self.height()
        if not avail_w or not avail_h:
            return
        base_scale = min(avail_w / iw, avail_h / ih, 1.0)
        effective_scale = base_scale * self._zoom
        disp_w, disp_h = iw * effective_scale, ih * effective_scale

        if disp_w <= avail_w:
            offset_x = (avail_w - disp_w) / 2
            self._pan.setX(0.0)
        else:
            max_pan_x = disp_w - avail_w
            self._pan.setX(max(0.0, min(self._pan.x(), max_pan_x)))
            offset_x = -self._pan.x()

        if disp_h <= avail_h:
            offset_y = (avail_h - disp_h) / 2
            self._pan.setY(0.0)
        else:
            max_pan_y = disp_h - avail_h
            self._pan.setY(max(0.0, min(self._pan.y(), max_pan_y)))
            offset_y = -self._pan.y()

        self._scale = effective_scale
        self._offset = QPointF(offset_x, offset_y)

    def _to_image(self, widget_pt: QPoint) -> QPoint:
        x = (widget_pt.x() - self._offset.x()) / self._scale
        y = (widget_pt.y() - self._offset.y()) / self._scale
        iw, ih = self._image.width(), self._image.height()
        return QPoint(max(0, min(iw, round(x))), max(0, min(ih, round(y))))

    def _to_image_f(self, widget_pt: QPointF) -> QPointF:
        """Unrounded, unclamped inverse mapping — used for the visible-region
        source rect so partial/off-image viewport edges compute correctly."""
        x = (widget_pt.x() - self._offset.x()) / self._scale
        y = (widget_pt.y() - self._offset.y()) / self._scale
        return QPointF(x, y)

    def _to_widget_rect(self, r: QRect) -> QRect:
        x = self._offset.x() + r.x() * self._scale
        y = self._offset.y() + r.y() * self._scale
        w = r.width() * self._scale
        h = r.height() * self._scale
        return QRect(int(x), int(y), int(w), int(h))

    @staticmethod
    def _snap_down(value: int) -> int:
        return (value // CROP_SNAP) * CROP_SNAP

    def _normalize(self, r: QRect) -> QRect:
        r = r.normalized()
        iw, ih = self._image.width(), self._image.height()
        x = max(0, min(self._snap_down(r.x()), iw - CROP_SNAP if iw >= CROP_SNAP else 0))
        y = max(0, min(self._snap_down(r.y()), ih - CROP_SNAP if ih >= CROP_SNAP else 0))
        x2 = max(x + 1, min(r.x() + r.width(), iw))
        y2 = max(y + 1, min(r.y() + r.height(), ih))
        return QRect(QPoint(x, y), QPoint(x2, y2))

    # -- painting -------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._recompute_view()
        self._reposition_toolbar()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#222"))
        if not self.has_image():
            painter.setPen(QColor("#aaa"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop a JPEG file above")
            return

        self._recompute_view()
        pixmap = QPixmap.fromImage(self._image)
        iw, ih = self._image.width(), self._image.height()
        # Only sample the visible portion of the (possibly zoomed-in-past-the-
        # viewport) image, so painting stays cheap regardless of zoom level.
        visible_tl = self._to_image_f(QPointF(0, 0))
        visible_br = self._to_image_f(QPointF(self.width(), self.height()))
        source_rect = QRectF(visible_tl, visible_br).intersected(QRectF(0, 0, iw, ih))
        target = self._to_widget_rect(QRect(0, 0, iw, ih)).intersected(self.rect())
        if not source_rect.isEmpty() and not target.isEmpty():
            painter.drawPixmap(QRectF(target), pixmap, source_rect)

        if self._crop is not None:
            crop_widget_rect = self._to_widget_rect(self._crop)
            overlay = QColor(0, 0, 0, 140)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(overlay)
            painter.drawRect(target.x(), target.y(), target.width(), crop_widget_rect.y() - target.y())
            painter.drawRect(target.x(), crop_widget_rect.bottom(), target.width(), target.bottom() - crop_widget_rect.bottom())
            painter.drawRect(target.x(), crop_widget_rect.y(), crop_widget_rect.x() - target.x(), crop_widget_rect.height())
            painter.drawRect(crop_widget_rect.right(), crop_widget_rect.y(), target.right() - crop_widget_rect.right(), crop_widget_rect.height())

            painter.setPen(QPen(QColor("#4da6ff"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(crop_widget_rect)

            painter.setBrush(QColor("#4da6ff"))
            for corner in self._corners(crop_widget_rect):
                painter.drawRect(corner.x() - self.HANDLE // 2, corner.y() - self.HANDLE // 2, self.HANDLE, self.HANDLE)

            painter.setPen(QColor("white"))
            label = f"{self._crop.width()} x {self._crop.height()} @ ({self._crop.x()},{self._crop.y()})"
            painter.drawText(max(0, crop_widget_rect.x()), max(0, crop_widget_rect.y() - 6), label)

    @staticmethod
    def _corners(r: QRect) -> list[QPoint]:
        return [r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()]

    def _hit_corner(self, widget_pt: QPoint) -> str | None:
        if self._crop is None:
            return None
        r = self._to_widget_rect(self._crop)
        names = ["tl", "tr", "bl", "br"]
        for name, corner in zip(names, self._corners(r)):
            if (widget_pt - corner).manhattanLength() <= self.HANDLE:
                return name
        return None

    # -- mouse handling ---------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.has_image():
            return
        pt = event.position().toPoint()
        if self._tool == "move":
            self._pan_drag_start_mouse = pt
            self._pan_drag_start_pan = QPointF(self._pan)
            return
        corner = self._hit_corner(pt)
        if corner:
            self._drag_mode = f"resize:{corner}"
            self._drag_start_crop = QRect(self._crop)
        elif self._crop is not None and self._to_widget_rect(self._crop).contains(pt):
            self._drag_mode = "move_rect"
            self._drag_start_crop = QRect(self._crop)
            self._drag_anchor_image = self._to_image(pt)
        else:
            self._drag_mode = "new"
            self._drag_anchor_image = self._to_image(pt)
            self._crop = QRect(self._drag_anchor_image, self._drag_anchor_image)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not self.has_image():
            return
        if self._tool == "move":
            if self._pan_drag_start_mouse is None:
                return
            pt = event.position().toPoint()
            dx = pt.x() - self._pan_drag_start_mouse.x()
            dy = pt.y() - self._pan_drag_start_mouse.y()
            self._pan = QPointF(self._pan_drag_start_pan.x() - dx, self._pan_drag_start_pan.y() - dy)
            self._recompute_view()
            self.update()
            return

        if self._drag_mode is None:
            return
        pt = self._to_image(event.position().toPoint())
        iw, ih = self._image.width(), self._image.height()

        if self._drag_mode == "new":
            self._crop = self._normalize(QRect(self._drag_anchor_image, pt))
        elif self._drag_mode == "move_rect":
            dx = pt.x() - self._drag_anchor_image.x()
            dy = pt.y() - self._drag_anchor_image.y()
            moved = self._drag_start_crop.translated(dx, dy)
            x = max(0, min(moved.x(), iw - moved.width()))
            y = max(0, min(moved.y(), ih - moved.height()))
            moved.moveTo(x, y)
            self._crop = self._normalize(QRect(moved.topLeft(), QPoint(moved.x() + moved.width(), moved.y() + moved.height())))
        elif self._drag_mode.startswith("resize:"):
            corner = self._drag_mode.split(":")[1]
            r = QRect(self._drag_start_crop)
            if "l" in corner:
                r.setLeft(pt.x())
            if "r" in corner:
                r.setRight(pt.x())
            if "t" in corner:
                r.setTop(pt.y())
            if "b" in corner:
                r.setBottom(pt.y())
            self._crop = self._normalize(r)

        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._tool == "move":
            self._pan_drag_start_mouse = None
            self._pan_drag_start_pan = None
            return
        if self._drag_mode is not None:
            self._drag_mode = None
            self.selectionChanged.emit(self._crop)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Scroll to zoom, centered on the cursor — works regardless of the
        active tool, since wheel events are independent of mouse-drag
        handling above."""
        if not self.has_image():
            return
        # Accumulate rather than react to every tick: a real mouse sends one
        # +-120 step per click, but trackpads send a stream of small deltas
        # for one physical gesture — without this a single swipe would blow
        # through several zoom levels at once.
        self._wheel_accum += event.angleDelta().y()
        step = 120
        if abs(self._wheel_accum) < step:
            event.accept()
            return
        zooming_in = self._wheel_accum > 0
        self._wheel_accum = 0
        new_zoom = min(self._zoom * 2, self.MAX_ZOOM) if zooming_in else max(self._zoom // 2, 1)
        self._set_zoom(new_zoom, event.position().toPoint())
        event.accept()


class CropTab(QWidget):
    def __init__(
        self,
        jpegtran_path: str,
        command_log: CommandLog,
        session_state: SessionState,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.jpegtran_path = jpegtran_path
        self.command_log = command_log
        self.session_state = session_state
        self.current_file: Path | None = None
        # A private copy of the loaded file, kept in the system temp dir so that
        # repeated crops (and the rotate buttons) always operate on the pixels
        # the user actually loaded — even if the original on-disk file gets
        # overwritten (e.g. by a prior "save in place") or removed in the
        # meantime. Never written next to the user's own files.
        self._snapshot_path: Path | None = None
        atexit.register(self._cleanup_snapshot)

        layout = QVBoxLayout(self)

        self.drop_zone = DropZone("Drop a single JPEG file here", multi=False)
        self.drop_zone.filesDropped.connect(self.load_file)
        layout.addWidget(self.drop_zone)

        self.crop_view = ImageCropView()
        self.crop_view.selectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.crop_view, stretch=1)

        save_controls_row = QHBoxLayout()
        self.save_mode = SaveModeWidget(default_postfix="_crop")
        save_controls_row.addWidget(self.save_mode)
        layout.addLayout(save_controls_row)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        self.save_button = QPushButton("Save image")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_crop)
        layout.addWidget(self.save_button)

    def load_file(self, paths: list[Path]) -> None:
        if len(paths) > 1:
            QMessageBox.information(self, "Single file only", "Crop only supports one file at a time — using the first.")
        new_file = paths[0]

        try:
            snapshot_path = self._make_system_temp_path(new_file.suffix)
            shutil.copy2(new_file, snapshot_path)
        except OSError as exc:
            QMessageBox.critical(self, "Couldn't load file", f"Failed to read {display_path(new_file)}:\n{exc}")
            return

        if not self.crop_view.load_image(snapshot_path):
            snapshot_path.unlink(missing_ok=True)
            QMessageBox.critical(
                self,
                "Couldn't load image",
                f"{display_path(new_file)} could not be decoded — it may be "
                "corrupt, an unsupported format, or too large to preview.",
            )
            return

        # Only discard the previous file's snapshot once the new one is
        # confirmed to have loaded — otherwise a failed load here would
        # leave the previously-loaded file's snapshot dangling while the UI
        # still shows it as loaded.
        self._cleanup_snapshot()
        self.current_file = new_file
        self._snapshot_path = snapshot_path
        self.save_mode.set_default_target_folder(new_file.parent)
        self.status_label.setText(f"Loaded: {display_path(self.current_file)}")
        self.save_button.setEnabled(False)

    def _cleanup_snapshot(self) -> None:
        if self._snapshot_path is not None:
            self._snapshot_path.unlink(missing_ok=True)
            self._snapshot_path = None

    def _on_selection_changed(self, rect: QRect | None) -> None:
        has_selection = bool(rect and rect.width() > 0 and rect.height() > 0)
        # Rotate buttons emit this too (with rect possibly None), so this also
        # covers "rotated but no crop selected" — lets Save work as a
        # single-image rotator with no crop needed.
        has_rotation = self.crop_view.rotation_degrees != 0
        self.save_button.setEnabled(has_selection or has_rotation)

    @staticmethod
    def _make_temp_path(like: Path) -> Path:
        """Scratch file for the final atomic in-place overwrite; must live next
        to the target file so the same-filesystem rename stays atomic."""
        fd, tmp_name = tempfile.mkstemp(suffix=like.suffix, dir=like.parent)
        os.close(fd)
        return Path(tmp_name)

    @staticmethod
    def _make_system_temp_path(suffix: str) -> Path:
        """Scratch file for intermediate/source data — always in the system
        temp dir, never in a folder the user might have their own files in."""
        fd, tmp_name = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return Path(tmp_name)

    def save_crop(self) -> None:
        rect = self.crop_view.crop_rect()
        has_selection = bool(rect and rect.width() > 0 and rect.height() > 0)
        rotation_degrees = self.crop_view.rotation_degrees
        if self.current_file is None or self._snapshot_path is None:
            return
        if not has_selection and not rotation_degrees:
            return  # nothing to do: no crop selected and image isn't rotated

        if self.save_mode.is_inplace():
            if not confirm_overwrite(
                self,
                self.session_state,
                f"This will overwrite the original file:\n{display_path(self.current_file)}\nContinue?",
            ):
                return

        final_outfile = self.save_mode.output_path_for(self.current_file)
        write_target = final_outfile
        tmp_path: Path | None = None
        if self.save_mode.is_inplace():
            tmp_path = self._make_temp_path(self.current_file)
            write_target = tmp_path

        # Always read from the in-memory-loaded snapshot, never the mutable
        # on-disk original — it may have already been overwritten by a
        # previous in-place save.
        if has_selection:
            # If the preview has been rotated, first losslessly rotate the
            # snapshot to match; the crop rectangle is already expressed in
            # that rotated image's coordinates (ImageCropView keeps it in
            # sync). Then crop that (possibly rotated) intermediate.
            source_for_crop = self._snapshot_path
            rotate_tmp: Path | None = None
            if rotation_degrees:
                rotate_tmp = self._make_system_temp_path(self._snapshot_path.suffix)
                rotate_result = run_jpegtran(
                    self.jpegtran_path, ["-rotate", str(rotation_degrees)], self._snapshot_path, rotate_tmp
                )
                self.command_log.log(rotate_result.command, rotate_result.ok, rotate_result.stderr)
                if not rotate_result.ok:
                    rotate_tmp.unlink(missing_ok=True)
                    self.status_label.setText(f"FAILED (rotate step): {rotate_result.stderr}")
                    return
                reset_exif_orientation(rotate_tmp)
                source_for_crop = rotate_tmp

            crop_arg = f"{rect.width()}x{rect.height()}+{rect.x()}+{rect.y()}"
            result = run_jpegtran(self.jpegtran_path, ["-crop", crop_arg], source_for_crop, write_target)

            if rotate_tmp is not None:
                rotate_tmp.unlink(missing_ok=True)
                # Orientation was already normalized on rotate_tmp above, and
                # -copy all carried that forward into write_target. A pure
                # crop (no rotation) must NOT touch Orientation — the pixels
                # weren't physically rotated, so the tag is still correct.
        else:
            # No crop selected — used purely as a whole-image rotator.
            result = run_jpegtran(
                self.jpegtran_path, ["-rotate", str(rotation_degrees)], self._snapshot_path, write_target
            )
            if result.ok:
                reset_exif_orientation(write_target)

        if result.ok:
            if tmp_path is not None:
                tmp_path.replace(final_outfile)
            self.status_label.setText(f"Saved: {display_path(final_outfile)}.")
            self.crop_view.clear_selection()
        else:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
            self.status_label.setText(f"FAILED: {result.stderr}")

        self.command_log.log(result.command, result.ok, result.stderr)


# --------------------------------------------------------------------------
# Log tab
# --------------------------------------------------------------------------


class LogTab(QWidget):
    def __init__(self, command_log: CommandLog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("jpegtran commands will appear here as they run.")
        layout.addWidget(self.text)
        command_log.logged.connect(self.append_line)

    def append_line(self, line: str) -> None:
        self.text.appendPlainText(line)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, jpegtran_path: str) -> None:
        super().__init__()
        self.setWindowTitle("JPEG Crop/Rotate")
        self.resize(800, 700)

        command_log = CommandLog()
        session_state = SessionState()

        tabs = QTabWidget()
        tabs.addTab(RotateTab(jpegtran_path, command_log, session_state), "Batch transform")
        tabs.addTab(CropTab(jpegtran_path, command_log, session_state), "Crop / Rotate")
        tabs.addTab(LogTab(command_log), "Log")

        self.setCentralWidget(tabs)


def main() -> None:
    app = QApplication(sys.argv)

    # Qt refuses to decode an image whose decoded pixel buffer would exceed
    # this limit (default 256 MB), as a guard against decompression-bomb
    # files. This app is specifically for editing large photos/scans, so
    # raise it generously -- still bounded, so a truly pathological/corrupt
    # file can't make Qt try to allocate an unbounded amount of memory.
    QImageReader.setAllocationLimit(4096)

    jpegtran_path = find_jpegtran()
    if jpegtran_path is None:
        QMessageBox.critical(
            None,
            "jpegtran not found",
            "This app requires the 'jpegtran' command-line tool, which was not found on your PATH.\n\n"
            "On macOS, install it with Homebrew:\n\n    brew install jpeg-turbo\n\n"
            "Then restart this app.",
        )
        sys.exit(1)

    window = MainWindow(jpegtran_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
