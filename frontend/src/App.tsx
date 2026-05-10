import { useState, useEffect, useRef, useCallback } from 'react'
import './App.css'

// ─── Types ────────────────────────────────────────────────────────────────────

type Tab = 'chat' | 'live' | 'monitor' | 'memories' | 'admin'

interface TickEntry {
  timestamp: string
  outcome: string
  threshold: number
  candidate_count: number
  best: { tool: string; verb: string; intent: string; score: number; source?: string } | null
  affect?: Record<string, any>
  executed?: boolean
  error?: string | null
}

interface EventEntry {
  timestamp: string
  source: string
  summary: string
  person_id?: string
}

interface AffectEntry {
  timestamp: string
  valence?: number | null
  arousal?: number | null
  dominance?: number | null
  label?: string | null
  current_activity?: string | null
}

interface LiveSnapshot {
  ticks: TickEntry[]
  events: EventEntry[]
  affect: AffectEntry[]
}

interface Msg { role: 'user' | 'chloe'; text: string }

interface Action {
  id: string; tool: string; verb: string; state: string
  intent: string; cost_usd: number; proposed_at: string; error?: string
}

interface Memory {
  id: number; kind: string; text: string; weight: number
  salience: number; source: string; created_at: string
}

interface StateNow {
  current_activity: string; affect_label: string; tone: string
  goals: { id: number; name: string; progress: number }[]
  top_interests: { label: string; intensity: number }[]
}

// ─── Constants ───────────────────────────────────────────────────────────────

const WS_URL = `ws://${location.host}/v1/mobile/ws?person_id=1`

const STATE_COLOR: Record<string, string> = {
  executed: 'var(--green)',
  self_aborted: 'var(--warn)', suppressed_by_leash: 'var(--warn)',
  denied: 'var(--danger)', failed: 'var(--danger)',
  awaiting_confirmation: 'var(--accent)', reverted: 'var(--muted)',
}

const KIND_COLORS: Record<string, string> = {
  episodic: '#a78bfa', semantic: '#6ee7b7',
  autobiographical: '#fbbf24', procedural: '#60a5fa',
}

// ─── Root ────────────────────────────────────────────────────────────────────

export default function App() {
  const [tab, setTab] = useState<Tab>('chat')
  const [stateNow, setStateNow] = useState<StateNow | null>(null)

  useEffect(() => {
    async function load() {
      try { setStateNow(await (await fetch('/v1/state/now')).json()) } catch {}
    }
    load()
    const id = setInterval(load, 15000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="app">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-logo">✦ Chloe</div>

        {/* State strip */}
        {stateNow && (
          <div className="state-strip">
            {stateNow.affect_label && (
              <div className="state-row">
                <span className="state-key">mood</span>
                <span className="state-val">{stateNow.affect_label}</span>
              </div>
            )}
            {stateNow.current_activity && (
              <div className="state-row">
                <span className="state-key">doing</span>
                <span className="state-val">{stateNow.current_activity}</span>
              </div>
            )}
            {stateNow.goals.slice(0, 2).map(g => (
              <div key={g.id} className="state-row">
                <span className="state-key">goal</span>
                <span className="state-val">{g.name}</span>
              </div>
            ))}
          </div>
        )}

        <nav className="sidebar-nav">
          {([
            ['chat', '💬', 'Chat'],
            ['live', '💓', 'Live'],
            ['monitor', '📡', 'Monitor'],
            ['memories', '🧠', 'Memories'],
            ['admin', '⚙️', 'Admin'],
          ] as [Tab, string, string][]).map(([t, icon, label]) => (
            <button key={t} className={`nav-item ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
              <span>{icon}</span> {label}
            </button>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <main className="main">
        {tab === 'chat' && <Chat />}
        {tab === 'live' && <LiveTab />}
        {tab === 'monitor' && <MonitorTab />}
        {tab === 'memories' && <MemoriesTab />}
        {tab === 'admin' && <AdminTab />}
      </main>
    </div>
  )
}

// ─── Chat ────────────────────────────────────────────────────────────────────

function Chat() {
  const [messages, setMessages] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [connected, setConnected] = useState(false)
  const [thinking, setThinking] = useState(false)
  const [memText, setMemText] = useState('')
  const [memKind, setMemKind] = useState('episodic')
  const [memStatus, setMemStatus] = useState('')
  const [showInject, setShowInject] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws
    ws.onopen = () => setConnected(true)
    ws.onclose = () => { setConnected(false); setTimeout(connect, 2000) }
    ws.onerror = () => ws.close()
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data)
      if (msg.type === 'chunk' && msg.text) {
        setThinking(false)
        setMessages(prev => [...prev, { role: 'chloe', text: msg.text }])
      }
      if (msg.type === 'done') setThinking(false)
    }
  }, [])

  useEffect(() => { connect(); return () => wsRef.current?.close() }, [connect])
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, thinking])

  function send() {
    const text = input.trim()
    if (!text || wsRef.current?.readyState !== WebSocket.OPEN) return
    setMessages(prev => [...prev, { role: 'user', text }])
    wsRef.current!.send(JSON.stringify({ type: 'message', text }))
    setInput('')
    setThinking(true)
  }

  async function injectMemory() {
    const text = memText.trim()
    if (!text) return
    setMemStatus('saving…')
    try {
      const res = await fetch('/admin/memories/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, kind: memKind }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setMemStatus(`✓ saved (id ${data.id})`)
      setMemText('')
    } catch (err: any) {
      setMemStatus(`✗ ${err.message}`)
    }
    setTimeout(() => setMemStatus(''), 3000)
  }

  return (
    <div className="chat-layout">
      {/* Messages */}
      <div className="chat-main">
        <div className="chat-header">
          <div className="status-dot" style={{ background: connected ? 'var(--green)' : 'var(--muted)' }} />
          <span className="muted sm">{connected ? 'connected' : 'reconnecting…'}</span>
          <div style={{ flex: 1 }} />
          <button onClick={() => setShowInject(s => !s)} style={{ fontSize: 12, padding: '4px 10px' }}>
            {showInject ? 'Hide inject' : '+ Inject memory'}
          </button>
        </div>

        <div className="messages">
          {messages.length === 0 && (
            <div className="empty-state">Start a conversation with Chloe</div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`bubble-row ${m.role}`}>
              <div className={`bubble ${m.role}`}>{m.text}</div>
            </div>
          ))}
          {thinking && (
            <div className="bubble-row chloe">
              <div className="bubble chloe thinking">…</div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="chat-input-row">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
            placeholder="Message Chloe…"
          />
          <button className="primary" onClick={send} disabled={!connected || !input.trim()}>Send</button>
        </div>
      </div>

      {/* Inject panel */}
      {showInject && (
        <div className="inject-panel">
          <div className="panel-title">Inject Memory</div>
          <label className="field-label">KIND</label>
          <select value={memKind} onChange={e => setMemKind(e.target.value)}>
            <option value="episodic">episodic</option>
            <option value="semantic">semantic</option>
            <option value="autobiographical">autobiographical</option>
            <option value="procedural">procedural</option>
          </select>
          <label className="field-label" style={{ marginTop: 10 }}>TEXT</label>
          <textarea
            value={memText}
            onChange={e => setMemText(e.target.value)}
            placeholder="e.g. Teo loves hiking in the mountains…"
            rows={6}
            style={{ resize: 'vertical' }}
          />
          <button className="primary" onClick={injectMemory} disabled={!memText.trim()} style={{ marginTop: 8 }}>
            Inject
          </button>
          {memStatus && (
            <div className="sm" style={{ marginTop: 6, color: memStatus.startsWith('✓') ? 'var(--green)' : 'var(--danger)' }}>
              {memStatus}
            </div>
          )}
          <div className="hint-box" style={{ marginTop: 12 }}>
            Memories surface during chat when Chloe retrieves them as relevant context. After injecting, ask about the topic.
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Live ────────────────────────────────────────────────────────────────────

const OUTCOME_COLOR: Record<string, string> = {
  action_executed: 'var(--green)',
  action_suppressed: 'var(--warn)',
  idle_below_threshold: 'var(--muted)',
  idle_no_candidates: 'var(--muted)',
  mutex_blocked: 'var(--warn)',
}

function fmtTime(iso: string) {
  try { return new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleTimeString() } catch { return iso }
}

function fmtNum(v: any, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(digits) : '—'
}

function LiveTab() {
  const [data, setData] = useState<LiveSnapshot>({ ticks: [], events: [], affect: [] })
  const [paused, setPaused] = useState(false)
  const [lastUpdate, setLastUpdate] = useState('')

  async function refresh() {
    try {
      const res = await fetch('/admin/live/recent')
      const json = await res.json()
      setData(json)
      setLastUpdate(new Date().toLocaleTimeString())
    } catch {}
  }

  useEffect(() => {
    refresh()
    if (paused) return
    const id = setInterval(refresh, 2000)
    return () => clearInterval(id)
  }, [paused])

  return (
    <div className="tab-layout" style={{ display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '8px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span className="section-title" style={{ margin: 0 }}>Live heartbeat & events</span>
        <span className="muted sm">{lastUpdate ? `Updated ${lastUpdate}` : 'Loading…'}</span>
        <div style={{ flex: 1 }} />
        <button onClick={() => setPaused(p => !p)} style={{ fontSize: 11, padding: '4px 10px' }}>
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
        <button onClick={refresh} style={{ fontSize: 11, padding: '4px 10px' }}>Refresh</button>
      </div>

      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr', gap: 1, background: 'var(--border)', overflow: 'hidden' }}>
        {/* Ticks column */}
        <div style={{ background: 'var(--surface)', overflowY: 'auto', padding: '10px 14px' }}>
          <div className="section-title" style={{ position: 'sticky', top: 0, background: 'var(--surface)', paddingBottom: 6 }}>
            Heartbeat ticks <span className="muted sm">({data.ticks.length})</span>
          </div>
          {data.ticks.length === 0 && <div className="empty-state">No ticks yet</div>}
          {data.ticks.map((t, i) => (
            <div key={i} className="card" style={{ marginBottom: 8, padding: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span className="muted mono sm">{fmtTime(t.timestamp)}</span>
                <span className="mono sm" style={{ color: OUTCOME_COLOR[t.outcome] ?? 'var(--text)' }}>
                  {t.outcome}
                </span>
                <div style={{ flex: 1 }} />
                <span className="muted sm">cands {t.candidate_count}</span>
                <span className="muted sm">thr {fmtNum(t.threshold, 2)}</span>
              </div>
              {t.best ? (
                <div style={{ fontSize: 12 }}>
                  <span className="mono" style={{ color: 'var(--accent)' }}>{t.best.tool}.{t.best.verb}</span>
                  <span className="muted sm" style={{ marginLeft: 6 }}>score {fmtNum(t.best.score, 2)}{t.best.source ? ` · ${t.best.source}` : ''}</span>
                  <div className="muted sm truncate" style={{ marginTop: 2 }}>{t.best.intent}</div>
                </div>
              ) : (
                <div className="muted sm">no candidates</div>
              )}
              {t.affect && (t.affect.label || t.affect.valence !== undefined) && (
                <div className="muted sm" style={{ marginTop: 4, fontFamily: 'var(--mono)', fontSize: 11 }}>
                  {t.affect.label ? `${t.affect.label} · ` : ''}
                  v {fmtNum(t.affect.valence)} · a {fmtNum(t.affect.arousal)} · d {fmtNum(t.affect.dominance)}
                </div>
              )}
              {t.error && <div className="sm" style={{ color: 'var(--danger)', marginTop: 4 }}>{t.error}</div>}
            </div>
          ))}
        </div>

        {/* Events column */}
        <div style={{ background: 'var(--surface)', overflowY: 'auto', padding: '10px 14px' }}>
          <div className="section-title" style={{ position: 'sticky', top: 0, background: 'var(--surface)', paddingBottom: 6 }}>
            Events <span className="muted sm">({data.events.length})</span>
          </div>
          {data.events.length === 0 && <div className="empty-state">No events yet</div>}
          {data.events.map((e, i) => (
            <div key={i} className="card" style={{ marginBottom: 8, padding: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <span className="muted mono sm">{fmtTime(e.timestamp)}</span>
                <span className="mono sm" style={{ color: 'var(--accent)' }}>{e.source}</span>
              </div>
              <div style={{ fontSize: 12 }}>{e.summary}</div>
            </div>
          ))}
        </div>

        {/* Affect deltas column */}
        <div style={{ background: 'var(--surface)', overflowY: 'auto', padding: '10px 14px' }}>
          <div className="section-title" style={{ position: 'sticky', top: 0, background: 'var(--surface)', paddingBottom: 6 }}>
            Affect / state deltas <span className="muted sm">({data.affect.length})</span>
          </div>
          {data.affect.length === 0 && <div className="empty-state">No state yet</div>}
          {data.affect.map((a, i) => {
            const prev = data.affect[i + 1]
            const delta = (k: 'valence' | 'arousal' | 'dominance') => {
              if (!prev || a[k] == null || prev[k] == null) return null
              const d = Number(a[k]) - Number(prev[k])
              if (Math.abs(d) < 0.005) return null
              return d
            }
            const deltas: [string, number][] = (['valence', 'arousal', 'dominance'] as const)
              .map(k => [k, delta(k)] as [string, number | null])
              .filter((x): x is [string, number] => x[1] !== null)
            return (
              <div key={i} className="card" style={{ marginBottom: 8, padding: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span className="muted mono sm">{fmtTime(a.timestamp)}</span>
                  {a.label && <span className="mono sm" style={{ color: 'var(--accent)' }}>{a.label}</span>}
                </div>
                <div className="muted sm" style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>
                  v {fmtNum(a.valence)} · a {fmtNum(a.arousal)} · d {fmtNum(a.dominance)}
                </div>
                {deltas.length > 0 && (
                  <div className="sm" style={{ fontFamily: 'var(--mono)', fontSize: 11, marginTop: 2 }}>
                    {deltas.map(([k, d]) => (
                      <span key={k} style={{ marginRight: 8, color: d > 0 ? 'var(--green)' : 'var(--danger)' }}>
                        {k[0]}{d > 0 ? '+' : ''}{d.toFixed(2)}
                      </span>
                    ))}
                  </div>
                )}
                {a.current_activity && (
                  <div className="muted sm" style={{ marginTop: 2 }}>doing: {a.current_activity}</div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ─── Monitor ─────────────────────────────────────────────────────────────────

function MonitorTab() {
  const [actions, setActions] = useState<Action[]>([])
  const [pending, setPending] = useState<any[]>([])
  const [lastUpdate, setLastUpdate] = useState('')

  async function refresh() {
    try {
      const [auditRes, pendingRes] = await Promise.all([
        fetch('/admin/audit?limit=200'),
        fetch('/v1/confirmations/pending'),
      ])
      setActions((await auditRes.json()).actions)
      setPending(await pendingRes.json())
      setLastUpdate(new Date().toLocaleTimeString())
    } catch {}
  }

  useEffect(() => { refresh(); const id = setInterval(refresh, 5000); return () => clearInterval(id) }, [])

  async function handleConfirm(id: string, action: 'confirm' | 'deny') {
    await fetch(`/v1/confirmations/${id}/${action}`, { method: 'POST' })
    refresh()
  }

  return (
    <div className="tab-layout">
      {/* Pending confirmations */}
      {pending.length > 0 && (
        <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
          <div className="section-title" style={{ marginBottom: 8 }}>Pending confirmations</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {pending.map((t: any) => (
              <div key={t.id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <span style={{ fontFamily: 'var(--mono)', fontSize: 12, color: 'var(--accent)' }}>{t.tool}</span>
                  <span className="muted sm" style={{ marginLeft: 8 }}>{t.intent}</span>
                </div>
                <button className="primary" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => handleConfirm(t.id, 'confirm')}>Allow</button>
                <button className="danger" style={{ padding: '4px 10px', fontSize: 12 }} onClick={() => handleConfirm(t.id, 'deny')}>Deny</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Audit feed */}
      <div style={{ padding: '8px 20px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="muted sm">{lastUpdate ? `Updated ${lastUpdate}` : 'Loading…'}</span>
        <button onClick={refresh} style={{ fontSize: 11, padding: '4px 10px' }}>Refresh</button>
      </div>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              {['Time', 'Tool', 'Verb', 'State', 'Intent', 'Cost'].map(h => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {actions.map(a => (
              <tr key={a.id}>
                <td className="muted mono">{new Date(a.proposed_at).toLocaleTimeString()}</td>
                <td className="mono">{a.tool}</td>
                <td className="mono">{a.verb}</td>
                <td style={{ color: STATE_COLOR[a.state] ?? 'var(--text)' }}>{a.state}</td>
                <td className="truncate" style={{ maxWidth: 360 }}>{a.intent}</td>
                <td className="muted">${a.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {actions.length === 0 && <div className="empty-state">No actions yet</div>}
      </div>
    </div>
  )
}

// ─── Memories ────────────────────────────────────────────────────────────────

function MemoriesTab() {
  const [memories, setMemories] = useState<Memory[]>([])
  const [limit, setLimit] = useState(50)
  const [filter, setFilter] = useState('')
  const [kindFilter, setKindFilter] = useState('all')
  const [injectText, setInjectText] = useState('')
  const [injectKind, setInjectKind] = useState('episodic')
  const [injectStatus, setInjectStatus] = useState('')

  async function load() {
    try {
      const data = await (await fetch(`/admin/memories?limit=${limit}`)).json()
      setMemories(data.memories)
    } catch {}
  }

  useEffect(() => { load() }, [limit])

  async function del(id: number) {
    await fetch(`/admin/memories/${id}`, { method: 'DELETE' })
    setMemories(prev => prev.filter(m => m.id !== id))
  }

  async function inject() {
    const text = injectText.trim()
    if (!text) return
    setInjectStatus('saving…')
    try {
      const res = await fetch('/admin/memories/inject', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, kind: injectKind }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      setInjectStatus(`✓ saved (id ${data.id})`)
      setInjectText('')
      load()
    } catch (err: any) {
      setInjectStatus(`✗ ${err.message}`)
    }
    setTimeout(() => setInjectStatus(''), 3000)
  }

  const filtered = memories.filter(m => {
    if (kindFilter !== 'all' && m.kind !== kindFilter) return false
    if (filter && !m.text.toLowerCase().includes(filter.toLowerCase())) return false
    return true
  })

  const kinds = ['all', 'episodic', 'semantic', 'autobiographical', 'procedural']

  return (
    <div className="tab-layout">
      {/* Inject form */}
      <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'flex-end' }}>
        <div style={{ flex: 1 }}>
          <label className="field-label">NEW MEMORY</label>
          <input
            value={injectText}
            onChange={e => setInjectText(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && inject()}
            placeholder="Write a memory to inject…"
          />
        </div>
        <div>
          <label className="field-label">KIND</label>
          <select value={injectKind} onChange={e => setInjectKind(e.target.value)} style={{ width: 140 }}>
            <option value="episodic">episodic</option>
            <option value="semantic">semantic</option>
            <option value="autobiographical">autobiographical</option>
            <option value="procedural">procedural</option>
          </select>
        </div>
        <button className="primary" onClick={inject} disabled={!injectText.trim()}>Inject</button>
        {injectStatus && (
          <span className="sm" style={{ color: injectStatus.startsWith('✓') ? 'var(--green)' : 'var(--danger)', whiteSpace: 'nowrap' }}>
            {injectStatus}
          </span>
        )}
      </div>

      {/* Filters */}
      <div style={{ padding: '10px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'center' }}>
        <input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Search text…" style={{ width: 220 }} />
        <div style={{ display: 'flex', gap: 4 }}>
          {kinds.map(k => (
            <button key={k} onClick={() => setKindFilter(k)}
              style={{
                padding: '3px 10px', fontSize: 11,
                background: kindFilter === k ? (KIND_COLORS[k] ?? 'var(--accent)') : 'var(--surface2)',
                color: kindFilter === k ? '#0d0d0f' : 'var(--muted)',
                borderColor: kindFilter === k ? 'transparent' : 'var(--border)',
              }}>
              {k}
            </button>
          ))}
        </div>
        <span className="muted sm" style={{ marginLeft: 'auto' }}>{filtered.length} / {memories.length}</span>
        <select value={limit} onChange={e => setLimit(Number(e.target.value))} style={{ width: 80 }}>
          {[20, 50, 100, 200].map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <button onClick={load} style={{ padding: '4px 10px', fontSize: 11 }}>Refresh</button>
      </div>

      {/* Table */}
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: 48 }}>ID</th>
              <th style={{ width: 100 }}>Kind</th>
              <th>Text</th>
              <th style={{ width: 64 }}>Weight</th>
              <th style={{ width: 96 }}>Source</th>
              <th style={{ width: 90 }}>Created</th>
              <th style={{ width: 44 }}></th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(m => (
              <tr key={m.id}>
                <td className="muted mono">{m.id}</td>
                <td>
                  <span style={{
                    fontSize: 11, padding: '2px 7px', borderRadius: 4,
                    background: `${KIND_COLORS[m.kind] ?? '#888'}22`,
                    color: KIND_COLORS[m.kind] ?? 'var(--muted)',
                  }}>
                    {m.kind}
                  </span>
                </td>
                <td style={{ maxWidth: 0 }}>
                  <div className="truncate">{m.text}</div>
                </td>
                <td className="muted">{m.weight?.toFixed(2)}</td>
                <td className="muted truncate" style={{ maxWidth: 96, fontSize: 11 }}>{m.source}</td>
                <td className="muted" style={{ fontSize: 11, whiteSpace: 'nowrap' }}>
                  {m.created_at ? new Date(m.created_at).toLocaleDateString() : ''}
                </td>
                <td>
                  <button className="danger" style={{ padding: '2px 6px', fontSize: 11 }} onClick={() => del(m.id)}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && <div className="empty-state">No memories</div>}
      </div>
    </div>
  )
}

// ─── Admin ───────────────────────────────────────────────────────────────────

function AdminTab() {
  const [cache, setCache] = useState<any>(null)
  const [, setHaAllow] = useState<string[]>([])
  const [, setHaBlock] = useState<string[]>([])
  const [haAllowInput, setHaAllowInput] = useState('')
  const [haBlockInput, setHaBlockInput] = useState('')
  const [saving, setSaving] = useState(false)

  async function loadCache() {
    try { setCache(await (await fetch('/admin/cache/status')).json()) } catch {}
  }

  async function loadHA() {
    try {
      const [al, bl] = await Promise.all([
        (await fetch('/admin/ha/allowlist')).json(),
        (await fetch('/admin/ha/blocklist')).json(),
      ])
      setHaAllow(al.entities)
      setHaBlock(bl.entities)
      setHaAllowInput(al.entities.join('\n'))
      setHaBlockInput(bl.entities.join('\n'))
    } catch {}
  }

  async function resetCache() {
    await fetch('/admin/cache/reset', { method: 'POST' })
    loadCache()
  }

  async function saveHA() {
    setSaving(true)
    try {
      await Promise.all([
        fetch('/admin/ha/allowlist', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entities: haAllowInput.split('\n').map(s => s.trim()).filter(Boolean) }),
        }),
        fetch('/admin/ha/blocklist', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ entities: haBlockInput.split('\n').map(s => s.trim()).filter(Boolean) }),
        }),
      ])
      loadHA()
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => { loadCache(); loadHA() }, [])

  return (
    <div className="tab-layout" style={{ overflowY: 'auto', padding: '20px 24px', display: 'block' }}>
      <div style={{ display: 'grid', gap: 20, gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))' }}>

        {/* Cache */}
        <div className="card">
          <div className="section-title">Cache</div>
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 10 }}>
            {cache ? (cache.active ? `Active: ${cache.cache_name}` : 'No active cache') : 'Loading…'}
          </div>
          <button onClick={resetCache}>Reset cache</button>
        </div>

        {/* OAuth */}
        <div className="card">
          <div className="section-title">OAuth</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <a href="/admin/oauth/google/start"><button style={{ width: '100%' }}>Connect Google</button></a>
            <a href="/admin/oauth/spotify/start"><button style={{ width: '100%' }}>Connect Spotify</button></a>
          </div>
        </div>

        {/* HomeAssistant */}
        <div className="card" style={{ gridColumn: 'span 2' }}>
          <div className="section-title">HomeAssistant</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
            <div>
              <label className="field-label">ALLOWLIST (one entity per line)</label>
              <textarea
                value={haAllowInput}
                onChange={e => setHaAllowInput(e.target.value)}
                rows={6}
                style={{ fontFamily: 'var(--mono)', fontSize: 12 }}
                placeholder="light.living_room&#10;switch.fan"
              />
            </div>
            <div>
              <label className="field-label">BLOCKLIST (one entity per line)</label>
              <textarea
                value={haBlockInput}
                onChange={e => setHaBlockInput(e.target.value)}
                rows={6}
                style={{ fontFamily: 'var(--mono)', fontSize: 12 }}
                placeholder="sensor.private"
              />
            </div>
          </div>
          <button className="primary" onClick={saveHA} disabled={saving} style={{ marginTop: 12 }}>
            {saving ? 'Saving…' : 'Save HA prefs'}
          </button>
        </div>

        {/* Links */}
        <div className="card">
          <div className="section-title">Raw endpoints</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {[
              ['/admin/audit', 'Audit JSON'],
              ['/admin/audit/ui', 'Audit UI'],
              ['/metrics', 'Prometheus'],
            ].map(([href, label]) => (
              <a key={href} href={href} target="_blank" rel="noreferrer"
                style={{ fontSize: 12, color: 'var(--accent)', fontFamily: 'var(--mono)' }}>
                {label} ↗
              </a>
            ))}
          </div>
        </div>

      </div>
    </div>
  )
}
