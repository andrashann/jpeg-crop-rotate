"""CropTab: the in-memory snapshot (survives the original being overwritten),
save-crop pipeline (postfix/in-place, source vs. target folder), rotate-only
saves, and toolbar-driven clear/rotate."""
import inspect
import tempfile
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage

import app
from conftest import synth_drop


def make_crop_tab(jpegtran_path, command_log, session_state):
    return app.CropTab(jpegtran_path, command_log, session_state)


# -- snapshot mechanism -------------------------------------------------------


def test_snapshot_lives_in_system_temp_dir_not_next_to_source(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])

    system_temp = Path(tempfile.gettempdir()).resolve()
    snap = tab._snapshot_path
    assert snap is not None and snap.exists()
    assert snap.parent.resolve() == system_temp
    assert snap.parent.resolve() != sample_jpeg.parent.resolve()


def test_image_stays_loaded_after_a_successful_save(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])

    tab.crop_view._crop = QRect(0, 0, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    assert tab.save_button.isEnabled()
    tab.save_crop()

    assert tab.current_file == sample_jpeg, "image should remain loaded after a successful save"
    assert tab.crop_view.has_image()
    assert tab.crop_view.crop_rect() is None, "selection should clear after a successful save"
    assert sample_jpeg.with_name("sample_crop.jpg").exists()


def test_second_independent_crop_from_the_same_loaded_image(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])

    tab.crop_view._crop = QRect(0, 0, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    tab.save_crop()

    tab.save_mode.postfix_field.setText("_crop2")
    tab.crop_view._crop = QRect(16, 16, 80, 64)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    tab.save_crop()

    out2 = sample_jpeg.with_name("sample_crop2.jpg")
    assert out2.exists()
    img = QImage(str(out2))
    assert (img.width(), img.height()) == (80, 64)


def test_crop_after_original_overwritten_in_place_still_uses_the_snapshot(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    orig_w, orig_h = tab.crop_view._image.width(), tab.crop_view._image.height()

    # Overwrite the original in place with a tiny crop.
    tab.save_mode.inplace_radio.setChecked(True)
    session_state.skip_overwrite_confirm = True
    tab.crop_view._crop = QRect(0, 0, 32, 32)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    tab.save_crop()

    on_disk = QImage(str(sample_jpeg))
    assert (on_disk.width(), on_disk.height()) == (32, 32)

    # A further crop must still be relative to the ORIGINAL (pre-overwrite)
    # dimensions, proving it reads from the in-memory snapshot, not the file.
    tab.save_mode.postfix_radio.setChecked(True)
    tab.save_mode.postfix_field.setText("_after")
    tab.crop_view._crop = QRect(0, 0, min(96, orig_w), min(80, orig_h))
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    tab.save_crop()

    out = sample_jpeg.with_name("sample_after.jpg")
    assert out.exists()
    img = QImage(str(out))
    assert (img.width(), img.height()) == (min(96, orig_w), min(80, orig_h))


def test_loading_a_new_file_cleans_up_the_previous_snapshot(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    snap1 = tab._snapshot_path

    tab.load_file([sample_jpeg])
    snap2 = tab._snapshot_path

    assert snap1 != snap2
    assert not snap1.exists()
    assert snap2.exists()


# -- save pipeline: rotate+crop combined, log sequence -----------------------


def test_rotate_then_crop_pipeline_logs_two_commands_in_order(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    orig_w, orig_h = tab.crop_view._image.width(), tab.crop_view._image.height()

    tab.crop_view.rotate_image(clockwise=True)
    assert (tab.crop_view._image.width(), tab.crop_view._image.height()) == (orig_h, orig_w)

    tab.crop_view._crop = QRect(16, 32, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)

    logged = []
    command_log.logged.connect(logged.append)
    tab.save_crop()

    out = sample_jpeg.with_name("sample_crop.jpg")
    assert out.exists()
    img = QImage(str(out))
    assert (img.width(), img.height()) == (96, 80)

    assert len(logged) == 2
    assert "-rotate" in logged[0]
    assert "-crop" in logged[1]


def test_rotate_only_save_with_no_selection_acts_as_a_rotator(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    assert not tab.save_button.isEnabled()

    tab.crop_view.toolbar.rotate_cw_button.click()
    assert tab.crop_view.rotation_degrees == 90
    assert tab.crop_view.crop_rect() is None
    assert tab.save_button.isEnabled(), "Save should be enabled by rotation alone"

    tab.save_mode.postfix_field.setText("_rotated")
    tab.save_crop()

    out = sample_jpeg.with_name("sample_rotated.jpg")
    assert out.exists()
    img = QImage(str(out))
    orig = QImage(str(sample_jpeg))
    assert (img.width(), img.height()) == (orig.height(), orig.width())

    # Rotating back to 0 with no selection should disable Save again.
    tab.crop_view.toolbar.rotate_cw_button.click()
    tab.crop_view.toolbar.rotate_cw_button.click()
    tab.crop_view.toolbar.rotate_cw_button.click()
    assert tab.crop_view.rotation_degrees == 0
    assert not tab.save_button.isEnabled()


# -- target folder integration -----------------------------------------------


def test_default_target_folder_is_the_loaded_images_folder(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    assert tab.save_mode.source_folder_radio.isChecked()
    assert tab.save_mode.target_folder() == sample_jpeg.parent


def test_explicit_save_to_folder_redirects_output(qapp, jpegtran_path, command_log, session_state, sample_jpeg, tmp_path):
    custom_dir = tmp_path / "custom_out"
    custom_dir.mkdir()
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])

    tab.save_mode.target_folder_radio.setChecked(True)
    tab.save_mode._target_folder = custom_dir
    tab.crop_view._crop = QRect(0, 0, 64, 48)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    tab.save_mode.postfix_field.setText("_customdir")
    tab.save_crop()

    out = custom_dir / "sample_customdir.jpg"
    assert out.exists()
    img = QImage(str(out))
    assert (img.width(), img.height()) == (64, 48)


# -- clear selection button ----------------------------------------------------


def test_clear_selection_button_clears_selection_without_unloading(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    assert not tab.crop_view.toolbar.clear_button.isEnabled()

    tab.crop_view._crop = QRect(0, 0, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    assert tab.crop_view.toolbar.clear_button.isEnabled()
    assert tab.save_button.isEnabled()

    tab.crop_view.toolbar.clear_button.click()
    assert tab.crop_view.crop_rect() is None
    assert not tab.crop_view.toolbar.clear_button.isEnabled()
    assert not tab.save_button.isEnabled()
    assert tab.crop_view.has_image()


# -- undecodable images (corrupt, unsupported, or too large for Qt) ----------


def test_load_file_shows_error_and_does_not_falsely_claim_loaded_when_qt_rejects_the_image(
    qapp, jpegtran_path, command_log, session_state, make_jpeg
):
    """Exercises the same failure path a genuinely oversized image hits
    against Qt's image allocation limit (QImageReader.setAllocationLimit) --
    using a deliberately tiny limit against a modestly-sized fixture here so
    the test stays fast, rather than generating a real multi-hundred-MB image.
    """
    # 600x600 decodes to ~1.4MB as RGB32 -- bigger than the 1MB limit below.
    # (The default small `sample_jpeg` fixture is only ~0.1MB decoded, which
    # a 1MB limit -- the smallest nonzero value setAllocationLimit takes --
    # wouldn't actually reject.)
    oversized_for_the_limit = make_jpeg(size=(600, 600))

    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    original_limit = app.QImageReader.allocationLimit()
    app.QImageReader.setAllocationLimit(1)  # MB
    try:
        with patch.object(app.QMessageBox, "critical") as mock_critical:
            tab.load_file([oversized_for_the_limit])
            assert mock_critical.called
    finally:
        app.QImageReader.setAllocationLimit(original_limit)

    assert tab.current_file is None
    assert not tab.crop_view.has_image()
    assert tab.status_label.text() == "", "must not claim 'Loaded' when the decode failed"


def test_load_file_with_garbage_data_shows_error(qapp, jpegtran_path, command_log, session_state, tmp_path):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    bad_file = tmp_path / "not_really_a_jpeg.jpg"
    bad_file.write_bytes(b"this is not jpeg data" * 100)

    with patch.object(app.QMessageBox, "critical") as mock_critical:
        tab.load_file([bad_file])
        assert mock_critical.called

    assert tab.current_file is None
    assert not tab.crop_view.has_image()


def test_failed_load_does_not_disturb_a_previously_loaded_files_snapshot(
    qapp, jpegtran_path, command_log, session_state, sample_jpeg, tmp_path
):
    """A failed load of a second file must not tear down the first file's
    still-displayed snapshot out from under it."""
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    first_snapshot = tab._snapshot_path
    assert first_snapshot.exists()

    bad_file = tmp_path / "bad.jpg"
    bad_file.write_bytes(b"garbage")
    with patch.object(app.QMessageBox, "critical"):
        tab.load_file([bad_file])

    assert tab.current_file == sample_jpeg, "should still show the first (successfully loaded) file"
    assert tab._snapshot_path == first_snapshot
    assert first_snapshot.exists(), "first file's snapshot must survive the second file's failed load"


def test_main_raises_the_image_allocation_limit():
    """Guards the actual fix: without this, `jpegtran` itself is unaffected
    (it's a subprocess with no such limit), but Qt's own image loading --
    used for the Crop tab preview -- refuses anything whose decoded buffer
    would exceed 256MB by default, which real photos/scans routinely do."""
    src = inspect.getsource(app.main)
    assert "QImageReader.setAllocationLimit" in src


# -- existing-target confirmation (postfix mode) -----------------------------


def test_save_asks_before_overwriting_an_existing_postfix_target(
    qapp, jpegtran_path, command_log, session_state, sample_jpeg
):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    tab.crop_view._crop = QRect(0, 0, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)

    target = sample_jpeg.with_name("sample_crop.jpg")
    target.write_bytes(b"pre-existing, unrelated content")
    target_mtime = target.stat().st_mtime_ns

    with patch.object(app, "confirm_existing_target", return_value=("skip", False)) as mock_confirm:
        tab.save_crop()
        assert mock_confirm.called, "must ask before silently overwriting an existing target"

    assert target.stat().st_mtime_ns == target_mtime, "target must be untouched after Skip"
    assert "Skipped" in tab.status_label.text()


def test_save_overwrite_choice_proceeds_normally(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    tab.crop_view._crop = QRect(0, 0, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)

    target = sample_jpeg.with_name("sample_crop.jpg")
    target.write_bytes(b"pre-existing, unrelated content")

    with patch.object(app, "confirm_existing_target", return_value=("overwrite", False)) as mock_confirm:
        tab.save_crop()
        assert mock_confirm.called

    img = QImage(str(target))
    assert (img.width(), img.height()) == (96, 80), "target should now hold the actual crop"


def test_save_with_no_existing_target_does_not_prompt(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    tab.crop_view._crop = QRect(0, 0, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)

    assert not sample_jpeg.with_name("sample_crop.jpg").exists()
    with patch.object(app, "confirm_existing_target") as mock_confirm:
        tab.save_crop()
        assert not mock_confirm.called


def test_in_place_mode_does_not_use_the_existing_target_prompt(
    qapp, jpegtran_path, command_log, session_state, sample_jpeg
):
    """In-place mode already has its own overwrite confirmation (confirm_overwrite) -
    it must not also trigger the postfix-target-exists dialog."""
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    tab.load_file([sample_jpeg])
    tab.crop_view._crop = QRect(0, 0, 96, 80)
    tab.crop_view.selectionChanged.emit(tab.crop_view._crop)
    tab.save_mode.inplace_radio.setChecked(True)
    session_state.skip_overwrite_confirm = True

    with patch.object(app, "confirm_existing_target") as mock_confirm:
        tab.save_crop()
        assert not mock_confirm.called


# -- drag-and-drop directly onto the image viewer -----------------------------


def test_dropping_a_file_onto_the_viewer_loads_it_like_the_drop_zone(
    qapp, jpegtran_path, command_log, session_state, sample_jpeg
):
    tab = make_crop_tab(jpegtran_path, command_log, session_state)
    synth_drop(tab.crop_view, [sample_jpeg])
    assert tab.current_file == sample_jpeg
    assert tab.crop_view.has_image()
