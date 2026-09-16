"""Tests for src.help — 从配置动态生成的功能帮助与导览。"""

import unittest

from src.config import BotConfig
from src.help import build_guide_text, build_help_text, is_help_request


def _cfg(**overrides) -> BotConfig:
    """从真实 BotConfig 默认值出发，避免手写假 config 漏字段。"""
    cfg = BotConfig()
    cfg.bot_display_name = "小柒"
    cfg.admin_wxid = ""
    cfg.trigger_keywords = ["总结一下", "说了啥", "之前发了什么"]
    cfg.summarize_enabled = True
    cfg.fun_enabled = True
    cfg.todo_enabled = False
    cfg.game_enabled = False
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class TestIsHelpRequest(unittest.TestCase):
    """哪些说法算「在问能做什么」。"""

    def test_bare_help_words(self):
        for text in ("帮助", "help", "命令", "?", "？", "菜单",
                     "你能做什么", "有什么功能", "怎么用", "使用说明"):
            self.assertTrue(is_help_request(text), f"{text!r} 应触发帮助")

    def test_case_insensitive_and_padded(self):
        self.assertTrue(is_help_request("  HELP  "))
        self.assertTrue(is_help_request("Help"))

    def test_colloquial_phrasings(self):
        # 带语气词/口语化的说法也要命中
        for text in ("你有什么功能吗", "你能做什么呀", "这个怎么用啊",
                     "你会什么", "你会干嘛", "你能干嘛", "这机器人干啥的",
                     "有啥功能"):
            self.assertTrue(is_help_request(text), f"{text!r} 应触发帮助")

    def test_normal_chat_not_help(self):
        for text in ("今天天气不错", "明天开会吗", "帮我看看这个", "", "   ", None):
            self.assertFalse(is_help_request(text), f"{text!r} 不该触发帮助")

    def test_partial_word_not_help(self):
        # 「命令」不能因为出现在长句里就触发
        self.assertFalse(is_help_request("这条命令我执行过了"))


class TestBuildHelpText(unittest.TestCase):
    """帮助正文必须跟着配置走。"""

    def test_lists_only_enabled_features(self):
        cfg = _cfg(todo_enabled=True, game_enabled=True)
        text = build_help_text(cfg, "小明")
        self.assertIn("总结群聊", text)
        self.assertIn("抽签", text)
        self.assertIn("群待办", text)
        self.assertIn("修仙玩法", text)

    def test_disabled_features_absent(self):
        cfg = _cfg(todo_enabled=False, game_enabled=False, fun_enabled=False)
        text = build_help_text(cfg, "小明")
        self.assertNotIn("群待办", text)
        self.assertNotIn("修仙玩法", text)
        self.assertNotIn("抽签", text)

    def test_advertises_real_trigger_keywords(self):
        """回归：帮助里宣传的触发词必须真的能触发。"""
        cfg = _cfg(trigger_keywords=["总结一下", "说了啥"])
        text = build_help_text(cfg, "小明")
        self.assertIn("总结一下", text)
        self.assertIn("说了啥", text)

    def test_no_stale_hardcoded_trigger(self):
        """回归：曾经帮助里写死的「说了什么」并不在触发词里。"""
        cfg = _cfg(trigger_keywords=["总结一下", "说了啥"])
        text = build_help_text(cfg, "小明")
        self.assertNotIn("说了什么", text)

    def test_mentions_game_sub_help_when_enabled(self):
        cfg = _cfg(game_enabled=True)
        self.assertIn("修仙帮助", build_help_text(cfg, "小明"))

    def test_admin_section_only_with_admin_wxid(self):
        self.assertNotIn("管理员", build_help_text(_cfg(), "小明"))
        self.assertIn("管理员", build_help_text(_cfg(admin_wxid="wxid_boss"), "小明"))

    def test_includes_requester_name_and_bot_name(self):
        text = build_help_text(_cfg(), "小明")
        self.assertIn("@小明", text)
        self.assertIn("小柒", text)

    def test_survives_empty_trigger_keywords(self):
        text = build_help_text(_cfg(trigger_keywords=[]), "小明")
        self.assertIn("总结群聊", text)

    def test_no_markdown(self):
        """微信渲染不了 markdown，router 会剥掉 —— 这里就不该产出。"""
        text = build_help_text(_cfg(todo_enabled=True, game_enabled=True), "小明")
        for token in ("**", "`", "~~", "__"):
            self.assertNotIn(token, text)


class TestBuildGuideText(unittest.TestCase):
    """空 @机器人 时的短导览。"""

    def test_not_empty(self):
        self.assertTrue(build_guide_text(_cfg(), "小明").strip())

    def test_short_enough_for_wechat(self):
        text = build_guide_text(
            _cfg(todo_enabled=True, game_enabled=True), "小明"
        )
        self.assertLessEqual(len(text), 120, f"导览过长:\n{text}")

    def test_points_to_help(self):
        self.assertIn("帮助", build_guide_text(_cfg(), "小明"))

    def test_suggests_enabled_features(self):
        cfg = _cfg(todo_enabled=True, game_enabled=True)
        text = build_guide_text(cfg, "小明")
        self.assertIn("修炼", text)
        self.assertIn("记一下", text)

    def test_no_suggestion_for_disabled_features(self):
        cfg = _cfg(todo_enabled=False, game_enabled=False, fun_enabled=False,
                   summarize_enabled=False)
        text = build_guide_text(cfg, "小明")
        self.assertNotIn("修炼", text)
        self.assertNotIn("抽签", text)
        # 但是仍然要有兜底话术，不能只剩一句空话
        self.assertIn("帮助", text)

    def test_includes_requester_name(self):
        self.assertIn("@小明", build_guide_text(_cfg(), "小明"))


if __name__ == "__main__":
    unittest.main()
