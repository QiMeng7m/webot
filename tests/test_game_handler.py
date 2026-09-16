"""Tests for src.game.handler — 命令解析、被动积累、机缘与论道。"""

import os
import random
import tempfile
import time
import unittest

from src.game import realms as R
from src.game.handler import GameHandler
from src.game.store import GameStore


class FakeConfig:
    """最小的 BotConfig 替身，只含玩法需要的字段。"""

    game_chat_exp = 2
    game_chat_cooldown_sec = 30
    game_daily_exp_cap = 200
    game_action_cooldown_sec = 300
    game_exp_multiplier = 1.0
    game_encounter_interval_min = 45
    game_ai_flavor_enabled = False


class FakeMessageStore:
    """总是报告群「很活跃」，用来放行机缘刷新。"""

    def __init__(self, active=True):
        self.active = active

    def get_messages_since(self, chat_id, since_ts, until_ts, limit):
        return list(range(R.ENCOUNTER_ACTIVE_MIN_MESSAGES)) if self.active else []


class HandlerTestCase(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.store = GameStore(self.db_path)
        self.handler = self._make_handler()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_handler(self, seed=1234, config=None, message_store=None):
        return GameHandler(
            self.store,
            config or FakeConfig(),
            summarizer=None,
            message_store=message_store or FakeMessageStore(),
            rng=random.Random(seed),
        )

    def _chat(self, content, uid="u1", name="小明", chat="c1"):
        return {"chat_id": chat, "sender_id": uid, "sender_name": name,
                "content": content}


class TestCommandRecognition(HandlerTestCase):
    """命令词匹配不能误伤正常聊天。"""

    def test_unknown_text_returns_none(self):
        self.assertIsNone(self.handler.handle("今天天气不错", "c1", "u1", "小明"))

    def test_normal_sentence_starting_with_command_word_not_matched(self):
        # 「我的意思是…」不应被判成「我的」命令
        self.assertIsNone(
            self.handler.handle("我的意思是这个方案不行", "c1", "u1", "小明")
        )

    def test_command_with_trailing_punctuation_matched(self):
        self.assertIsNotNone(self.handler.handle("修炼！", "c1", "u1", "小明"))

    def test_empty_text_returns_none(self):
        self.assertIsNone(self.handler.handle("", "c1", "u1", "小明"))
        self.assertIsNone(self.handler.handle("   ", "c1", "u1", "小明"))

    def test_help_lists_commands(self):
        reply = self.handler.handle("修仙帮助", "c1", "u1", "小明")
        self.assertIn("修炼", reply)
        self.assertIn("突破", reply)
        self.assertIn("抢机缘", reply)


class TestCultivation(HandlerTestCase):

    def test_first_cultivate_grants_exp(self):
        reply = self.handler.handle("修炼", "c1", "u1", "小明")
        self.assertIn("修为 +", reply)
        char = self.store.get_character("c1", "u1")
        self.assertGreater(char.exp, 0)
        self.assertGreater(char.total_exp, 0)

    def test_second_cultivate_blocked_by_cooldown(self):
        self.handler.handle("修炼", "c1", "u1", "小明")
        reply = self.handler.handle("打坐", "c1", "u1", "小明")
        self.assertIn("调息", reply)

    def test_cultivate_does_not_exceed_realm_cap(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.exp = R.exp_needed(0) - 1
        self.store.save_character(char)
        self.handler.handle("修炼", "c1", "u1", "小明")
        self.assertLessEqual(self.store.get_character("c1", "u1").exp,
                             R.exp_needed(0))

    def test_cultivate_at_max_realm_is_rejected(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.realm_index = R.IMMORTAL_INDEX
        self.store.save_character(char)
        reply = self.handler.handle("修炼", "c1", "u1", "小明")
        self.assertIn("仙人", reply)


class TestBreakthrough(HandlerTestCase):

    def test_requires_full_exp(self):
        reply = self.handler.handle("突破", "c1", "u1", "小明")
        self.assertIn("修为未满", reply)

    def test_success_advances_realm(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.exp = R.exp_needed(0)
        char.total_exp = char.exp
        self.store.save_character(char)
        # 强制成功
        self.handler._rng.random = lambda: 0.0
        reply = self.handler.handle("突破", "c1", "u1", "小明")
        char = self.store.get_character("c1", "u1")
        self.assertEqual(char.realm_index, 1)
        self.assertEqual(char.exp, 0)
        self.assertIn("炼气中期", reply)

    def test_failure_keeps_realm_and_records_event(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.exp = R.exp_needed(0)
        char.total_exp = char.exp
        self.store.save_character(char)
        self.handler._rng.random = lambda: 1.0  # 强制失败
        self.handler.handle("突破", "c1", "u1", "小明")
        char = self.store.get_character("c1", "u1")
        self.assertEqual(char.realm_index, 0)
        self.assertEqual(char.fail_streak, 1)
        self.assertEqual(char.bt_fails, 1)
        events = self.store.list_events("c1")
        self.assertTrue(any(e.kind == "breakthrough_fail" for e in events))

    def test_pending_bonus_consumed_on_attempt(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.exp = R.exp_needed(0)
        char.pending_bonus = 0.2
        self.store.save_character(char)
        self.handler.handle("突破", "c1", "u1", "小明")
        self.assertEqual(
            self.store.get_character("c1", "u1").pending_bonus, 0.0
        )

    def test_tribulation_rejected_below_last_stage(self):
        reply = self.handler.handle("渡劫", "c1", "u1", "小明")
        self.assertIn("尚不到渡劫之时", reply)

    def test_at_max_realm_breakthrough_rejected(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.realm_index = R.IMMORTAL_INDEX
        self.store.save_character(char)
        reply = self.handler.handle("突破", "c1", "u1", "小明")
        self.assertIn("仙人", reply)


class TestPassiveAccrual(HandlerTestCase):

    def test_valid_message_grants_exp(self):
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        char = self.store.get_character("c1", "u1")
        self.assertGreater(char.exp, 0)
        self.assertGreater(char.daily_exp, 0)

    def test_short_message_ignored(self):
        self.handler.record_activity(self._chat("嗯"))
        self.assertIsNone(self.store.get_character("c1", "u1"))

    def test_emoji_only_message_ignored(self):
        self.handler.record_activity(self._chat("😀😀😀😀"))
        self.assertIsNone(self.store.get_character("c1", "u1"))

    def test_repeat_message_ignored(self):
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        before = self.store.get_character("c1", "u1").exp
        # 绕过冷却，确保拦截来自「复读」而不是冷却
        char = self.store.get_character("c1", "u1")
        char.last_chat_ts = 0
        self.store.save_character(char)
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        self.assertEqual(self.store.get_character("c1", "u1").exp, before)

    def test_cooldown_blocks_second_message(self):
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        before = self.store.get_character("c1", "u1").exp
        self.handler.record_activity(self._chat("换一句完全不同的话"))
        self.assertEqual(self.store.get_character("c1", "u1").exp, before)

    def test_daily_cap_respected(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.daily_key = time.strftime("%Y-%m-%d")
        char.daily_exp = FakeConfig.game_daily_exp_cap
        self.store.save_character(char)
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        self.assertEqual(self.store.get_character("c1", "u1").exp, 0)

    def test_missing_ids_returns_none(self):
        self.assertIsNone(self.handler.record_activity({"chat_id": "", "sender_id": ""}))
        self.assertIsNone(self.handler.record_activity({"chat_id": "c1"}))


class TestEncounter(HandlerTestCase):

    def _arm(self, chat="c1"):
        """让下一次消息必定刷新机缘：把上次刷新时间推到很久以前。"""
        state = self.store.get_group_state(chat)
        state.last_encounter_at = time.time() - 46 * 60
        self.store.save_group_state(state)

    def test_first_sighting_only_starts_the_clock(self):
        # 首次观测不刷新，避免机器人重启后立刻刷屏
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        state = self.store.get_group_state("c1")
        self.assertEqual(state.active_reward_kind, "")
        self.assertGreater(state.last_encounter_at, 0)

    def test_encounter_spawns_after_interval(self):
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        self._arm()
        reply = self.handler.record_activity(self._chat("换一句完全不同的话"))
        self.assertIsNotNone(reply)
        self.assertIn("抢机缘", reply)
        self.assertEqual(self.store.get_group_state("c1").active_reward_kind,
                         "stones")

    def test_spawn_skipped_for_inactive_group(self):
        handler = self._make_handler(message_store=FakeMessageStore(active=False))
        handler.record_activity(self._chat("今天天气真不错啊"))
        self._arm()
        self.assertIsNone(handler.record_activity(self._chat("换一句完全不同的话")))

    def test_spawn_disabled_by_zero_interval(self):
        cfg = FakeConfig()
        cfg.game_encounter_interval_min = 0
        handler = self._make_handler(config=cfg)
        handler.record_activity(self._chat("今天天气真不错啊"))
        self._arm()
        self.assertIsNone(handler.record_activity(self._chat("换一句完全不同的话")))

    def test_claim_awards_stones(self):
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        self._arm()
        self.handler.record_activity(self._chat("换一句完全不同的话"))
        amount = self.store.get_group_state("c1").active_reward_amount
        self.assertGreater(amount, 0)

        reply = self.handler.record_activity(
            self._chat("抢机缘", uid="u2", name="小红")
        )
        self.assertIn("灵石 +", reply)
        self.assertEqual(
            self.store.get_character("c1", "u2").spirit_stones, amount
        )

    def test_claim_only_works_once(self):
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        self._arm()
        self.handler.record_activity(self._chat("换一句完全不同的话"))
        first = self.handler.record_activity(
            self._chat("抢机缘", uid="u2", name="小红")
        )
        second = self.handler.record_activity(
            self._chat("抢机缘", uid="u3", name="小刚")
        )
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_claim_without_active_encounter_is_safe(self):
        # 「抢机缘」只有 3 个字，低于被动收益门槛——必须仍能被识别
        reply = self.handler.record_activity(self._chat("抢机缘"))
        self.assertIsNone(reply)
        self.assertEqual(self.store.get_character("c1", "u1").spirit_stones, 0)

    def test_claim_via_at_command_reports_nothing_to_grab(self):
        reply = self.handler.handle("抢机缘", "c1", "u1", "小明")
        self.assertIn("手慢了", reply)

    def test_expired_encounter_is_reclaimed(self):
        self.store.spawn_encounter("c1", now=0.0, expires_at=1.0,
                                   reward_kind="stones", reward_amount=9)
        self._arm()
        self.handler.record_activity(self._chat("今天天气真不错啊"))
        # 过期的机缘被回收，新的机缘得以刷新
        self.assertEqual(self.store.get_group_state("c1").active_reward_kind,
                         "stones")
        self.assertNotEqual(self.store.get_group_state("c1").active_reward_amount, 9)


class TestAlchemy(HandlerTestCase):

    def test_requires_stones(self):
        reply = self.handler.handle("炼丹", "c1", "u1", "小明")
        self.assertIn("灵石", reply)

    def test_produces_a_pill_and_costs_stones(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.spirit_stones = 100
        self.store.save_character(char)
        self.handler.handle("炼丹", "c1", "u1", "小明")
        char = self.store.get_character("c1", "u1")
        self.assertEqual(char.spirit_stones, 100 - R.ALCHEMY_COST)
        self.assertEqual(sum(char.pills.values()), 1)

    def test_daily_limit_enforced(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.spirit_stones = 10_000
        char.daily_key = time.strftime("%Y-%m-%d")
        char.alchemy_used = R.ALCHEMY_DAILY_LIMIT
        self.store.save_character(char)
        reply = self.handler.handle("炼丹", "c1", "u1", "小明")
        self.assertIn("次数已用尽", reply)

    def test_daily_counters_reset_on_new_day(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.spirit_stones = 10_000
        char.daily_key = "2020-01-01"  # 早于今天 → 触发跨天重置
        char.alchemy_used = R.ALCHEMY_DAILY_LIMIT
        self.store.save_character(char)
        reply = self.handler.handle("炼丹", "c1", "u1", "小明")
        self.assertNotIn("次数已用尽", reply)
        self.assertEqual(self.store.get_character("c1", "u1").alchemy_used, 1)

    def test_take_pill_consumes_it(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.pills = {"回气丹": 1}
        self.store.save_character(char)
        self.handler.handle("服用 回气丹", "c1", "u1", "小明")
        char = self.store.get_character("c1", "u1")
        self.assertEqual(char.pills, {})
        self.assertEqual(char.exp, 50)

    def test_take_pill_without_space(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.pills = {"回气丹": 1}
        self.store.save_character(char)
        reply = self.handler.handle("服用回气丹", "c1", "u1", "小明")
        self.assertIn("回气丹", reply)
        self.assertEqual(self.store.get_character("c1", "u1").pills, {})

    def test_take_pill_allows_partial_name(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.pills = {"破境丹": 1}
        self.store.save_character(char)
        reply = self.handler.handle("服用 破境", "c1", "u1", "小明")
        self.assertIn("破境丹", reply)

    def test_take_missing_pill(self):
        reply = self.handler.handle("服用 回气丹", "c1", "u1", "小明")
        self.assertIn("没有", reply)

    def test_take_pill_without_argument(self):
        reply = self.handler.handle("服用", "c1", "u1", "小明")
        self.assertIn("要服用什么丹药", reply)


class TestProfileAndRanking(HandlerTestCase):

    def test_profile_creates_character(self):
        reply = self.handler.handle("我的", "c1", "u1", "小明")
        self.assertIn("炼气初期", reply)
        self.assertIsNotNone(self.store.get_character("c1", "u1"))

    def test_ranking_empty_group(self):
        reply = self.handler.handle("排行榜", "c1", "u1", "小明")
        self.assertIn("尚无修士", reply)

    def test_ranking_lists_members(self):
        for i, (uid, name) in enumerate([("u1", "甲"), ("u2", "乙")]):
            c = self.store.get_or_create("c1", uid, name)
            c.realm_index = i
            self.store.save_character(c)
        reply = self.handler.handle("排行榜", "c1", "u1", "小明")
        self.assertIn("甲", reply)
        self.assertIn("乙", reply)


class TestDuel(HandlerTestCase):

    def _pair(self, a_exp=40, b_exp=30):
        a = self.store.get_or_create("c1", "u1", "小明")
        a.exp, a.total_exp = a_exp, a_exp
        self.store.save_character(a)
        b = self.store.get_or_create("c1", "u2", "小红")
        b.exp, b.total_exp = b_exp, b_exp
        self.store.save_character(b)

    def test_requires_target(self):
        reply = self.handler.handle("论道", "c1", "u1", "小明")
        self.assertIn("想和谁论道", reply)

    def test_unknown_target(self):
        reply = self.handler.handle("论道 老王", "c1", "u1", "小明")
        self.assertIn("没有找到", reply)

    def test_cannot_duel_self(self):
        self._pair()
        reply = self.handler.handle("论道 小明", "c1", "u1", "小明")
        self.assertIn("与自己论道", reply)

    def test_realm_gap_blocks_duel(self):
        a = self.store.get_or_create("c1", "u1", "小明")
        a.realm_index = 10
        self.store.save_character(a)
        self.store.get_or_create("c1", "u2", "小红")
        reply = self.handler.handle("论道 小红", "c1", "u1", "小明")
        self.assertIn("欺负新人", reply)

    def test_win_transfers_exp(self):
        self._pair()
        self.handler._rng.random = lambda: 0.0  # 挑战者必胜
        self.handler.handle("论道 小红", "c1", "u1", "小明")
        a = self.store.get_character("c1", "u1")
        b = self.store.get_character("c1", "u2")
        self.assertGreater(a.exp, 40)
        self.assertLess(b.exp, 30)

    def test_daily_limit_enforced(self):
        self._pair()
        char = self.store.get_character("c1", "u1")
        char.daily_key = time.strftime("%Y-%m-%d")
        char.duel_used = R.DUEL_DAILY_LIMIT
        self.store.save_character(char)
        reply = self.handler.handle("论道 小红", "c1", "u1", "小明")
        self.assertIn("次数已用尽", reply)

    def test_same_target_rejected_within_day(self):
        self._pair()
        self.handler.handle("论道 小红", "c1", "u1", "小明")
        reply = self.handler.handle("论道 小红", "c1", "u1", "小明")
        self.assertIn("已与", reply)

    def test_duel_logs_event(self):
        self._pair()
        self.handler.handle("论道 小红", "c1", "u1", "小明")
        events = self.store.list_events("c1")
        self.assertTrue(any(e.kind == "duel" for e in events))


class TestAiFlavorFallback(HandlerTestCase):
    """AI 不可用时必须静默降级到模板，不能中断回复。"""

    def test_breakthrough_works_without_summarizer(self):
        char = self.store.get_or_create("c1", "u1", "小明")
        char.exp = R.exp_needed(0)
        self.store.save_character(char)
        self.handler._rng.random = lambda: 0.0
        reply = self.handler.handle("突破", "c1", "u1", "小明")
        self.assertIn("炼气中期", reply)

    def test_flavor_generator_handles_broken_summarizer(self):
        class Boom:
            def _call_chat_api(self, system_prompt, messages):
                raise RuntimeError("api down")

        handler = GameHandler(
            self.store, FakeConfig(), summarizer=Boom(),
            message_store=FakeMessageStore(), rng=random.Random(0),
        )
        # game_ai_flavor_enabled 为 False，直接走模板
        char = self.store.get_or_create("c1", "u1", "小明")
        char.exp = R.exp_needed(0)
        self.store.save_character(char)
        handler._rng.random = lambda: 0.0
        reply = handler.handle("突破", "c1", "u1", "小明")
        self.assertIn("炼气中期", reply)


if __name__ == "__main__":
    unittest.main()
