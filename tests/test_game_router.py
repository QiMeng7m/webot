"""Router 集成测试 —— 修仙玩法接线 + 新人功能发现。

覆盖 src/router.py 里的接线：
  * 每条消息的被动修为累积
  * @mention 命令链里的玩法钩子
  * passive_reply 合并（机缘播报在 @ 与非-@ 两条路径都能发出）
  * 空 @机器人 → 功能导览；「帮助」类问法 → 帮助清单

（文件名里的 game 是历史遗留：这里的 fake 是通用的 Router 协作者替身。）
"""

import os
import tempfile
import time
import unittest

from src.config import BotConfig
from src.game import realms as R
from src.game.store import GameStore
from src.router import MessageRouter


def _make_config(db_path: str, **overrides) -> BotConfig:
    """从真实的 BotConfig 默认值出发再改几个字段。

    手写一个假 config 很容易漏字段（Router 还会读 proactive_* / sticky_* 等），
    直接实例化 dataclass 可以自动跟随 BotConfig 的演进。
    """
    cfg = BotConfig()
    cfg.db_path = db_path
    # 关掉会干扰断言的可选功能
    cfg.fun_enabled = False
    cfg.todo_enabled = False
    cfg.welcome_enabled = False
    cfg.proactive_enabled = False
    cfg.sticky_mention_enabled = False
    cfg.admin_wxid = ""
    cfg.bot_display_name = "小助手"
    # 玩法
    cfg.game_enabled = True
    cfg.game_groups = ["*"]
    cfg.game_ai_flavor_enabled = False
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class FakeMessageStore:
    """记录插入的消息，并为玩法提供「群是否活跃」的答案。"""

    def __init__(self, active=True):
        self.messages = []
        self.active = active

    def insert_message(self, msg):
        self.messages.append(msg)
        return True

    def get_messages_since(self, *args, **kwargs):
        """签名放宽：MessageStore 的这个方法有默认参数，Router 与玩法
        用了不同的调用形式，假实现只需关心「活跃与否」。

        返回真正的消息 dict——Router 的 _handle_chat 会逐条读取 sender_id。
        """
        if not self.active:
            return []
        return [
            {"sender_id": f"u{i}", "sender_name": f"群友{i}", "content": f"消息{i}"}
            for i in range(R.ENCOUNTER_ACTIVE_MIN_MESSAGES)
        ]

    def get_group_memory(self, chat_id):
        return None

    def get_new_message_count(self, chat_id, since_message_id):
        """MemoryConsolidator 每次都会调；返回 0 表示无需合并。"""
        return 0


class FakeDetector:
    def is_trigger(self, content, is_at_mentioned, sender_name):
        return False


class FakeSummarizer:
    """记录是否被调用。

    不能用「抛异常」当信号：_handle_chat 把 AI 调用包在 try/except 里，
    AssertionError 会被吞掉。改成记录调用次数再断言。
    """

    def __init__(self):
        self.chat_calls = 0
        self.summarize_calls = 0

    def chat(self, *args, **kwargs):
        self.chat_calls += 1
        return "AI 回复"

    def summarize(self, *args, **kwargs):
        self.summarize_calls += 1
        return None


class FakeAdmin:
    def handle(self, content, requester_name):
        return None


class FakeNicknames:
    def resolve_wxids(self, text):
        return text

    def resolve_name(self, wxid):
        return wxid


def _make_msg(content, chat_id="room@chatroom", sender_id="u1",
              sender_name="小明", is_at=False, ts=None):
    return {
        "message_id": f"m-{content}-{time.time()}",
        "chat_id": chat_id,
        "group_name": "测试群",
        "sender_id": sender_id,
        "sender_name": sender_name,
        "content": content,
        "msg_type": 1,
        "timestamp": int(ts if ts is not None else time.time()),
        "is_at_mentioned": is_at,
        "is_system_join": False,
    }


class TestRouterGameIntegration(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.store = FakeMessageStore()
        self.summarizer = FakeSummarizer()
        self.config = _make_config(self.db_path)
        self.router = self._make_router(self.config)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_router(self, config):
        return MessageRouter(
            store=self.store,
            detector=FakeDetector(),
            summarizer=self.summarizer,
            admin_handler=FakeAdmin(),
            nickname_service=FakeNicknames(),
            config=config,
        )

    def _game_store(self):
        return self.router._game_handler._store

    # ── 初始化 ───────────────────────────────────────────────────

    def test_handler_built_when_enabled(self):
        self.assertIsNotNone(self.router._game_handler)

    def test_handler_absent_when_disabled(self):
        router = self._make_router(
            _make_config(self.db_path, game_enabled=False)
        )
        self.assertIsNone(router._game_handler)

    def test_disabled_game_ignores_commands(self):
        router = self._make_router(
            _make_config(self.db_path, game_enabled=False)
        )
        # 玩法关闭时「修炼」不被拦截，应照常落到 AI 聊天
        router.handle(_make_msg("修炼", is_at=True))
        self.assertEqual(self.summarizer.chat_calls, 1)

    # ── 被动积累 ─────────────────────────────────────────────────

    def test_passive_accrual_on_plain_message(self):
        reply = self.router.handle(_make_msg("今天天气真不错啊"))
        self.assertIsNone(reply, "被动积累必须静默，不该回复")
        char = self._game_store().get_character("room@chatroom", "u1")
        self.assertIsNotNone(char)
        self.assertGreater(char.exp, 0)

    def test_passive_accrual_skipped_when_game_disabled(self):
        router = self._make_router(
            _make_config(self.db_path, game_enabled=False)
        )
        router.handle(_make_msg("今天天气真不错啊"))
        self.assertIsNone(
            GameStore(self.db_path).get_character("room@chatroom", "u1")
        )

    def test_short_message_does_not_create_character(self):
        self.router.handle(_make_msg("嗯"))
        self.assertIsNone(
            self._game_store().get_character("room@chatroom", "u1")
        )

    # ── 命令拦截 ─────────────────────────────────────────────────

    def test_at_command_returns_game_reply(self):
        reply = self.router.handle(_make_msg("修炼", is_at=True))
        self.assertIsNotNone(reply)
        self.assertIn("修为 +", reply)

    def test_at_command_creates_and_persists_character(self):
        self.router.handle(_make_msg("我的", is_at=True))
        char = self._game_store().get_character("room@chatroom", "u1")
        self.assertIsNotNone(char)
        self.assertEqual(char.user_name, "小明")

    def test_game_enabled_but_group_not_allowed(self):
        router = self._make_router(
            _make_config(self.db_path, game_groups=["某个别的群"])
        )
        # 群不在白名单 → 玩法不介入，应落到 AI 聊天
        router.handle(_make_msg("修炼", is_at=True))
        self.assertEqual(self.summarizer.chat_calls, 1)
        self.assertIsNone(
            self._game_store().get_character("room@chatroom", "u1")
        )

    def test_allowed_group_intercepts_before_ai(self):
        self.router.handle(_make_msg("修炼", is_at=True))
        self.assertEqual(self.summarizer.chat_calls, 0,
                         "玩法命令不应继续落到 AI 聊天")

    # ── 机缘播报 ─────────────────────────────────────────────────

    def _arm_encounter(self):
        gs = self._game_store()
        state = gs.get_group_state("room@chatroom")
        state.last_encounter_at = time.time() - 46 * 60
        gs.save_group_state(state)

    def test_encounter_announcement_delivered_on_plain_message(self):
        self.router.handle(_make_msg("今天天气真不错啊"))  # 起算计时
        self._arm_encounter()
        reply = self.router.handle(_make_msg("换一句完全不同的话"))
        self.assertIsNotNone(reply, "机缘刷新必须通过 passive_reply 发出")
        self.assertIn("抢机缘", reply)

    def test_claim_via_plain_message_returns_reward(self):
        self.router.handle(_make_msg("今天天气真不错啊"))
        self._arm_encounter()
        self.router.handle(_make_msg("换一句完全不同的话"))
        reply = self.router.handle(
            _make_msg("抢机缘", sender_id="u2", sender_name="小红")
        )
        self.assertIsNotNone(reply)
        self.assertIn("灵石 +", reply)

    def test_encounter_text_is_markdown_stripped(self):
        self.router.handle(_make_msg("今天天气真不错啊"))
        self._arm_encounter()
        reply = self.router.handle(_make_msg("换一句完全不同的话"))
        self.assertNotIn("**", reply)

    def test_bot_own_messages_ignored(self):
        reply = self.router.handle(_make_msg("今天天气真不错啊",
                                             sender_name="小助手"))
        self.assertIsNone(reply)
        self.assertIsNone(
            self._game_store().get_character("room@chatroom", "u1")
        )

    def test_stale_at_mention_ignored(self):
        old = time.time() - 3600
        reply = self.router.handle(_make_msg("修炼", is_at=True, ts=old))
        self.assertIsNone(reply)

    def test_non_game_command_reaches_ai(self):
        """不是玩法命令时，玩法必须返回 None 让后面的处理器接手。"""
        self.router.handle(_make_msg("今天午饭吃什么好呢", is_at=True))
        self.assertEqual(self.summarizer.chat_calls, 1)


class TestNewcomerDiscovery(unittest.TestCase):
    """新人 @ 一下机器人时，能不能知道有什么功能。

    回归背景：此前空 @机器人 走「粘性监听」分支，reply 保持 None，机器人
    一个字都不回；而「?」「你能做什么」这类问法全部落到 AI 闲聊，拿到的是
    模型即兴发挥而不是权威清单。
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmpdir.name, "test.db")
        self.store = FakeMessageStore()
        self.summarizer = FakeSummarizer()
        self.config = _make_config(self.db_path)
        self.router = self._make_router(self.config)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_router(self, config):
        return MessageRouter(
            store=self.store, detector=FakeDetector(),
            summarizer=self.summarizer, admin_handler=FakeAdmin(),
            nickname_service=FakeNicknames(), config=config,
        )

    # ── 空 @机器人 ───────────────────────────────────────────────

    def test_bare_at_mention_is_not_silent(self):
        reply = self.router.handle(_make_msg("", is_at=True))
        self.assertIsNotNone(reply, "空 @机器人 不该一片沉默")
        self.assertIn("帮助", reply)

    def test_bare_at_mention_does_not_hit_ai(self):
        self.router.handle(_make_msg("", is_at=True))
        self.assertEqual(self.summarizer.chat_calls, 0)

    def test_bare_at_mention_suggests_enabled_features(self):
        self.config.todo_enabled = True
        self.config.fun_enabled = True
        reply = self.router.handle(_make_msg("", is_at=True))
        self.assertIn("修炼", reply)      # game_enabled 由 _make_config 打开
        self.assertIn("记一下", reply)
        self.assertIn("抽签", reply)

    def test_bare_at_mention_still_registers_sticky(self):
        """导览不能挤掉粘性监听 —— 用户接着说话仍应被接住。"""
        self.config.sticky_mention_enabled = True
        router = self._make_router(self.config)
        router.handle(_make_msg("", is_at=True))
        self.assertIsNotNone(router._sticky)
        # 下一条不带 @ 的消息应被粘性提到，从而拿到玩法回复
        reply = router.handle(_make_msg("修炼"))
        self.assertIsNotNone(reply)
        self.assertIn("修为", reply)

    # ── 帮助类问法 ───────────────────────────────────────────────

    def test_help_words_route_to_help(self):
        for text in ("帮助", "help", "命令", "?", "？", "菜单",
                     "你能做什么", "有什么功能", "怎么用", "使用说明"):
            reply = self.router.handle(_make_msg(text, is_at=True))
            self.assertIsNotNone(reply, f"{text!r} 应得到帮助回复")
            self.assertIn("能做什么", reply, f"{text!r} 没有走到帮助")

    def test_colloquial_help_phrasing(self):
        reply = self.router.handle(_make_msg("你有什么功能吗", is_at=True))
        self.assertIsNotNone(reply)
        self.assertIn("能做什么", reply)

    def test_help_does_not_hit_ai(self):
        self.router.handle(_make_msg("你能做什么", is_at=True))
        self.assertEqual(self.summarizer.chat_calls, 0)

    def test_help_lists_game_when_enabled(self):
        self.config.game_enabled = True
        reply = self.router.handle(_make_msg("帮助", is_at=True))
        self.assertIn("修仙玩法", reply)
        self.assertIn("修仙帮助", reply)

    def test_help_omits_disabled_features(self):
        self.config.game_enabled = False
        self.config.fun_enabled = False
        self.config.todo_enabled = False
        reply = self.router.handle(_make_msg("帮助", is_at=True))
        self.assertNotIn("修仙玩法", reply)
        self.assertNotIn("抽签", reply)

    def test_normal_chat_still_reaches_ai(self):
        """不能因为放宽了帮助匹配，就把普通闲聊也吃掉。"""
        self.router.handle(_make_msg("今天午饭吃什么好呢", is_at=True))
        self.assertEqual(self.summarizer.chat_calls, 1)


if __name__ == "__main__":
    unittest.main()
