"""修仙玩法 — 命令解析与分发。

对外只有两个入口：

``handle(...)``
    @bot 后的命令（``修炼`` / ``突破`` / ``排行榜`` …），返回回复文本。

``record_activity(msg)``
    每条群消息都会调用一次（由 ``MessageRouter.handle`` 挂在去重之后）。
    绝大多数情况返回 None（静默累积修为）；只有刷新天降机缘或成功抢占时
    才返回一段需要播报的文本。
"""

import logging
import random
import re
import time
from typing import Optional

from . import engine as E
from . import realms as R
from .flavor import FlavorGenerator
from .store import GameCharacter, GameStore

logger = logging.getLogger(__name__)

#: 复读检测的 LRU 上限（user_key → 上一条消息内容）
_MAX_LAST_CONTENT = 500


def _progress_bar(ratio: float, width: int = 10) -> str:
    """修为进度条。

    刻意只用 ``█``/``□``（都在 GBK 码表内）——``░`` 在 Windows 中文控制台
    下无法编码，一旦这些文本被日志带出去就会抛 UnicodeEncodeError。
    """
    filled = int(round(ratio * width))
    return "█" * filled + "□" * (width - filled)


def _format_realm_line(char: GameCharacter) -> str:
    """角色卡的境界 + 修为进度行。"""
    label = R.realm_label(char.realm_index)
    if R.is_max_realm(char.realm_index):
        return f"【{label}】修为圆满，已无境界可破"
    need = R.exp_needed(char.realm_index)
    ratio = E.exp_progress(char.realm_index, char.exp)
    return (
        f"【{label}】修为 {char.exp}/{need} "
        f"{_progress_bar(ratio)} {int(ratio * 100)}%"
    )


class GameHandler:
    """修仙玩法命令处理器。"""

    def __init__(self, store: GameStore, config, summarizer=None,
                 message_store=None, rng=None):
        """
        Args:
            store: :class:`GameStore` 实例。
            config: ``BotConfig`` 实例（读取 ``game_*`` 字段）。
            summarizer: ``AbstractSummarizer`` 实例，用于关键节点 AI 文案。
            message_store: ``MessageStore`` 实例，用于判断群是否活跃。
            rng: ``random.Random`` 实例（注入以便测试可复现）。
        """
        self._store = store
        self._config = config
        self._message_store = message_store
        self._rng = rng or random.Random()
        self._flavor = FlavorGenerator(
            summarizer=summarizer,
            enabled=getattr(config, "game_ai_flavor_enabled", True),
            rng=self._rng,
        )
        self._last_content: dict[str, str] = {}

    # ── 被动收益入口 ─────────────────────────────────────────────────

    def record_activity(self, msg: dict) -> Optional[str]:
        """每条群消息调用一次：累积修为，并顺带检查天降机缘。

        Returns:
            需要播报的文本（机缘刷新 / 抢占成功），否则 None。
        """
        chat_id = msg.get("chat_id", "")
        user_id = msg.get("sender_id", "")
        if not chat_id or not user_id:
            return None

        now = time.time()

        # 机缘是**群级**事件，与这条消息本身是否算「有效发言」无关，
        # 因此先于防刷判定执行（否则一条复读消息就能把刷新时机推后）。
        encounter_reply = self._maybe_spawn_encounter(chat_id, now)

        content = msg.get("content", "") or ""

        # 「抢机缘」只有 3 个字，低于被动收益的最短长度门槛，必须先于防刷
        # 判定处理，否则群友永远抢不到机缘。抢机缘也不计入修为。
        if self._is_claim_command(content):
            char = self._store.get_or_create(chat_id, user_id,
                                             msg.get("sender_name", ""))
            return self._claim_encounter(chat_id, char, now) or encounter_reply

        key = f"{chat_id}:{user_id}"
        if not E.is_valid_chat_content(content, self._last_content.get(key, "")):
            return encounter_reply

        # 复读检测用（有界，防止长跑内存膨胀）
        if len(self._last_content) >= _MAX_LAST_CONTENT:
            self._last_content.clear()
        self._last_content[key] = content.strip()

        char = self._store.get_or_create(chat_id, user_id, msg.get("sender_name", ""))
        E.rollover_daily(char, E.today_key(now))

        changed = False
        if E.cooldown_remaining(char.last_chat_ts,
                                self._config.game_chat_cooldown_sec, now) == 0:
            remaining = E.remaining_daily_exp(char, self._config.game_daily_exp_cap)
            if remaining > 0:
                gained = min(
                    remaining,
                    E.passive_exp(
                        char.realm_index, self._config.game_chat_exp,
                        char.technique, self._config.game_exp_multiplier,
                    ),
                )
                need = R.exp_needed(char.realm_index)
                if need > 0:
                    gained = min(gained, max(0, need - char.exp))
                if gained > 0:
                    char.exp += gained
                    char.total_exp += gained
                    char.daily_exp += gained
                    char.last_chat_ts = now
                    changed = True

        if changed:
            self._store.save_character(char)

        return encounter_reply

    @staticmethod
    def _is_claim_command(content: str) -> bool:
        return (content or "").strip() in ("抢机缘", "机缘", "夺取机缘", "抢")

    # ── 天降机缘 ─────────────────────────────────────────────────────

    def _maybe_spawn_encounter(self, chat_id: str, now: float) -> Optional[str]:
        """按间隔与群活跃度决定是否刷新一次机缘。"""
        interval_sec = self._config.game_encounter_interval_min * 60
        if interval_sec <= 0:
            return None

        state = self._store.get_group_state(chat_id)

        # 过期回收
        if state.active_reward_kind and state.active_expires_at <= now:
            self._store.clear_encounter(chat_id)
            state.active_reward_kind = ""

        # 已有活跃机缘 → 不重复刷新
        if state.active_reward_kind:
            return None

        # 首次观测：只起算计时，不立刻刷新（避免机器人重启后刷屏）
        if state.last_encounter_at <= 0:
            state.last_encounter_at = now
            self._store.save_group_state(state)
            return None

        if now - state.last_encounter_at < interval_sec:
            return None

        if not self._group_is_active(chat_id, now):
            return None

        stones = E.encounter_stones(0, "", self._rng)
        self._store.spawn_encounter(
            chat_id, now, now + R.ENCOUNTER_WINDOW_SEC, "stones", stones,
        )
        return self._flavor.render_template(
            FlavorGenerator.pool("encounter_spawn"),
            minutes=R.ENCOUNTER_WINDOW_SEC // 60,
        )

    def _group_is_active(self, chat_id: str, now: float) -> bool:
        """最近 10 分钟是否有足够消息量（避免在死群里刷机缘）。"""
        if self._message_store is None:
            return False
        try:
            recent = self._message_store.get_messages_since(
                chat_id,
                int(now - R.ENCOUNTER_ACTIVE_WINDOW_SEC),
                int(now) + 1,
                R.ENCOUNTER_ACTIVE_MIN_MESSAGES,
            )
        except Exception:
            logger.exception("Failed to check group activity for encounter")
            return False
        return len(recent or []) >= R.ENCOUNTER_ACTIVE_MIN_MESSAGES

    def _claim_encounter(self, chat_id: str, char: GameCharacter,
                         now: float) -> Optional[str]:
        """抢占当前机缘（原子操作，先到先得）。"""
        claimed = self._store.claim_encounter(chat_id, now)
        if not claimed:
            return None
        kind, amount = claimed
        if kind != "stones":
            return None

        tech_mult = R.technique_effect(char.technique, "encounter_mult", 1.0)
        stones = max(1, int(round(amount * tech_mult)))
        char.spirit_stones += stones
        self._store.save_character(char)
        self._store.log_event(
            chat_id, "encounter", char.user_id, char.user_name,
            f"{char.user_name} 获得机缘灵石 {stones}",
            {"stones": stones},
        )
        return self._flavor.render_template(
            FlavorGenerator.pool("encounter_claim"),
            name=char.user_name, stones=stones,
        )

    # ── 命令入口 ─────────────────────────────────────────────────────

    def handle(self, clean_content: str, chat_id: str, sender_id: str,
               sender_name: str) -> Optional[str]:
        """解析并执行一条 @bot 修仙命令。

        Returns:
            回复文本；不是修仙命令时返回 None（交给后续处理器）。
        """
        text = (clean_content or "").strip()
        if not text:
            return None

        # 抢夺机缘不要求先有角色，且必须在创建角色前判定
        if self._is_claim_command(text):
            char = self._store.get_or_create(chat_id, sender_id, sender_name)
            now = time.time()
            reply = self._claim_encounter(chat_id, char, now)
            if reply:
                return reply
            return (
                f"@{sender_name} 手慢了，此刻并无机缘可夺。"
                f"（天降机缘会在群里播报，看到就赶紧回「抢机缘」）"
            )

        now = time.time()

        if self._match(text, ("修仙帮助", "修仙说明", "修仙命令", "玩法帮助")):
            return self._cmd_help(sender_name)

        if self._match(text, ("修炼", "打坐", "吐纳")):
            return self._cmd_cultivate(chat_id, sender_id, sender_name, now)

        if self._match(text, ("突破", "冲关")):
            return self._cmd_breakthrough(chat_id, sender_id, sender_name, now)

        if self._match(text, ("渡劫",)):
            return self._cmd_tribulation(chat_id, sender_id, sender_name, now)

        if self._match(text, ("我的", "面板", "角色", "查看修为", "属性")):
            return self._cmd_profile(chat_id, sender_id, sender_name)

        if self._match(text, ("排行榜", "修为榜", "榜单", "排名")):
            return self._cmd_ranking(chat_id, sender_name)

        if self._match(text, ("炼丹", "炼药")):
            return self._cmd_alchemy(chat_id, sender_id, sender_name, now)

        # 丹药命令必须带参数（或裸命令给出提示），故直接前缀匹配，
        # 这样「服用」与「服用回气丹」（无空格）都能命中。
        if text.startswith(("服用", "吃掉", "嗑药")):
            return self._cmd_take_pill(chat_id, sender_id, sender_name, text)

        if text.startswith(("论道", "挑战", "切磋")):
            return self._cmd_duel(chat_id, sender_id, sender_name, text)

        return None

    @staticmethod
    def _match(text: str, keywords: tuple[str, ...]) -> bool:
        """命令词匹配。

        整句等于关键词，或关键词作前缀后紧跟空白/标点。后半条规则是为了
        避免「我的意思是…」这类正常聊天被误判成「我的」命令。
        """
        for kw in keywords:
            if text == kw:
                return True
            if text.startswith(kw):
                rest = text[len(kw):]
                if rest and (rest[0].isspace() or rest[0] in "：:，,。.！!、"):
                    return True
        return False

    # ── 各命令实现 ───────────────────────────────────────────────────

    def _cmd_help(self, sender_name: str) -> str:
        return (
            f"@{sender_name} 【修仙玩法·指令】\n"
            "修炼 / 打坐 — 打坐一次，获得修为（冷却 5 分钟）\n"
            "突破 — 修为满后冲击下一境界\n"
            "渡劫 — 渡劫后期专属，扛过天劫即飞升仙人\n"
            "我的 — 查看自己的境界、修为、灵石与丹药\n"
            "排行榜 — 本群修为榜\n"
            "炼丹 — 消耗灵石随机炼出丹药（每日 3 次）\n"
            "服用 <丹药名> — 服下丹药\n"
            "论道 <群友名> — 与境界相近的群友切磋\n"
            "抢机缘 — 天降机缘播报后，第一个回复者夺得\n"
            "另：正常群聊发言也会缓慢累积修为（有冷却与每日上限）"
        )

    def _cmd_cultivate(self, chat_id: str, sender_id: str,
                       sender_name: str, now: float) -> str:
        char = self._store.get_or_create(chat_id, sender_id, sender_name)
        E.rollover_daily(char, E.today_key(now))

        remain = E.cooldown_remaining(
            char.last_action_ts, self._config.game_action_cooldown_sec, now,
        )
        if remain > 0:
            return (
                f"@{sender_name} 你刚打坐过，经脉尚需调息——"
                f"{remain // 60} 分 {remain % 60} 秒后再来。"
            )

        if R.is_max_realm(char.realm_index):
            return f"@{sender_name} 你已是仙人，再打坐也无境界可进。不如去指点后辈。"

        gained = E.active_exp(
            char.realm_index, self._config.game_chat_exp * 4,
            char.technique, self._config.game_exp_multiplier,
        )
        need = R.exp_needed(char.realm_index)
        room = max(0, need - char.exp)
        gained = min(gained, room) if room > 0 else 0

        if gained <= 0:
            char.last_action_ts = now
            self._store.save_character(char)
            return (
                f"@{sender_name} 你的修为已经满了——发送「突破」冲击下一境界吧。"
            )

        char.exp += gained
        char.total_exp += gained
        char.last_action_ts = now
        char.daily_actions += 1
        self._store.save_character(char)

        return (
            f"@{sender_name} 打坐完毕，灵气入体，修为 +{gained}\n"
            f"{_format_realm_line(char)}"
        )

    def _cmd_breakthrough(self, chat_id: str, sender_id: str,
                          sender_name: str, now: float) -> str:
        return self._do_breakthrough(chat_id, sender_id, sender_name, now,
                                     require_tribulation=False)

    def _cmd_tribulation(self, chat_id: str, sender_id: str,
                         sender_name: str, now: float) -> str:
        return self._do_breakthrough(chat_id, sender_id, sender_name, now,
                                     require_tribulation=True)

    def _do_breakthrough(self, chat_id: str, sender_id: str, sender_name: str,
                         now: float, require_tribulation: bool) -> str:
        char = self._store.get_or_create(chat_id, sender_id, sender_name)
        E.rollover_daily(char, E.today_key(now))

        if R.is_max_realm(char.realm_index):
            return f"@{sender_name} 你已是仙人，此界再无可破之境。"

        is_trib = R.is_tribulation(char.realm_index)
        if require_tribulation and not is_trib:
            return (
                f"@{sender_name} 你当前是【{R.realm_label(char.realm_index)}】，"
                f"尚不到渡劫之时。先「突破」到渡劫后期再来。"
            )

        need = R.exp_needed(char.realm_index)
        if char.exp < need:
            return (
                f"@{sender_name} 修为未满，无法冲关。\n"
                f"{_format_realm_line(char)}\n"
                f"（还需 {need - char.exp} 点修为，可发「修炼」或继续群聊积累）"
            )

        result = E.resolve_breakthrough(
            char.realm_index, char.exp, char.technique,
            char.pending_bonus, char.fail_streak, self._rng,
        )

        char.bt_attempts += 1
        char.pending_bonus = 0.0

        if result.success:
            char.realm_index = result.realm_after
            char.exp = result.exp_after
            char.fail_streak = 0
            char.spirit_stones += result.spirit_stones_gained
            if result.learned_technique:
                char.technique = result.learned_technique
            self._store.save_character(char)

            kind = "tribulation_success" if result.is_tribulation else "breakthrough_success"
            body = self._flavor.generate(
                kind,
                FlavorGenerator.pool(kind),
                name=sender_name,
                from_realm=R.realm_label(result.from_index),
                to_realm=R.realm_label(result.realm_after),
            )
            lines = [
                f"@{sender_name} {body}",
                f"灵石 +{result.spirit_stones_gained}",
            ]
            if result.learned_technique:
                desc = R.TECHNIQUES[result.learned_technique]["desc"]
                lines.append(f"习得功法【{result.learned_technique}】· {desc}")
            lines.append(_format_realm_line(char))

            self._store.log_event(
                chat_id, kind, sender_id, sender_name,
                f"{sender_name} 突破至 {R.realm_label(result.realm_after)}",
                {"from": result.from_index, "to": result.realm_after,
                 "technique": result.learned_technique},
            )
            return "\n".join(lines)

        # ── 失败 ──────────────────────────────────────────────────
        char.bt_fails += 1
        lost = char.exp - result.exp_after
        char.realm_index = result.realm_after
        char.exp = result.exp_after
        char.fail_streak = result.fail_streak
        self._store.save_character(char)

        kind = "tribulation_fail" if result.is_tribulation else "breakthrough_fail"
        body = self._flavor.generate(
            kind,
            FlavorGenerator.pool(kind),
            name=sender_name,
            from_realm=R.realm_label(result.from_index),
            to_realm=R.realm_label(result.realm_after),
            lost=lost,
        )
        self._store.log_event(
            chat_id, kind, sender_id, sender_name,
            f"{sender_name} 突破失败，损失修为 {lost}",
            {"realm": result.realm_after, "lost": lost},
        )
        hint = (
            f"（成功率 {int(result.chance * 100)}%，连续失败会积累道心补偿："
            f"下次 +{min(result.fail_streak, R.MAX_FAIL_STREAK_BONUS) * 5}%。"
            f"服用「破境丹」也能提升成功率）"
        )
        return f"@{sender_name} {body}\n{hint}\n{_format_realm_line(char)}"

    def _cmd_profile(self, chat_id: str, sender_id: str, sender_name: str) -> str:
        char = self._store.get_or_create(chat_id, sender_id, sender_name)
        lines = [f"@{sender_name} 道号：{char.user_name}", _format_realm_line(char)]
        lines.append(f"灵石 {char.spirit_stones}")
        if char.technique:
            desc = R.TECHNIQUES.get(char.technique, {}).get("desc", "")
            lines.append(f"功法【{char.technique}】· {desc}")
        else:
            lines.append("功法：尚未习得（突破时有几率顿悟）")
        if char.pills:
            pills = "、".join(f"{n}×{c}" for n, c in char.pills.items() if c > 0)
            lines.append(f"丹药：{pills}" if pills else "丹药：无")
        else:
            lines.append("丹药：无（发「炼丹」可炼制）")
        if char.fail_streak > 0:
            lines.append(f"连续失败 {char.fail_streak} 次，道心补偿已生效")
        return "\n".join(lines)

    def _cmd_ranking(self, chat_id: str, sender_name: str) -> str:
        top = self._store.list_ranking(chat_id, 10)
        if not top:
            return f"@{sender_name} 本群尚无修士。发送「修炼」开启你的修仙之路。"

        medals = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"]
        lines = [f"@{sender_name} 【本群修为榜】"]
        for i, c in enumerate(top):
            mark = medals[i] if i < len(medals) else f"{i + 1}."
            lines.append(
                f"{mark} {c.user_name} · {R.realm_label(c.realm_index)} "
                f"· 累计 {c.total_exp}"
            )
        return "\n".join(lines)

    def _cmd_alchemy(self, chat_id: str, sender_id: str,
                     sender_name: str, now: float) -> str:
        char = self._store.get_or_create(chat_id, sender_id, sender_name)
        E.rollover_daily(char, E.today_key(now))

        if char.alchemy_used >= R.ALCHEMY_DAILY_LIMIT:
            return (
                f"@{sender_name} 今日炼丹次数已用尽"
                f"（每日上限 {R.ALCHEMY_DAILY_LIMIT} 次），明日再来。"
            )

        if char.spirit_stones < R.ALCHEMY_COST:
            return (
                f"@{sender_name} 炼丹需要 {R.ALCHEMY_COST} 灵石，"
                f"你只有 {char.spirit_stones}。多抢机缘、多突破吧。"
            )

        char.spirit_stones -= R.ALCHEMY_COST
        char.alchemy_used += 1
        pill = R.weighted_pick(R.PILLS, self._rng)
        char.pills[pill] = char.pills.get(pill, 0) + 1
        self._store.save_character(char)

        desc = R.PILLS[pill]["desc"]
        body = self._flavor.render_template(
            FlavorGenerator.pool("alchemy"),
            name=sender_name, pill=pill, desc=desc,
        )
        left = R.ALCHEMY_DAILY_LIMIT - char.alchemy_used
        return (
            f"@{sender_name} {body}\n"
            f"灵石 {char.spirit_stones}（-{R.ALCHEMY_COST}）· "
            f"今日还可炼丹 {left} 次"
        )

    def _cmd_take_pill(self, chat_id: str, sender_id: str,
                       sender_name: str, text: str) -> str:
        char = self._store.get_or_create(chat_id, sender_id, sender_name)
        arg = text
        for prefix in ("服用", "吃掉", "嗑药"):
            if arg.startswith(prefix):
                arg = arg[len(prefix):]
                break
        arg = arg.strip().lstrip("@").strip()

        if not arg:
            owned = "、".join(n for n, c in char.pills.items() if c > 0) or "无"
            return f"@{sender_name} 要服用什么丹药？你持有：{owned}"

        name = self._resolve_pill(char, arg)
        if not name:
            owned = "、".join(n for n, c in char.pills.items() if c > 0) or "无"
            return f"@{sender_name} 你身上没有「{arg}」。持有：{owned}"

        char.pills[name] -= 1
        if char.pills[name] <= 0:
            char.pills.pop(name, None)
        desc = E.apply_pill(char, name, exp_cap=R.exp_needed(char.realm_index))
        self._store.save_character(char)
        return f"@{sender_name} 服下「{name}」：{desc}\n{_format_realm_line(char)}"

    @staticmethod
    def _resolve_pill(char: GameCharacter, arg: str) -> str:
        """把用户输入的丹药名解析成实际持有的丹药名（支持省略「丹」字）。"""
        if char.pills.get(arg, 0) > 0:
            return arg
        for name, count in char.pills.items():
            if count > 0 and (name.startswith(arg) or arg in name):
                return name
        return ""

    def _cmd_duel(self, chat_id: str, sender_id: str,
                  sender_name: str, text: str) -> str:
        target_name = re.sub(r"^(论道|挑战|切磋)", "", text, count=1).strip()
        target_name = target_name.lstrip("@").strip()
        if not target_name:
            return f"@{sender_name} 想和谁论道？格式：「论道 群友昵称」"

        challenger = self._store.get_or_create(chat_id, sender_id, sender_name)
        target = self._find_member(chat_id, target_name)
        if target is None:
            return f"@{sender_name} 本群没有找到「{target_name}」这名修士。"
        if target.user_id == sender_id:
            return f"@{sender_name} 与自己论道，是悟不出东西的。"

        allowed, reason = E.duel_allowed(challenger.realm_index, target.realm_index)
        if not allowed:
            return f"@{sender_name} {reason}"

        now = time.time()
        E.rollover_daily(challenger, E.today_key(now))

        if challenger.duel_used >= R.DUEL_DAILY_LIMIT:
            return (
                f"@{sender_name} 今日论道次数已用尽"
                f"（每日上限 {R.DUEL_DAILY_LIMIT} 次），明日再来。"
            )
        if target.user_id in challenger.duel_targets:
            return f"@{sender_name} 今日已与「{target.user_name}」论道过，换个人吧。"

        # 论道胜率：境界差 + 一点随机
        gap = challenger.realm_index - target.realm_index
        win_chance = max(0.1, min(0.9, 0.5 + gap * 0.12))
        challenger_wins = self._rng.random() < win_chance

        winner, loser = (challenger, target) if challenger_wins else (target, challenger)
        stake = E.duel_stake(loser.exp)
        loser.exp -= stake
        loser.total_exp -= min(stake, loser.total_exp)
        winner.exp += stake
        winner.total_exp += stake

        # 胜者修为不应超过其当前境界上限
        need = R.exp_needed(winner.realm_index)
        if need > 0 and winner.exp > need:
            winner.exp = need

        challenger.duel_used += 1
        challenger.duel_targets.append(target.user_id)

        self._store.save_character(challenger)
        self._store.save_character(target)

        body = self._flavor.render_template(
            FlavorGenerator.pool("duel_win" if challenger_wins else "duel_lose"),
            winner=winner.user_name, loser=loser.user_name, stake=stake,
        )
        self._store.log_event(
            chat_id, "duel", sender_id, sender_name,
            f"{winner.user_name} 论道胜 {loser.user_name}，夺取修为 {stake}",
            {"winner": winner.user_id, "loser": loser.user_id, "stake": stake},
        )
        return (
            f"@{sender_name} {body}\n"
            f"你的修为：{challenger.exp}/{R.exp_needed(challenger.realm_index) or '∞'}"
        )

    def _find_member(self, chat_id: str, name: str) -> Optional[GameCharacter]:
        """按昵称在本群角色里查找（精确优先，其次包含匹配）。"""
        members = self._store.list_ranking(chat_id, limit=1000)
        for c in members:
            if c.user_name == name:
                return c
        for c in members:
            if name in c.user_name:
                return c
        return None
