"""Absent and unreadable are not the same thing.

`Path.exists()` returns **False** when the OS refuses access — macOS TCC, unix
permissions, an unmounted network share. Used as a guard before reading a file,
it silently turns "I am not allowed to read this" into "this does not exist",
and the caller falls back to defaults without anyone noticing.

For a publishing tool that failure is not cosmetic. A plan file that cannot be
read looks like *no plan*, so the scheduler quietly books against built-in
defaults; a store that cannot be read looks like *no history*, so the damped
planner sees an empty performance record. Both produce confident, wrong output.

The rule this module enforces: a read either returns content, returns ``None``
because the file genuinely is not there, or raises. It never degrades quietly.
"""
from __future__ import annotations

from pathlib import Path


class Unreadable(OSError):
    """The path exists (or its status is unknowable) but could not be read."""


def read_text_if_present(path: Path) -> str | None:
    """Return file contents, or ``None`` if the file genuinely does not exist.

    Raises :class:`Unreadable` for every other failure — permission denied,
    unreadable mount, a directory where a file was expected. Callers that want
    a default must choose it explicitly after catching, so the fallback is a
    decision in the code rather than an accident of the filesystem.
    """
    try:
        return Path(path).read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:                       # PermissionError, NotADirectoryError, EIO …
        raise Unreadable(f"{path} exists but could not be read: {exc}") from exc


def read_lines_if_present(path: Path) -> list[str]:
    """``read_text_if_present`` split into lines; ``[]`` when the file is absent."""
    raw = read_text_if_present(path)
    return raw.splitlines() if raw is not None else []
