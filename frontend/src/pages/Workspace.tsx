import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import type { Activity, ChannelName, Health, TeamMember } from '../api'
import { api } from '../api'
import { CHANNEL_LABEL, EngineTag, Empty, fmtDay, fmtTime, PageHeader, Person, plain, Tag } from '../ui'

/* ---------------------------------------------------------------- activity */

const FILTERS: [string, string, (a: Activity) => boolean][] = [
  ['all', 'All', () => true],
  ['messages', 'Messages', (a) => a.type === 'message' && a.direction !== 'out'],
  ['changes', 'Status changes', (a) => a.type === 'event'],
  ['agent', 'From the agent', (a) => a.type === 'message' && a.direction === 'out'],
]
const rowKey = (a: Activity) => `${a.type}|${a.at}|${a.who ?? a.task ?? ''}|${a.text.slice(0, 48)}`

export function ActivityPage({ items }: { items: Activity[] }) {
  const [filter, setFilter] = useState('all')
  const shown = items.filter(FILTERS.find((f) => f[0] === filter)![2])
  let lastDay = ''
  const seen = new Map<string, number>()
  const keyOf = (a: Activity) => {
    const base = rowKey(a), n = seen.get(base) ?? 0
    seen.set(base, n + 1)
    return n ? `${base}#${n}` : base
  }
  return (
    <>
      <PageHeader icon="🕘" title="Activity" description="Every message the agent sent or received, and every change it made." />
      <div className="views" role="tablist" aria-label="Filter activity">
        {FILTERS.map(([key, label, test]) => (
          <button key={key} role="tab" aria-selected={filter === key} className={filter === key ? 'on' : ''} onClick={() => setFilter(key)}>
            {label} <span className="views-n">{items.filter(test).length}</span>
          </button>
        ))}
        <span className="views-count"><span className="live-dot" aria-hidden="true" />updates live</span>
      </div>
      <ol className="feed">
        <AnimatePresence initial={false}>
        {shown.map((a) => {
          const day = fmtDay(a.at)
          const header = day !== lastDay ? (lastDay = day) : null
          const kind = a.type === 'event' ? 'change' : a.direction === 'out' ? 'agent' : 'message'
          return (
            <motion.li key={keyOf(a)} layout="position" className={`feed-row k-${kind} ${a.kind === 'conflict' ? 'conflict' : ''}`}
              initial={{ opacity: 0, x: -14, backgroundColor: 'rgba(34, 57, 111, 0.10)' }}
              animate={{ opacity: 1, x: 0, backgroundColor: 'rgba(34, 57, 111, 0)' }} exit={{ opacity: 0 }}
              transition={{ duration: 0.45, ease: [0.2, 0.7, 0.2, 1], backgroundColor: { duration: 1.6 } }}>
              {header && <div className="feed-day">{header}</div>}
              <span className="muted small ts">{fmtTime(a.at)}</span>
              {a.type === 'event' ? (
                <span><b>{a.task}</b> {a.text} {a.engine && <EngineTag engine={a.engine} confidence={a.confidence} />}{a.reverted && <span className="muted"> (undone)</span>}</span>
              ) : (
                <span>
                  <b>{a.direction === 'out' ? `Saarathi to ${a.who}` : a.who}</b>{' '}
                  {a.kind === 'conflict' && <Tag color="orange">Belief conflict</Tag>}{' '}
                  {a.kind === 'meeting' && <Tag color="purple">Meeting</Tag>}{' '}
                  <span className="muted">{plain(a.text)}</span>
                </span>
              )}
            </motion.li>
          )
        })}
        </AnimatePresence>
      </ol>
      {items.length === 0 && <Empty>Nothing yet. Send check-ins to get things moving.</Empty>}
      {items.length > 0 && shown.length === 0 && <Empty>Nothing of this kind yet.</Empty>}
    </>
  )
}

/* ---------------------------------------------------------------- connections */

export function IntegrationsPage({ health }: { health: Health | null }) {
  const h = health
  const rows: { icon: string; name: string; on: boolean; what: string; detail: string; setup: string }[] = [
    { icon: '✈️', name: 'Telegram', on: !!h?.telegram, what: 'Check-ins and replies by DM; conflicts in the group.',
      detail: h?.bot_username ? `@${h.bot_username}` : '', setup: 'Create a bot with @BotFather, set TELEGRAM_BOT_TOKEN.' },
    { icon: '💬', name: 'Slack', on: !!h?.slack, what: 'Check-ins by DM, conflicts in the project channel, meeting summaries.',
      detail: [h?.slack_project_channel ? 'Project channel set' : 'No project channel yet', h?.slack_summary_channel ? 'summaries channel set' : 'no summaries channel yet'].join(', '), setup: 'Install the app from deploy/slack-manifest.yaml, set SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET.' },
    { icon: '🕸️', name: 'Neo4j graph database', on: !!h?.neo4j, what: 'The project as a graph: people, tasks and who waits for whom (see the Graph page).',
      detail: '', setup: 'Run Neo4j (docker run -p 7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5) or create a free Aura instance, then set NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD.' },
    { icon: '🟪', name: 'Microsoft Teams', on: !!h?.teams, what: 'Check-ins in personal chat with Adaptive Card buttons.',
      detail: '', setup: 'Register an Azure Bot, set TEAMS_CLIENT_ID/SECRET/TENANT_ID, upload deploy/teams-manifest.' },
    { icon: '🎙️', name: 'Meeting agent (Recall.ai)', on: !!h?.recall, what: 'Joins Teams, Zoom or Meet calls and streams the transcript.',
      detail: '', setup: 'Set RECALL_API_KEY and a public PUBLIC_BASE_URL for its webhooks.' },
    { icon: '⚡', name: 'Jev', on: !!h?.jev, what: 'Fast typed decisions: what a reply means, which meeting lines matter.',
      detail: h?.jev_model ?? '', setup: 'Set OPENROUTER_API_KEY. Without it, rules decide.' },
    { icon: '🧠', name: 'LLM', on: !!h?.llm, what: 'Hard replies, blocker wording, meeting summaries.',
      detail: h?.llm_model ?? '', setup: 'Set LLM_API_KEY and LLM_MODEL (any OpenAI-compatible API).' },
  ]
  return (
    <>
      <PageHeader icon="🔌" title="Connections" description="Where the agent talks to people, and the models it uses to understand them." />
      <div className="connections">
        {rows.map((r) => (
          <div key={r.name} className="connection">
            <div className="conn-body">
              <div className="conn-name">{r.name} <Tag color={r.on ? 'green' : 'gray'}>{r.on ? 'Connected' : 'Not connected'}</Tag>
                {r.detail && <span className="muted small"> {r.detail}</span>}</div>
              <div className="muted small">{r.what}</div>
              {!r.on && <div className="small conn-setup">{r.setup}</div>}
            </div>
          </div>
        ))}
      </div>
    </>
  )
}

/* ---------------------------------------------------------------- team */

export function TeamPage({ tick }: { tick: number }) {
  const [rows, setRows] = useState<TeamMember[]>([])
  useEffect(() => { api.team().then(setRows).catch(() => setRows([])) }, [tick])
  return (
    <>
      <PageHeader icon="👥" title="Team"
        description="Slack and Teams accounts link automatically by email. Telegram users join with their invite link." />
      <div className="table-wrap">
        <table className="db-table">
          <thead><tr><th>Name</th><th>Role</th><th>Email</th><th>Connected apps</th><th>Reach them on</th><th>Telegram invite</th></tr></thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.key}>
                <td><Person name={m.name} /></td>
                <td>{m.role === 'lead' ? <Tag color="purple">Lead</Tag> : <span className="muted">Member</span>}</td>
                <td className="muted">{m.email ?? '—'}</td>
                <td>{m.channels.length ? m.channels.map((c) => <Tag key={c}>{CHANNEL_LABEL[c]}</Tag>) : <span className="muted">None yet</span>}</td>
                <td>
                  {m.channels.length > 1 ? (
                    <select className="input small" value={m.preferred ?? ''} aria-label={`Preferred app for ${m.name}`}
                      onChange={(e) => api.setPreferred(m.key, e.target.value as ChannelName)}>
                      {m.channels.map((c) => <option key={c} value={c}>{CHANNEL_LABEL[c]}</option>)}
                    </select>
                  ) : m.preferred ? CHANNEL_LABEL[m.preferred] : <span className="muted">—</span>}
                </td>
                <td>{m.telegram_link && !m.channels.includes('telegram')
                  ? <a href={m.telegram_link} target="_blank" rel="noreferrer">Copy link</a> : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}
