"""群内功能帮助 —— 从配置动态生成。

帮助文案必须跟着配置走，不能硬编码。历史上这里吃过亏：帮助里宣传的触发词是
「说了什么」，而 `TRIGGER_KEYWORDS` 里实际配的是「说了啥」——照着帮助用的群友
拿到的是 AI 闲聊，不是总结。同时待办、修仙玩法这些功能开了也从不出现。

所以本模块只读 `BotConfig` 生成清单：功能开关一变，帮助跟着变。
"""

#: 触发「帮助」的整句关键词（比对前会 strip + lower）。
HELP_TRIGGERS = frozenset({
    "帮助", "help", "命令", "?", "？", "菜单",
    "你能做什么", "有什么功能", "会什么", "能干什么", "怎么用", "使用说明",
})

#: 需要按「包含」匹配的问法 —— 「有什么功能吗」「你能做什么呀」这类带语气词的说法。
_HELP_CONTAINS = (
    "有什么功能", "有啥功能", "有什么作用",
    "能做什么", "会什么", "会做什么", "能干什么", "会干什么",
    "能干嘛", "会干嘛", "干啥的",
    "怎么用", "使用说明", "使用帮助",
)


def is_help_request(text: str) -> bool:
    """判断一条 @bot 消息是否在问「你能做什么」。

    Args:
        text: 去掉 @bot 前缀后的消息内容。

    Returns:
        是求助/探路消息返回 True。
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    if t in HELP_TRIGGERS:
        return True
    return any(kw in t for kw in _HELP_CONTAINS)


def build_help_text(config, requester_name: str) -> str:
    """生成完整的功能清单（群友发送「帮助」时回复）。

    Args:
        config: ``BotConfig`` 实例 —— 只列出**已开启**的功能。
        requester_name: 请求者显示名，用于 @ 前缀。

    Returns:
        WeChat 可直接发送的纯文本（不含 markdown）。
    """
    lines = [f"@{requester_name} 【{config.bot_display_name} 能做什么】", ""]
    lines.append("· 聊天 —— 直接 @我 说你想说的")

    if getattr(config, "summarize_enabled", True):
        keywords = [k for k in (config.trigger_keywords or []) if k][:3]
        sample = "」「".join(keywords) if keywords else "总结一下"
        lines.append(f"· 总结群聊 —— 发送「{sample}」")

    if getattr(config, "fun_enabled", False):
        lines.append("· 抽签 —— 发送「抽签」")

    if getattr(config, "todo_enabled", False):
        lines.append("· 群待办 —— 发送「记一下 内容」；回复「完成 1」「删除 1」")

    if getattr(config, "game_enabled", False):
        lines.append(
            "· 修仙玩法 —— 发送「修炼」「我的」「排行榜」「突破」；"
            "发送「修仙帮助」看全部玩法"
        )

    if getattr(config, "admin_wxid", ""):
        lines.append("")
        lines.append(
            "管理命令（仅管理员）：改名 wxid = 昵称 / 删除昵称 wxid / 刷新昵称"
        )

    return "\n".join(lines)


def build_guide_text(config, requester_name: str) -> str:
    """生成简短导览（群友只 @ 了机器人、没打字时回复）。

    新人进群最可能的第一个动作就是 @ 一下看看。此前这条路径是完全静默的
    ——机器人一个字都不回，等于把「怎么用」彻底藏了起来。

    Args:
        config: ``BotConfig`` 实例。
        requester_name: 请求者显示名。

    Returns:
        一两行的短回复。
    """
    hints: list[str] = []
    if getattr(config, "summarize_enabled", True):
        keywords = [k for k in (config.trigger_keywords or []) if k]
        if keywords:
            hints.append(f"「{keywords[0]}」总结群聊")
    if getattr(config, "game_enabled", False):
        hints.append("「修炼」玩修仙玩法")
    if getattr(config, "todo_enabled", False):
        hints.append("「记一下 xxx」记待办")
    if getattr(config, "fun_enabled", False):
        hints.append("「抽签」抽一签")

    lines = [f"@{requester_name} 我在～发送「帮助」可以看我会做什么。"]
    if hints:
        lines.append("也可以直接试试：" + " · ".join(hints))
    else:
        lines.append("直接把想说的告诉我，或者 @我 提问。")
    return "\n".join(lines)
