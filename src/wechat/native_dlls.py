"""Locate and validate the native Windows DLLs webot needs at runtime.

These binaries are deliberately **not** tracked in git: ``.gitignore``
excludes ``native/windows/*.dll`` because they are third-party /
reverse-engineered artifacts (WCDB + a WeChat hook DLL).  They are,
however, embedded inside the released ``webot.exe``, so a source
checkout can recover them with ``python tools/fetch_native.py``.

For packaged runs the DLLs live next to the EXE (or in ``_MEIPASS``);
for source runs they must be placed in ``native/windows/``.

This module exists so that a missing DLL produces an accurate,
actionable error instead of being silently mistaken for a timeout.
"""
from __future__ import annotations

import ctypes as ct
import logging
import os
import sys
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

#: DLLs required by the Windows backends (key extraction + WCDB direct read).
#: ``wx_key.dll`` also needs the three VC++ runtime DLLs beside it, otherwise
#: ``LoadLibrary`` fails with error 126 ("module could not be found").
REQUIRED_WINDOWS_DLLS: tuple[str, ...] = (
    "wx_key.dll",          # WeChat hook used for WCDB key extraction
    "wcdb_api.dll",        # ctypes wrapper around WCDB
    "WCDB.dll",            # wcdb_api.dll dependency
    "MSVCP140.dll",        # VC++ runtime (wx_key.dll dependency)
    "VCRUNTIME140.dll",    # VC++ runtime (wx_key.dll dependency)
    "VCRUNTIME140_1.dll",  # VC++ runtime (wx_key.dll dependency)
)

#: Referenced by src/wechat/native/injector.py, which is not the primary
#: key-extraction path — missing files here are not fatal.
OPTIONAL_WINDOWS_DLLS: tuple[str, ...] = ("keyhook.dll",)

#: Relative location every candidate directory is rooted at.
_NATIVE_SUBDIR = Path("native") / "windows"

_FETCH_HINT = "python tools/fetch_native.py"


class NativeDllMissingError(RuntimeError):
    """Raised when a required native DLL is not present on disk."""


def project_root() -> Path:
    """Repository root (the directory containing ``native/``)."""
    return Path(__file__).resolve().parent.parent.parent


def candidate_dll_dirs() -> list[Path]:
    """Directories searched for native DLLs, most specific first.

    A frozen build looks inside the PyInstaller bundle and next to the
    EXE; a source run falls back to the checkout's ``native/windows/``.
    """
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass) / _NATIVE_SUBDIR)
        exe_dir = Path(sys.executable).resolve().parent
        dirs.append(exe_dir / _NATIVE_SUBDIR)
    dirs.append(project_root() / _NATIVE_SUBDIR)
    # De-duplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def canonical_dll_dir() -> Path:
    """The directory DLLs are expected to be installed into.

    For a frozen build this is next to the EXE (writable, unlike
    ``_MEIPASS``); for a source run it is the checkout's
    ``native/windows/``.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / _NATIVE_SUBDIR
    return project_root() / _NATIVE_SUBDIR


def find_dll(name: str) -> Path | None:
    """Return the first existing path for ``name``, or None."""
    for d in candidate_dll_dirs():
        candidate = d / name
        if candidate.exists():
            return candidate
    return None


def missing_in(dll_dir: Path, required: Sequence[str] = REQUIRED_WINDOWS_DLLS) -> list[str]:
    """Names from ``required`` that are absent from ``dll_dir``."""
    if not dll_dir.is_dir():
        return list(required)
    return [n for n in required if not (dll_dir / n).exists()]


def scan(required: Sequence[str] = REQUIRED_WINDOWS_DLLS) -> tuple[Path, list[str], list[Path]]:
    """Find the most complete DLL directory.

    Returns:
        ``(best_dir, missing, searched)`` — ``best_dir`` is the candidate
        holding the most required DLLs (falling back to the canonical
        location when none exist), ``missing`` lists what is still absent
        there, and ``searched`` is every directory that was examined.
    """
    searched = candidate_dll_dirs()
    best: Path | None = None
    best_missing: list[str] = list(required)

    for d in searched:
        if not d.is_dir():
            continue
        miss = missing_in(d, required)
        if best is None or len(miss) < len(best_missing):
            best, best_missing = d, miss
        if not miss:
            break

    if best is None:
        best = canonical_dll_dir()
        best_missing = list(required)
    return best, best_missing, searched


def describe_missing(
    dll_dir: Path,
    missing: Sequence[str],
    searched: Sequence[Path] | None = None,
    include_searched: bool = True,
) -> str:
    """Build an actionable, user-facing explanation of missing DLLs.

    Args:
        dll_dir: Directory the DLLs are expected in.
        missing: Names that are absent (``missing[0]`` is the one that
            triggered the error).
        searched: Override the list of searched directories.
        include_searched: Set False when the caller already printed the
            search path, to avoid repeating it.
    """
    lines = [f"缺少原生 DLL：{dll_dir / missing[0]}"]

    if include_searched:
        if searched is None:
            searched = candidate_dll_dirs()
        lines.append("")
        lines.append("已查找目录：")
        for d in searched:
            mark = "" if d.is_dir() else "（不存在）"
            lines.append(f"  - {d}{mark}")

    if len(missing) > 1:
        lines.append("")
        lines.append(
            f"{dll_dir} 还缺少 {len(missing) - 1} 个 DLL："
            + ", ".join(missing[1:])
        )

    lines += [
        "",
        "这些二进制不在 git 仓库中，发行版 webot.exe 内已内含。获取方式：",
        f"  {_FETCH_HINT}",
        f"或手动把 {len(REQUIRED_WINDOWS_DLLS)} 个 DLL 复制到 {dll_dir}",
    ]
    return "\n".join(lines)


def ensure_dll(name: str) -> Path:
    """Return the path to ``name``, or raise NativeDllMissingError.

    The error message lists every searched directory plus the remaining
    missing DLLs, so a single failure explains the whole situation.
    """
    found = find_dll(name)
    if found is not None:
        return found

    dll_dir, missing, searched = scan()
    # The requested DLL might be optional and therefore absent from
    # `missing`; make sure it heads the list shown to the user.
    if name not in missing:
        missing = [name, *missing]
    raise NativeDllMissingError(describe_missing(dll_dir, missing, searched))


def verify_loadable(dll_path: Path) -> str | None:
    """Try to load ``dll_path``; return None if OK, else an error string.

    Distinguishes the two failures that actually happen in the field:
    error 126 (a dependency such as the VC++ runtime is missing) and
    error 193 (wrong architecture).
    """
    dll_dir = str(Path(dll_path).parent)
    if dll_dir:
        try:
            os.add_dll_directory(dll_dir)
        except AttributeError:  # Python < 3.8
            k32 = ct.WinDLL("kernel32", use_last_error=True)
            k32.SetDllDirectoryW(dll_dir)

    try:
        ct.WinDLL(str(dll_path))
        return None
    except OSError as e:
        err = ct.get_last_error()
        if err == 126:
            return (
                f"{Path(dll_path).name} 加载失败：缺少依赖库（错误码 126）。"
                f"请确认 {dll_dir} 下的 VC++ 运行时 DLL 完整"
                f"（MSVCP140.dll / VCRUNTIME140.dll / VCRUNTIME140_1.dll）。\n"
                f"详情: {e}"
            )
        if err == 193:
            return (
                f"{Path(dll_path).name} 加载失败：不是有效的 Win32/Win64 程序"
                f"（错误码 193）。请确认 DLL 架构与当前 Python 一致。\n详情: {e}"
            )
        return f"{Path(dll_path).name} 加载失败（错误码 {err}）: {e}"


def format_report() -> str:
    """Human-readable status of every required DLL (used by the CLI)."""
    dll_dir, missing, searched = scan()
    present = [n for n in REQUIRED_WINDOWS_DLLS if n not in missing]

    lines = [f"原生 DLL 目录: {dll_dir}", ""]
    lines.append(f"必需 DLL ({len(present)}/{len(REQUIRED_WINDOWS_DLLS)} 就绪):")
    for n in REQUIRED_WINDOWS_DLLS:
        if n in missing:
            lines.append(f"  [缺失] {n}")
        else:
            p = dll_dir / n
            note = verify_loadable(p)
            if note:
                lines.append(f"  [异常] {n} — {note}")
            else:
                lines.append(f"  [正常] {n}  ({p.stat().st_size:,} bytes)")

    optional_present = [n for n in OPTIONAL_WINDOWS_DLLS if find_dll(n)]
    if optional_present:
        lines.append("")
        lines.append(f"可选 DLL: {', '.join(optional_present)}")

    lines.append("")
    lines.append("查找路径:")
    for d in searched:
        lines.append(f"  - {d}{'' if d.is_dir() else '（不存在）'}")

    if missing:
        lines += ["", describe_missing(dll_dir, missing, searched,
                                       include_searched=False)]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: report DLL status; exit 1 when something is missing."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(format_report())
    _, missing, _ = scan()
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
