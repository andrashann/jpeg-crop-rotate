"""SaveModeWidget: in-place/postfix, source-folder vs. target-folder, and the
QGroupBox chrome around it."""
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QGroupBox

import app


def test_is_a_titled_group_box(qapp):
    widget = app.SaveModeWidget(default_postfix="_x")
    assert isinstance(widget, QGroupBox)
    assert widget.title() == "Output settings"


def test_defaults_to_postfix_and_source_folder(qapp):
    widget = app.SaveModeWidget(default_postfix="_x")
    assert widget.postfix_radio.isChecked()
    assert not widget.is_inplace()
    assert widget.source_folder_radio.isChecked()
    assert not widget.target_folder_radio.isChecked()


def test_source_folder_mode_ignores_any_preloaded_target(qapp):
    widget = app.SaveModeWidget(default_postfix="_x")
    widget.set_default_target_folder(Path("/somewhere/else"))
    result = widget.output_path_for(Path("/a/b/photo.jpg"))
    assert result == Path("/a/b/photo_x.jpg")


def test_switching_to_target_folder_mode_redirects_output(qapp):
    widget = app.SaveModeWidget(default_postfix="_x")
    widget.set_default_target_folder(Path("/dest"))
    widget.target_folder_radio.setChecked(True)
    assert widget.output_path_for(Path("/a/b/photo.jpg")) == Path("/dest/photo_x.jpg")


def test_target_folder_browse_button_enabled_only_in_that_mode(qapp):
    widget = app.SaveModeWidget(default_postfix="_x")
    assert not widget.target_folder_browse_button.isEnabled()
    widget.target_folder_radio.setChecked(True)
    assert widget.target_folder_browse_button.isEnabled()


def test_switching_to_target_folder_radio_is_sticky(qapp):
    """Deliberately picking 'Save to folder' should stop later per-file
    defaults (e.g. loading a new source file) from silently overriding it."""
    widget = app.SaveModeWidget(default_postfix="_x")
    widget.set_default_target_folder(Path("/first/default"))
    widget.target_folder_radio.setChecked(True)
    widget.set_default_target_folder(Path("/second/default"))
    assert widget.target_folder() == Path("/first/default")


def test_manual_browse_also_sticks_and_redirects_output(qapp, tmp_path):
    widget = app.SaveModeWidget(default_postfix="_x")
    widget.target_folder_radio.setChecked(True)  # Browse is only reachable in this mode in the real UI
    with patch.object(app.QFileDialog, "getExistingDirectory", return_value=str(tmp_path)):
        widget._browse_target_folder()
    assert widget.target_folder() == tmp_path
    assert widget.output_path_for(Path("/a/b/photo.jpg")) == tmp_path / "photo_x.jpg"


def test_in_place_mode_disables_the_whole_folder_row(qapp):
    widget = app.SaveModeWidget(default_postfix="_x")
    widget.inplace_radio.setChecked(True)
    assert not widget.source_folder_radio.isEnabled()
    assert not widget.target_folder_radio.isEnabled()
    assert not widget.target_folder_browse_button.isEnabled()


def test_in_place_mode_output_path_is_the_input_itself(qapp):
    widget = app.SaveModeWidget(default_postfix="_x")
    widget.inplace_radio.setChecked(True)
    infile = Path("/a/b/photo.jpg")
    assert widget.output_path_for(infile) == infile


def test_postfix_radio_and_inplace_radio_are_mutually_exclusive_and_independent_of_folder_radios(qapp):
    """Regression: all four radios share this widget as their Qt parent, and
    Qt auto-groups same-parent radios into ONE exclusive set by default —
    without explicit QButtonGroups, checking 'Save to folder' would silently
    uncheck 'Save with postfix' too."""
    widget = app.SaveModeWidget(default_postfix="_x")
    assert widget.postfix_radio.isChecked()
    widget.target_folder_radio.setChecked(True)
    assert widget.postfix_radio.isChecked(), "unrelated radio pair must not have been disturbed"
    assert not widget.inplace_radio.isChecked()
