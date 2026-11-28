"""Access controls for staged manifest and output replacements."""

import errno
import os
import shutil
import sys
from pathlib import Path


def _copy_windows_security(source: Path, staged: Path) -> None:
    import ctypes
    from ctypes import wintypes

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    fields = 0x7  # Owner, group and discretionary ACL.

    def descriptor(path: Path):
        size = wintypes.DWORD()
        security.GetFileSecurityW(str(path), fields, None, 0, ctypes.byref(size))
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not security.GetFileSecurityW(str(path), fields, buffer, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer

    original = descriptor(source)
    expected = original.raw
    if expected == descriptor(staged).raw:
        return
    control, revision = wintypes.WORD(), wintypes.DWORD()
    if not security.GetSecurityDescriptorControl(original, ctypes.byref(control), ctypes.byref(revision)):
        raise ctypes.WinError(ctypes.get_last_error())
    # SetFileSecurity keeps SE_DACL_AUTO_INHERITED when SE_DACL_AUTO_INHERIT_REQ is set.
    if control.value & 0x0400 and not security.SetSecurityDescriptorControl(original, 0x0100, 0x0100):
        raise ctypes.WinError(ctypes.get_last_error())
    protection = 0x80000000 if control.value & 0x1000 else 0x20000000
    if not security.SetFileSecurityW(str(staged), fields | protection, original):
        raise ctypes.WinError(ctypes.get_last_error())
    if descriptor(staged).raw != expected:
        raise PermissionError("The staged file could not retain the destination's access rules.")


def prepare_replacement(source: Path, staged: Path) -> None:
    """Set permissions on the empty staging file before writing the new content."""
    staged.touch(exist_ok=False)
    if os.name == "nt":
        _copy_windows_security(source, staged)
    else:
        original, new = source.stat(), staged.stat()
        if (original.st_uid, original.st_gid) != (new.st_uid, new.st_gid):
            os.chown(staged, original.st_uid, original.st_gid)
    shutil.copystat(source, staged)
    if sys.platform == "darwin":
        import ctypes

        # The native copyfile API copies the macOS ACL separately from mode and xattrs.
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        if libc.copyfile(os.fsencode(source), os.fsencode(staged), None, 1) != 0:  # COPYFILE_ACL
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), staged)
    if sys.platform.startswith("linux"):
        # copystat can skip an ACL or leave an ACL inherited from the staging directory.
        attribute = "system.posix_acl_access"
        try:
            acl = os.getxattr(source, attribute)
        except OSError as exc:
            if exc.errno == errno.ENOTSUP:
                return
            if exc.errno != errno.ENODATA:
                raise
            if attribute in os.listxattr(staged):
                os.removexattr(staged, attribute)
        else:
            os.setxattr(staged, attribute, acl)
