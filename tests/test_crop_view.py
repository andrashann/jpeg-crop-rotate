"""ImageCropView: rotate-rect math, the zoom/pan "virtual camera", tool
switching (crop vs. move), crop-snap precision while zoomed, and the
crop-dimension label positioning regression."""
from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent

import app


def synth_mouse(view, kind, pos: QPoint) -> None:
    ev = QMouseEvent(kind, QPointF(pos), Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    {
        QMouseEvent.Type.MouseButtonPress: view.mousePressEvent,
        QMouseEvent.Type.MouseMove: view.mouseMoveEvent,
        QMouseEvent.Type.MouseButtonRelease: view.mouseReleaseEvent,
    }[kind](ev)


# -- rect rotation math (pure, no image needed) ------------------------------


def test_rotate_rect_cw_then_ccw_round_trips(qapp):
    r = QRect(10, 20, 50, 40)
    cw = app.ImageCropView._rotate_rect(r, clockwise=True, old_w=200, old_h=130)
    back = app.ImageCropView._rotate_rect(cw, clockwise=False, old_w=130, old_h=200)
    assert back == r


def test_rotate_rect_cw_matches_expected_geometry(qapp):
    # 200x130 image, rect (10,20,50,40) rotated CW into a 130x200 canvas.
    r = QRect(10, 20, 50, 40)
    cw = app.ImageCropView._rotate_rect(r, clockwise=True, old_w=200, old_h=130)
    assert cw.getRect() == (70, 10, 40, 50)


# -- toolbar wiring: disabled with no image, reflects zoom/selection state --


def test_toolbar_disabled_with_no_image(qapp):
    view = app.ImageCropView()
    assert not view.toolbar.crop_button.isEnabled()
    assert not view.toolbar.zoom_in_button.isEnabled()


def test_loading_an_image_enables_crop_tool_and_zoom_in(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    assert view.toolbar.crop_button.isChecked()
    assert view.toolbar.zoom_in_button.isEnabled()
    assert not view.toolbar.zoom_out_button.isEnabled()
    assert not view.toolbar.move_button.isEnabled()
    assert view._zoom == 1


# -- zoom in/out/1:1 ----------------------------------------------------------


def test_zoom_in_doubles_up_to_cap(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    for expected in (2, 4, 8, 16, 32):
        view.toolbar.zoom_in_button.click()
        assert view._zoom == expected
    assert not view.toolbar.zoom_in_button.isEnabled()
    view.toolbar.zoom_in_button.click()
    assert view._zoom == 32, "clicking past the cap must be a no-op"


def test_zoom_out_halves_down_to_floor(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(32)
    for expected in (16, 8, 4, 2, 1):
        view.toolbar.zoom_out_button.click()
        assert view._zoom == expected
    view.toolbar.zoom_out_button.click()
    assert view._zoom == 1, "clicking past the floor must be a no-op"


def test_reset_zoom_button(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(4)
    view.toolbar.reset_zoom_button.click()
    assert view._zoom == 1
    assert view._pan == QPointF(0.0, 0.0)


def test_zooming_back_to_one_auto_switches_off_move_tool(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(2)
    view.toolbar.move_button.setChecked(True)
    assert view._tool == "move"

    view.toolbar.reset_zoom_button.click()
    assert view._zoom == 1
    assert view._tool == "crop"
    assert view.toolbar.crop_button.isChecked()


# -- panning ------------------------------------------------------------------


def test_move_tool_pans_without_touching_selection(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(8)
    view.toolbar.move_button.setChecked(True)

    before_pan = QPointF(view._pan)
    synth_mouse(view, QMouseEvent.Type.MouseButtonPress, QPoint(200, 150))
    synth_mouse(view, QMouseEvent.Type.MouseMove, QPoint(150, 150))
    synth_mouse(view, QMouseEvent.Type.MouseButtonRelease, QPoint(150, 150))

    assert view._pan.x() > before_pan.x(), "dragging left should reveal content to the right"
    assert view.crop_rect() is None


def test_pan_is_clamped_to_valid_range(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(8)
    view.toolbar.move_button.setChecked(True)

    for _ in range(20):
        synth_mouse(view, QMouseEvent.Type.MouseButtonPress, QPoint(0, 0))
        synth_mouse(view, QMouseEvent.Type.MouseMove, QPoint(-5000, -5000))
        synth_mouse(view, QMouseEvent.Type.MouseButtonRelease, QPoint(-5000, -5000))

    iw, ih = view._image.width(), view._image.height()
    base_scale = min(400 / iw, 300 / ih, 1.0)
    eff = base_scale * view._zoom
    max_pan_x = max(0.0, iw * eff - 400)
    max_pan_y = max(0.0, ih * eff - 300)
    assert 0.0 <= view._pan.x() <= max_pan_x + 0.01
    assert 0.0 <= view._pan.y() <= max_pan_y + 0.01


def test_crop_tool_drag_creates_selection_not_a_pan(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(8)
    view.toolbar.move_button.setChecked(True)
    view.toolbar.crop_button.setChecked(True)  # back to crop tool
    assert view._tool == "crop"

    pan_before = QPointF(view._pan)
    synth_mouse(view, QMouseEvent.Type.MouseButtonPress, QPoint(50, 50))
    synth_mouse(view, QMouseEvent.Type.MouseMove, QPoint(150, 120))
    synth_mouse(view, QMouseEvent.Type.MouseButtonRelease, QPoint(150, 120))

    assert view.crop_rect() is not None
    assert view._pan == pan_before


# -- crop selection: snapping, precision while zoomed ------------------------


def test_crop_selection_snaps_to_16px_grid_and_stays_in_bounds_while_zoomed(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(16)

    synth_mouse(view, QMouseEvent.Type.MouseButtonPress, QPoint(180, 130))
    synth_mouse(view, QMouseEvent.Type.MouseMove, QPoint(220, 170))
    synth_mouse(view, QMouseEvent.Type.MouseButtonRelease, QPoint(220, 170))

    r = view.crop_rect()
    assert r is not None and r.width() > 0 and r.height() > 0
    assert r.x() % app.CROP_SNAP == 0
    assert r.y() % app.CROP_SNAP == 0
    assert 0 <= r.x() and r.x() + r.width() <= view._image.width()
    assert 0 <= r.y() and r.y() + r.height() <= view._image.height()


def test_clear_selection_keeps_image_and_rotation_loaded(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._crop = QRect(0, 0, 32, 32)
    view.clear_selection()
    assert view.crop_rect() is None
    assert view.has_image()


# -- rotate resets the camera -------------------------------------------------


def test_rotate_resets_zoom_and_pan(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.resize(400, 300)
    view.load_image(sample_jpeg)
    view._set_zoom(8)
    assert view._zoom != 1

    view.rotate_image(clockwise=True)
    assert view._zoom == 1
    assert view._pan == QPointF(0.0, 0.0)


def test_rotate_dimension_swaps(qapp, sample_jpeg):
    view = app.ImageCropView()
    view.load_image(sample_jpeg)
    orig_w, orig_h = view._image.width(), view._image.height()
    view.rotate_image(clockwise=True)
    assert (view._image.width(), view._image.height()) == (orig_h, orig_w)
    assert view.rotation_degrees == 90


# -- crop-dimension label position regression --------------------------------


def test_crop_label_tracks_the_crop_rect_not_the_whole_image(qapp, sample_jpeg):
    """Regression: the label used to be drawn at the whole image's target
    rect x-position instead of the crop rect's own position, which only
    matched when unzoomed/uncentered — panning plus a resize (which
    re-clamps/re-centers the pan) made them visibly diverge."""
    view = app.ImageCropView()
    view.resize(600, 500)
    view.load_image(sample_jpeg)

    view._zoom = 4
    view._pan = QPointF(60.0, 30.0)
    view._recompute_view()
    view._crop = QRect(32, 16, 48, 32)
    view.update()

    crop_widget_rect = view._to_widget_rect(view._crop)
    target = view._to_widget_rect(QRect(0, 0, view._image.width(), view._image.height()))
    assert crop_widget_rect.x() != target.x(), "test setup should exercise the divergent case"

    view.resize(900, 500)
    view.update()
    crop_widget_rect_after = view._to_widget_rect(view._crop)

    import inspect
    src = inspect.getsource(app.ImageCropView.paintEvent)
    assert "drawText(max(0, crop_widget_rect.x())" in src
    assert "drawText(target.x()" not in src
    # crop_widget_rect itself is recomputed correctly on resize (it's the
    # label's positioning source that was the actual bug):
    assert crop_widget_rect_after.x() == crop_widget_rect.x()
