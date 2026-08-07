"""Join tab: SOF-marker parsing, the iMCU-aligned layout math, the
lossless drop-based compositing pipeline, and the tab's UI plumbing
(last-two-drops tracking, defaults, switch/direction controls)."""
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from PyQt6.QtGui import QColor, QImage

import app
from conftest import synth_drop


def make_jpeg_with_subsampling(path: Path, size: tuple[int, int], color: tuple[int, int, int], subsampling: int) -> Path:
    img = Image.new("RGB", size, color)
    img.save(path, quality=90, subsampling=subsampling)
    return path


# -- SOF parsing --------------------------------------------------------------


def test_read_jpeg_frame_info_returns_dimensions_and_sampling(sample_jpeg):
    width, height, components = app.read_jpeg_frame_info(sample_jpeg)
    assert (width, height) == (200, 130)
    assert len(components) == 3  # Y, Cb, Cr


def test_jpeg_sampling_factors_reads_4_2_0_default(sample_jpeg):
    _w, _h, components = app.read_jpeg_frame_info(sample_jpeg)
    # PIL's default subsampling at quality 90 is 4:2:0 -- luma sampled 2x2.
    assert app.jpeg_sampling_factors(components) == (2, 2)


def test_jpeg_sampling_factors_reads_4_4_4(tmp_path):
    p = make_jpeg_with_subsampling(tmp_path / "444.jpg", (64, 64), (10, 20, 30), subsampling=0)
    _w, _h, components = app.read_jpeg_frame_info(p)
    assert app.jpeg_sampling_factors(components) == (1, 1)


# -- layout math ----------------------------------------------------------


def test_horizontal_layout_places_second_image_after_aligned_first_width():
    layout = app.compute_join_layout(203, 150, 120, 300, "horizontal", 16, 16)
    assert layout.first_offset == (0, 64)  # (300-150)/2=75 -> floored to 64 (16px grid)
    assert layout.second_offset == (208, 0)  # 203 rounded up to next 16 = 208
    assert layout.canvas_width == 208 + 120
    assert layout.canvas_height == 300


def test_vertical_layout_places_second_image_after_aligned_first_height():
    layout = app.compute_join_layout(203, 150, 120, 300, "vertical", 16, 16)
    assert layout.first_offset == (0, 0)
    assert layout.second_offset[1] == 160  # 150 rounded up to next 16 = 160
    assert layout.canvas_height == 160 + 300
    assert layout.canvas_width == 203


def test_layout_with_no_alignment_slack_leaves_no_gap():
    # 160 is already a multiple of 16, so image 2 should butt right up against it.
    layout = app.compute_join_layout(160, 100, 50, 100, "horizontal", 16, 16)
    assert layout.second_offset == (160, 0)
    assert layout.canvas_width == 210


def test_layout_never_places_an_image_past_the_canvas_bound():
    # The shorter image's centering offset must never push it past canvas_h.
    for h1, h2 in [(100, 17), (33, 100), (17, 17), (1000, 3)]:
        layout = app.compute_join_layout(100, h1, 100, h2, "horizontal", 16, 16)
        assert layout.first_offset[1] + h1 <= layout.canvas_height
        assert layout.second_offset[1] + h2 <= layout.canvas_height


# -- the real lossless pipeline ------------------------------------------


def test_build_joined_jpeg_horizontal(qapp, jpegtran_path, cjpeg_path, command_log, tmp_path):
    p1 = make_jpeg_with_subsampling(tmp_path / "a.jpg", (203, 150), (255, 0, 0), subsampling=2)
    p2 = make_jpeg_with_subsampling(tmp_path / "b.jpg", (120, 300), (0, 255, 0), subsampling=2)
    out = tmp_path / "joined.jpg"

    ok, stderr = app.build_joined_jpeg(jpegtran_path, cjpeg_path, command_log, p1, p2, "horizontal", QColor("blue"), out)
    assert ok, stderr
    assert out.exists()

    width, height, _ = app.read_jpeg_frame_info(out)
    assert (width, height) == (328, 300)

    img = QImage(str(out))

    def px(x, y):
        c = img.pixelColor(x, y)
        return (c.red(), c.green(), c.blue())

    assert px(5, 5) == (0, 0, 254) or px(5, 5) == (0, 0, 255)  # background, before image 1's y-offset
    assert px(5, 74)[0] > 200 and px(5, 74)[1] < 50  # inside image 1 (red)
    assert px(218, 10)[1] > 200 and px(218, 10)[0] < 50  # inside image 2 (green)
    assert px(204, 10)[2] > 200  # the alignment gap between them shows the background


def test_build_joined_jpeg_raises_on_subsampling_mismatch(qapp, jpegtran_path, cjpeg_path, command_log, tmp_path):
    p1 = make_jpeg_with_subsampling(tmp_path / "a.jpg", (64, 64), (1, 2, 3), subsampling=0)  # 4:4:4
    p2 = make_jpeg_with_subsampling(tmp_path / "b.jpg", (64, 64), (1, 2, 3), subsampling=2)  # 4:2:0
    out = tmp_path / "joined.jpg"

    try:
        app.build_joined_jpeg(jpegtran_path, cjpeg_path, command_log, p1, p2, "horizontal", QColor("white"), out)
        assert False, "should have raised JoinIncompatibleError"
    except app.JoinIncompatibleError:
        pass
    assert not out.exists()


# -- JoinTab UI -------------------------------------------------------------


def make_join_tab(jpegtran_path, command_log, session_state):
    return app.JoinTab(jpegtran_path, command_log, session_state)


def test_dropping_two_files_fills_both_slots(qapp, jpegtran_path, command_log, session_state, sample_jpeg, make_jpeg):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    second = make_jpeg()
    tab._on_files_dropped([sample_jpeg, second])
    assert tab._first_path == sample_jpeg
    assert tab._second_path == second
    assert tab.join_button.isEnabled()


def test_dropping_more_than_two_keeps_only_the_last_two(qapp, jpegtran_path, command_log, session_state, make_jpeg):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    a, b, c = make_jpeg(), make_jpeg(), make_jpeg()
    tab._on_files_dropped([a, b, c])
    assert (tab._first_path, tab._second_path) == (b, c)


def test_a_single_later_drop_evicts_the_oldest(qapp, jpegtran_path, command_log, session_state, make_jpeg):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    a, b, c = make_jpeg(), make_jpeg(), make_jpeg()
    tab._on_files_dropped([a, b])
    tab._on_files_dropped([c])
    assert (tab._first_path, tab._second_path) == (b, c)


def test_switch_button_swaps_the_two_images(qapp, jpegtran_path, command_log, session_state, sample_jpeg, make_jpeg):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    second = make_jpeg()
    tab._on_files_dropped([sample_jpeg, second])
    tab._on_switch_clicked()
    assert tab._first_path == second
    assert tab._second_path == sample_jpeg


def test_default_filename_and_target_folder(qapp, jpegtran_path, command_log, session_state, sample_jpeg, make_jpeg):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    second = make_jpeg(Path(sample_jpeg).parent / "other.jpg")
    tab._on_files_dropped([sample_jpeg, second])
    assert tab.output_settings.filename() == "sample_other.jpg"
    assert tab.output_settings.target_folder() is None  # "source folder" still selected
    assert tab.output_settings.output_path_for(sample_jpeg.parent) == sample_jpeg.parent / "sample_other.jpg"


def test_join_button_disabled_until_two_images_loaded(qapp, jpegtran_path, command_log, session_state, sample_jpeg):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    assert not tab.join_button.isEnabled()
    tab._on_files_dropped([sample_jpeg])
    assert not tab.join_button.isEnabled()


def test_direction_combo_updates_switch_button_icon(qapp, jpegtran_path, command_log, session_state):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    horizontal_icon = tab.switch_button.icon()
    tab.direction_combo.setCurrentIndex(1)  # vertical
    vertical_icon = tab.switch_button.icon()
    assert horizontal_icon.cacheKey() != vertical_icon.cacheKey()


def test_corrupt_drop_is_rejected_and_not_kept_loaded(qapp, jpegtran_path, command_log, session_state, sample_jpeg, tmp_path):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"not a real jpeg")
    with patch.object(app.QMessageBox, "critical") as mock_critical:
        tab._on_files_dropped([sample_jpeg, corrupt])
        assert mock_critical.called
    assert tab._first_path == sample_jpeg
    assert tab._second_path is None
    assert not tab.join_button.isEnabled()


def test_drop_zone_and_preview_both_feed_the_same_handler(qapp, jpegtran_path, command_log, session_state, sample_jpeg, make_jpeg):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    second = make_jpeg()
    synth_drop(tab.drop_zone, [sample_jpeg])
    synth_drop(tab.preview, [second])
    assert (tab._first_path, tab._second_path) == (sample_jpeg, second)


def test_end_to_end_join_via_the_tab(qapp, jpegtran_path, cjpeg_path, command_log, session_state, sample_jpeg, make_jpeg, tmp_path):
    tab = make_join_tab(jpegtran_path, command_log, session_state)
    second = make_jpeg()
    tab._on_files_dropped([sample_jpeg, second])
    tab.output_settings.target_folder_radio.setChecked(True)
    tab.output_settings._target_folder = tmp_path
    tab.output_settings.target_folder_field.setText(str(tmp_path))

    tab._do_join()

    out = tmp_path / "sample_extra0.jpg"
    assert out.exists(), tab.status_label.text()
