"""修仙玩法 — 数据表与查表函数（纯数据，无副作用）。

境界共 27 阶（9 大境界 × 初期/中期/后期），第 28 个下标 27 表示飞升后的「仙人」。
所有数值调整都集中在本文件，方便调平衡而不动逻辑代码。
"""

from typing import NamedTuple

# ── 境界 ─────────────────────────────────────────────────────────────

# (境界名, 该境界每阶基础所需修为)
REALMS: list[tuple[str, int]] = [
    ("炼气", 60),
    ("筑基", 180),
    ("金丹", 480),
    ("元婴", 1200),
    ("化神", 2800),
    ("炼虚", 6000),
    ("合体", 12000),
    ("大乘", 24000),
    ("渡劫", 48000),
]

STAGES: tuple[str, ...] = ("初期", "中期", "后期")

#: 27 个可修炼阶位（下标 0..26）之后的下标 27 = 仙人（满级）
IMMORTAL_INDEX = len(REALMS) * len(STAGES)  # 27
IMMORTAL_LABEL = "仙人"

#: 同一境界内，中/后期所需修为相对初期的放大系数
STAGE_EXP_STEP = 0.35

# ── 收益与突破参数 ───────────────────────────────────────────────────

#: 每提升 1 个阶位，收益放大 15%
REALM_GAIN_STEP = 0.15

#: 被动发言收益的最短消息长度（防止「嗯」「哦」刷修为）
MIN_CHAT_LENGTH = 4

#: 突破基础成功率随阶位递减的斜率
BREAKTHROUGH_SLOPE = 0.025
#: 渡劫（渡劫后期 → 仙人）的基础成功率
TRIBULATION_BASE_CHANCE = 0.35
#: 突破成功率下限 / 上限
MIN_CHANCE = 0.15
MAX_CHANCE = 0.98
#: 每次连续失败提供的补偿成功率，最多叠 3 层
FAIL_STREAK_BONUS = 0.05
MAX_FAIL_STREAK_BONUS = 3

#: 突破失败扣除当前修为的比例
FAIL_EXP_LOSS_RATIO = 0.30
#: 渡劫失败扣除当前修为的比例，并跌落 1 个境界
TRIBULATION_FAIL_EXP_LOSS_RATIO = 0.50

#: 突破成功时习得功法的概率
TECHNIQUE_LEARN_CHANCE = 0.25
#: 每次突破成功获得的灵石（乘以阶位）
SPIRIT_STONES_PER_REALM = 10

#: 炼丹消耗灵石 / 每日炼丹次数上限
ALCHEMY_COST = 20
ALCHEMY_DAILY_LIMIT = 3

#: 论道每日次数上限 / 可挑战的境界差上限（防止欺负新人）
DUEL_DAILY_LIMIT = 3
DUEL_MAX_REALM_GAP = 2
#: 论道胜者夺取败者当前修为的比例
DUEL_STAKE_RATIO = 0.05

#: 机缘窗口时长（秒）与判定「活跃群」的阈值
ENCOUNTER_WINDOW_SEC = 300
ENCOUNTER_ACTIVE_WINDOW_SEC = 600
ENCOUNTER_ACTIVE_MIN_MESSAGES = 5

# ── 丹药 ─────────────────────────────────────────────────────────────

PILLS: dict[str, dict] = {
    "回气丹": {
        "weight": 55,
        "desc": "服下后立即获得 50 点修为",
        "effect": {"exp": 50},
    },
    "破境丹": {
        "weight": 33,
        "desc": "下次突破成功率 +15%",
        "effect": {"pending_bonus": 0.15},
    },
    "洗髓丹": {
        "weight": 12,
        "desc": "清空连续失败计数，且下次突破成功率 +20%",
        "effect": {"pending_bonus": 0.20, "reset_fail_streak": True},
    },
}

# ── 功法（同时只能装备 1 本，新学的顶替旧的）─────────────────────────

TECHNIQUES: dict[str, dict] = {
    "长春功": {
        "weight": 24,
        "desc": "被动修为 +10%",
        "effect": {"passive_mult": 1.10},
    },
    "太玄经": {
        "weight": 24,
        "desc": "主动修炼修为 +10%",
        "effect": {"active_mult": 1.10},
    },
    "金刚诀": {
        "weight": 18,
        "desc": "突破成功率 +5%",
        "effect": {"breakthrough": 0.05},
    },
    "御风术": {
        "weight": 18,
        "desc": "机缘收益 +50%",
        "effect": {"encounter_mult": 1.50},
    },
    "五雷正法": {
        "weight": 10,
        "desc": "突破成功率 +8%",
        "effect": {"breakthrough": 0.08},
    },
    "玄天心法": {
        "weight": 6,
        "desc": "被动修为 +20%",
        "effect": {"passive_mult": 1.20},
    },
}

# ── 文案模板池（AI 不可用时的降级文案）───────────────────────────────

TEMPLATES: dict[str, list[str]] = {
    "breakthrough_success": [
        "{name} 周身灵气骤然收束，丹田轰鸣——境界突破，已至【{to_realm}】！",
        "一声清啸破空，{name} 睁眼时眸中已映出【{to_realm}】的气象。",
        "{name} 足下灵纹浮现又碎裂，桎梏尽去，稳稳落在【{to_realm}】。",
        "天地一线灵气垂落，{name} 借此冲开关窍，晋入【{to_realm}】。",
        "{name} 闭目三日，再抬眼时，已是【{to_realm}】修士。",
    ],
    "breakthrough_fail": [
        "{name} 冲关未果，灵气溃散，好在此次未伤及道基。",
        "{name} 强冲关隘，经脉一阵刺痛——差了一线，修为倒退了些。",
        "{name} 这一次没能冲过去。道心若有裂痕，下次反而更难。",
        "{name} 突破失败，原地调息良久才缓过气来。",
        "{name} 冲关之际杂念丛生，功亏一篑。",
    ],
    "tribulation_success": [
        "九霄雷云散尽，{name} 立于劫灰之上——从此仙凡两隔，是为【仙人】！",
        "最后一道天雷落下时，{name} 没有退。雷光散去，天上多了一位仙人。",
        "{name} 扛过九重天劫，凡骨尽蜕，飞升为【仙人】。",
    ],
    "tribulation_fail": [
        "天雷无情，{name} 被生生劈落一个境界，道基受损。",
        "{name} 终究没能扛住最后一道雷，仙路暂时断了。",
        "劫云散去时，{name} 已跌落境界，气息萎靡。",
    ],
    "encounter_spawn": [
        "天地间忽有异动——一缕先天灵气自虚空渗出，正在本群上空盘旋！\n率先回复「抢机缘」者得之。（{minutes} 分钟内有效）",
        "机缘现世：一枚无主灵物坠入本群，灵光流转，引得四下目光汇聚。\n回复「抢机缘」可抢先夺取。（{minutes} 分钟内有效）",
        "异象横生，一道古老禁制在此处裂开缝隙，灵气外溢。\n回复「抢机缘」者，可入内一探。（{minutes} 分钟内有效）",
    ],
    "encounter_claim": [
        "{name} 抢得先机，夺得机缘：灵石 +{stones}。",
        "{name} 眼疾手快，一把攫住那缕灵气：灵石 +{stones}。",
        "机缘落入 {name} 之手，灵石 +{stones}。",
    ],
    "encounter_miss": [
        "有人抢先一步，机缘已被取走。",
        "慢了一线——那缕灵气已经散了。",
    ],
    "technique_learned": [
        "{name} 于突破之际忽有所悟，习得功法【{technique}】（{desc}）。",
        "关窍洞开的一瞬，{name} 脑海中浮现出【{technique}】的运转法门（{desc}）。",
    ],
    "alchemy": [
        "{name} 以灵石为引，炉火三日不熄——丹成【{pill}】。\n{desc}",
        "{name} 开炉炼药，丹香四溢，得【{pill}】一枚。\n{desc}",
    ],
    "duel_win": [
        "{winner} 与 {loser} 论道于虚空，三百招后 {winner} 略胜一筹，夺其修为 {stake} 点。",
        "{winner} 一指点出，{loser} 心神剧震，退让三分。{winner} 得修为 {stake} 点。",
    ],
    "duel_lose": [
        "{winner} 与 {loser} 论道，{loser} 力有不逮，被夺去修为 {stake} 点。",
        "论道场上，{loser} 稍逊一筹，被 {winner} 取走修为 {stake} 点。",
    ],
}


# ── 查表函数 ─────────────────────────────────────────────────────────


def realm_label(realm_index: int) -> str:
    """境界下标 → 显示名。

    Args:
        realm_index: 0..26 为 9 大境界 × 初期/中期/后期，27 为仙人。

    Returns:
        如 ``"金丹中期"`` / ``"仙人"``。越界下标会被夹到有效范围内。
    """
    if realm_index >= IMMORTAL_INDEX:
        return IMMORTAL_LABEL
    if realm_index < 0:
        realm_index = 0
    realm_name, _ = REALMS[realm_index // len(STAGES)]
    return f"{realm_name}{STAGES[realm_index % len(STAGES)]}"


def realm_short_name(realm_index: int) -> str:
    """只取大境界名（不含初/中/后期），用于排行榜徽章。"""
    if realm_index >= IMMORTAL_INDEX:
        return IMMORTAL_LABEL
    if realm_index < 0:
        realm_index = 0
    return REALMS[realm_index // len(STAGES)][0]


def exp_needed(realm_index: int) -> int:
    """突破到下一阶所需的修为。仙人（满级）返回 0。"""
    if realm_index >= IMMORTAL_INDEX:
        return 0
    if realm_index < 0:
        realm_index = 0
    _, base = REALMS[realm_index // len(STAGES)]
    return int(base * (1 + STAGE_EXP_STEP * (realm_index % len(STAGES))))


def is_tribulation(realm_index: int) -> bool:
    """该阶位的突破是否为渡劫（渡劫后期 → 仙人）。"""
    return realm_index == IMMORTAL_INDEX - 1


def is_max_realm(realm_index: int) -> bool:
    """是否已达最高境界（仙人）。"""
    return realm_index >= IMMORTAL_INDEX


def technique_effect(technique: str, key: str, default=0.0):
    """读取某本功法的某项加成，未习得该功法时返回 default。"""
    return TECHNIQUES.get(technique, {}).get("effect", {}).get(key, default)


def weighted_pick(table: dict[str, dict], rng) -> str:
    """按 ``weight`` 字段从数据表中随机取一个键。

    Args:
        table: 形如 ``{"回气丹": {"weight": 55, ...}, ...}``。
        rng: ``random.Random`` 实例（注入以便测试可复现）。

    Returns:
        被选中的键；表为空时返回空字符串。
    """
    if not table:
        return ""
    names = list(table)
    weights = [max(0.0, float(table[n].get("weight", 1))) for n in names]
    if sum(weights) <= 0:
        return rng.choice(names)
    return rng.choices(names, weights=weights, k=1)[0]
