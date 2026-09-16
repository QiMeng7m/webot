"""修仙玩法 — 关键节点文案生成。

普通动作走模板池（零成本、零延迟）；只有突破、渡劫、传说机缘这类「大事」
才调用 AI 生成个性化剧情。AI 超时或报错一律降级到模板，**绝不阻塞群聊**。
"""

import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from . import realms as R

logger = logging.getLogger(__name__)

#: AI 文案的等待上限（秒）。超过就降级模板，不让群友干等。
FLAVOR_TIMEOUT_SEC = 3.0

#: AI 返回文本的长度上限，防止模型写出长篇大论刷屏。
MAX_FLAVOR_CHARS = 120

SYSTEM_PROMPT = (
    "你是一款中文修仙题材群聊小游戏的文案作者。玩家在群聊中修炼、突破境界。"
    "请为刚刚发生的事件写一句**简短的旁白**，用于播报给整个群。\n"
    "要求：\n"
    "1. 只输出旁白正文，不要任何解释、引号、markdown 或前缀；\n"
    "2. 一句话到两句话，总长不超过 60 个字；\n"
    "3. 使用第三人称称呼玩家，直接使用给定的人物名；\n"
    "4. 语气要有修仙小说的画面感，可以略微中二，但不要低俗或攻击性；\n"
    "5. 不要编造未在事实中给出的数值。"
)

#: 每种事件对应的「事实描述」，作为 AI 的输入。
_EVENT_FACTS = {
    "breakthrough_success": "玩家「{name}」刚刚突破成功，从 {from_realm} 晋升到 {to_realm}。",
    "breakthrough_fail": "玩家「{name}」尝试突破到 {to_realm} 失败，修为倒退了 {lost} 点。",
    "tribulation_success": "玩家「{name}」扛过九重天劫，成功飞升为「仙人」，这是本群第一位/又一位仙人。",
    "tribulation_fail": "玩家「{name}」渡劫失败，从 {from_realm} 跌落回 {to_realm}，修为损失 {lost} 点。",
    "encounter_claim": "玩家「{name}」抢到了天降机缘，获得 {stones} 块灵石。",
}


def _clean(text: str) -> str:
    """清洗 AI 输出：去掉引号、markdown、换行与超长内容。"""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    t = t.strip("\"'“”‘’「」『』")
    # 只保留第一段，避免模型输出多段刷屏
    t = t.split("\n")[0].strip()
    if len(t) > MAX_FLAVOR_CHARS:
        t = t[:MAX_FLAVOR_CHARS].rstrip() + "…"
    return t


class FlavorGenerator:
    """关键节点文案生成器：优先 AI，失败降级模板。"""

    def __init__(self, summarizer=None, enabled: bool = True, rng=None):
        """
        Args:
            summarizer: ``AbstractSummarizer`` 实例；为 None 时全部走模板。
            enabled: 配置项 ``game_ai_flavor_enabled``，False 时跳过 AI。
            rng: ``random.Random`` 实例（注入以便测试可复现）。
        """
        self._summarizer = summarizer
        self._enabled = bool(enabled) and summarizer is not None
        self._rng = rng or random
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="game-flavor")

    def generate(self, kind: str, fallback_pool: list[str],
                 **fields) -> str:
        """生成一条事件旁白。

        Args:
            kind: 事件类型，见 :data:`_EVENT_FACTS`。
            fallback_pool: 模板池，``str.format(**fields)`` 渲染。
            **fields: 渲染模板与事实描述用的字段（name / realm / …）。

        Returns:
            旁白文本；AI 不可用、超时或返回空时回落到模板。
        """
        if self._enabled:
            text = self._try_ai(kind, **fields)
            if text:
                return text
        return self.render_template(fallback_pool, **fields)

    def _try_ai(self, kind: str, **fields) -> str:
        """尝试调用 AI；任何异常/超时都返回空字符串。"""
        fact_tpl = _EVENT_FACTS.get(kind)
        if not fact_tpl:
            return ""
        try:
            fact = fact_tpl.format(**fields)
        except (KeyError, IndexError):
            logger.debug("Flavor fact template missing fields for kind=%s", kind)
            return ""

        future = self._pool.submit(
            self._summarizer._call_chat_api,
            SYSTEM_PROMPT,
            [{"role": "user", "content": fact}],
        )
        try:
            raw = future.result(timeout=FLAVOR_TIMEOUT_SEC)
        except FutureTimeout:
            future.cancel()
            logger.info("AI flavor timed out (kind=%s) — falling back to template", kind)
            return ""
        except Exception:
            logger.exception("AI flavor failed (kind=%s) — falling back to template", kind)
            return ""

        return _clean(raw)

    def render_template(self, pool: list[str], **fields) -> str:
        """从模板池随机取一条并渲染。

        模板里若引用了缺失的字段，退化为返回未渲染的原文，而不是抛异常
        —— 群聊回复路径上绝不能因为文案出错而中断。
        """
        if not pool:
            return ""
        chosen = self._rng.choice(pool)
        try:
            return chosen.format(**fields)
        except (KeyError, IndexError):
            logger.warning("Template render failed, returning raw text: %r", chosen)
            return chosen

    @staticmethod
    def pool(kind: str) -> list[str]:
        """按事件类型取模板池。"""
        return R.TEMPLATES.get(kind, [])
