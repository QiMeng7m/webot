"""修仙玩法 — 群聊小游戏。

模块分层（参照 src/todo/ 的 store + handler 结构）：

    realms.py   纯数据表：境界 / 丹药 / 功法 / 文案模板池
    engine.py   纯逻辑：收益计算、突破成功率、防刷过滤（不碰 DB / IO / 网络）
    store.py    SQLite 持久化：角色 / 事件 / 群状态
    flavor.py   关键节点 AI 文案生成（失败降级模板池）
    handler.py  命令解析与分发
"""

from .store import GameStore
from .handler import GameHandler

__all__ = ["GameStore", "GameHandler"]
