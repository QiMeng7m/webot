"""Tests for src.game.store — SQLite 持久化（角色 / 事件 / 群状态 / 机缘抢占）。"""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

from src.game import realms as R
from src.game.store import GameStore


def _raw_exec(db_path: str, sql: str) -> None:
    """Run a statement on a separate short-lived connection.

    必须显式关闭连接：Windows 上未关闭的 sqlite 句柄会一直锁住 db 文件，
    导致 ``TemporaryDirectory.cleanup()`` 抛 PermissionError。
    """
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(sql)
        conn.commit()


class GameStoreTestCase(unittest.TestCase):
    """每个测试用独立的临时数据库。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.store = GameStore(self.db_path)

    def tearDown(self):
        self._tmpdir.cleanup()


class TestSchema(GameStoreTestCase):

    def test_init_is_idempotent(self):
        # 重复初始化不应报错，也不应清空已有数据
        c = self.store.get_or_create("chat1", "user1", "小明")
        c.exp = 42
        self.store.save_character(c)
        GameStore(self.db_path)
        again = self.store.get_character("chat1", "user1")
        self.assertEqual(again.exp, 42)

    def test_tables_created(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        self.assertIn("game_characters", names)
        self.assertIn("game_events", names)
        self.assertIn("game_group_state", names)


class TestCharacters(GameStoreTestCase):

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.store.get_character("nope", "nobody"))

    def test_get_or_create_persists(self):
        char = self.store.get_or_create("chat1", "user1", "小明")
        self.assertEqual(char.user_name, "小明")
        self.assertEqual(char.realm_index, 0)
        again = self.store.get_character("chat1", "user1")
        self.assertIsNotNone(again)
        self.assertEqual(again.user_name, "小明")

    def test_get_or_create_updates_display_name(self):
        self.store.get_or_create("chat1", "user1", "旧名")
        char = self.store.get_or_create("chat1", "user1", "新名")
        self.assertEqual(char.user_name, "新名")
        self.assertEqual(
            self.store.get_character("chat1", "user1").user_name, "新名"
        )

    def test_save_character_roundtrip_all_fields(self):
        char = self.store.get_or_create("chat1", "user1", "小明")
        char.realm_index = 7
        char.exp = 123
        char.total_exp = 4567
        char.spirit_stones = 89
        char.technique = "长春功"
        char.pills = {"回气丹": 2, "破境丹": 1}
        char.pending_bonus = 0.15
        char.fail_streak = 2
        char.last_chat_ts = 111.5
        char.last_action_ts = 222.5
        char.daily_key = "2026-01-01"
        char.daily_exp = 77
        char.daily_actions = 3
        char.alchemy_used = 1
        char.duel_used = 2
        char.duel_targets = ["u9"]
        char.bt_attempts = 5
        char.bt_fails = 4
        self.store.save_character(char)

        got = self.store.get_character("chat1", "user1")
        for field in ("realm_index", "exp", "total_exp", "spirit_stones",
                      "technique", "pending_bonus", "fail_streak",
                      "last_chat_ts", "last_action_ts", "daily_key",
                      "daily_exp", "daily_actions", "alchemy_used",
                      "duel_used", "duel_targets", "bt_attempts", "bt_fails"):
            self.assertEqual(getattr(got, field), getattr(char, field),
                             f"field {field} mismatch")
        self.assertEqual(got.pills, {"回气丹": 2, "破境丹": 1})

    def test_characters_are_scoped_per_chat(self):
        self.store.get_or_create("chatA", "user1", "小明")
        self.assertIsNone(self.store.get_character("chatB", "user1"))

    def test_corrupt_pills_json_falls_back_to_empty(self):
        self.store.get_or_create("chat1", "user1", "小明")
        _raw_exec(
            self.db_path,
            "UPDATE game_characters SET pills = 'not json' "
            "WHERE chat_id = 'chat1'",
        )
        self.assertEqual(self.store.get_character("chat1", "user1").pills, {})

    def test_corrupt_duel_targets_json_falls_back_to_empty(self):
        self.store.get_or_create("chat1", "user1", "小明")
        _raw_exec(
            self.db_path,
            "UPDATE game_characters SET duel_targets = '{{{' "
            "WHERE chat_id = 'chat1'",
        )
        self.assertEqual(
            self.store.get_character("chat1", "user1").duel_targets, []
        )


class TestRanking(GameStoreTestCase):

    def _mk(self, chat, uid, name, realm, total):
        c = self.store.get_or_create(chat, uid, name)
        c.realm_index = realm
        c.total_exp = total
        self.store.save_character(c)

    def test_ranking_sorted_by_realm_then_total_exp(self):
        self._mk("c1", "u1", "甲", 5, 100)
        self._mk("c1", "u2", "乙", 9, 50)
        self._mk("c1", "u3", "丙", 5, 900)
        names = [c.user_name for c in self.store.list_ranking("c1")]
        self.assertEqual(names, ["乙", "丙", "甲"])

    def test_ranking_scoped_to_chat(self):
        self._mk("c1", "u1", "甲", 5, 100)
        self._mk("c2", "u2", "乙", 9, 50)
        self.assertEqual(len(self.store.list_ranking("c1")), 1)

    def test_ranking_respects_limit(self):
        for i in range(15):
            self._mk("c1", f"u{i}", f"修士{i}", i, i)
        self.assertEqual(len(self.store.list_ranking("c1", limit=5)), 5)

    def test_list_characters_search(self):
        self._mk("c1", "u1", "小明", 1, 10)
        self._mk("c1", "u2", "小红", 1, 10)
        found = self.store.list_characters("c1", search="小")
        self.assertEqual(len(found), 2)
        found = self.store.list_characters("c1", search="小明")
        self.assertEqual(len(found), 1)


class TestOverview(GameStoreTestCase):

    def test_overview_empty(self):
        o = self.store.get_overview("c1")
        self.assertEqual(o["total"], 0)
        self.assertEqual(o["max_realm"], 0)
        self.assertEqual(o["stones"], 0)

    def test_overview_aggregates(self):
        for i, realm in enumerate([2, 6, 4]):
            c = self.store.get_or_create("c1", f"u{i}", f"甲{i}")
            c.realm_index = realm
            c.spirit_stones = 10
            self.store.save_character(c)
        o = self.store.get_overview("c1")
        self.assertEqual(o["total"], 3)
        self.assertEqual(o["max_realm"], 6)
        self.assertEqual(o["stones"], 30)


class TestAdminOps(GameStoreTestCase):

    def setUp(self):
        super().setUp()
        c = self.store.get_or_create("c1", "u1", "小明")
        c.realm_index = 8
        c.exp = 500
        c.total_exp = 9000
        c.spirit_stones = 300
        c.technique = "长春功"
        c.pills = {"回气丹": 3}
        c.fail_streak = 2
        c.alchemy_used = 2
        c.duel_targets = ["u2"]
        self.store.save_character(c)

    def test_reset_character(self):
        self.assertTrue(self.store.reset_character("c1", "u1"))
        c = self.store.get_character("c1", "u1")
        self.assertEqual(c.realm_index, 0)
        self.assertEqual(c.exp, 0)
        self.assertEqual(c.total_exp, 0)
        self.assertEqual(c.spirit_stones, 0)
        self.assertEqual(c.technique, "")
        self.assertEqual(c.pills, {})
        self.assertEqual(c.fail_streak, 0)
        self.assertEqual(c.alchemy_used, 0)
        self.assertEqual(c.duel_targets, [])

    def test_reset_missing_returns_false(self):
        self.assertFalse(self.store.reset_character("c1", "ghost"))

    def test_grant_stones(self):
        self.assertTrue(self.store.grant_stones("c1", "u1", 50))
        self.assertEqual(
            self.store.get_character("c1", "u1").spirit_stones, 350
        )

    def test_grant_stones_never_negative(self):
        self.store.grant_stones("c1", "u1", -10_000)
        self.assertEqual(
            self.store.get_character("c1", "u1").spirit_stones, 0
        )

    def test_set_realm_clears_exp(self):
        self.assertTrue(self.store.set_realm("c1", "u1", 12))
        c = self.store.get_character("c1", "u1")
        self.assertEqual(c.realm_index, 12)
        self.assertEqual(c.exp, 0)
        self.assertEqual(c.fail_streak, 0)


class TestEvents(GameStoreTestCase):

    def test_log_and_list(self):
        self.store.log_event("c1", "breakthrough", "u1", "小明", "突破成功")
        self.store.log_event("c1", "encounter", "u2", "小红", "抢到机缘",
                             {"stones": 12})
        events = self.store.list_events("c1")
        self.assertEqual(len(events), 2)
        # 倒序：最新在前
        self.assertEqual(events[0].actor_name, "小红")
        self.assertEqual(events[0].detail, {"stones": 12})

    def test_events_scoped_to_chat(self):
        self.store.log_event("c1", "x", summary="a")
        self.store.log_event("c2", "x", summary="b")
        self.assertEqual(len(self.store.list_events("c1")), 1)

    def test_count_events_since(self):
        self.store.log_event("c1", "x", summary="a")
        self.assertEqual(self.store.count_events_since(0, "c1"), 1)
        # 用一个未来的时间点，应当统计不到
        self.assertEqual(self.store.count_events_since(2 ** 31, "c1"), 0)


class TestEncounter(GameStoreTestCase):

    def test_group_state_defaults(self):
        state = self.store.get_group_state("c1")
        self.assertEqual(state.chat_id, "c1")
        self.assertEqual(state.active_reward_kind, "")
        self.assertEqual(state.last_encounter_at, 0.0)

    def test_spawn_and_claim(self):
        self.store.spawn_encounter("c1", now=1000.0, expires_at=1300.0,
                                   reward_kind="stones", reward_amount=42)
        claimed = self.store.claim_encounter("c1", now=1100.0)
        self.assertEqual(claimed, ("stones", 42))

    def test_claim_only_succeeds_once(self):
        self.store.spawn_encounter("c1", now=1000.0, expires_at=1300.0,
                                   reward_kind="stones", reward_amount=42)
        first = self.store.claim_encounter("c1", now=1100.0)
        second = self.store.claim_encounter("c1", now=1101.0)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_claim_expired_returns_none(self):
        self.store.spawn_encounter("c1", now=1000.0, expires_at=1300.0,
                                   reward_kind="stones", reward_amount=42)
        self.assertIsNone(self.store.claim_encounter("c1", now=1400.0))

    def test_claim_without_spawn_returns_none(self):
        self.assertIsNone(self.store.claim_encounter("c1", now=1000.0))

    def test_clear_encounter(self):
        self.store.spawn_encounter("c1", now=1000.0, expires_at=1300.0,
                                   reward_kind="stones", reward_amount=42)
        self.store.clear_encounter("c1")
        self.assertIsNone(self.store.claim_encounter("c1", now=1100.0))

    def test_group_state_survives_reinit(self):
        self.store.spawn_encounter("c1", now=1000.0, expires_at=1300.0,
                                   reward_kind="stones", reward_amount=42)
        GameStore(self.db_path)
        self.assertEqual(
            self.store.get_group_state("c1").active_reward_amount, 42
        )


class TestActiveChatIds(GameStoreTestCase):

    def test_returns_distinct_chat_ids(self):
        self.store.get_or_create("c1", "u1", "甲")
        self.store.get_or_create("c1", "u2", "乙")
        self.store.get_or_create("c2", "u3", "丙")
        self.assertEqual(self.store.get_active_chat_ids(), ["c1", "c2"])


if __name__ == "__main__":
    unittest.main()
