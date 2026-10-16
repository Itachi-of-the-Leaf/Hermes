import { useState } from 'react'
import type { Board, Health } from './api'
import { api, useLiveData, useRoute } from './api'
import { ForecastPage } from './pages/Forecast'
import { NetworkPage } from './pages/Network'
import { presetsFor } from './devPresets'
import { MeetingDoc, MeetingsPage } from './pages/Meetings'
import { TasksPage } from './pages/Tasks'
import { ActivityPage, IntegrationsPage, ReviewsPage, TeamPage } from './pages/Workspace'
import { Empty } from './ui'

const NAV: [string, string, string][] = [
  ['tasks', '✅', 'Project tasks'],
  ['forecast', '📈', 'Forecast & agent'],
  ['graph', '🕸️', 'Graph database'],
  ['meetings', '🎙️', 'Meetings'],
  ['reviews', '🙋', 'Needs your call'],
  ['activity', '🕘', 'Activity'],
  ['team', '👥', 'Team'],
  ['integrations', '🔌', 'Connections'],
]

export default function App() {
  const { board, activity, reviews, health, live, tick } = useLiveData()
  const [route, go] = useRoute()
  const [toast, setToast] = useState<string | null>(null)
  const [navOpen, setNavOpen] = useState(false)
  const say = (m: string) => {
    setToast(m)
    window.setTimeout(() => setToast(null), 2600)
  }

  const page = route[0]
  const current = NAV.find((n) => n[0] === page) ?? NAV[0]

  let body
  if (!board) {
    body = <Empty>Connecting to the coordinator on localhost:8000… Start it with <code>uvicorn app.main:app --reload</code>.</Empty>
  } else if (page === 'meetings' && route[1]) {
    body = <MeetingDoc id={Number(route[1])} tick={tick} go={go} />
  } else if (page === 'meetings') {
    body = <MeetingsPage health={health} tick={tick} go={go} />
  } else if (page === 'graph') {
    body = <NetworkPage tick={tick} />
  } else if (page === 'forecast') {
    body = <ForecastPage tick={tick} />
  } else if (page === 'reviews') {
    body = <ReviewsPage reviews={reviews} />
  } else if (page === 'activity') {
    body = <ActivityPage items={activity} />
  } else if (page === 'team') {
    body = <TeamPage tick={tick} />
  } else if (page === 'integrations') {
    body = <IntegrationsPage health={health} />
  } else {
    body = <TasksPage board={board} tick={tick} />
  }

  return (
    <div className={`shell ${navOpen ? 'nav-open' : ''}`}>
      <aside className="sidebar" aria-label="Workspace">
        <div className="workspace">
          <span className="ws-icon" aria-hidden>G</span>
          <span className="ws-name">Group project</span>
        </div>
        <nav>
          {NAV.map(([key, icon, label]) => (
            <a key={key} href={`#/${key}`} className={`nav-item ${current[0] === key ? 'on' : ''}`} onClick={() => setNavOpen(false)}>
              <span aria-hidden>{icon}</span>
              <span className="nav-label">{label}</span>
              {key === 'reviews' && reviews.length > 0 && <span className="badge">{reviews.length}</span>}
            </a>
          ))}
        </nav>
        {board && <Pulse board={board} />}
        {board && health?.env !== 'prod' && <DevPanel board={board} />}
      </aside>

      <div className="main">
        <div className="topbar">
          <button className="btn-text menu" onClick={() => setNavOpen(!navOpen)} aria-label="Menu">☰</button>
          <span className="crumbs"><span className="hide-sm">Group project / </span><b>{current[2]}</b></span>
          <span className={`live ${live ? 'on' : ''}`} title={live ? 'Live updates on' : 'Reconnecting'}>
            <span className="dot" />{live ? 'Live' : 'Reconnecting'}
          </span>
          <Agents health={health} />
          {health?.env !== 'prod' && (
            <button className="btn-text hide-sm" onClick={async () => {
              if (window.confirm('Reset tasks and clear history? Connected apps stay linked.')) { await api.reset(); say('Demo reset') }
            }}>Reset</button>
          )}
          <button className="btn-primary" onClick={async () => say(`Sent ${(await api.checkin()).sent} check-ins`)}>
            Send check-ins
          </button>
        </div>
        <main className="page">{body}</main>
      </div>
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  )
}

/** Small status of the agent's brains in the top bar. */
function Agents({ health }: { health: Health | null }) {
  if (!health) return null
  const apps = (['telegram', 'slack', 'teams'] as const).filter((c) => health[c])
  return (
    <span className="agents muted small" title="Connected apps and models">
      {apps.length ? `${apps.map((a) => a[0].toUpperCase() + a.slice(1)).join(', ')} connected` : 'No apps connected'}
      {', using '}{[health.jev && 'Jev', health.llm && 'an LLM'].filter(Boolean).join(' and ') || 'rules only'}
    </span>
  )
}

/** Sidebar pulse: the project's status in one glance. */
function Pulse({ board }: { board: Board }) {
  const c = board.counts
  const total = board.tasks.length || 1
  const seg = (n: number, cls: string) => n > 0 && <span className={`pulse-seg ${cls}`} style={{ flex: n }} />
  return (
    <div className="pulse" aria-label="Project status">
      <div className="pulse-bar">
        {seg(c['Complete'] ?? 0, 'green')}{seg(c['In Progress'] ?? 0, 'blue')}{seg(c['Blocked'] ?? 0, 'red')}{seg(c['Pending'] ?? 0, 'gray')}
      </div>
      <div className="small muted">
        {Math.round(((c['Complete'] ?? 0) / total) * 100)}% done
        {(c['Blocked'] ?? 0) > 0 && `, ${c['Blocked']} blocked`}
        {(c['At Risk'] ?? 0) > 0 && `, ${c['At Risk']} at risk`}
      </div>
    </div>
  )
}

/** Development only: talk as a team member without phones. Live demos must use real apps. */
function DevPanel({ board }: { board: Board }) {
  const [member, setMember] = useState(board.members[0]?.key ?? '')
  const presets = presetsFor(board)
  const nameOf = (key: string) => board.members.find((m) => m.key === key)?.name ?? key
  const [text, setText] = useState('')
  const [group, setGroup] = useState(false)
  const send = async (m = member, t = text, g = group) => {
    if (!t.trim()) return
    await api.devMessage(m, t, g)
    setText('')
  }
  return (
    <details className="dev">
      <summary>Test without apps</summary>
      <div className="dev-row">
        <select className="input small" value={member} onChange={(e) => setMember(e.target.value)} aria-label="Send as">
          {board.members.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
        </select>
        <label className="small muted check"><input type="checkbox" checked={group} onChange={(e) => setGroup(e.target.checked)} /> group</label>
      </div>
      <textarea className="input area small" rows={2} value={text} placeholder="Type a reply"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
      {presets.map(([m, t, g], i) => (
        <button key={i} className="btn-text small preset" onClick={() => send(m, t, g)}>{nameOf(m)}: {t.slice(0, 26)}…</button>
      ))}
    </details>
  )
}
