"""Low-level jpegtran plumbing: run_jpegtran/reset_exif_orientation, and the
empirically-verified crop-offset rounding behavior the CROP_SNAP=16 grid in
ImageCropView depends on (see jtransform_adjust_parameters in libjpeg's
transupp.c)."""
from PyQt6.QtGui import QImage

import app


def test_find_jpegtran_returns_a_path(jpegtran_path):
    assert jpegtran_path
    assert app.find_jpegtran() == jpegtran_path


def test_rotate_swaps_dimensions(qapp, jpegtran_path, sample_jpeg, tmp_path):
    out = tmp_path / "rotated.jpg"
    result = app.run_jpegtran(jpegtran_path, ["-rotate", "90"], sample_jpeg, out)
    assert result.ok, result.stderr

    orig = QImage(str(sample_jpeg))
    rotated = QImage(str(out))
    assert (rotated.width(), rotated.height()) == (orig.height(), orig.width())


def test_crop_produces_exact_requested_size(qapp, jpegtran_path, sample_jpeg, tmp_path):
    out = tmp_path / "cropped.jpg"
    result = app.run_jpegtran(jpegtran_path, ["-crop", "96x80+0+0"], sample_jpeg, out)
    assert result.ok, result.stderr
    img = QImage(str(out))
    assert (img.width(), img.height()) == (96, 80)


def test_misaligned_crop_offset_extends_outward_by_the_remainder(qapp, jpegtran_path, sample_jpeg, tmp_path):
    """This is *why* ImageCropView snaps the selection origin to a 16px grid:
    without alignment, jpegtran doesn't error on an off-grid offset — it
    silently rounds the origin down and grows the output to compensate,
    so what you see would NOT match what gets saved."""
    out = tmp_path / "misaligned.jpg"
    result = app.run_jpegtran(jpegtran_path, ["-crop", "96x80+10+5"], sample_jpeg, out)
    assert result.ok, result.stderr
    img = QImage(str(out))
    # offset(10,5) against a 16px iMCU grid -> output grows by the remainder:
    # 96+10=106, 80+5=85 (assuming 4:2:0 chroma subsampling, the common case).
    assert (img.width(), img.height()) == (106, 85)


def test_aligned_crop_offset_has_no_extension(qapp, jpegtran_path, sample_jpeg, tmp_path):
    out = tmp_path / "aligned.jpg"
    result = app.run_jpegtran(jpegtran_path, ["-crop", "96x80+16+16"], sample_jpeg, out)
    assert result.ok, result.stderr
    img = QImage(str(out))
    assert (img.width(), img.height()) == (96, 80), "16px-aligned offset should have zero extension"


def test_reset_exif_orientation_is_a_noop_on_a_file_with_no_exif(qapp, sample_jpeg):
    # Should not raise even though the fixture has no EXIF Orientation tag.
    app.reset_exif_orientation(sample_jpeg)
