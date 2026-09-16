"""Extract WCDB decrypt key from WeChat using wx_key.dll.

Zero external dependencies — wx_key.dll is bundled with the EXE.
Asks user to re-login to WeChat for reliable capture.

Usage:
    python -m src.wechat.extract_key
"""
import ctypes as ct
from ctypes import wintypes
import logging
import os
import sys
import time

from .native_dlls import NativeDllMissingError, ensure_dll, verify_loadable

logger = logging.getLogger(__name__)

TH32CS_SNAPPROCESS = 0x00000002
kernel32 = ct.WinDLL("kernel32", use_last_error=True)


def _find_wechat_pid():
    """Find running WeChat process PID."""
    class PE(ct.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ct.c_void_p),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", ct.c_wchar * 260),
        ]
    # Get our own PID to exclude webot.exe from results
    import os as _os
    my_pid = _os.getpid()

    h = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h == -1:
        return None
    pe = PE()
    pe.dwSize = ct.sizeof(PE)
    if kernel32.Process32FirstW(h, ct.byref(pe)):
        while True:
            n = pe.szExeFile.lower()
            # Only match actual WeChat (Weixin.exe/WeChat.exe), not our own webot.exe
            if pe.th32ProcessID != my_pid and (n == "weixin.exe" or n == "wechat.exe"):
                kernel32.CloseHandle(h)
                return pe.th32ProcessID
            if not kernel32.Process32NextW(h, ct.byref(pe)):
                break
    kernel32.CloseHandle(h)
    return None


def _verify_dll_loadable(dll_path: str) -> str | None:
    """Try to load wx_key.dll and return None if OK, or an error string."""
    return verify_loadable(dll_path)


def _log_console(msg):
    """Log a user-visible message (survives console=False in EXE).

    Uses the module logger — callers (e.g. web.server) should separately
    propagate status to the Web UI.
    """
    logger.warning(msg)


# ── Primary: wx_key.dll (zero-dependency key extraction) ─────────────

def extract_wcdb_key(require_restart: bool = True,
                     on_progress=None):
    """Extract WCDB key by asking user to re-login to WeChat.

    Args:
        require_restart: If True (default), ask user to restart WeChat
            when direct hooking fails. If False, return None immediately
            on direct hook failure.
        on_progress: Optional callback(phase, message) called at each
            stage change so callers can update their UI in real time.
            Phases: hooking, waiting_exit, waiting_login, hooking_restart.

    Returns 64-char hex key, or None on failure.
    """
    def _progress(phase, msg):
        logger.info("[%s] %s", phase, msg)
        if on_progress:
            try:
                on_progress(phase, msg)
            except Exception:
                pass

    # A missing wx_key.dll is a hard setup error, not a failed attempt —
    # raise so callers surface the real reason instead of reporting a
    # bogus "timed out, make sure WeChat is logged in".
    dll_path = str(ensure_dll("wx_key.dll"))

    # Pre-flight: verify the DLL actually loads before asking user
    # to restart WeChat.  Missing VC++ runtimes or architecture
    # mismatches are surfaced immediately rather than after a
    # confusing timeout.
    dll_err = _verify_dll_loadable(dll_path)
    if dll_err:
        logger.error(dll_err)
        raise NativeDllMissingError(dll_err)

    pid = _find_wechat_pid()

    # ── Fast path: WeChat is running, try to hook directly ──────
    if pid:
        logger.info("检测到微信正在运行 (PID %d)，直接提取密钥...", pid)
        _progress("hooking", "检测到微信正在运行，尝试直接获取密钥...")
        time.sleep(1)
        key = _hook_and_poll(pid, dll_path, timeout=10)
        if key:
            return key
        if not require_restart:
            logger.warning(
                "直接提取密钥失败 — 微信已运行较久，密钥已在启动时加载完毕。"
                "请通过新用户引导流程重新提取密钥。"
            )
            return None
        logger.warning("直接提取失败，请重启微信...")

    if pid:
        # WeChat is running but direct hook failed — ask user to restart
        _progress("waiting_exit",
                  "直接提取失败，请右键任务栏微信图标选择「退出微信」，然后重新登录")
        logger.info("请退出微信并重新登录（右键托盘→退出微信）")
    else:
        # WeChat is not running at all — ask user to start it
        _progress("waiting_login",
                  "请启动微信并登录（首次启动更容易捕获密钥）")
        logger.info("微信未运行，等待用户启动微信...")

    # Phase 1: wait for WeChat to exit (if it was running)
    while _find_wechat_pid():
        time.sleep(1)

    if pid:
        # We were waiting for exit — now ask for login
        _progress("waiting_login", "微信已退出，请重新启动并登录微信")
        logger.info("微信已退出，等待重新登录...")

    # Phase 2: wait for WeChat to restart
    new_pid = None
    while not new_pid:
        time.sleep(0.5)
        new_pid = _find_wechat_pid()

    _progress("hooking_restart",
              f"检测到微信启动 (PID {new_pid})，正在安装 Hook...")
    logger.info("检测到微信启动 (PID %d)，安装 Hook...", new_pid)

    # Give WeChat a moment to initialize then hook
    time.sleep(2)

    return _hook_and_poll(new_pid, dll_path)


def _hook_and_poll(pid: int, dll_path: str, timeout=180):
    """Install hook on WeChat process and poll for key."""
    try:
        # Add the DLL's directory to the search path so Windows can
        # find its dependencies (VCRUNTIME140.dll etc.) which are
        # bundled alongside it.  Without this, LoadLibrary may fail
        # with "The specified module could not be found" (error 126)
        # even though the DLL file itself exists.
        dll_dir = os.path.dirname(dll_path)
        if dll_dir:
            try:
                os.add_dll_directory(dll_dir)
            except AttributeError:
                # Python < 3.8 — fall back to SetDllDirectoryW
                k32 = ct.WinDLL("kernel32", use_last_error=True)
                k32.SetDllDirectoryW(dll_dir)

        lib = ct.WinDLL(dll_path)
        lib.InitializeHook.argtypes = [wintypes.DWORD]
        lib.InitializeHook.restype = wintypes.BOOL
        lib.PollKeyData.argtypes = [ct.c_char_p, ct.c_int]
        lib.PollKeyData.restype = wintypes.BOOL
        lib.CleanupHook.argtypes = []
        lib.CleanupHook.restype = wintypes.BOOL

        if not lib.InitializeHook(wintypes.DWORD(pid)):
            err = ct.get_last_error()
            logger.error(
                "Hook 安装失败 — InitializeHook 返回 False"
                "（请确认微信版本兼容，错误码: %d）", err,
            )
            return None

        logger.info("Hook 已安装，等待微信加载数据...")

        buf = ct.create_string_buffer(128)
        deadline = time.monotonic() + timeout
        key = None

        while time.monotonic() < deadline:
            if lib.PollKeyData(buf, 128):
                data = bytes(buf)
                n = data.find(0)
                raw = data[:n] if n >= 0 else data
                key = raw.decode("utf-8", errors="replace").strip()
                if len(key) == 64 and all(c in "0123456789abcdefABCDEF" for c in key):
                    logger.info("密钥捕获成功: %s...", key[:16])
                    break

            # PID check every 1s
            cur = _find_wechat_pid()
            if cur and cur != pid:
                logger.info("PID 变化 (%d→%d)，重新 Hook", pid, cur)
                pid = cur
                lib.InitializeHook(wintypes.DWORD(pid))

            time.sleep(0.1)

        lib.CleanupHook()

        if key:
            return key
        logger.error("未在 %ds 内捕获密钥 — PollKeyData 始终未返回有效数据", timeout)
        return None

    except OSError as e:
        # DLL load failures — missing dependencies, corrupted file, etc.
        # Surface these as a setup error rather than returning None, which
        # callers would otherwise report as a key-capture timeout.
        detail = verify_loadable(dll_path) or f"无法加载 wx_key.dll: {e}"
        logger.error(detail)
        raise NativeDllMissingError(detail) from e
    except Exception as e:
        logger.error("Hook 失败: %s", e, exc_info=True)
        try:
            lib.CleanupHook()
        except Exception:
            pass
        return None


# ── Compatibility API ────────────────────────────────────────────────

def extract_aes_key():
    """Extract WCDB key from running WeChat memory."""
    return extract_wcdb_key()


def decrypt_wcdb_key(aes_hex):
    """Return a plain hex key as-is if valid."""
    if not aes_hex:
        return None
    if len(aes_hex) == 64 and all(c in "0123456789abcdefABCDEF" for c in aes_hex):
        return aes_hex
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        k = extract_wcdb_key()
    except NativeDllMissingError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if k:
        print(k)
    else:
        print("ERROR: Could not extract key.", file=sys.stderr)
        sys.exit(1)
