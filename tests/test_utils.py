"""Pure-function / small-helper tests: no widgets, no jpegtran subprocess."""
from pathlib import Path

import app


def test_display_path_shortens_home_dir():
    home = Path.home()
    assert app.display_path(home / "Desktop" / "photo.jpg") == "~/Desktop/photo.jpg"
    assert app.display_path(home) == "~"


def test_display_path_leaves_other_paths_alone():
    assert app.display_path("/tmp/photo.jpg") == "/tmp/photo.jpg"


def test_display_path_does_not_mangle_a_sibling_with_a_similar_prefix():
    # e.g. home=/Users/andras must not turn /Users/andras2/foo into "~2/foo"
    home = Path.home()
    fake_sibling = str(home) + "2/foo.jpg"
    assert app.display_path(fake_sibling) == fake_sibling


def test_build_postfix_path_default_dir_is_source_dir():
    result = app.build_postfix_path(Path("/a/b/photo.jpg"), "_rot")
    assert result == Path("/a/b/photo_rot.jpg")


def test_build_postfix_path_with_explicit_target_dir():
    result = app.build_postfix_path(Path("/a/b/photo.jpg"), "_rot", Path("/elsewhere"))
    assert result == Path("/elsewhere/photo_rot.jpg")


def test_is_jpeg():
    assert app.is_jpeg(Path("photo.jpg"))
    assert app.is_jpeg(Path("photo.JPEG"))
    assert not app.is_jpeg(Path("photo.png"))


def test_command_log_shortens_home_paths_in_logged_line(qapp):
    home = Path.home()
    log = app.CommandLog()
    lines = []
    log.logged.connect(lines.append)

    infile = home / "Desktop" / "in.jpg"
    outfile = home / "Desktop" / "out.jpg"
    log.log(["/usr/bin/jpegtran", "-copy", "all", "-rotate", "90", "-outfile", str(outfile), str(infile)], True)

    assert len(lines) == 1
    assert "~" in lines[0]
    assert str(home) not in lines[0]


def test_command_log_includes_stderr_on_failure(qapp):
    log = app.CommandLog()
    lines = []
    log.logged.connect(lines.append)
    log.log(["jpegtran", "-outfile", "x.jpg", "in.jpg"], False, "bad file")
    assert "FAILED" in lines[0]
    assert "bad file" in lines[0]
