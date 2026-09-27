from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable

_REPARSE_POINT = 0x400


def _is_reparse(path: Path) -> bool:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    attrs = int(getattr(stat, "st_file_attributes", 0) or 0)
    return bool(attrs & _REPARSE_POINT)


def assert_owned_path(
    value: str | Path,
    *,
    roots: Iterable[str | Path],
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError("OWNED_PATH_NOT_ABSOLUTE")
    if ".." in path.parts:
        raise RuntimeError("OWNED_PATH_TRAVERSAL_REJECTED")

    allowed = [Path(root) for root in roots]
    if not allowed:
        raise RuntimeError("OWNED_PATH_ROOTS_EMPTY")

    raw_match: Path | None = None
    for root in allowed:
        raw_root = root.absolute()
        try:
            if path.absolute().is_relative_to(raw_root):
                raw_match = root
                break
        except ValueError:
            continue
    if raw_match is None:
        raise RuntimeError("OWNED_PATH_OUTSIDE_ROOT")

    resolved_root = raw_match.resolve()
    resolved_path = path.resolve(strict=False)
    if not resolved_path.is_relative_to(resolved_root):
        raise RuntimeError("OWNED_PATH_RESOLVE_ESCAPE")

    current = resolved_root
    if _is_reparse(current):
        raise RuntimeError("OWNED_ROOT_REPARSE_REJECTED")
    relative = resolved_path.relative_to(resolved_root)
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            raise RuntimeError("OWNED_PATH_REPARSE_REJECTED")
    return resolved_path


def remove_owned_path(
    value: str | Path,
    *,
    roots: Iterable[str | Path],
) -> int:
    path = assert_owned_path(value, roots=roots)
    if not path.exists():
        return 0
    if path.is_dir():
        size = sum(
            item.stat().st_size
            for item in path.rglob("*")
            if item.is_file() and not item.is_symlink()
        )
        shutil.rmtree(path)
    else:
        size = int(path.stat().st_size)
        path.unlink()
    if path.exists():
        raise RuntimeError("OWNED_PATH_DELETE_VERIFICATION_FAILED")
    return int(size)


def file_size_tree(value: str | Path) -> int:
    path = Path(value)
    if not path.exists():
        return 0
    if path.is_file():
        return int(path.stat().st_size)
    return sum(
        int(item.stat().st_size)
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )
