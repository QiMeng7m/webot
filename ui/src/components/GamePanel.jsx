import { useState, useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Sparkle, Trophy, Lightning, Flask, Gift, ArrowCounterClockwise,
  MagnifyingGlass, CheckCircle, Warning, Info, FloppyDisk,
} from '@phosphor-icons/react'
import { Toggle, Input } from './SharedComponents'

const API = 'http://127.0.0.1:7327'

// 修仙主题强调色（与「趣味抽签」的橙、「群聊待办」的红区分开）
const GAME_ACCENT = '#7c6cf0'

const configPanel = {
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.3, ease: 'easeOut' } },
  exit: { opacity: 0, y: 20, transition: { duration: 0.2 } },
}

const EVENT_META = {
  breakthrough_success: { icon: Lightning, color: '#18E299', label: '突破' },
  tribulation_success: { icon: Sparkle, color: '#e8b04b', label: '飞升' },
  breakthrough_fail: { icon: Warning, color: '#d45656', label: '突破失败' },
  tribulation_fail: { icon: Warning, color: '#d45656', label: '渡劫失败' },
  encounter: { icon: Gift, color: '#7c6cf0', label: '机缘' },
  duel: { icon: Trophy, color: '#4b9fe8', label: '论道' },
}

function ParamRow({ label, hint, children }) {
  return (
    <div>
      <p className="text-[14px] text-text-main font-medium">{label}</p>
      <p className="text-xs text-text-muted mt-0.5 mb-2">{hint}</p>
      {children}
    </div>
  )
}

function StatCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div className="bg-bg-card border border-border-main rounded-2xl p-5">
      <div className="flex items-center gap-2 mb-3">
        <Icon size={16} weight="fill" style={{ color }} />
        <span className="text-xs text-text-muted font-medium">{label}</span>
      </div>
      <p className="text-2xl font-semibold text-text-main tracking-tight">{value}</p>
      {sub && <p className="text-xs text-text-muted mt-1">{sub}</p>}
    </div>
  )
}

function RealmBadge({ realmName, realmLabel }) {
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-md text-[12px] font-medium shrink-0"
      style={{
        backgroundColor: `${GAME_ACCENT}1a`,
        color: GAME_ACCENT,
        border: `1px solid ${GAME_ACCENT}33`,
      }}
      title={realmLabel}
    >
      {realmName}
    </span>
  )
}

function ProgressBar({ progress, exp, expNeeded }) {
  const pct = Math.round((progress || 0) * 100)
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 h-1.5 bg-bg-raised rounded-full overflow-hidden min-w-[60px]">
        <motion.div
          className="h-full rounded-full"
          style={{ backgroundColor: '#18E299' }}
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.4, ease: 'easeOut' }}
        />
      </div>
      <span className="text-[11px] text-text-muted font-mono tabular-nums shrink-0">
        {expNeeded > 0 ? `${exp}/${expNeeded}` : '圆满'}
      </span>
    </div>
  )
}

export default function GamePanel() {
  // ── Config state ───────────────────────────────────────────────
  const [configLoaded, setConfigLoaded] = useState(false)
  const [configSaved, setConfigSaved] = useState(false)
  const [configSaveError, setConfigSaveError] = useState('')
  const [gameEnabled, setGameEnabled] = useState(false)
  const [gameGroups, setGameGroups] = useState(['*'])
  const [gameChatExp, setGameChatExp] = useState(2)
  const [gameChatCooldown, setGameChatCooldown] = useState(30)
  const [gameDailyCap, setGameDailyCap] = useState(200)
  const [gameActionCooldown, setGameActionCooldown] = useState(300)
  const [gameMultiplier, setGameMultiplier] = useState(1.0)
  const [gameEncounterInterval, setGameEncounterInterval] = useState(45)
  const [gameAiFlavor, setGameAiFlavor] = useState(true)

  // ── Data state ─────────────────────────────────────────────────
  const [availableGroups, setAvailableGroups] = useState([])
  const [selectedGroup, setSelectedGroup] = useState('')
  const [overview, setOverview] = useState(null)
  const [ranking, setRanking] = useState([])
  const [events, setEvents] = useState([])
  const [groups, setGroups] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [characters, setCharacters] = useState([])
  const [actionMsg, setActionMsg] = useState('')
  const [showManage, setShowManage] = useState(false)
  // 配置项是一次性的事，排行榜是天天看的 —— 默认收起配置，把版面让给玩法数据
  const [showConfig, setShowConfig] = useState(false)

  const retryRef = useRef(0)

  // ── Load config on mount ───────────────────────────────────────
  useEffect(() => {
    async function loadConfig() {
      try {
        const res = await fetch(`${API}/api/load-config`)
        const data = await res.json()
        if (data.ok && data.config) {
          const c = data.config
          if (typeof c.game_enabled === 'boolean') setGameEnabled(c.game_enabled)
          if (c.game_groups) setGameGroups(Array.isArray(c.game_groups) ? c.game_groups : ['*'])
          if (c.game_chat_exp != null) setGameChatExp(c.game_chat_exp)
          if (c.game_chat_cooldown_sec != null) setGameChatCooldown(c.game_chat_cooldown_sec)
          if (c.game_daily_exp_cap != null) setGameDailyCap(c.game_daily_exp_cap)
          if (c.game_action_cooldown_sec != null) setGameActionCooldown(c.game_action_cooldown_sec)
          if (c.game_exp_multiplier != null) setGameMultiplier(c.game_exp_multiplier)
          if (c.game_encounter_interval_min != null) setGameEncounterInterval(c.game_encounter_interval_min)
          if (typeof c.game_ai_flavor_enabled === 'boolean') setGameAiFlavor(c.game_ai_flavor_enabled)
        }
      } catch {}
      setConfigLoaded(true)
    }
    loadConfig()
  }, [])

  // ── Load available groups ──────────────────────────────────────
  useEffect(() => {
    async function loadGroups() {
      try {
        const res = await fetch(`${API}/api/nicknames/groups`)
        const data = await res.json()
        if (data.ok) setAvailableGroups(data.groups || [])
      } catch {}
    }
    loadGroups()
  }, [])

  // ── Load game data ─────────────────────────────────────────────
  useEffect(() => { loadGame() }, [selectedGroup])

  useEffect(() => {
    if (!error) return
    if (retryRef.current >= 5) return
    const delay = Math.min(1000 * Math.pow(2, retryRef.current), 16000)
    const timer = setTimeout(() => { retryRef.current++; loadGame() }, delay)
    return () => clearTimeout(timer)
  }, [error])

  async function loadGame() {
    setLoading(true)
    setError('')
    try {
      const params = new URLSearchParams()
      if (selectedGroup) params.set('chat_id', selectedGroup)
      const res = await fetch(`${API}/api/game/overview?${params}`)
      const d = await res.json()
      if (d.ok) {
        setOverview(d.overview || null)
        setRanking(d.ranking || [])
        setEvents(d.events || [])
        setGroups(d.groups || [])
        retryRef.current = 0
      } else {
        setError(d.error || '加载失败')
      }
    } catch { setError('无法连接到服务器，请确认机器人已启动') }
    setLoading(false)
  }

  useEffect(() => {
    if (!showManage) return
    async function loadChars() {
      try {
        const params = new URLSearchParams()
        if (selectedGroup) params.set('chat_id', selectedGroup)
        if (search.trim()) params.set('search', search.trim())
        const res = await fetch(`${API}/api/game/characters?${params}`)
        const d = await res.json()
        if (d.ok) setCharacters(d.items || [])
      } catch {}
    }
    const timer = setTimeout(loadChars, search.trim() ? 300 : 0)
    return () => clearTimeout(timer)
  }, [showManage, selectedGroup, search])

  // ── Save config ────────────────────────────────────────────────
  async function handleSaveConfig() {
    setConfigSaved(false)
    setConfigSaveError('')
    try {
      const res = await fetch(`${API}/api/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          game_enabled: gameEnabled,
          game_groups: gameGroups,
          game_chat_exp: gameChatExp,
          game_chat_cooldown_sec: gameChatCooldown,
          game_daily_exp_cap: gameDailyCap,
          game_action_cooldown_sec: gameActionCooldown,
          game_exp_multiplier: gameMultiplier,
          game_encounter_interval_min: gameEncounterInterval,
          game_ai_flavor_enabled: gameAiFlavor,
        }),
      })
      const data = await res.json()
      if (data.ok) {
        setConfigSaved(true)
        setTimeout(() => setConfigSaved(false), 3000)
      } else {
        setConfigSaveError(data.error || '保存失败')
        setTimeout(() => setConfigSaveError(''), 5000)
      }
    } catch {
      setConfigSaveError('无法连接到服务器，请确认机器人已启动')
      setTimeout(() => setConfigSaveError(''), 5000)
    }
  }

  async function doGameAction(action, chatId, userId, extra = {}) {
    setActionMsg('')
    try {
      const res = await fetch(`${API}/api/game/action`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, chat_id: chatId, user_id: userId, ...extra }),
      })
      const d = await res.json()
      setActionMsg(d.ok ? '操作成功' : (d.error || '操作失败'))
      setTimeout(() => setActionMsg(''), 3000)
      if (d.ok) {
        loadGame()
        if (showManage) {
          const params = new URLSearchParams()
          if (selectedGroup) params.set('chat_id', selectedGroup)
          const r = await fetch(`${API}/api/game/characters?${params}`)
          const dd = await r.json()
          if (dd.ok) setCharacters(dd.items || [])
        }
      }
    } catch {
      setActionMsg('网络错误')
      setTimeout(() => setActionMsg(''), 3000)
    }
  }

  function addGroup(chatId) {
    if (!chatId) return
    if (gameGroups.includes('*')) setGameGroups([chatId])
    else if (!gameGroups.includes(chatId)) setGameGroups([...gameGroups, chatId])
  }
  function removeGroup(index) {
    const next = gameGroups.filter((_, i) => i !== index)
    setGameGroups(next.length === 0 ? ['*'] : next)
  }

  function groupLabel(chatId) {
    const info = availableGroups.find(ag => ag.chat_id === chatId)
    return info?.group_name || chatId.slice(0, 12)
  }

  return (
    <div className="max-w-3xl">
      {/* ── 未开启横幅 ─────────────────────────────────────────── */}
      <AnimatePresence>
        {configLoaded && !gameEnabled && (
          <motion.div
            initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
            className="mb-6 p-5 rounded-2xl border flex items-center justify-between gap-6"
            style={{ backgroundColor: `${GAME_ACCENT}0d`, borderColor: `${GAME_ACCENT}33` }}
          >
            <div className="flex items-start gap-3">
              <Sparkle size={20} weight="fill" style={{ color: GAME_ACCENT }} className="mt-0.5 shrink-0" />
              <div>
                <p className="text-[15px] font-semibold text-text-main">修仙玩法尚未开启</p>
                <p className="text-sm text-text-muted mt-1">
                  开启后，群友正常聊天即会缓慢积累修为，@机器人可打坐、突破、渡劫。
                </p>
              </div>
            </div>
            <button
              onClick={() => { setGameEnabled(true); setTimeout(handleSaveConfig, 0) }}
              className="shrink-0 px-5 py-2.5 rounded-full text-[14px] font-semibold text-white transition-opacity hover:opacity-90 cursor-pointer"
              style={{ backgroundColor: GAME_ACCENT }}
            >
              一键开启
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* ── 数据总览 ───────────────────────────────────────────── */}
      <div className="bg-bg-card border border-border-main rounded-2xl shadow-[rgba(0,0,0,0.03)_0px_2px_4px] dark:shadow-none p-7">
        <div className="flex items-center gap-2.5 mb-5 pl-1">
          <div className="w-1.5 h-4.5 rounded-full shadow-sm" style={{ backgroundColor: GAME_ACCENT }} />
          <h3 className="text-sm font-semibold tracking-tight text-text-main">修仙世界</h3>
          <div className="ml-auto flex items-center gap-2">
            <select value={selectedGroup} onChange={e => setSelectedGroup(e.target.value)}
              className="bg-bg-raised border border-border-main rounded-full px-4 py-1.5 text-[13px] text-text-main focus:outline-none focus:border-brand-green cursor-pointer">
              <option value="">全部群聊</option>
              {groups.map(g => <option key={g} value={g}>{groupLabel(g)}</option>)}
            </select>
            <button onClick={loadGame}
              className="p-1.5 rounded-full bg-bg-raised border border-border-main text-text-muted hover:text-text-main transition-colors cursor-pointer"
              title="刷新">
              <ArrowCounterClockwise size={15} />
            </button>
          </div>
        </div>

        {loading && !overview && (
          <p className="text-sm text-text-muted py-8 text-center">加载中...</p>
        )}
        {error && (
          <p className="text-sm text-[#d45656] py-8 text-center">{error}</p>
        )}

        {overview && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
              <StatCard icon={Trophy} color={GAME_ACCENT} label="在册修士"
                value={overview.total.toLocaleString()} sub="已创建角色的群友数" />
              <StatCard icon={Sparkle} color="#18E299" label="最高境界"
                value={overview.max_realm_label}
                sub={overview.total ? `平均境界 ${overview.avg_realm}` : '尚无修士'} />
              <StatCard icon={Lightning} color="#e8b04b" label="近 24 小时动态"
                value={overview.events_today.toLocaleString()}
                sub={`灵石总量 ${overview.stones.toLocaleString()}`} />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* 修为榜 */}
              <div>
                <p className="text-[14px] font-semibold text-text-main mb-3">
                  修为排行榜
                  {!selectedGroup && (
                    <span className="ml-2 text-[11px] font-normal text-text-muted">全服榜</span>
                  )}
                </p>
                {ranking.length === 0 ? (
                  <p className="text-sm text-text-muted py-6">
                    {overview.total > 0
                      ? '这个群还没有修士，换个群看看，或选择「全部群聊」看全服榜。'
                      : '还没有任何修士——群友在群里聊几句，或在群里 @机器人 发送「修炼」即可入道。'}
                  </p>
                ) : (
                  <div className="space-y-2">
                    {ranking.map((c, i) => (
                      <div key={`${c.chat_id}:${c.user_id}`} className="flex items-center gap-3 p-3 bg-bg-raised rounded-xl">
                        <span className={`w-6 text-center text-[13px] font-mono shrink-0 ${i < 3 ? 'font-semibold' : 'text-text-muted'}`}
                          style={i < 3 ? { color: GAME_ACCENT } : undefined}>
                          {i + 1}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-[13px] text-text-main truncate">{c.user_name}</span>
                            {!selectedGroup && (
                              <span className="text-[11px] text-text-muted truncate shrink-0 max-w-[110px]"
                                title={groupLabel(c.chat_id)}>
                                {groupLabel(c.chat_id)}
                              </span>
                            )}
                            <RealmBadge realmName={c.realm_name} realmLabel={c.realm_label} />
                          </div>
                          <ProgressBar progress={c.progress} exp={c.exp} expNeeded={c.exp_needed} />
                        </div>
                        <span className="text-[11px] text-text-muted font-mono shrink-0">
                          {c.spirit_stones} 石
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* 最近动态 */}
              <div>
                <p className="text-[14px] font-semibold text-text-main mb-3">最近动态</p>
                {events.length === 0 ? (
                  <p className="text-sm text-text-muted py-6">暂无突破、渡劫或机缘记录。</p>
                ) : (
                  <div className="space-y-1.5 max-h-[420px] overflow-y-auto pr-1">
                    {events.map(e => {
                      const meta = EVENT_META[e.kind] || { icon: Info, color: '#888', label: e.kind }
                      const Icon = meta.icon
                      return (
                        <div key={e.id} className="flex items-start gap-2.5 p-2.5 rounded-lg hover:bg-bg-raised transition-colors">
                          <Icon size={14} weight="fill" style={{ color: meta.color }} className="mt-0.5 shrink-0" />
                          <div className="min-w-0">
                            <p className="text-[13px] text-text-main break-words">{e.summary}</p>
                            <p className="text-[11px] text-text-muted mt-0.5 font-mono">
                              {new Date(e.created_at * 1000).toLocaleString('zh-CN', { hour12: false })}
                            </p>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* 角色管理 */}
            <div className="mt-7 pt-6 border-t border-border-main">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <p className="text-[14px] font-semibold text-text-main">角色管理</p>
                  <p className="text-xs text-text-muted mt-0.5">重置角色、发放灵石、修正境界</p>
                </div>
                <button onClick={() => setShowManage(!showManage)}
                  className="px-4 py-2 rounded-full text-[13px] font-medium bg-bg-raised border border-border-main text-text-main hover:border-text-muted/30 transition-colors cursor-pointer">
                  {showManage ? '收起' : '展开'}
                </button>
              </div>

              <AnimatePresence>
                {showManage && (
                  <motion.div variants={configPanel} initial="initial" animate="animate" exit="exit">
                    <div className="relative mb-3">
                      <MagnifyingGlass size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted" />
                      <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                        placeholder="搜索昵称或 wxid"
                        className="w-full bg-bg-raised border border-border-main rounded-full pl-10 pr-5 py-2.5 text-[14px] text-text-main placeholder:text-text-muted/65 focus:outline-none focus:border-brand-green focus:ring-2 focus:ring-brand-green/15 transition-all" />
                    </div>

                    {actionMsg && (
                      <p className="text-xs text-brand-green-hover dark:text-brand-green mb-2">{actionMsg}</p>
                    )}

                    {characters.length === 0 ? (
                      <p className="text-sm text-text-muted py-4">没有匹配的修士。</p>
                    ) : (
                      <div className="space-y-2">
                        {characters.map(c => (
                          <div key={`${c.chat_id}:${c.user_id}`} className="flex items-center gap-3 p-3 bg-bg-raised rounded-xl">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-[13px] text-text-main truncate">{c.user_name}</span>
                                <RealmBadge realmName={c.realm_label} realmLabel={c.realm_label} />
                              </div>
                              <p className="text-[11px] text-text-muted font-mono truncate">
                                {groupLabel(c.chat_id)} · 累计 {c.total_exp} · {c.spirit_stones} 石
                                {c.technique ? ` · ${c.technique}` : ''}
                                {c.bt_fails > 0 ? ` · 失败 ${c.bt_fails} 次` : ''}
                              </p>
                            </div>
                            <div className="flex items-center gap-1.5 shrink-0">
                              <button onClick={() => doGameAction('grant_stones', c.chat_id, c.user_id, { amount: 100 })}
                                title="发放 100 灵石"
                                className="p-1.5 rounded-lg bg-bg-card border border-border-main text-text-muted hover:text-brand-green transition-colors cursor-pointer">
                                <Gift size={14} />
                              </button>
                              <button onClick={() => doGameAction('reset', c.chat_id, c.user_id)}
                                title="重置为炼气初期"
                                className="p-1.5 rounded-lg bg-bg-card border border-border-main text-text-muted hover:text-[#d45656] transition-colors cursor-pointer">
                                <ArrowCounterClockwise size={14} />
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </>
        )}
      </div>
      {/* ── 功能开关与配置 ─────────────────────────────────────── */}
      <div className="bg-bg-card border border-border-main rounded-2xl shadow-[rgba(0,0,0,0.03)_0px_2px_4px] dark:shadow-none p-7">
        <div className="flex items-center gap-2.5 pl-1">
          <div className="w-1.5 h-4.5 rounded-full shadow-sm" style={{ backgroundColor: GAME_ACCENT }} />
          <h3 className="text-sm font-semibold tracking-tight text-text-main">修仙玩法配置</h3>
          <button onClick={() => setShowConfig(!showConfig)}
            className="ml-auto px-4 py-1.5 rounded-full text-[13px] font-medium bg-bg-raised border border-border-main text-text-main hover:border-text-muted/30 transition-colors cursor-pointer">
            {showConfig ? '收起配置' : '展开配置'}
          </button>
        </div>

        <div className="flex items-center justify-between mt-5 mb-1">
          <div className="flex-1 mr-8">
            <p className="text-[15px] text-text-main font-medium">修仙体系</p>
            <p className="text-sm text-text-muted mt-1.5">
              群友正常聊天自动积累修为（有冷却与每日上限），@机器人发送「修炼」「突破」「渡劫」「排行榜」等命令。
            </p>
          </div>
          <Toggle enabled={gameEnabled} onChange={setGameEnabled} />
        </div>

        <AnimatePresence>
          {gameEnabled && showConfig && (
            <motion.div variants={configPanel} initial="initial" animate="animate" exit="exit"
              className="p-4 bg-bg-raised rounded-lg space-y-5">

              <div>
                <p className="text-[14px] text-text-main font-medium">生效群聊范围</p>
                <p className="text-xs text-text-muted mt-0.5 mb-2">选择哪些群聊启用修仙玩法，未选中的群不积累修为也不响应命令</p>
                <div className="flex flex-wrap gap-2 mb-2">
                  {(gameGroups || []).map((g, i) => (
                    <span key={i} className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[13px]"
                      style={{ backgroundColor: `${GAME_ACCENT}1a`, color: GAME_ACCENT, border: `1px solid ${GAME_ACCENT}33` }}>
                      {g === '*' ? '全部群聊' : groupLabel(g)}
                      <button type="button" onClick={() => removeGroup(i)}
                        disabled={gameGroups.length === 1 && gameGroups[0] === '*'}
                        className={`ml-0.5 leading-none text-base transition-colors ${(gameGroups.length === 1 && gameGroups[0] === '*') ? 'text-text-muted cursor-not-allowed' : 'opacity-60 hover:text-[#d45656] cursor-pointer'}`}>&times;</button>
                    </span>
                  ))}
                </div>
                <select value=""
                  onChange={e => { if (e.target.value) { addGroup(e.target.value); e.target.value = '' } }}
                  className="w-full bg-bg-raised border border-border-main rounded-lg px-3 py-2 text-[14px] text-text-main focus:outline-none focus:border-brand-green focus:ring-1 focus:ring-brand-green/15 transition-all cursor-pointer">
                  <option value="">{availableGroups.length === 0 ? '加载群聊列表...' : '选择群聊...'}</option>
                  {availableGroups.filter(ag => !gameGroups.includes(ag.chat_id)).map(ag => (
                    <option key={ag.chat_id} value={ag.chat_id}>
                      {ag.group_name} — {ag.member_count}人
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <ParamRow label="每句修为" hint="每条有效发言的基础修为（1-100）">
                  <Input type="number" value={String(gameChatExp)}
                    onChange={v => setGameChatExp(Math.max(1, Math.min(100, parseInt(v) || 2)))} />
                </ParamRow>
                <ParamRow label="发言冷却（秒）" hint="同一用户多久结算一次被动收益">
                  <Input type="number" value={String(gameChatCooldown)}
                    onChange={v => setGameChatCooldown(Math.max(0, parseInt(v) || 0))} />
                </ParamRow>
                <ParamRow label="每日上限" hint="每日被动修为上限（0=不限）">
                  <Input type="number" value={String(gameDailyCap)}
                    onChange={v => setGameDailyCap(Math.max(0, parseInt(v) || 0))} />
                </ParamRow>
                <ParamRow label="打坐冷却（秒）" hint="「修炼」命令的冷却时间">
                  <Input type="number" value={String(gameActionCooldown)}
                    onChange={v => setGameActionCooldown(Math.max(0, parseInt(v) || 0))} />
                </ParamRow>
                <ParamRow label="收益倍率" hint="全局调节玩法节奏（0.1-20.0）">
                  <Input type="number" value={String(gameMultiplier)}
                    onChange={v => setGameMultiplier(Math.max(0.1, Math.min(20, parseFloat(v) || 1)))} />
                </ParamRow>
                <ParamRow label="机缘间隔（分钟）" hint="天降机缘刷新间隔（0=关闭机缘）">
                  <Input type="number" value={String(gameEncounterInterval)}
                    onChange={v => setGameEncounterInterval(Math.max(0, parseInt(v) || 0))} />
                </ParamRow>
              </div>

              <div className="flex items-center justify-between pt-1">
                <div className="flex-1 mr-8">
                  <p className="text-[14px] text-text-main font-medium">AI 剧情文案</p>
                  <p className="text-xs text-text-muted mt-0.5">
                    突破、渡劫等关键节点调用 AI 生成个性化旁白；关闭或调用失败时使用内置文案模板。
                  </p>
                </div>
                <Toggle enabled={gameAiFlavor} onChange={setGameAiFlavor} />
              </div>

              <div className="p-3 bg-bg-main/60 border border-border-main rounded-xl">
                <p className="text-xs text-text-muted leading-relaxed">
                  💡 <strong>群内玩法：</strong><br />
                  @机器人 <code>修炼</code> · <code>突破</code> · <code>渡劫</code> · <code>我的</code>
                  · <code>排行榜</code> · <code>炼丹</code> · <code>服用 回气丹</code> · <code>论道 昵称</code> · <code>修仙帮助</code><br />
                  <span className="text-text-muted/60">
                    天降机缘会全群播报，第一个回复「抢机缘」的群友夺得。
                  </span>
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="mt-6 flex items-center gap-4">
          <AnimatePresence>
            {configSaved && (
              <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
                className="flex items-center gap-2 px-5 py-2.5 bg-brand-green-light border border-brand-green/20 rounded-full text-sm text-brand-green-hover dark:text-brand-green font-medium shadow-sm">
                <CheckCircle size={18} weight="fill" /> 配置已保存。需要重启机器人才能生效。
              </motion.div>
            )}
            {configSaveError && (
              <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
                className="flex items-center gap-2 px-5 py-2.5 bg-[#d45656]/5 border border-[#d45656]/20 rounded-full text-sm text-[#d45656] font-medium shadow-sm">
                <Warning size={18} weight="fill" /> {configSaveError}
              </motion.div>
            )}
          </AnimatePresence>
          {!configSaved && !configSaveError && (
            <motion.button whileTap={{ scale: 0.97 }} whileHover={{ scale: 1.02 }} onClick={handleSaveConfig}
              className="w-48 py-2.5 rounded-full text-[14px] font-semibold tracking-wide shadow-sm transition-all duration-300 flex items-center justify-center gap-2 cursor-pointer bg-[#0d0d0d] dark:bg-white text-white dark:text-[#0d0d0d] border border-[#0d0d0d] dark:border-border-main hover:opacity-90">
              <FloppyDisk size={18} /> 保存配置
            </motion.button>
          )}
        </div>
      </div>

    </div>
  )
}
