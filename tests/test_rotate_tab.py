"""RotateTab (the "Batch transform" tab): file list (thumbnails, per-row
delete, multi-select remove), processing all vs. selected files, in-place
thumbnail refresh, and the existing-target skip/overwrite/apply-to-all flow.
"""
from unittest.mock import patch

from PyQt6.QtGui import QImage

import app


def make_rotate_tab(jpegtran_path, command_log, session_state):
    return app.RotateTab(jpegtran_path, command_log, session_state)


# -- file list: add / remove / clear / thumbnails / delete button ----------


def test_add_files_populates_thumbnail_path_and_delete_columns(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])

    assert tab.files == [sample_jpeg]
    assert tab.file_table.rowCount() == 1
    assert tab.file_table.columnCount() == 3

    thumb_item = tab.file_table.item(0, 0)
    assert thumb_item is not None and not thumb_item.icon().isNull()

    path_item = tab.file_table.item(0, 1)
    assert path_item.text() == app.display_path(sample_jpeg)

    delete_btn = tab.file_table.cellWidget(0, 2)
    assert isinstance(delete_btn, app.QToolButton)
    assert not delete_btn.icon().isNull()


def test_add_files_dedupes(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg, sample_jpeg])
    assert tab.files == [sample_jpeg]
    assert tab.file_table.rowCount() == 1


def test_add_files_rejects_a_file_with_a_jpeg_extension_but_non_jpeg_content(
    qapp, jpegtran_path, command_log, session_state, sample_jpeg, tmp_path
):
    """The drop zone only filters by .jpg/.jpeg extension, which doesn't
    guarantee the content actually is one (e.g. a renamed text file) --
    add_files must reject it rather than silently listing an unprocessable
    row."""
    fake_jpeg = tmp_path / "not_really_a_jpeg.jpeg"
    fake_jpeg.write_text("this is plain text, not image data")

    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    with patch.object(app.QMessageBox, "warning") as mock_warning:
        tab.add_files([fake_jpeg, sample_jpeg])
        assert mock_warning.called

    assert tab.files == [sample_jpeg], "only the real JPEG should have been added"
    assert tab.file_table.rowCount() == 1


def test_thumbnail_is_decoded_at_the_configured_size(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.THUMBNAIL_SIZE = 60
    tab.add_files([sample_jpeg])
    item = tab.file_table.item(0, 0)
    actual = item.icon().actualSize(app.QSize(200, 200))
    # QIcon never scales a source pixmap UP, only down, so if this doesn't
    # match, the decode silently fell back to some other hardcoded size.
    assert max(actual.width(), actual.height()) == 60


def test_multi_select_mode_enabled(qapp, jpegtran_path, command_log, session_state):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    assert tab.file_table.selectionMode() == app.QAbstractItemView.SelectionMode.ExtendedSelection


def test_remove_selected_removes_exactly_the_selected_rows(qapp, jpegtran_path, command_log, session_state, make_jpeg):
    f1, f2, f3 = make_jpeg(), make_jpeg(), make_jpeg()
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([f1, f2, f3])

    tab.file_table.item(0, 1).setSelected(True)
    tab.file_table.item(2, 1).setSelected(True)
    tab.remove_selected()

    assert tab.files == [f2]
    assert tab.file_table.rowCount() == 1
    assert tab.file_table.item(0, 1).text() == app.display_path(f2)


def test_remove_selected_with_no_selection_is_a_noop(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    tab.remove_selected()
    assert tab.files == [sample_jpeg]


def test_per_row_delete_button_removes_the_correct_row(qapp, jpegtran_path, command_log, session_state, make_jpeg):
    f1, f2, f3 = make_jpeg(), make_jpeg(), make_jpeg()
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([f1, f2, f3])

    middle_button = tab.file_table.cellWidget(1, 2)
    middle_button.click()

    assert tab.files == [f1, f3]
    assert tab.file_table.rowCount() == 2
    assert tab.file_table.item(0, 1).text() == app.display_path(f1)
    assert tab.file_table.item(1, 1).text() == app.display_path(f3)


def test_clear_list_empties_everything(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    tab.clear_files()
    assert tab.files == []
    assert tab.file_table.rowCount() == 0


def test_no_standalone_browse_button_dropzone_handles_it(qapp, jpegtran_path, command_log, session_state):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    assert not hasattr(tab, "browse_button")
    assert tab.drop_zone.multi is True


# -- default target folder tracks the batch --------------------------------


def test_default_target_folder_is_first_files_folder(qapp, jpegtran_path, command_log, session_state, make_jpeg, tmp_path):
    sub = tmp_path / "sub"
    f1 = make_jpeg(tmp_path / "a.jpg")
    f2 = make_jpeg(sub / "b.jpg")
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([f1, f2])
    assert tab.save_mode.target_folder() == f1.parent


def test_default_target_folder_follows_first_file_after_removal(qapp, jpegtran_path, command_log, session_state, make_jpeg, tmp_path):
    sub = tmp_path / "sub"
    f1 = make_jpeg(tmp_path / "a.jpg")
    f2 = make_jpeg(sub / "b.jpg")
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([f1, f2])
    tab.file_table.item(0, 1).setSelected(True)
    tab.remove_selected()
    assert tab.files == [f2]
    assert tab.save_mode.target_folder() == f2.parent


# -- processing: all files vs. selected files -------------------------------


def test_process_all_files(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    tab.save_mode.postfix_field.setText("_rot")
    tab.process_files()

    out = sample_jpeg.with_name("sample_rot.jpg")
    assert out.exists()
    orig = QImage(str(sample_jpeg))
    rotated = QImage(str(out))
    assert (rotated.width(), rotated.height()) == (orig.height(), orig.width())
    assert "OK" in tab.file_table.item(0, 1).text()


def test_process_files_with_empty_list_shows_message_and_does_nothing(qapp, jpegtran_path, command_log, session_state):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    with patch.object(app.QMessageBox, "information") as mock_info:
        tab.process_files()
        assert mock_info.called


def test_process_selected_files_only_processes_selected_rows(qapp, jpegtran_path, command_log, session_state, make_jpeg):
    f1, f2, f3 = make_jpeg(), make_jpeg(), make_jpeg()
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([f1, f2, f3])
    tab.save_mode.postfix_field.setText("_sel")

    tab.file_table.item(0, 1).setSelected(True)
    tab.file_table.item(2, 1).setSelected(True)
    tab.process_selected_files()

    assert f1.with_name(f1.stem + "_sel.jpg").exists()
    assert f3.with_name(f3.stem + "_sel.jpg").exists()
    assert not f2.with_name(f2.stem + "_sel.jpg").exists()
    assert "OK" in tab.file_table.item(0, 1).text()
    assert "OK" in tab.file_table.item(2, 1).text()
    assert tab.file_table.item(1, 1).text() == app.display_path(f2), "untouched row should show no status"


def test_process_selected_files_with_no_selection_shows_message(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    with patch.object(app.QMessageBox, "information") as mock_info:
        tab.process_selected_files()
        assert mock_info.called


def test_list_controls_reenabled_after_processing(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    tab.process_files()

    assert tab.add_button.isEnabled()
    assert tab.remove_button.isEnabled()
    assert tab.clear_button.isEnabled()
    assert tab.process_button.isEnabled()
    assert tab.process_selected_button.isEnabled()
    for row in range(tab.file_table.rowCount()):
        assert tab.file_table.cellWidget(row, 2).isEnabled()


def test_status_text_color_reflects_ok(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    tab.process_files()
    color = tab.file_table.item(0, 1).foreground().color()
    assert color.green() > color.red(), "expected a green-ish success color"


def test_mixed_folder_batch_each_file_saves_to_its_own_source_folder(qapp, jpegtran_path, command_log, session_state, make_jpeg, tmp_path):
    sub = tmp_path / "sub"
    f1 = make_jpeg(tmp_path / "a.jpg")
    f2 = make_jpeg(sub / "b.jpg")
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([f1, f2])
    assert tab.save_mode.source_folder_radio.isChecked()
    tab.save_mode.postfix_field.setText("_mixed")
    tab.process_files()

    assert (tmp_path / "a_mixed.jpg").exists()
    assert (sub / "b_mixed.jpg").exists()
    assert not (tmp_path / "b_mixed.jpg").exists(), "must not have been redirected to a's folder"


# -- in-place editing: thumbnail refresh ------------------------------------


def test_in_place_edit_refreshes_the_thumbnail(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])

    before = tab.file_table.item(0, 0).icon().actualSize(app.QSize(200, 200))
    assert before.width() > before.height(), "fixture is landscape"

    tab.save_mode.inplace_radio.setChecked(True)
    session_state.skip_overwrite_confirm = True
    tab.process_files()

    after = tab.file_table.item(0, 0).icon().actualSize(app.QSize(200, 200))
    assert after.height() > after.width(), "thumbnail should reflect the now-rotated (portrait) file"


# -- existing-target skip / overwrite / apply-to-all -------------------------


def test_existing_target_skip_leaves_file_untouched_and_marks_row(qapp, jpegtran_path, command_log, session_state, sample_jpeg, make_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    tab.save_mode.postfix_field.setText("_dup")

    target = sample_jpeg.with_name("sample_dup.jpg")
    make_jpeg(target, size=(10, 10))  # pre-create the target
    mtime_before = target.stat().st_mtime_ns

    with patch.object(app, "confirm_existing_target", return_value=("skip", False)) as mock_confirm:
        tab.process_files()
        assert mock_confirm.called

    assert target.stat().st_mtime_ns == mtime_before
    assert "Skipped" in tab.file_table.item(0, 1).text()


def test_existing_target_overwrite_apply_to_all_suppresses_further_prompts(qapp, jpegtran_path, command_log, session_state, make_jpeg):
    f1, f2 = make_jpeg(), make_jpeg()
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([f1, f2])
    tab.save_mode.postfix_field.setText("_dup")

    target1 = f1.with_name(f1.stem + "_dup.jpg")
    target2 = f2.with_name(f2.stem + "_dup.jpg")
    make_jpeg(target1, size=(10, 10))
    make_jpeg(target2, size=(10, 10))
    size_before = target1.stat().st_size

    with patch.object(app, "confirm_existing_target", return_value=("overwrite", True)) as mock_confirm:
        tab.process_files()
        assert mock_confirm.call_count == 1, "apply-to-all should suppress the prompt for the 2nd file"

    assert target1.stat().st_size != size_before
    assert "OK" in tab.file_table.item(0, 1).text()
    assert "OK" in tab.file_table.item(1, 1).text()


def test_in_place_mode_never_triggers_existing_target_prompt(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_rotate_tab(jpegtran_path, command_log, session_state)
    tab.add_files([sample_jpeg])
    tab.save_mode.inplace_radio.setChecked(True)
    session_state.skip_overwrite_confirm = True

    with patch.object(app, "confirm_existing_target") as mock_confirm:
        tab.process_files()
        assert not mock_confirm.called
