"""Read regular campaign files through handles that exclude links and junctions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
from typing import TextIO


def _open_entry(path: Path, parent_fd: int | None, directory: bool) -> int:
    if os.name == "nt":
        import _winapi
        import msvcrt

        # Open the reparse point itself. While descending, hold each directory
        # with read sharing only so it cannot be replaced or redirected.
        open_reparse_point = 0x00200000
        backup_semantics = 0x02000000  # allows a directory handle
        share_read = 1
        sharing = share_read if directory else share_read | 2 | 4  # also share writes and deletes
        handle = _winapi.CreateFile(
            str(path), _winapi.GENERIC_READ, sharing, 0, _winapi.OPEN_EXISTING,
            open_reparse_point | backup_semantics, 0,
        )
        try:
            return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _winapi.CloseHandle(handle)
            raise

    flags = os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else os.O_NONBLOCK)
    name = path if parent_fd is None else (path.name or ".")
    return os.open(name, flags, dir_fd=parent_fd)


@contextmanager
def _local_fd(path: Path, parent_fd: int | None, *, directory: bool) -> Iterator[int | None]:
    try:
        fd = _open_entry(path, parent_fd, directory)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
            raise
        yield None
        return
    try:
        entry = os.fstat(fd)
        linked = getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        yield fd if not linked and expected_type(entry.st_mode) else None
    finally:
        os.close(fd)


def local_files(
    root: Path, pattern: str, *, recursive: bool = False, preferred_name: str | None = None,
) -> Iterator[tuple[Path, TextIO]]:
    """Yield a path and its verified UTF-8 stream for the current iteration."""
    def walk(directory: Path, parent_fd: int | None) -> Iterator[tuple[Path, TextIO]]:
        with _local_fd(directory, parent_fd, directory=True) as dir_fd:
            if dir_fd is None:
                return
            with os.scandir(directory if os.name == "nt" else dir_fd) as scan:
                entries = sorted(scan, key=lambda entry: entry.name)
            if preferred_name is not None:
                entries.sort(key=lambda entry: entry.name != preferred_name)
            for entry in entries:
                path = directory / entry.name
                if path.match(pattern) and entry.is_file(follow_symlinks=False):
                    with _local_fd(path, dir_fd, directory=False) as fd:
                        if fd is not None:
                            with open(fd, encoding="utf-8", closefd=False) as stream:
                                yield path, stream
                elif recursive and entry.is_dir(follow_symlinks=False):
                    yield from walk(path, dir_fd)

    root = Path(root)
    with _local_fd(root.parent, None, directory=True) as parent_fd:
        if parent_fd is not None:
            yield from walk(root, parent_fd)
