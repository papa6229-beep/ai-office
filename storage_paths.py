"""Recognize only the three explicitly approved local storage migration aliases.

This does not resolve arbitrary symlinks. Descendants are returned lexically so
the caller's existing reparse-point and private-file guards still apply.
"""
import os
from pathlib import Path
import stat


def protected_roots():
    """Private migration staging and originals stay out of routine organizers."""
    home = Path.home()
    suffix = '.C-drive-rollback-20261103'
    return (Path('D:/Aemeath'), home / ('AI_OFFICE_GitHub' + suffix),
            home / ('.codex' + suffix), home / '.cache' / ('codex-runtimes' + suffix))


def _ordinary_ancestors(path):
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            return False
    return True


def canonical(path):
    value = Path(path)
    if os.name != 'nt' or not value.is_absolute() or any(p == '..' for p in value.parts):
        return value
    home = Path.home()
    base = Path('D:/Aemeath')
    aliases = (
        (home / 'AI_OFFICE_GitHub', base / 'AI_OFFICE_GitHub'),
        (home / '.codex', base / 'Codex'),
        (home / '.cache' / 'codex-runtimes', base / 'codex-runtimes'),
    )
    for source, target in aliases:
        try:
            relative = value.relative_to(source)
        except ValueError:
            continue
        try:
            info = source.lstat()
            if getattr(info, 'st_reparse_tag', 0) != 0xA0000003:
                return value
            if (not _ordinary_ancestors(source.parent)
                    or not _ordinary_ancestors(target)
                    or source.resolve(strict=True) != target):
                return value
        except OSError:
            return value
        return target / relative
    return value
