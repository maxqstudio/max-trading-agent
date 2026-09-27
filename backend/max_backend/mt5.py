from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _read_origin(path: Path) -> str:
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    for encoding in ("utf-16", "utf-8"):
        try:
            text = raw.decode(encoding).strip().lstrip("\ufeff")
            if text:
                return text
        except UnicodeError:
            continue
    return ""


def _auto_terminal_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.getenv(env_name)
        if base:
            candidates.append(Path(base) / "MetaTrader 5" / "terminal64.exe")
    return candidates


def _data_roots() -> list[Path]:
    appdata = os.getenv("APPDATA")
    if not appdata:
        return []
    root = Path(appdata) / "MetaQuotes" / "Terminal"
    if not root.is_dir():
        return []
    return [p for p in root.iterdir() if p.is_dir() and (p / "MQL5").is_dir()]


def _unavailable(reason: str, **fields: str) -> dict[str, Any]:
    return {"status": "UNAVAILABLE", "reason": reason, **fields}


def detect_mt5() -> dict[str, Any]:
    explicit_present = "MAX_MT5_TERMINAL" in os.environ
    if explicit_present:
        raw = os.environ.get("MAX_MT5_TERMINAL", "").strip()
        if not raw:
            return _unavailable("EXPLICIT_TERMINAL_EMPTY")
        terminal = Path(raw)
        if not terminal.is_file():
            return _unavailable(
                "EXPLICIT_TERMINAL_NOT_FOUND",
                terminal=str(terminal),
            )
    else:
        terminal = next(
            (p for p in _auto_terminal_candidates() if p.is_file()),
            None,
        )
        if terminal is None:
            return _unavailable("TERMINAL_NOT_FOUND")

    if terminal.name.lower() != "terminal64.exe":
        return _unavailable("WRONG_EXECUTABLE", terminal=str(terminal))

    metaeditor = terminal.with_name("metaeditor64.exe")
    if not metaeditor.is_file():
        return _unavailable(
            "METAEDITOR_NOT_FOUND",
            terminal=str(terminal),
            metaeditor=str(metaeditor),
        )

    install_dir = str(terminal.parent.resolve()).casefold()
    roots = _data_roots()
    matched = [
        path
        for path in roots
        if _read_origin(path / "origin.txt").casefold() == install_dir
    ]
    if not matched:
        return _unavailable(
            "DATA_ROOT_MISSING",
            terminal=str(terminal),
            metaeditor=str(metaeditor),
        )
    if len(matched) > 1:
        return _unavailable(
            "DATA_ROOT_AMBIGUOUS",
            terminal=str(terminal),
            metaeditor=str(metaeditor),
        )

    return {
        "status": "READY_EXECUTABLE_AND_DATA_ROOT",
        "reason": "READY",
        "terminal": str(terminal),
        "metaeditor": str(metaeditor),
        "data_root": str(matched[0]),
        "execution_truth": "MT5_STRATEGY_TESTER",
        "terminal_authority": "EXPLICIT" if explicit_present else "AUTO_DETECTED",
    }
