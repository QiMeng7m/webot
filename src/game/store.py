"""修仙玩法 — SQLite 持久化。

表结构由本模块自建（``__init__`` 里 ``CREATE TABLE IF NOT EXISTS``），与
``src/todo/store.py`` 保持一致：不共用长连接，每次操作开一个短连接。
"""

import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


CREATE_TABLE = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;

-- 每个群每人一行角色卡。realm_index: 0..26 = 9大境界×初/中/后期, 27 = 仙人
CREATE TABLE IF NOT EXISTS game_characters (
    chat_id        TEXT    NOT NULL,
    user_id        TEXT    NOT NULL,
    user_name      TEXT    NOT NULL DEFAULT '',
    realm_index    INTEGER NOT NULL DEFAULT 0,
    exp            INTEGER NOT NULL DEFAULT 0,
    total_exp      INTEGER NOT NULL DEFAULT 0,
    spirit_stones  INTEGER NOT NULL DEFAULT 0,
    technique      TEXT    NOT NULL DEFAULT '',
    pills          TEXT    NOT NULL DEFAULT '{}',
    pending_bonus  REAL    NOT NULL DEFAULT 0,
    fail_streak    INTEGER NOT NULL DEFAULT 0,
    last_chat_ts   REAL    NOT NULL DEFAULT 0,
    last_action_ts REAL    NOT NULL DEFAULT 0,
    daily_key      TEXT    NOT NULL DEFAULT '',
    daily_exp      INTEGER NOT NULL DEFAULT 0,
    daily_actions  INTEGER NOT NULL DEFAULT 0,
    alchemy_used   INTEGER NOT NULL DEFAULT 0,
    duel_used      INTEGER NOT NULL DEFAULT 0,
    duel_targets   TEXT    NOT NULL DEFAULT '[]',
    bt_attempts    INTEGER NOT NULL DEFAULT 0,
    bt_fails       INTEGER NOT NULL DEFAULT 0,
    created_at     REAL    NOT NULL DEFAULT (unixepoch()),
    updated_at     REAL    NOT NULL DEFAULT (unixepoch()),
    PRIMARY KEY (chat_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_game_char_rank
    ON game_characters(chat_id, realm_index DESC, total_exp DESC);

-- 修仙大事记：突破 / 渡劫 / 机缘 / 习得功法
CREATE TABLE IF NOT EXISTS game_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    actor_id   TEXT NOT NULL DEFAULT '',
    actor_name TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS idx_game_events_chat
    ON game_events(chat_id, created_at DESC);

-- 每群状态：机缘刷新节流 + 当前活跃机缘（全群共享一条）
CREATE TABLE IF NOT EXISTS game_group_state (
    chat_id             TEXT PRIMARY KEY,
    last_encounter_at   REAL NOT NULL DEFAULT 0,
    active_expires_at   REAL NOT NULL DEFAULT 0,
    active_reward_kind  TEXT NOT NULL DEFAULT '',
    active_reward_amount INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class GameCharacter:
    """一名修士的角色卡。"""

    chat_id: str
    user_id: str
    user_name: str = ""
    realm_index: int = 0
    exp: int = 0
    total_exp: int = 0
    spirit_stones: int = 0
    technique: str = ""
    pills: dict[str, int] = field(default_factory=dict)
    pending_bonus: float = 0.0
    fail_streak: int = 0
    last_chat_ts: float = 0.0
    last_action_ts: float = 0.0
    daily_key: str = ""
    daily_exp: int = 0
    daily_actions: int = 0
    alchemy_used: int = 0
    duel_used: int = 0
    duel_targets: list[str] = field(default_factory=list)
    bt_attempts: int = 0
    bt_fails: int = 0


@dataclass
class GameEvent:
    """一条修仙大事记。"""

    id: int
    chat_id: str
    kind: str
    actor_id: str
    actor_name: str
    summary: str
    detail: dict
    created_at: float


@dataclass
class GroupState:
    """一个群的机缘状态。"""

    chat_id: str
    last_encounter_at: float = 0.0
    active_expires_at: float = 0.0
    active_reward_kind: str = ""
    active_reward_amount: int = 0


def _row_to_character(row: sqlite3.Row) -> GameCharacter:
    try:
        pills = json.loads(row["pills"] or "{}")
        if not isinstance(pills, dict):
            pills = {}
    except (json.JSONDecodeError, TypeError):
        pills = {}
    try:
        duel_targets = json.loads(row["duel_targets"] or "[]")
        if not isinstance(duel_targets, list):
            duel_targets = []
    except (json.JSONDecodeError, TypeError):
        duel_targets = []
    return GameCharacter(
        chat_id=row["chat_id"],
        user_id=row["user_id"],
        user_name=row["user_name"] or "",
        realm_index=row["realm_index"],
        exp=row["exp"],
        total_exp=row["total_exp"],
        spirit_stones=row["spirit_stones"],
        technique=row["technique"] or "",
        pills=pills,
        pending_bonus=row["pending_bonus"],
        fail_streak=row["fail_streak"],
        last_chat_ts=row["last_chat_ts"],
        last_action_ts=row["last_action_ts"],
        daily_key=row["daily_key"] or "",
        daily_exp=row["daily_exp"],
        daily_actions=row["daily_actions"],
        alchemy_used=row["alchemy_used"],
        duel_used=row["duel_used"],
        duel_targets=duel_targets,
        bt_attempts=row["bt_attempts"],
        bt_fails=row["bt_fails"],
    )


class GameStore:
    """修仙玩法的 SQLite 存储层。"""

    def __init__(self, db_path: str):
        """
        Args:
            db_path: SQLite 数据库路径（与 messages.db 共用同一个文件）。
        """
        self._db_path = db_path
        with self._connect() as conn:
            conn.executescript(CREATE_TABLE)
            conn.commit()

    def _connect(self):
        """Open a short-lived connection that is closed on context exit.

        注意：``with sqlite3.connect(...) as conn`` 只在退出时 **commit**，
        并不会关闭连接——句柄要等 GC 才释放，在 Windows 上会让 ``-wal`` /
        ``-shm`` 文件一直被占用。所以这里用 ``closing()`` 包一层。

        因此所有写操作都必须自己显式 ``conn.commit()``（本模块均已如此）。
        """
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return closing(conn)

    # ── 角色 ─────────────────────────────────────────────────────────

    def get_character(self, chat_id: str, user_id: str) -> Optional[GameCharacter]:
        """读取角色卡，不存在时返回 None。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM game_characters WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
        return _row_to_character(row) if row else None

    def get_or_create(self, chat_id: str, user_id: str,
                      user_name: str = "") -> GameCharacter:
        """读取角色卡，不存在则创建（并顺带刷新显示名）。"""
        char = self.get_character(chat_id, user_id)
        if char is None:
            char = GameCharacter(chat_id=chat_id, user_id=user_id,
                                 user_name=user_name or user_id)
            self.save_character(char)
        elif user_name and char.user_name != user_name:
            char.user_name = user_name
            self.save_character(char)
        return char

    def save_character(self, char: GameCharacter) -> None:
        """整体写回角色卡（UPSERT）。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO game_characters (
                    chat_id, user_id, user_name, realm_index, exp, total_exp,
                    spirit_stones, technique, pills, pending_bonus, fail_streak,
                    last_chat_ts, last_action_ts, daily_key, daily_exp,
                    daily_actions, alchemy_used, duel_used, duel_targets,
                    bt_attempts, bt_fails, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, unixepoch())
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    user_name      = excluded.user_name,
                    realm_index    = excluded.realm_index,
                    exp            = excluded.exp,
                    total_exp      = excluded.total_exp,
                    spirit_stones  = excluded.spirit_stones,
                    technique      = excluded.technique,
                    pills          = excluded.pills,
                    pending_bonus  = excluded.pending_bonus,
                    fail_streak    = excluded.fail_streak,
                    last_chat_ts   = excluded.last_chat_ts,
                    last_action_ts = excluded.last_action_ts,
                    daily_key      = excluded.daily_key,
                    daily_exp      = excluded.daily_exp,
                    daily_actions  = excluded.daily_actions,
                    alchemy_used   = excluded.alchemy_used,
                    duel_used      = excluded.duel_used,
                    duel_targets   = excluded.duel_targets,
                    bt_attempts    = excluded.bt_attempts,
                    bt_fails       = excluded.bt_fails,
                    updated_at     = unixepoch()
                """,
                (
                    char.chat_id, char.user_id, char.user_name, char.realm_index,
                    char.exp, char.total_exp, char.spirit_stones, char.technique,
                    json.dumps(char.pills, ensure_ascii=False), char.pending_bonus,
                    char.fail_streak, char.last_chat_ts, char.last_action_ts,
                    char.daily_key, char.daily_exp, char.daily_actions,
                    char.alchemy_used, char.duel_used,
                    json.dumps(char.duel_targets, ensure_ascii=False),
                    char.bt_attempts, char.bt_fails,
                ),
            )
            conn.commit()

    def list_ranking(self, chat_id: str, limit: int = 20) -> list[GameCharacter]:
        """群内修为榜（境界优先，同境界比累计修为）。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM game_characters
                 WHERE chat_id = ?
                 ORDER BY realm_index DESC, total_exp DESC, updated_at ASC
                 LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()
        return [_row_to_character(r) for r in rows]

    def list_characters(self, chat_id: str = "", search: str = "",
                        limit: int = 200, offset: int = 0) -> list[GameCharacter]:
        """角色列表（Web UI 管理用，可按群与昵称过滤）。"""
        sql = "SELECT * FROM game_characters WHERE 1=1"
        params: list = []
        if chat_id:
            sql += " AND chat_id = ?"
            params.append(chat_id)
        if search:
            sql += " AND (user_name LIKE ? OR user_id LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        sql += " ORDER BY realm_index DESC, total_exp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_character(r) for r in rows]

    def get_overview(self, chat_id: str = "") -> dict:
        """汇总统计：修士数 / 最高境界 / 平均境界 / 灵石总量。"""
        where, params = ("WHERE chat_id = ?", [chat_id]) if chat_id else ("", [])
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*)             AS total,
                       COALESCE(MAX(realm_index), 0) AS max_realm,
                       COALESCE(AVG(realm_index), 0) AS avg_realm,
                       COALESCE(SUM(spirit_stones), 0) AS stones
                  FROM game_characters {where}
                """,
                params,
            ).fetchone()
        return {
            "total": row["total"],
            "max_realm": row["max_realm"],
            "avg_realm": round(row["avg_realm"], 2),
            "stones": row["stones"],
        }

    def reset_character(self, chat_id: str, user_id: str) -> bool:
        """把角色打回炼气初期（管理员操作）。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE game_characters
                   SET realm_index = 0, exp = 0, total_exp = 0,
                       spirit_stones = 0, technique = '', pills = '{}',
                       pending_bonus = 0, fail_streak = 0,
                       alchemy_used = 0, duel_used = 0, duel_targets = '[]',
                       bt_attempts = 0, bt_fails = 0,
                       updated_at = unixepoch()
                 WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def grant_stones(self, chat_id: str, user_id: str, amount: int) -> bool:
        """发放灵石（管理员操作，amount 可为负，余额不会低于 0）。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE game_characters
                   SET spirit_stones = MAX(0, spirit_stones + ?),
                       updated_at = unixepoch()
                 WHERE chat_id = ? AND user_id = ?
                """,
                (amount, chat_id, user_id),
            )
            conn.commit()
        return cur.rowcount > 0

    def set_realm(self, chat_id: str, user_id: str, realm_index: int) -> bool:
        """直接设定境界（管理员操作，会同时清零当前修为）。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE game_characters
                   SET realm_index = ?, exp = 0, fail_streak = 0,
                       updated_at = unixepoch()
                 WHERE chat_id = ? AND user_id = ?
                """,
                (realm_index, chat_id, user_id),
            )
            conn.commit()
        return cur.rowcount > 0

    # ── 事件日志 ─────────────────────────────────────────────────────

    def log_event(self, chat_id: str, kind: str, actor_id: str = "",
                  actor_name: str = "", summary: str = "",
                  detail: dict | None = None) -> None:
        """记录一条修仙大事记。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO game_events
                    (chat_id, kind, actor_id, actor_name, summary, detail)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chat_id, kind, actor_id, actor_name, summary,
                 json.dumps(detail or {}, ensure_ascii=False)),
            )
            conn.commit()

    def list_events(self, chat_id: str = "", limit: int = 50) -> list[GameEvent]:
        """最近的事件（按时间倒序）。"""
        sql = "SELECT * FROM game_events"
        params: list = []
        if chat_id:
            sql += " WHERE chat_id = ?"
            params.append(chat_id)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        events = []
        for r in rows:
            try:
                detail = json.loads(r["detail"] or "{}")
            except (json.JSONDecodeError, TypeError):
                detail = {}
            events.append(GameEvent(
                id=r["id"], chat_id=r["chat_id"], kind=r["kind"],
                actor_id=r["actor_id"], actor_name=r["actor_name"],
                summary=r["summary"], detail=detail,
                created_at=r["created_at"],
            ))
        return events

    def count_events_since(self, since_ts: float, chat_id: str = "") -> int:
        """统计某时间点之后的事件数（概览卡片「今日动态」用）。"""
        sql = "SELECT COUNT(*) AS n FROM game_events WHERE created_at >= ?"
        params: list = [since_ts]
        if chat_id:
            sql += " AND chat_id = ?"
            params.append(chat_id)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return row["n"] if row else 0

    # ── 群状态与机缘 ─────────────────────────────────────────────────

    def get_group_state(self, chat_id: str) -> GroupState:
        """读取群状态，不存在时返回全零默认值（不落库）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM game_group_state WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if not row:
            return GroupState(chat_id=chat_id)
        return GroupState(
            chat_id=row["chat_id"],
            last_encounter_at=row["last_encounter_at"],
            active_expires_at=row["active_expires_at"],
            active_reward_kind=row["active_reward_kind"],
            active_reward_amount=row["active_reward_amount"],
        )

    def save_group_state(self, state: GroupState) -> None:
        """整体写回群状态（UPSERT）。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO game_group_state
                    (chat_id, last_encounter_at, active_expires_at,
                     active_reward_kind, active_reward_amount)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    last_encounter_at    = excluded.last_encounter_at,
                    active_expires_at    = excluded.active_expires_at,
                    active_reward_kind   = excluded.active_reward_kind,
                    active_reward_amount = excluded.active_reward_amount
                """,
                (state.chat_id, state.last_encounter_at, state.active_expires_at,
                 state.active_reward_kind, state.active_reward_amount),
            )
            conn.commit()

    def spawn_encounter(self, chat_id: str, now: float, expires_at: float,
                        reward_kind: str, reward_amount: int) -> None:
        """刷新一次天降机缘。"""
        state = self.get_group_state(chat_id)
        state.last_encounter_at = now
        state.active_expires_at = expires_at
        state.active_reward_kind = reward_kind
        state.active_reward_amount = reward_amount
        self.save_group_state(state)

    def claim_encounter(self, chat_id: str, now: float) -> Optional[tuple[str, int]]:
        """原子抢占当前机缘。

        在同一个 ``BEGIN IMMEDIATE`` 事务里先读后写，保证并发下只有一个
        调用者能拿到（其余返回 None），且不会丢失奖励数额。

        Returns:
            抢占成功时返回 ``(reward_kind, reward_amount)``，否则 None。
        """
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT active_reward_kind, active_reward_amount
                  FROM game_group_state
                 WHERE chat_id = ?
                   AND active_reward_kind != ''
                   AND active_expires_at > ?
                """,
                (chat_id, now),
            ).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE game_group_state
                   SET active_expires_at = 0,
                       active_reward_kind = '',
                       active_reward_amount = 0
                 WHERE chat_id = ?
                """,
                (chat_id,),
            )
            conn.commit()
        return (row["active_reward_kind"], row["active_reward_amount"])

    def clear_encounter(self, chat_id: str) -> None:
        """清空当前机缘（过期回收）。"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE game_group_state
                   SET active_expires_at = 0,
                       active_reward_kind = '',
                       active_reward_amount = 0
                 WHERE chat_id = ?
                """,
                (chat_id,),
            )
            conn.commit()

    def get_active_chat_ids(self) -> list[str]:
        """有角色数据的群列表（Web UI 群选择器用）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT chat_id FROM game_characters ORDER BY chat_id"
            ).fetchall()
        return [r["chat_id"] for r in rows]
