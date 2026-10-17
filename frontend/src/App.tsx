import { useState } from 'react'
import type { Board, Health } from './api'
import { api, API_BASE, useLiveData, useRoute } from './api'
import { ForecastPage } from './pages/Forecast'
import { NetworkPage } from './pages/Network'
import { presetsFor } from './devPresets'
import { MeetingDoc, MeetingsPage } from './pages/Meetings'
import { TasksPage } from './pages/Tasks'
import { ActivityPage, IntegrationsPage, TeamPage } from './pages/Workspace'
import { motion, MotionConfig } from 'motion/react'
import CountUp from './components/reactbits/CountUp'
import { Logo } from './components/Logo'
import { Empty } from './ui'

const NAV: [string, string, string][] = [
  ['tasks', '', 'Project tasks'],
  ['forecast', '', 'Forecast & agent'],
  ['graph', '', 'Graph database'],
  ['meetings', '', 'Meetings'],
  ['activity', '', 'Activity'],
  ['team', '', 'Team'],
  ['integrations', '', 'Connections'],
]

export default function App() {
  const { board, activity, health, live, tick } = useLiveData()
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
    body = API_BASE
      ? <Empty>Connecting to Saarathi… If the server was asleep it can take up to a minute to wake up.</Empty>
      : <Empty>Connecting to Saarathi on localhost:8000… Start it with <code>uvicorn app.main:app --reload</code>.</Empty>
  } else if (page === 'meetings' && route[1]) {
    body = <MeetingDoc id={Number(route[1])} tick={tick} go={go} />
  } else if (page === 'meetings') {
    body = <MeetingsPage health={health} tick={tick} go={go} />
  } else if (page === 'graph') {
    body = <NetworkPage tick={tick} />
  } else if (page === 'forecast') {
    body = <ForecastPage tick={tick} />
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
    <MotionConfig reducedMotion="user">
    <div className={`shell ${navOpen ? 'nav-open' : ''}`}>
      <aside className="sidebar" aria-label="Workspace">
        <a className="workspace" href="/" title="Back to the Saarathi home page">
          <span className="ws-name"><Logo size={26} />Saarathi</span>
          <span className="ws-project">Group project</span>
        </a>
        <nav>
          {NAV.map(([key, , label]) => (
            <a key={key} href={`#/${key}`} className={`nav-item ${current[0] === key ? 'on' : ''}`} onClick={() => setNavOpen(false)}>
              {current[0] === key && <motion.span layoutId="nav-active" className="nav-indicator" aria-hidden="true"
                transition={{ type: 'spring', stiffness: 520, damping: 42 }} />}
              <span className="nav-label">{label}</span>            </a>
          ))}
        </nav>
        {board && <Pulse board={board} />}
        {board && health?.env !== 'prod' && <DevPanel board={board} />}
      </aside>

      <div className="main">
        <div className="topbar">
          <button className="btn-text menu" onClick={() => setNavOpen(!navOpen)} aria-label="Menu">Menu</button>
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
        <main className="page">
          <motion.div key={route.slice(0, 2).join('/')} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.36, ease: [0.2, 0.7, 0.2, 1] }}>
            {body}
          </motion.div>
        </main>
      </div>
      {toast && <motion.div className="toast" role="status" initial={{ opacity: 0, y: 12, x: '-50%' }} animate={{ opacity: 1, y: 0, x: '-50%' }}>{toast}</motion.div>}
    </div>
    </MotionConfig>
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
        <CountUp to={Math.round(((c['Complete'] ?? 0) / total) * 100)} duration={1.2} trigger="load" suffix="% done" />
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
