"""DropZone: click-to-browse and file-type filtering, for both the
multi-file (Batch tab) and single-file (Crop tab) variants."""
from pathlib import Path
from unittest.mock import patch

import app


def test_multi_dropzone_click_opens_multi_file_dialog(qapp, sample_jpeg):
    zone = app.DropZone("hint", multi=True)
    received = []
    zone.filesDropped.connect(received.extend)

    with patch.object(app.QFileDialog, "getOpenFileNames", return_value=([str(sample_jpeg)], "")):
        zone._browse()

    assert received == [sample_jpeg]


def test_single_dropzone_click_opens_single_file_dialog(qapp, sample_jpeg):
    zone = app.DropZone("hint", multi=False)
    received = []
    zone.filesDropped.connect(received.extend)

    with patch.object(app.QFileDialog, "getOpenFileName", return_value=(str(sample_jpeg), "")):
        zone._browse()

    assert received == [sample_jpeg]


def test_non_jpeg_files_are_rejected(qapp, tmp_path):
    zone = app.DropZone("hint", multi=True)
    received = []
    zone.filesDropped.connect(received.extend)

    not_a_jpeg = tmp_path / "notes.txt"
    not_a_jpeg.write_text("hello")

    with patch.object(app.QMessageBox, "warning") as mock_warning:
        zone._accept([not_a_jpeg])
        assert mock_warning.called

    assert received == []


def test_mixed_valid_and_invalid_files_keeps_only_jpegs(qapp, sample_jpeg, tmp_path):
    zone = app.DropZone("hint", multi=True)
    received = []
    zone.filesDropped.connect(received.extend)

    not_a_jpeg = tmp_path / "notes.txt"
    not_a_jpeg.write_text("hello")

    with patch.object(app.QMessageBox, "warning"):
        zone._accept([sample_jpeg, not_a_jpeg])

    assert received == [sample_jpeg]
