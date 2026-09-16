"""Tests for the 修仙玩法 HTTP API — /api/game/* and config plumbing.

Starts the real web server (like tests/test_functional.py) so the tests cover
routing, the POST allowlist, and JSON serialisation end to end.

Written with ``unittest`` so it runs under both ``pytest`` and
``python -m unittest`` (pytest is not always installed in this repo).
"""

import http.client
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

SERVER_HOST = "127.0.0.1"
SERVER_PORT = None
_server_thread = None
_env_backup = None
_env_path = None


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _api_get(path, timeout=5):
    conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=timeout)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(body)
    except json.JSONDecodeError:
        return resp.status, None


def _api_post(path, body_dict=None, timeout=5):
    conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=timeout)
    body = json.dumps(body_dict or {}).encode()
    conn.request("POST", path, body=body,
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except json.JSONDecodeError:
        return resp.status, None


def _start_server(port):
    import traceback
    from src.web.server import _run_server

    errors = []

    def _run():
        try:
            _run_server(SERVER_HOST, port)
        except Exception:
            errors.append(traceback.format_exc())

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    for _ in range(50):
        if errors:
            raise RuntimeError(f"Server crashed:\n{errors[0]}")
        try:
            conn = http.client.HTTPConnection(SERVER_HOST, port, timeout=0.5)
            conn.request("GET", "/api/status")
            conn.getresponse().read()
            conn.close()
            return t
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"Server did not start on port {port}")


def setUpModule():
    """Start one server for the whole module, with a throwaway .env."""
    global SERVER_PORT, _server_thread, _env_backup, _env_path

    ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist" / "index.html"
    if not ui_dist.exists():
        raise unittest.SkipTest("UI not built. Run: cd ui && npm run build")

    from src.config import resolve_env_file
    _env_path = resolve_env_file()
    if _env_path.exists():
        _env_backup = _env_path.read_text(encoding="utf-8")
    else:
        _env_path.parent.mkdir(parents=True, exist_ok=True)
        _env_path.write_text(
            "AI_BACKEND=deepseek\n"
            "DEEPSEEK_API_KEY=\n"
            "DEEPSEEK_MODEL=deepseek-v4-flash\n"
            "WECHAT_GROUPS=*\n"
            "ONBOARDING_DONE=true\n",
            encoding="utf-8",
        )
        _env_backup = None

    SERVER_PORT = _find_free_port()
    _server_thread = _start_server(SERVER_PORT)


def tearDownModule():
    """Restore the developer's real .env — these tests must not clobber it."""
    if _env_path is None:
        return
    try:
        if _env_backup is None:
            if _env_path.exists():
                _env_path.unlink()
        else:
            _env_path.write_text(_env_backup, encoding="utf-8")
    except OSError:
        pass


class TestGameOverviewApi(unittest.TestCase):

    def test_returns_ok(self):
        status, data = _api_get("/api/game/overview")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)

    def test_has_expected_shape(self):
        _, data = _api_get("/api/game/overview")
        for key in ("ok", "overview", "ranking", "events", "groups"):
            self.assertIn(key, data)
        for key in ("total", "max_realm", "max_realm_label", "stones",
                    "avg_realm", "events_today"):
            self.assertIn(key, data["overview"])

    def test_accepts_chat_id(self):
        status, data = _api_get("/api/game/overview?chat_id=room%40chatroom")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["chat_id"], "room@chatroom")

    def test_empty_group_has_no_ranking(self):
        _, data = _api_get(
            "/api/game/overview?chat_id=definitely-not-a-real-room"
        )
        self.assertEqual(data["ranking"], [])
        self.assertEqual(data["overview"]["total"], 0)


class TestGameCharactersApi(unittest.TestCase):

    def test_returns_ok(self):
        status, data = _api_get("/api/game/characters")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)

    def test_has_items_and_groups(self):
        _, data = _api_get("/api/game/characters")
        self.assertIn("items", data)
        self.assertIn("groups", data)
        self.assertIsInstance(data["items"], list)

    def test_accepts_search(self):
        status, data = _api_get("/api/game/characters?search=nobody-xyz")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["items"], [])


class TestGameActionApi(unittest.TestCase):
    """POST /api/game/action 必须在 do_POST 的 allowlist 里。"""

    def test_post_is_not_405(self):
        status, _ = _api_post("/api/game/action", {
            "action": "reset", "chat_id": "x", "user_id": "y",
        })
        self.assertNotEqual(status, 405)

    def test_missing_ids_returns_error(self):
        status, data = _api_post("/api/game/action", {"action": "reset"})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("chat_id", data["error"])

    def test_unknown_action_returns_error(self):
        _, data = _api_post("/api/game/action", {
            "action": "nuke", "chat_id": "x", "user_id": "y",
        })
        self.assertFalse(data["ok"])
        self.assertIn("未知操作", data["error"])

    def test_action_on_missing_character_returns_error(self):
        _, data = _api_post("/api/game/action", {
            "action": "reset", "chat_id": "no-such-room", "user_id": "nobody",
        })
        self.assertFalse(data["ok"])
        self.assertIn("没有找到", data["error"])

    def test_bad_amount_returns_error_not_500(self):
        status, data = _api_post("/api/game/action", {
            "action": "grant_stones", "chat_id": "x", "user_id": "y",
            "amount": "not-a-number",
        })
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])


class TestPostAllowlist(unittest.TestCase):
    """回归保护：未知 POST 路径仍应返回 405。"""

    def test_unknown_path_returns_405(self):
        conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
        conn.request("POST", "/api/definitely-not-real", body=b"{}",
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
        self.assertEqual(status, 405)


class TestGameConfigPlumbing(unittest.TestCase):
    """配置读写管道：默认值、序列化、往返。"""

    def test_load_config_exposes_game_fields(self):
        status, data = _api_get("/api/load-config")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)
        cfg = data["config"]
        for key in ("game_enabled", "game_groups", "game_chat_exp",
                    "game_chat_cooldown_sec", "game_daily_exp_cap",
                    "game_action_cooldown_sec", "game_exp_multiplier",
                    "game_encounter_interval_min", "game_ai_flavor_enabled"):
            self.assertIn(key, cfg)

    def test_game_config_from_raw_defaults(self):
        from src.web.server import _game_config_from_raw
        cfg = _game_config_from_raw({})
        self.assertFalse(cfg["game_enabled"])
        self.assertEqual(cfg["game_groups"], ["*"])
        self.assertEqual(cfg["game_chat_exp"], 2)
        self.assertTrue(cfg["game_ai_flavor_enabled"])

    def test_game_updates_from_config_serialises_lists(self):
        from src.web.server import _game_updates_from_config
        updates = _game_updates_from_config({
            "game_enabled": True,
            "game_groups": ["群A", "群B"],
        })
        self.assertEqual(updates["GAME_ENABLED"], "true")
        self.assertEqual(updates["GAME_GROUPS"], "群A,群B")

    def test_game_updates_empty_groups_falls_back_to_wildcard(self):
        from src.web.server import _game_updates_from_config
        self.assertEqual(
            _game_updates_from_config({"game_groups": []})["GAME_GROUPS"], "*"
        )

    def test_save_config_writes_game_keys(self):
        """POST /api/config 应把 GAME_* 写进 .env。"""
        status, data = _api_post("/api/config", {
            "game_enabled": True,
            "game_groups": ["*"],
            "game_chat_exp": 3,
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"], data)

        raw = _env_path.read_text(encoding="utf-8")
        self.assertIn("GAME_ENABLED=true", raw)
        self.assertIn("GAME_CHAT_EXP=3", raw)

        # 写回后 /api/load-config 应读到新值
        _, cfg_data = _api_get("/api/load-config")
        self.assertTrue(cfg_data["config"]["game_enabled"])
        self.assertEqual(cfg_data["config"]["game_chat_exp"], 3)

    def test_save_config_can_disable_game(self):
        _api_post("/api/config", {"game_enabled": False, "game_groups": ["*"]})
        raw = _env_path.read_text(encoding="utf-8")
        self.assertIn("GAME_ENABLED=false", raw)


class TestRequiresTwoCallRoundTrip(unittest.TestCase):
    """配置导出应包含 game_* 字段（导入导出往返用）。"""

    def test_export_contains_game_fields(self):
        conn = http.client.HTTPConnection(SERVER_HOST, SERVER_PORT, timeout=5)
        conn.request("GET", "/api/config/export")
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        self.assertEqual(resp.status, 200)
        data = json.loads(body)
        self.assertIn("game_enabled", data)
        self.assertIn("game_chat_exp", data)


if __name__ == "__main__":
    unittest.main()
