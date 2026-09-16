"""Install native/windows/*.dll from a webot release.

The native DLLs (WCDB + the WeChat key-extraction hook) are intentionally
excluded from git — see the ``native/windows/*.dll`` rule in .gitignore.
They *are* embedded inside the released ``webot.exe`` (a PyInstaller
onefile bundle), so this script downloads a release and unpacks them
into ``native/windows/``, which is where source runs look for them.

PyInstaller's own CArchiveReader does the unpacking, so the archive
format is handled by the library rather than by hand-rolled parsing.

Usage:
    python tools/fetch_native.py                  # newest release
    python tools/fetch_native.py --tag v1.2.2     # a specific release
    python tools/fetch_native.py --from-exe webot.exe
    python tools/fetch_native.py --list           # show what's available
    python tools/fetch_native.py --dry-run        # don't write anything
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.wechat.native_dlls import (  # noqa: E402
    REQUIRED_WINDOWS_DLLS,
    canonical_dll_dir,
)

DEFAULT_REPO = "GuMu599/webot"
EXE_ASSET = "webot.exe"
_TIMEOUT = 60
_CHUNK = 1 << 20  # 1 MiB
_MZ = b"MZ"


class FetchError(RuntimeError):
    """Raised for any recoverable failure in this script."""


# ── GitHub release lookup ─────────────────────────────────────────────

def _api_get(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "webot-fetch-native",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FetchError(f"找不到发布版本: {url}") from e
        raise FetchError(f"GitHub API 请求失败 ({e.code}): {url}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"无法连接 GitHub: {e.reason}") from e


def resolve_release(repo: str, tag: str | None) -> tuple[str, str]:
    """Return ``(tag_name, webot.exe download URL)`` for a release."""
    endpoint = (
        f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        if tag
        else f"https://api.github.com/repos/{repo}/releases/latest"
    )
    data = _api_get(endpoint)

    tag_name = data.get("tag_name", tag or "?")
    for asset in data.get("assets", []):
        if asset.get("name") == EXE_ASSET:
            return tag_name, asset["browser_download_url"]

    available = ", ".join(a.get("name", "?") for a in data.get("assets", []))
    raise FetchError(
        f"发布 {tag_name} 中找不到 {EXE_ASSET}。可用资源: {available or '(无)'}"
    )


def list_releases(repo: str) -> None:
    """Print recent releases and whether they carry webot.exe.

    Markers are deliberately ASCII/GBK-safe: a Windows console running
    codepage 936 cannot encode symbols such as U+2713.
    """
    for rel in _api_get(f"https://api.github.com/repos/{repo}/releases"):
        names = [a.get("name") for a in rel.get("assets", []) if a.get("name")]
        mark = "[有]" if EXE_ASSET in names else "[无]"
        print(f"  {mark} {rel.get('tag_name', '?'):12} {', '.join(names)}")


# ── Download ──────────────────────────────────────────────────────────

def download(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest``, printing coarse progress."""
    req = urllib.request.Request(url, headers={"User-Agent": "webot-fetch-native"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            last_pct = -1
            with open(dest, "wb") as fh:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        if pct != last_pct:
                            last_pct = pct
                            print(
                                f"\r  下载中 {pct:3d}%  "
                                f"({done / 1048576:.1f}/{total / 1048576:.1f} MB)",
                                end="",
                                flush=True,
                            )
            print()
    except urllib.error.URLError as e:
        raise FetchError(f"下载失败: {e.reason}") from e

    if not dest.exists() or dest.stat().st_size == 0:
        raise FetchError("下载结果为空")


# ── Archive extraction ────────────────────────────────────────────────

def _norm(name: str) -> str:
    return name.replace("\\", "/")


def extract_dlls(exe_path: Path, names: list[str]) -> dict[str, bytes]:
    """Pull ``names`` out of the PyInstaller bundle in ``exe_path``.

    Entries are matched case-insensitively by basename, preferring the
    copy under ``native/windows/`` — PyInstaller also places some of
    these DLLs at the archive root, and that copy is not the documented
    one.  Returns a mapping of requested name -> raw bytes.
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as e:
        raise FetchError(
            "需要 PyInstaller 才能解包 webot.exe：pip install pyinstaller"
        ) from e

    reader = CArchiveReader(str(exe_path))
    entries = list(reader.toc.keys())
    # Index by (basename.lower(), under native/windows?) for lookup.
    index: dict[str, list[str]] = {}
    for entry in entries:
        base = os.path.basename(_norm(entry)).lower()
        index.setdefault(base, []).append(entry)

    out: dict[str, bytes] = {}
    missing: list[str] = []

    for name in names:
        candidates = index.get(name.lower(), [])
        if not candidates:
            missing.append(name)
            continue
        preferred = [c for c in candidates if "native/windows/" in _norm(c)]
        key = (preferred or candidates)[0]

        data = reader.extract(key)
        if isinstance(data, tuple):  # (typecode, bytes) in some versions
            data = data[1]
        if not data.startswith(_MZ):
            raise FetchError(
                f"{name} 提取结果不是有效的 PE 文件（magic={data[:2]!r}），"
                f"发布包可能已损坏"
            )
        out[name] = data

    if missing:
        raise FetchError(
            f"发布包 webot.exe 中缺少以下 DLL: {', '.join(missing)}\n"
            f"这通常说明该发布版本是用不同版本的 build.spec 打包的"
        )
    return out


# ── Main ──────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    if args.list:
        print(f"最近的发布版本 ({args.repo}):")
        list_releases(args.repo)
        return 0

    dll_dir = Path(args.dest).resolve() if args.dest else canonical_dll_dir()
    names = list(REQUIRED_WINDOWS_DLLS)

    tmp_exe: Path | None = None
    try:
        if args.from_exe:
            exe_path = Path(args.from_exe).resolve()
            if not exe_path.exists():
                raise FetchError(f"文件不存在: {exe_path}")
            print(f"使用本地文件: {exe_path}")
        else:
            tag, url = resolve_release(args.repo, args.tag)
            print(f"发布版本: {tag}")
            print(f"资源: {url}")
            if args.dry_run:
                print("--dry-run：不下载。")
                return 0
            fd, tmp_name = tempfile.mkstemp(prefix="webot-", suffix=".exe")
            os.close(fd)
            tmp_exe = Path(tmp_name)
            download(url, tmp_exe)
            exe_path = tmp_exe

        print(f"解包 {len(names)} 个 DLL ...")
        payloads = extract_dlls(exe_path, names)

        if args.dry_run:
            for n in names:
                print(f"  {n:22} {len(payloads[n]):>10,} bytes")
            print("--dry-run：未写入任何文件。")
            return 0

        dll_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            target = dll_dir / name
            existed = target.exists()
            target.write_bytes(payloads[name])
            print(
                f"  {'覆盖' if existed else '写入'} {target}  "
                f"({len(payloads[name]):,} bytes)"
            )

        print(f"\n完成 — DLL 已就位: {dll_dir}")
        return 0

    except FetchError as e:
        print(f"\n错误: {e}", file=sys.stderr)
        return 1
    finally:
        if tmp_exe is not None and not args.keep_exe:
            try:
                tmp_exe.unlink()
            except OSError:
                pass
        elif tmp_exe is not None:
            print(f"已保留下载的 EXE: {tmp_exe}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="从 webot 发布版中提取 native/windows/*.dll",
    )
    p.add_argument("--repo", default=DEFAULT_REPO,
                   help=f"GitHub 仓库 (默认 {DEFAULT_REPO})")
    p.add_argument("--tag", default=None,
                   help="指定发布 tag，默认取最新")
    p.add_argument("--from-exe", default=None,
                   help="跳过下载，直接从本地 webot.exe 提取")
    p.add_argument("--dest", default=None,
                   help="目标目录，默认 native/windows/")
    p.add_argument("--list", action="store_true", help="列出可用发布版本")
    p.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    p.add_argument("--keep-exe", action="store_true", help="保留下载的 EXE")
    return p


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
