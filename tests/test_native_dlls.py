"""Tests for native_dlls.py — DLL discovery, validation and error messaging.

The point of this module is that a missing native DLL is reported
*accurately* (as a setup problem with a remedy) rather than being
swallowed and misreported as a key-capture timeout, so most of these
tests pin down the diagnostic text and the search order.
"""

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.wechat.native_dlls import (
    OPTIONAL_WINDOWS_DLLS,
    REQUIRED_WINDOWS_DLLS,
    NativeDllMissingError,
    candidate_dll_dirs,
    canonical_dll_dir,
    describe_missing,
    ensure_dll,
    find_dll,
    format_report,
    missing_in,
    scan,
    verify_loadable,
)

MODULE = "src.wechat.native_dlls"


def _search_path(*dirs):
    """Patch the DLL search path to exactly ``dirs``."""
    return patch.multiple(
        MODULE,
        candidate_dll_dirs=lambda *a, **k: [Path(d) for d in dirs],
        canonical_dll_dir=lambda *a, **k: Path(dirs[0]) if dirs else Path("."),
    )


def _make_dlls(directory, names):
    """Create placeholder files (content is irrelevant for existence checks)."""
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    for n in names:
        (d / n).write_bytes(b"MZ placeholder")
    return d


class RequiredDllListTests(unittest.TestCase):
    def test_key_extraction_and_wcdb_dlls_are_required(self):
        """The two DLLs whose absence broke source runs must be listed."""
        self.assertIn("wx_key.dll", REQUIRED_WINDOWS_DLLS)
        self.assertIn("wcdb_api.dll", REQUIRED_WINDOWS_DLLS)

    def test_vcxx_runtimes_are_required_alongside_wx_key(self):
        """wx_key.dll fails to load (error 126) without these three."""
        for n in ("MSVCP140.dll", "VCRUNTIME140.dll", "VCRUNTIME140_1.dll"):
            self.assertIn(n, REQUIRED_WINDOWS_DLLS)

    def test_no_duplicate_entries(self):
        self.assertEqual(len(REQUIRED_WINDOWS_DLLS), len(set(REQUIRED_WINDOWS_DLLS)))

    def test_keyhook_is_optional_not_required(self):
        """injector.py is not the primary path — its DLL must not be required."""
        self.assertIn("keyhook.dll", OPTIONAL_WINDOWS_DLLS)
        self.assertNotIn("keyhook.dll", REQUIRED_WINDOWS_DLLS)


class SearchPathTests(unittest.TestCase):
    def test_project_native_windows_is_always_searched(self):
        dirs = candidate_dll_dirs()
        self.assertIn(Path(__file__).resolve().parent.parent / "native" / "windows", dirs)

    def test_search_path_has_no_duplicates(self):
        dirs = candidate_dll_dirs()
        self.assertEqual(len(dirs), len({str(d) for d in dirs}))

    def test_canonical_dir_is_under_native_windows(self):
        self.assertEqual(canonical_dll_dir().name, "windows")


class MissingDetectionTests(unittest.TestCase):
    def test_nonexistent_dir_reports_everything_missing(self):
        with TemporaryDirectory() as tmp:
            missing = missing_in(Path(tmp) / "nope")
        self.assertEqual(sorted(missing), sorted(REQUIRED_WINDOWS_DLLS))

    def test_partial_dir_reports_only_the_absent_ones(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, ["wx_key.dll", "wcdb_api.dll"])
            missing = missing_in(d)
        self.assertNotIn("wx_key.dll", missing)
        self.assertIn("WCDB.dll", missing)

    def test_complete_dir_reports_nothing_missing(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, REQUIRED_WINDOWS_DLLS)
            self.assertEqual(missing_in(d), [])


class ScanTests(unittest.TestCase):
    def test_prefers_the_most_complete_directory(self):
        with TemporaryDirectory() as tmp:
            sparse = _make_dlls(Path(tmp) / "sparse", ["wx_key.dll"])
            full = _make_dlls(Path(tmp) / "full", REQUIRED_WINDOWS_DLLS)
            with _search_path(sparse, full):
                best, missing, searched = scan()
        self.assertEqual(best, full)
        self.assertEqual(missing, [])
        self.assertEqual(searched, [sparse, full])

    def test_falls_back_to_canonical_when_nothing_exists(self):
        with TemporaryDirectory() as tmp:
            absent = Path(tmp) / "absent"
            with _search_path(absent):
                best, missing, _ = scan()
        self.assertEqual(best, absent)
        self.assertEqual(missing, list(REQUIRED_WINDOWS_DLLS))

    def test_ignores_files_that_are_not_directories(self):
        with TemporaryDirectory() as tmp:
            real_dir = _make_dlls(Path(tmp) / "real", REQUIRED_WINDOWS_DLLS)
            not_a_dir = Path(tmp) / "file.txt"
            not_a_dir.write_text("x")
            with _search_path(not_a_dir, real_dir):
                best, missing, _ = scan()
        self.assertEqual(best, real_dir)
        self.assertEqual(missing, [])


class FindDllTests(unittest.TestCase):
    def test_returns_path_when_present(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, ["wx_key.dll"])
            with _search_path(d):
                self.assertEqual(find_dll("wx_key.dll"), d / "wx_key.dll")

    def test_returns_none_when_absent(self):
        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                self.assertIsNone(find_dll("wx_key.dll"))


class EnsureDllTests(unittest.TestCase):
    def test_returns_path_when_present(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, REQUIRED_WINDOWS_DLLS)
            with _search_path(d):
                self.assertEqual(ensure_dll("wx_key.dll"), d / "wx_key.dll")

    def test_raises_native_dll_missing_error(self):
        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                with self.assertRaises(NativeDllMissingError):
                    ensure_dll("wx_key.dll")

    def test_error_names_the_requested_dll_first(self):
        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                with self.assertRaises(NativeDllMissingError) as ctx:
                    ensure_dll("wx_key.dll")
        self.assertTrue(str(ctx.exception).startswith("缺少原生 DLL："))
        self.assertIn("wx_key.dll", str(ctx.exception).splitlines()[0])

    def test_error_is_a_runtime_error(self):
        """Callers that catch RuntimeError must keep working."""
        self.assertTrue(issubclass(NativeDllMissingError, RuntimeError))

    def test_error_explains_how_to_fix(self):
        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                with self.assertRaises(NativeDllMissingError) as ctx:
                    ensure_dll("wx_key.dll")
        message = str(ctx.exception)
        self.assertIn("tools/fetch_native.py", message)
        self.assertIn("不在 git 仓库中", message)

    def test_error_lists_the_searched_directories(self):
        with TemporaryDirectory() as tmp:
            searched = Path(tmp) / "empty"
            with _search_path(searched):
                with self.assertRaises(NativeDllMissingError) as ctx:
                    ensure_dll("wx_key.dll")
        self.assertIn(str(searched), str(ctx.exception))

    def test_error_mentions_the_other_missing_dlls(self):
        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                with self.assertRaises(NativeDllMissingError) as ctx:
                    ensure_dll("wx_key.dll")
        self.assertIn("wcdb_api.dll", str(ctx.exception))

    def test_optional_dll_is_reported_by_name_when_missing(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, REQUIRED_WINDOWS_DLLS)
            with _search_path(d):
                with self.assertRaises(NativeDllMissingError) as ctx:
                    ensure_dll("keyhook.dll")
        self.assertIn("keyhook.dll", str(ctx.exception).splitlines()[0])


class DescribeMissingTests(unittest.TestCase):
    def test_omits_directory_list_when_asked(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp) / "empty"
            with _search_path(d):
                text = describe_missing(d, ["wx_key.dll"], include_searched=False)
        self.assertNotIn("已查找目录", text)
        self.assertIn("tools/fetch_native.py", text)

    def test_marks_nonexistent_directories(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp) / "empty"
            with _search_path(d):
                text = describe_missing(d, ["wx_key.dll"])
        self.assertIn("（不存在）", text)

    def test_single_missing_dll_has_no_extra_list_line(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp) / "empty"
            with _search_path(d):
                text = describe_missing(d, ["wx_key.dll"])
        self.assertNotIn("还缺少", text)


class FormatReportTests(unittest.TestCase):
    def test_marks_missing_dlls(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, ["wx_key.dll"])
            with _search_path(d):
                report = format_report()
        self.assertIn("[缺失]", report)
        self.assertIn("wcdb_api.dll", report)

    def test_counts_ready_dlls(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, ["wx_key.dll", "wcdb_api.dll"])
            with _search_path(d):
                report = format_report()
        self.assertIn(f"2/{len(REQUIRED_WINDOWS_DLLS)}", report)

    def test_reports_full_readiness(self):
        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, REQUIRED_WINDOWS_DLLS)
            with _search_path(d):
                report = format_report()
        self.assertIn(f"{len(REQUIRED_WINDOWS_DLLS)}/{len(REQUIRED_WINDOWS_DLLS)}", report)
        self.assertNotIn("[缺失]", report)


@unittest.skipUnless(sys.platform == "win32", "requires Windows LoadLibrary")
class VerifyLoadableTests(unittest.TestCase):
    def test_non_pe_file_returns_error_string_without_raising(self):
        with TemporaryDirectory() as tmp:
            bogus = Path(tmp) / "not_a_dll.dll"
            bogus.write_text("this is not a PE file")
            err = verify_loadable(bogus)
        self.assertIsInstance(err, str)
        self.assertIn("not_a_dll.dll", err)


class ExtractKeyIntegrationTests(unittest.TestCase):
    """extract_wcdb_key must fail loudly, not silently, when DLLs are absent."""

    def test_raises_when_wx_key_dll_missing(self):
        from src.wechat.extract_key import extract_wcdb_key

        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                with self.assertRaises(NativeDllMissingError) as ctx:
                    extract_wcdb_key(require_restart=False)
        self.assertIn("wx_key.dll", str(ctx.exception))

    def test_error_reaches_the_caller_verbatim(self):
        """server.py surfaces str(exc) in the UI, so it must stay readable."""
        from src.wechat.extract_key import extract_wcdb_key

        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                with self.assertRaises(NativeDllMissingError) as ctx:
                    extract_wcdb_key(require_restart=False)
        self.assertIn("fetch_native.py", str(ctx.exception))


class WcdbClientIntegrationTests(unittest.TestCase):
    def test_find_dll_raises_native_dll_missing_error(self):
        from src.wechat import wcdb_client

        with TemporaryDirectory() as tmp:
            with _search_path(Path(tmp) / "empty"):
                with self.assertRaises(NativeDllMissingError):
                    wcdb_client._find_dll()

    def test_find_dll_returns_dir_and_path(self):
        from src.wechat import wcdb_client

        with TemporaryDirectory() as tmp:
            d = _make_dlls(tmp, ["wcdb_api.dll"])
            with _search_path(d):
                dll_dir, dll_path = wcdb_client._find_dll()
        self.assertEqual(Path(dll_dir), d)
        self.assertEqual(Path(dll_path), d / "wcdb_api.dll")


if __name__ == "__main__":
    unittest.main()
