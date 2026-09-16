"""修仙玩法 — 纯逻辑引擎。

本模块**不触碰数据库、文件系统与网络**，所有函数都是纯函数（随机性通过
``rng`` 参数注入）。这样突破概率、收益曲线这类关键数值可以在单元测试里
被精确验证，而不需要构造 DB 或 mock 时间。
"""

import random
import time
from dataclasses import dataclass
from datetime import date

from . import realms as R


@dataclass(frozen=True)
class BreakthroughResult:
    """一次突破尝试的结算结果。"""

    success: bool
    chance: float
    is_tribulation: bool
    from_index: int
    to_index: int          # 失败时等于 from_index（渡劫失败另见 realm_after）
    realm_after: int       # 失败可能跌落境界，故与 to_index 分开
    exp_after: int
    fail_streak: int
    spirit_stones_gained: int
    learned_technique: str


# ── 基础换算 ─────────────────────────────────────────────────────────


def gain_for_realm(base: int, realm_index: int, multiplier: float = 1.0) -> int:
    """按境界放大一笔基础收益。

    Args:
        base: 基础数值（如 ``game_chat_exp``）。
        realm_index: 当前阶位，越高收益越大。
        multiplier: 全局倍率与功法加成的乘积。

    Returns:
        至少为 1 的整数收益。
    """
    scaled = base * (1 + realm_index * R.REALM_GAIN_STEP) * multiplier
    return max(1, int(round(scaled)))


def passive_exp(realm_index: int, base: int, technique: str = "",
                multiplier: float = 1.0) -> int:
    """群聊发言的被动修为收益（已计入功法加成）。"""
    tech = R.technique_effect(technique, "passive_mult", 1.0)
    return gain_for_realm(base, realm_index, multiplier * tech)


def active_exp(realm_index: int, base: int, technique: str = "",
               multiplier: float = 1.0) -> int:
    """主动「修炼」的修为收益（已计入功法加成）。"""
    tech = R.technique_effect(technique, "active_mult", 1.0)
    return gain_for_realm(base, realm_index, multiplier * tech)


def encounter_stones(realm_index: int, technique: str = "",
                     rng: random.Random | None = None) -> int:
    """一次天降机缘的灵石收益（已计入御风术加成）。"""
    rng = rng or random
    base = rng.randint(5, 30)
    tech = R.technique_effect(technique, "encounter_mult", 1.0)
    return max(1, int(round(base * (1 + realm_index * 0.05) * tech)))


def exp_progress(realm_index: int, exp: int) -> float:
    """当前阶的修为进度，取值 ``0.0 ~ 1.0``。仙人恒为 1.0。"""
    need = R.exp_needed(realm_index)
    if need <= 0:
        return 1.0
    return max(0.0, min(1.0, exp / need))


# ── 突破 ─────────────────────────────────────────────────────────────


def breakthrough_chance(realm_index: int, technique: str = "",
                        pending_bonus: float = 0.0,
                        fail_streak: int = 0) -> float:
    """计算突破成功率，夹在 ``[MIN_CHANCE, MAX_CHANCE]`` 之间。

    Args:
        realm_index: 当前阶位。
        technique: 已习得功法名（可为空）。
        pending_bonus: 丹药提供的一次性加成（服用后累积）。
        fail_streak: 连续失败次数，每次 +5%，最多叠 3 层。
    """
    if R.is_tribulation(realm_index):
        base = R.TRIBULATION_BASE_CHANCE
    else:
        base = 0.90 - realm_index * R.BREAKTHROUGH_SLOPE

    bonus = (
        R.technique_effect(technique, "breakthrough", 0.0)
        + pending_bonus
        + min(max(fail_streak, 0), R.MAX_FAIL_STREAK_BONUS) * R.FAIL_STREAK_BONUS
    )
    return max(R.MIN_CHANCE, min(R.MAX_CHANCE, base + bonus))


def resolve_breakthrough(realm_index: int, exp: int, technique: str = "",
                         pending_bonus: float = 0.0, fail_streak: int = 0,
                         rng: random.Random | None = None) -> BreakthroughResult:
    """结算一次突破尝试。

    成功：阶位 +1，修为清零，按阶位获得灵石，并有概率习得功法。
    失败：扣除当前修为的 30%（渡劫失败扣 50% 且跌落 1 个境界），阶位不变。

    调用方需先确认修为已满（``exp >= exp_needed(realm_index)``）。
    """
    rng = rng or random
    chance = breakthrough_chance(realm_index, technique, pending_bonus, fail_streak)
    tribulation = R.is_tribulation(realm_index)
    success = rng.random() < chance

    if success:
        to_index = min(realm_index + 1, R.IMMORTAL_INDEX)
        stones = (realm_index + 1) * R.SPIRIT_STONES_PER_REALM
        learned = ""
        if to_index < R.IMMORTAL_INDEX and rng.random() < R.TECHNIQUE_LEARN_CHANCE:
            learned = R.weighted_pick(R.TECHNIQUES, rng)
        return BreakthroughResult(
            success=True, chance=chance, is_tribulation=tribulation,
            from_index=realm_index, to_index=to_index, realm_after=to_index,
            exp_after=0, fail_streak=0, spirit_stones_gained=stones,
            learned_technique=learned,
        )

    ratio = (R.TRIBULATION_FAIL_EXP_LOSS_RATIO if tribulation
             else R.FAIL_EXP_LOSS_RATIO)
    realm_after = realm_index
    base_exp = exp
    if tribulation:
        # 渡劫失败跌落一个境界（3 个阶位），但不低于炼气初期。
        # 修为按**新境界**的量级折算——直接沿用原境界的数值会因两个境界的
        # 需求量恰好错开而显得惩罚时轻时重。
        realm_after = max(0, realm_index - len(R.STAGES))
        need_after = R.exp_needed(realm_after)
        if need_after > 0:
            base_exp = need_after
    return BreakthroughResult(
        success=False, chance=chance, is_tribulation=tribulation,
        from_index=realm_index, to_index=realm_index, realm_after=realm_after,
        exp_after=max(0, int(base_exp * (1 - ratio))), fail_streak=fail_streak + 1,
        spirit_stones_gained=0, learned_technique="",
    )


def apply_pill(character, pill_name: str, exp_cap: int = 0) -> str:
    """把丹药效果作用到角色上（就地修改），返回给玩家看的说明文字。

    Args:
        character: ``store.GameCharacter`` 实例。
        pill_name: 丹药名。
        exp_cap: 当前境界的修为上限；>0 时丹药提供的修为不会溢出该上限。

    Returns:
        效果描述；丹药不存在时返回空字符串（调用方据此判定失败）。
    """
    pill = R.PILLS.get(pill_name)
    if not pill:
        return ""
    effect = pill.get("effect", {})

    if effect.get("reset_fail_streak"):
        character.fail_streak = 0
    if "pending_bonus" in effect:
        character.pending_bonus += float(effect["pending_bonus"])
    if "exp" in effect:
        gained = int(effect["exp"])
        if exp_cap > 0:
            gained = min(gained, max(0, exp_cap - character.exp))
        character.exp += gained
        character.total_exp += gained

    return pill["desc"]


# ── 冷却与防刷 ───────────────────────────────────────────────────────


def cooldown_remaining(last_ts: float, cooldown_sec: int,
                       now: float | None = None) -> int:
    """距离下次可用还剩多少秒，0 表示已可用。

    ``last_ts <= 0``（从未使用过）一律视为已可用——不要依赖 ``now - 0``
    恰好很大这个巧合。
    """
    if not last_ts or last_ts <= 0:
        return 0
    now = time.time() if now is None else now
    elapsed = now - last_ts
    if elapsed >= cooldown_sec:
        return 0
    return max(0, int(cooldown_sec - elapsed) + 1)


def is_valid_chat_content(text: str, last_content: str = "") -> bool:
    """判断一条群消息是否算「有效发言」，可用于发放被动修为。

    拦截：过短、纯表情/标点、与本人上一条完全相同（复读）。
    """
    t = (text or "").strip()
    if len(t) < R.MIN_CHAT_LENGTH:
        return False
    # 至少 2 个实义字符（汉字/字母/数字）；纯 emoji 与标点都过不了 isalnum()
    if sum(1 for ch in t if ch.isalnum()) < 2:
        return False
    if last_content and t == last_content.strip():
        return False
    return True


# ── 每日额度 ─────────────────────────────────────────────────────────


def today_key(now: float | None = None) -> str:
    """当日标识 ``YYYY-MM-DD``；角色行上的 ``daily_key`` 变化即触发额度重置。"""
    if now is None:
        return date.today().isoformat()
    return date.fromtimestamp(now).isoformat()


def rollover_daily(character, key: str) -> None:
    """跨天时重置每日计数（就地修改）。"""
    if character.daily_key != key:
        character.daily_key = key
        character.daily_exp = 0
        character.daily_actions = 0
        character.alchemy_used = 0
        character.duel_used = 0
        character.duel_targets = []


def remaining_daily_exp(character, cap: int) -> int:
    """今日还能通过被动发言获得多少修为。"""
    return max(0, cap - character.daily_exp)


# ── 论道 ─────────────────────────────────────────────────────────────


def duel_allowed(challenger_realm: int, target_realm: int) -> tuple[bool, str]:
    """校验论道是否被允许。

    Returns:
        ``(是否允许, 拒绝原因)``，允许时原因为空字符串。
    """
    if challenger_realm < target_realm - R.DUEL_MAX_REALM_GAP:
        return False, (
            f"对方境界高出你 {target_realm - challenger_realm} 阶，"
            f"强闯只会自取其辱（最多可挑战高出 {R.DUEL_MAX_REALM_GAP} 阶者）。"
        )
    if target_realm < challenger_realm - R.DUEL_MAX_REALM_GAP:
        return False, "对方境界远低于你，胜之不武——欺负新人是要遭天谴的。"
    return True, ""


def duel_stake(loser_exp: int) -> int:
    """论道败者被夺走的修为（至少 1 点，且不超过其当前修为）。"""
    return max(1, min(loser_exp, int(loser_exp * R.DUEL_STAKE_RATIO)))
