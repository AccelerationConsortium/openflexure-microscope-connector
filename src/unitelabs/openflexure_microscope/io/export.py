"""
Save stored captures as files on the computer running the connector.

Nothing here knows about SiLA: `export_capture` fetches a capture through any `MicroscopeApi` and writes it into a
folder, never overwriting an existing file.
"""

import asyncio
import collections.abc
import errno
import itertools
import pathlib
import re
import typing

from .errors import ExportFailedError

if typing.TYPE_CHECKING:
    from .api import MicroscopeApi

# Characters no file system accepts everywhere, plus lone surrogates (text that cannot be encoded at all).
_INVALID_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f\ud800-\udfff]')
# Windows device names; reserved with any extension and with trailing spaces, as in Python's `ntpath.isreserved`.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"{device}{digit}" for device in ("COM", "LPT") for digit in "123456789\u00b9\u00b2\u00b3"}
)
_DEFAULT_STEM = "capture"
_DEFAULT_EXTENSION = "jpeg"
_IMAGE_EXTENSIONS = ("jpeg", "jpg")  # the exported data is always a JPEG image
_MAX_NAME_BYTES = 150  # well inside the 255-byte limit of file systems, leaving room for " (n)" and the folder path
_MAX_NAME_ATTEMPTS = 10_000


def safe_file_name(name: str) -> str:
    """
    Turn a capture name from the microscope into a JPEG file name that is safe on Windows, macOS and Linux.

    Any directory part is dropped; characters invalid in file names, and text that cannot be encoded, become `_`;
    leading and trailing dots and spaces are removed (no hidden files); `.jpeg` is added unless the name already ends
    in `.jpeg` or `.jpg`; long names are shortened; and reserved Windows device names get a `_` prefix.
    """
    base = re.split(r"[\\/]", str(name))[-1]
    base = _INVALID_CHARACTERS.sub("_", base).strip()
    stem, dot, extension = base.rpartition(".")
    if not dot or extension.lower() not in _IMAGE_EXTENSIONS:
        stem, extension = base, _DEFAULT_EXTENSION
    budget = _MAX_NAME_BYTES - 2 - len(extension.encode("utf-8"))  # room for the dot and a "_" prefix
    stem = _truncate(stem.strip(". "), budget).rstrip(". ") or _DEFAULT_STEM
    if stem.partition(".")[0].rstrip(" ").upper() in _RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem}.{extension}"


def _truncate(text: str, max_bytes: int) -> str:
    """Shorten `text` to at most `max_bytes` UTF-8 bytes without splitting a character."""
    return text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")


def _candidate_names(name: str) -> collections.abc.Iterator[str]:
    """Yield `name`, then `name (1).ext`, `name (2).ext`, ..."""
    path = pathlib.PurePath(name)
    yield name
    for number in itertools.count(1):
        yield f"{path.stem} ({number}){path.suffix}"


def write_new_file(folder: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    """
    Create `folder` if needed and write `data` to a file that did not exist before; return its path.

    The file is `name`, or `name (n).ext` with the smallest free n. A partly written file is removed on failure.

    Raises:
      OSError: If the folder or file cannot be created or written, or no free name is found.
    """
    folder.mkdir(parents=True, exist_ok=True)
    for candidate in itertools.islice(_candidate_names(name), _MAX_NAME_ATTEMPTS):
        path = folder / candidate
        try:
            handle = path.open("xb")  # exclusive create: never overwrite, even under a race
        except FileExistsError:
            continue  # taken, possibly by something exists() cannot see, such as a dangling link
        except PermissionError:
            if path.exists():
                continue  # on Windows, a folder with this name refuses the file with "access denied"
            raise
        try:
            with handle:
                handle.write(data)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return path
    msg = f"no free file name for {name!r} after {_MAX_NAME_ATTEMPTS} attempts"
    raise FileExistsError(errno.EEXIST, msg, str(folder))


async def export_capture(api: "MicroscopeApi", capture_id: str, folder: pathlib.Path) -> pathlib.Path:
    """
    Download a stored capture and save it in `folder` under the capture's own (sanitised) name.

    Raises:
      CaptureNotFoundError: If no capture with this identifier exists (raised by the API before anything is written).
      ExportFailedError: If the file cannot be written.
    """
    info = await api.get_capture(capture_id)
    data = await api.download_capture(capture_id)
    folder = pathlib.Path(folder)
    try:
        return await asyncio.to_thread(write_new_file, folder, safe_file_name(info.name), data)
    except OSError as exc:
        reason = exc.strerror or str(exc)
        where = f" ({exc.filename})" if exc.filename and str(exc.filename) != str(folder) else ""
        msg = f"Could not save capture {capture_id!r} into {folder}: {reason}{where}."
        raise ExportFailedError(msg) from exc
