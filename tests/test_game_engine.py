"""Tests for src.game.engine — 纯逻辑：收益、突破概率、冷却、防刷、论道。"""

import random
import unittest

from src.game import engine as E
from src.game import realms as R


class TestRealms(unittest.TestCase):
    """境界查表函数。"""

    def test_realm_label_lower_bound(self):
        self.assertEqual(R.realm_label(0), "炼气初期")
        self.assertEqual(R.realm_label(1), "炼气中期")
        self.assertEqual(R.realm_label(2), "炼气后期")
        self.assertEqual(R.realm_label(3), "筑基初期")

    def test_realm_label_immortal(self):
        self.assertEqual(R.realm_label(R.IMMORTAL_INDEX), R.IMMORTAL_LABEL)
        # 越界下标应被夹住，而不是抛 IndexError
        self.assertEqual(R.realm_label(999), R.IMMORTAL_LABEL)

    def test_realm_label_negative_clamped(self):
        self.assertEqual(R.realm_label(-5), "炼气初期")

    def test_exp_needed_grows_within_and_across_realms(self):
        # 同一境界内逐阶变贵
        self.assertLess(R.exp_needed(0), R.exp_needed(1))
        self.assertLess(R.exp_needed(1), R.exp_needed(2))
        # 跨境界跳变
        self.assertLess(R.exp_needed(2), R.exp_needed(3))

    def test_exp_needed_zero_at_max(self):
        self.assertEqual(R.exp_needed(R.IMMORTAL_INDEX), 0)

    def test_is_tribulation_only_at_last_stage(self):
        self.assertFalse(R.is_tribulation(0))
        self.assertFalse(R.is_tribulation(R.IMMORTAL_INDEX - 2))
        self.assertTrue(R.is_tribulation(R.IMMORTAL_INDEX - 1))
        self.assertFalse(R.is_tribulation(R.IMMORTAL_INDEX))

    def test_weighted_pick_respects_zero_weight(self):
        table = {"a": {"weight": 0}, "b": {"weight": 10}}
        rng = random.Random(0)
        picks = {R.weighted_pick(table, rng) for _ in range(50)}
        self.assertEqual(picks, {"b"})

    def test_weighted_pick_empty_table(self):
        self.assertEqual(R.weighted_pick({}, random.Random(0)), "")


class TestGain(unittest.TestCase):
    """收益随境界放大。"""

    def test_gain_scales_with_realm(self):
        low = E.passive_exp(0, 2)
        high = E.passive_exp(20, 2)
        self.assertLess(low, high)

    def test_gain_never_below_one(self):
        self.assertGreaterEqual(E.gain_for_realm(1, 0, 0.01), 1)

    def test_passive_exp_technique_bonus(self):
        base = E.passive_exp(10, 10, technique="")
        boosted = E.passive_exp(10, 10, technique="长春功")  # +10%
        self.assertGreater(boosted, base)

    def test_active_exp_uses_active_multiplier(self):
        base = E.active_exp(10, 10, technique="")
        # 太玄经只加主动收益，长春功只加被动收益
        self.assertGreater(E.active_exp(10, 10, technique="太玄经"), base)
        self.assertEqual(E.active_exp(10, 10, technique="长春功"), base)

    def test_global_multiplier_applies(self):
        one = E.passive_exp(10, 10, multiplier=1.0)
        two = E.passive_exp(10, 10, multiplier=2.0)
        self.assertAlmostEqual(two, one * 2, delta=2)


class TestBreakthroughChance(unittest.TestCase):
    """突破成功率与边界夹取。"""

    def test_chance_decreases_with_realm(self):
        self.assertGreater(
            E.breakthrough_chance(0), E.breakthrough_chance(10)
        )

    def test_chance_clamped_to_bounds(self):
        # 极低的 base + 巨大惩罚也不可能低于下限
        self.assertGreaterEqual(E.breakthrough_chance(100), R.MIN_CHANCE)
        # 巨大加成也不可能超过上限
        self.assertLessEqual(
            E.breakthrough_chance(0, technique="五雷正法",
                                  pending_bonus=5.0, fail_streak=99),
            R.MAX_CHANCE,
        )

    def test_tribulation_uses_dedicated_base(self):
        idx = R.IMMORTAL_INDEX - 1
        self.assertAlmostEqual(
            E.breakthrough_chance(idx), R.TRIBULATION_BASE_CHANCE, places=6
        )

    def test_fail_streak_bonus_caps_at_three(self):
        three = E.breakthrough_chance(10, fail_streak=3)
        ten = E.breakthrough_chance(10, fail_streak=10)
        self.assertAlmostEqual(three, ten, places=6)

    def test_pending_bonus_increases_chance(self):
        self.assertGreater(
            E.breakthrough_chance(10, pending_bonus=0.15),
            E.breakthrough_chance(10),
        )


class TestResolveBreakthrough(unittest.TestCase):
    """突破结算的成功/失败分支。"""

    def test_success_advances_and_clears_exp(self):
        idx = 5
        exp = R.exp_needed(idx)
        rng = random.Random(0)
        rng.random = lambda: 0.0  # 必定成功
        result = E.resolve_breakthrough(idx, exp, rng=rng)
        self.assertTrue(result.success)
        self.assertEqual(result.realm_after, idx + 1)
        self.assertEqual(result.exp_after, 0)
        self.assertEqual(result.fail_streak, 0)
        self.assertGreater(result.spirit_stones_gained, 0)

    def test_success_never_exceeds_immortal(self):
        idx = R.IMMORTAL_INDEX - 1
        rng = random.Random(0)
        rng.random = lambda: 0.0
        result = E.resolve_breakthrough(idx, R.exp_needed(idx), rng=rng)
        self.assertTrue(result.success)
        self.assertEqual(result.realm_after, R.IMMORTAL_INDEX)
        self.assertFalse(R.is_max_realm(result.realm_after - 1))

    def test_failure_keeps_realm_and_loses_exp(self):
        idx = 5
        exp = R.exp_needed(idx)
        rng = random.Random(0)
        rng.random = lambda: 1.0  # 必定失败
        result = E.resolve_breakthrough(idx, exp, rng=rng)
        self.assertFalse(result.success)
        self.assertEqual(result.realm_after, idx)
        self.assertEqual(result.fail_streak, 1)
        self.assertLess(result.exp_after, exp)
        self.assertEqual(result.exp_after, int(exp * (1 - R.FAIL_EXP_LOSS_RATIO)))

    def test_tribulation_failure_drops_one_realm(self):
        idx = R.IMMORTAL_INDEX - 1  # 渡劫后期
        rng = random.Random(0)
        rng.random = lambda: 1.0
        result = E.resolve_breakthrough(idx, R.exp_needed(idx), rng=rng)
        self.assertFalse(result.success)
        self.assertTrue(result.is_tribulation)
        self.assertEqual(result.realm_after, idx - len(R.STAGES))
        # 修为按新境界的量级折算，不是沿用原境界的数值
        self.assertLessEqual(result.exp_after, R.exp_needed(result.realm_after))

    def test_tribulation_failure_at_lowest_realm_clamps_to_zero(self):
        rng = random.Random(0)
        rng.random = lambda: 1.0
        result = E.resolve_breakthrough(0, 10, rng=rng)
        self.assertEqual(result.realm_after, 0)

    def test_success_can_learn_technique(self):
        # 强制两次 random() 都返回 0 → 成功 + 习得功法
        rng = random.Random(0)
        rng.random = lambda: 0.0
        result = E.resolve_breakthrough(3, R.exp_needed(3), rng=rng)
        self.assertTrue(result.success)
        self.assertIn(result.learned_technique, R.TECHNIQUES)


class TestPills(unittest.TestCase):
    """丹药效果。"""

    def _char(self):
        from src.game.store import GameCharacter
        return GameCharacter(chat_id="c", user_id="u")

    def test_recovery_pill_adds_exp(self):
        c = self._char()
        E.apply_pill(c, "回气丹")
        self.assertEqual(c.exp, 50)
        self.assertEqual(c.total_exp, 50)

    def test_recovery_pill_respects_exp_cap(self):
        c = self._char()
        c.exp = 40
        E.apply_pill(c, "回气丹", exp_cap=60)
        self.assertEqual(c.exp, 60)

    def test_breakthrough_pill_stacks_pending_bonus(self):
        c = self._char()
        E.apply_pill(c, "破境丹")
        E.apply_pill(c, "破境丹")
        self.assertAlmostEqual(c.pending_bonus, 0.30, places=6)

    def test_marrow_pill_resets_fail_streak(self):
        c = self._char()
        c.fail_streak = 4
        E.apply_pill(c, "洗髓丹")
        self.assertEqual(c.fail_streak, 0)
        self.assertAlmostEqual(c.pending_bonus, 0.20, places=6)

    def test_unknown_pill_returns_empty(self):
        self.assertEqual(E.apply_pill(self._char(), "不存在丹"), "")


class TestCooldown(unittest.TestCase):
    """冷却判定。"""

    def test_ready_when_elapsed(self):
        self.assertEqual(E.cooldown_remaining(100.0, 30, now=200.0), 0)

    def test_remaining_while_cooling(self):
        self.assertGreater(E.cooldown_remaining(100.0, 30, now=110.0), 0)

    def test_zero_last_ts_means_ready(self):
        self.assertEqual(E.cooldown_remaining(0, 30, now=5.0), 0)


class TestValidChatContent(unittest.TestCase):
    """被动收益的防刷过滤。"""

    def test_normal_sentence_ok(self):
        self.assertTrue(E.is_valid_chat_content("今天天气真不错啊"))

    def test_too_short_rejected(self):
        self.assertFalse(E.is_valid_chat_content("嗯"))
        self.assertFalse(E.is_valid_chat_content("好的"))

    def test_pure_emoji_rejected(self):
        self.assertFalse(E.is_valid_chat_content("😀😀😀😀"))

    def test_pure_punctuation_rejected(self):
        self.assertFalse(E.is_valid_chat_content("。。。。。"))

    def test_repeat_of_last_message_rejected(self):
        text = "今天天气真不错啊"
        self.assertFalse(E.is_valid_chat_content(text, last_content=text))

    def test_similar_but_different_message_ok(self):
        self.assertTrue(
            E.is_valid_chat_content("今天天气真不错呀", last_content="今天天气真不错啊")
        )

    def test_empty_rejected(self):
        self.assertFalse(E.is_valid_chat_content(""))
        self.assertFalse(E.is_valid_chat_content(None))


class TestDailyQuota(unittest.TestCase):
    """每日额度与跨天重置。"""

    def _char(self):
        from src.game.store import GameCharacter
        return GameCharacter(chat_id="c", user_id="u")

    def test_remaining_daily_exp(self):
        c = self._char()
        c.daily_exp = 150
        self.assertEqual(E.remaining_daily_exp(c, 200), 50)

    def test_remaining_never_negative(self):
        c = self._char()
        c.daily_exp = 500
        self.assertEqual(E.remaining_daily_exp(c, 200), 0)

    def test_rollover_resets_counters_on_new_day(self):
        c = self._char()
        c.daily_key = "2026-01-01"
        c.daily_exp = 180
        c.daily_actions = 2
        c.alchemy_used = 3
        c.duel_used = 3
        c.duel_targets = ["x"]
        E.rollover_daily(c, "2026-01-02")
        self.assertEqual(c.daily_key, "2026-01-02")
        self.assertEqual(c.daily_exp, 0)
        self.assertEqual(c.daily_actions, 0)
        self.assertEqual(c.alchemy_used, 0)
        self.assertEqual(c.duel_used, 0)
        self.assertEqual(c.duel_targets, [])

    def test_rollover_noop_within_same_day(self):
        c = self._char()
        c.daily_key = "2026-01-01"
        c.daily_exp = 180
        E.rollover_daily(c, "2026-01-01")
        self.assertEqual(c.daily_exp, 180)

    def test_today_key_format(self):
        self.assertRegex(E.today_key(), r"^\d{4}-\d{2}-\d{2}$")


class TestDuel(unittest.TestCase):
    """论道的境界限制与赌注。"""

    def test_same_realm_allowed(self):
        allowed, reason = E.duel_allowed(10, 10)
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_far_weaker_challenger_blocked(self):
        allowed, reason = E.duel_allowed(0, 10)
        self.assertFalse(allowed)
        self.assertIn("境界高出你", reason)

    def test_far_stronger_challenger_blocked(self):
        allowed, reason = E.duel_allowed(10, 0)
        self.assertFalse(allowed)
        self.assertIn("欺负新人", reason)

    def test_gap_within_limit_allowed(self):
        self.assertTrue(E.duel_allowed(10, 12)[0])
        self.assertTrue(E.duel_allowed(12, 10)[0])

    def test_stake_is_bounded(self):
        self.assertEqual(E.duel_stake(0), 1)
        self.assertLessEqual(E.duel_stake(100), 100)

    def test_stake_is_fraction_of_exp(self):
        self.assertEqual(E.duel_stake(1000), int(1000 * R.DUEL_STAKE_RATIO))


class TestProgress(unittest.TestCase):
    """修为进度条。"""

    def test_progress_zero_to_one(self):
        idx = 3
        self.assertAlmostEqual(E.exp_progress(idx, 0), 0.0)
        self.assertAlmostEqual(E.exp_progress(idx, R.exp_needed(idx)), 1.0)

    def test_progress_clamped(self):
        self.assertEqual(E.exp_progress(3, 10 ** 9), 1.0)

    def test_immortal_always_full(self):
        self.assertEqual(E.exp_progress(R.IMMORTAL_INDEX, 0), 1.0)


if __name__ == "__main__":
    unittest.main()
