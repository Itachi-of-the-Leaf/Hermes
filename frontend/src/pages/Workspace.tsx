import { useEffect, useState } from 'react'
import type { Activity, ChannelName, Health, Review, Status, TeamMember } from '../api'
import { api } from '../api'
import { Callout, CHANNEL_ICON, CHANNEL_LABEL, EngineTag, Empty, fmtDay, fmtTime, PageHeader, Person, StatusTag, Tag } from '../ui'

/* ---------------------------------------------------------------- reviews */

export function ReviewsPage({ reviews }: { reviews: Review[] }) {
  return (
    <>
      <PageHeader icon="🙋" title="Needs your call"
        description="Updates the agent wasn't sure about, or heard in a meeting. Nothing changes on the board until you choose." />
      {reviews.length === 0 && <Empty>All clear. Uncertain updates will show up here.</Empty>}
      <div className="reviews">
        {reviews.map((r) => (
          <article key={r.id} className="review">
            <div className="review-who">
              <Person name={r.member ?? '?'} />
              <span className="muted small">on <b className="ink">{r.task}</b> {r.source === 'meeting' ? 'in a meeting' : 'in chat'}, {fmtTime(r.at)}</span>
            </div>
            {r.text && <blockquote className="quote">{r.text.replace(/^\[[^\]]*\]\s*/, '')}</blockquote>}
            <div className="review-guess">
              <span className="muted small">Agent's guess</span>
              <StatusTag status={r.proposal.status} />
              <EngineTag engine={r.proposal.engine} confidence={r.proposal.confidence} />
            </div>
            <p className="muted small">{r.reasons.join('; ')}</p>
            <div className="review-actions">
              <button className="btn-primary small" onClick={() => api.resolveReview(r.id, 'approve')}>Approve {r.proposal.status}</button>
              {(['In Progress', 'Blocked', 'Complete'] as Status[]).filter((s) => s !== r.proposal.status).map((s) => (
                <button key={s} className="btn-secondary small" onClick={() => api.resolveReview(r.id, s)}>Mark {s}</button>
              ))}
            </div>
          </article>
        ))}
      </div>
    </>
  )
}

/* ---------------------------------------------------------------- activity */

export function ActivityPage({ items }: { items: Activity[] }) {
  let lastDay = ''
  return (
    <>
      <PageHeader icon="🕘" title="Activity" description="Every message the agent sent or received, and every change it made." />
      <ol className="feed">
        {items.map((a, i) => {
          const day = fmtDay(a.at)
          const header = day !== lastDay ? (lastDay = day) : null
          return (
            <li key={i} className={`feed-row ${a.kind === 'conflict' ? 'conflict' : ''}`}>
              {header && <div className="feed-day">{header}</div>}
              <span className="muted small ts">{fmtTime(a.at)}</span>
              {a.type === 'event' ? (
                <span><b>{a.task}</b> {a.text} {a.engine && <EngineTag engine={a.engine} confidence={a.confidence} />}{a.reverted && <span className="muted"> (undone)</span>}</span>
              ) : (
                <span>
                  <b>{a.direction === 'out' ? `Coordinator to ${a.who}` : a.who}</b>{' '}
                  {a.kind === 'conflict' && <Tag color="orange">Belief conflict</Tag>}{' '}
                  {a.kind === 'meeting' && <Tag color="purple">Meeting</Tag>}{' '}
                  <span className="muted">{a.text}</span>
                </span>
              )}
            </li>
          )
        })}
      </ol>
      {items.length === 0 && <Empty>Nothing yet. Send check-ins to get things moving.</Empty>}
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
            <span className="conn-icon" aria-hidden>{r.icon}</span>
            <div className="conn-body">
              <div className="conn-name">{r.name} <Tag color={r.on ? 'green' : 'gray'}>{r.on ? 'Connected' : 'Not connected'}</Tag>
                {r.detail && <span className="muted small"> {r.detail}</span>}</div>
              <div className="muted small">{r.what}</div>
              {!r.on && <div className="small conn-setup">{r.setup}</div>}
            </div>
          </div>
        ))}
      </div>
      {h && <Callout icon="ℹ️" color="gray">Running as <b>{h.role}</b> in <b>{h.env}</b>, mode <b>{h.mode}</b>.</Callout>}
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
                <td>{m.channels.length ? m.channels.map((c) => <Tag key={c}>{CHANNEL_ICON[c]} {CHANNEL_LABEL[c]}</Tag>) : <span className="muted">None yet</span>}</td>
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
