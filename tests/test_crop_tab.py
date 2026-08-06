"""CropTab: the in-memory snapshot (survives the original being overwritten),
save-crop pipeline (postfix/in-place, source vs. target folder), rotate-only
saves, and toolbar-driven clear/rotate."""
import tempfile
from pathlib import Path

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage

import app


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
