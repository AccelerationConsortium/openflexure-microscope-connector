import pathlib

import pytest

from unitelabs.openflexure_microscope.io import errors, export, types
from unitelabs.openflexure_microscope.io.export import export_capture, safe_file_name, write_new_file
from unitelabs.openflexure_microscope.io.v2.client import V2Client
from unitelabs.openflexure_microscope.io.v2.simulation import SIMULATED_JPEG, SimulatedBackend


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("cells.jpeg", "cells.jpeg"),
        ("my scan #2 (ok).jpeg", "my scan #2 (ok).jpeg"),
        ("../../etc/passwd", "passwd.jpeg"),
        ("..\\..\\Windows\\win.ini", "win.ini.jpeg"),
        ('bad<>:"|?*name.jpeg', "bad_______name.jpeg"),
        ("trailing. ", "trailing.jpeg"),
        ("", "capture.jpeg"),
        ("CON.jpeg", "_CON.jpeg"),
        ("con.tar.jpeg", "_con.tar.jpeg"),
        ("aux .jpeg", "_aux.jpeg"),
        ("COM\u00b9.jpeg", "_COM\u00b9.jpeg"),
        ("CONIN$.jpeg", "_CONIN$.jpeg"),
        (".jpeg", "capture.jpeg"),
        ("..", "capture.jpeg"),
        ("noextension", "noextension.jpeg"),
        ("plate 3.5mm", "plate 3.5mm.jpeg"),  # a dot is not an extension unless it is .jpeg or .jpg
        ("IMG_01.JPG", "IMG_01.JPG"),
        ("photo.jpg", "photo.jpg"),
        ("x.123456789012345 6", "x.123456789012345 6.jpeg"),
        ("..cells", "cells.jpeg"),  # no hidden files on macOS/Linux
        (".a.jpeg", "a.jpeg"),
        ("..jpeg", "capture.jpeg"),
        ("\ud800.jpeg", "_.jpeg"),  # lone surrogates cannot be encoded in a file name
        ("a\udc80.jpeg", "a_.jpeg"),
    ],
)
def test_safe_file_name(name, expected):
    assert safe_file_name(name) == expected


def test_write_new_file_never_overwrites(tmp_path):
    assert write_new_file(tmp_path, "a.jpeg", b"x") == tmp_path / "a.jpeg"
    assert write_new_file(tmp_path, "a.jpeg", b"y") == tmp_path / "a (1).jpeg"
    assert write_new_file(tmp_path, "a.jpeg", b"z") == tmp_path / "a (2).jpeg"
    assert [(tmp_path / n).read_bytes() for n in ("a.jpeg", "a (1).jpeg", "a (2).jpeg")] == [b"x", b"y", b"z"]


@pytest.fixture
def api() -> V2Client:
    return V2Client(
        real_base_url="http://microscope.local:5000/api/v2", simulated=True, simulated_backend=SimulatedBackend()
    )


async def test_export_capture_writes_the_image_and_returns_its_path(api, tmp_path):
    info = await api.capture_to_storage("cells", [], types.CaptureResolution.PREVIEW)
    folder = tmp_path / "exports" / "nested"
    path = await export_capture(api, info.id, folder)
    assert path == folder / "cells.jpeg"
    assert path.read_bytes() == SIMULATED_JPEG
    again = await export_capture(api, info.id, folder)
    assert again == folder / "cells (1).jpeg"
    assert path.read_bytes() == SIMULATED_JPEG  # the first export was not overwritten


async def test_export_of_unknown_capture_raises_not_found(api, tmp_path):
    with pytest.raises(errors.CaptureNotFoundError):
        await export_capture(api, "nope", tmp_path)
    assert list(tmp_path.iterdir()) == []


async def test_unwritable_folder_raises_export_failed(api, tmp_path):
    info = await api.capture_to_storage("cells", [], types.CaptureResolution.PREVIEW)
    blocker = tmp_path / "a-file"
    blocker.write_bytes(b"not a folder")
    with pytest.raises(errors.ExportFailedError, match="a-file"):
        await export_capture(api, info.id, blocker / "sub")


def test_a_failed_write_leaves_no_partial_file(tmp_path):
    with pytest.raises(TypeError):
        write_new_file(tmp_path, "x.jpeg", "not bytes")  # type: ignore[arg-type]
    assert list(pathlib.Path(tmp_path).iterdir()) == []


def test_a_name_taken_by_a_dangling_link_is_skipped_not_retried_forever(tmp_path, monkeypatch):
    real_open = pathlib.Path.open
    attempts = []

    def fake_open(self, mode="r", *args, **kwargs):
        if mode == "xb" and self.name == "x.jpeg":  # exists() is False for a dangling link, but creating it fails
            attempts.append(self)
            if len(attempts) > 20:
                raise RuntimeError("retried the same file name forever")
            raise FileExistsError(17, "File exists", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", fake_open)
    path = export.write_new_file(tmp_path, "x.jpeg", b"data")
    assert path.name == "x (1).jpeg"
    assert path.read_bytes() == b"data"


async def test_running_out_of_free_names_is_an_export_error(api, tmp_path, monkeypatch):
    info = await api.capture_to_storage("cells", [], types.CaptureResolution.PREVIEW)
    real_open = pathlib.Path.open
    attempts = []

    def every_name_is_taken(self, mode="r", *args, **kwargs):
        if mode == "xb":
            attempts.append(self)
            if len(attempts) > 20:
                raise RuntimeError("retried forever")
            raise FileExistsError(17, "File exists", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(export, "_MAX_NAME_ATTEMPTS", 3)
    monkeypatch.setattr(pathlib.Path, "open", every_name_is_taken)
    with pytest.raises(errors.ExportFailedError, match="cells"):
        await export_capture(api, info.id, tmp_path)
    assert len(attempts) == 3


def test_a_failed_close_leaves_no_partial_file(tmp_path, monkeypatch):
    real_open = pathlib.Path.open

    class CloseFails:
        def __init__(self, handle):
            self._handle = handle

        def write(self, data):
            return self._handle.write(data)

        def close(self):
            self._handle.close()
            raise OSError(28, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.close()

    def fake_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        return CloseFails(handle) if mode == "xb" else handle

    monkeypatch.setattr(pathlib.Path, "open", fake_open)
    with pytest.raises(OSError, match="No space"):
        export.write_new_file(tmp_path, "x.jpeg", b"data")
    assert list(tmp_path.iterdir()) == []


def test_long_names_are_shortened_to_a_safe_length():
    ascii_name = safe_file_name("a" * 300 + ".jpeg")
    assert ascii_name.endswith(".jpeg")
    assert len(ascii_name.encode("utf-8")) <= 150
    unicode_name = safe_file_name("\u00e9" * 300 + ".jpeg")
    assert unicode_name.endswith(".jpeg")
    assert unicode_name.startswith("\u00e9")
    assert len(unicode_name.encode("utf-8")) <= 150


async def test_export_error_names_the_file_it_could_not_write(api, tmp_path, monkeypatch):
    info = await api.capture_to_storage("cells", [], types.CaptureResolution.PREVIEW)
    real_open = pathlib.Path.open

    def invalid(self, mode="r", *args, **kwargs):
        if mode == "xb":
            raise OSError(22, "Invalid argument", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", invalid)
    with pytest.raises(errors.ExportFailedError, match=r"cells\.jpeg"):
        await export_capture(api, info.id, tmp_path)


def test_a_reserved_name_exposed_by_shortening_still_gets_its_prefix():
    assert safe_file_name("NUL" + " " * 150 + "x.jpeg") == "_NUL.jpeg"


@pytest.mark.parametrize(
    "name",
    [
        "NUL" + " " * 150 + "x.jpeg",
        "a." + " " * 16 + "b",
        "con" + "." * 200,
        "x" * 149 + " .jpeg",
        "\u00e9" * 100 + ".JPG",
        ".." + "a" * 300,
        "COM1 " + "." * 3 + "jpeg",
        " . . ",
    ],
)
def test_every_exported_name_is_safe(name):
    result = safe_file_name(name)
    stem = result.rpartition(".")[0]
    assert result.lower().endswith((".jpeg", ".jpg"))
    assert len(result.encode("utf-8")) <= 150
    assert stem
    assert not result.startswith((".", " "))
    assert not stem.endswith((" ", "."))
    assert stem.partition(".")[0].rstrip(" ").upper() not in export._RESERVED_NAMES


def test_a_folder_with_the_capture_name_is_skipped(tmp_path):
    (tmp_path / "cells.jpeg").mkdir()  # on Windows creating a file over a folder fails with PermissionError
    assert write_new_file(tmp_path, "cells.jpeg", b"x") == tmp_path / "cells (1).jpeg"


async def test_a_capture_name_that_cannot_be_sent_is_a_capture_error(api, tmp_path, monkeypatch):
    info = await api.capture_to_storage("cells", [], types.CaptureResolution.PREVIEW)
    stored = await api._capture(info.id)
    monkeypatch.setattr(api, "_capture", lambda capture_id: _resolved({**stored, "name": "a\udc80.jpeg"}))
    with pytest.raises(errors.CaptureFailedError, match="cannot be sent"):
        await api.download_capture(info.id)
    with pytest.raises(errors.CaptureFailedError, match="cannot be sent"):
        await export_capture(api, info.id, tmp_path)


async def _resolved(value):
    return value


async def test_a_read_only_export_folder_is_an_export_error(api, tmp_path, monkeypatch):
    info = await api.capture_to_storage("cells", [], types.CaptureResolution.PREVIEW)
    real_open = pathlib.Path.open

    def read_only(self, mode="r", *args, **kwargs):
        if mode == "xb":
            raise PermissionError(13, "Permission denied", str(self))
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", read_only)
    with pytest.raises(errors.ExportFailedError, match="Permission denied"):
        await export_capture(api, info.id, tmp_path)
