import { useEffect, useState } from 'react'
import type { Appointment, Health, Meeting } from '../api'
import { api } from '../api'
import { Callout, Empty, fmtDay, mmss, PageHeader, Person, Tag } from '../ui'

const STATUS_TAG: Record<Meeting['status'], [string, string]> = {
  created: ['gray', 'Created'],
  joining: ['yellow', 'Joining call'],
  in_call: ['blue', 'Listening'],
  processing: ['purple', 'Summarising'],
  done: ['green', 'Summary ready'],
  failed: ['red', 'Failed'],
}

export function MeetingsPage({ health, tick, go }: { health: Health | null; tick: number; go: (p: string) => void }) {
  const [rows, setRows] = useState<Meeting[]>([])
  const [upcoming, setUpcoming] = useState<Appointment[]>([])
  const [url, setUrl] = useState('')
  const [title, setTitle] = useState('Team sync')
  const [file, setFile] = useState<File | null>(null)
  const [paste, setPaste] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { api.meetings().then(setRows).catch(() => setRows([])) }, [tick])
  useEffect(() => { api.appointments().then(setUpcoming).catch(() => setUpcoming([])) }, [tick])

  const run = async (fn: () => Promise<Meeting>) => {
    setErr(null)
    setBusy(true)
    try {
      const m = await fn()
      go(`meetings/${m.id}`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader icon="🎙️" title="Meetings"
        description="Send the agent into a Teams call. It listens, picks out updates, blockers and decisions, then posts a summary to Slack." />

      <section className="block">
        <h3 className="block-title">Upcoming team meetings</h3>
        <p className="muted small">Announced in the group chat (“group meeting in 20 minutes”). The agent reminds everyone shortly before the start.</p>
        {upcoming.length === 0 && <p className="muted small">None scheduled.</p>}
        {upcoming.map((a) => (
          <div key={a.id} className="form-row" data-testid="appointment">
            <strong>{a.title}</strong>
            <span className="muted small">{new Date(a.starts_at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</span>
            <span className="muted small">{a.attendees.join(', ')}</span>
            {a.status === 'scheduled' && (
              <button className="btn-secondary" onClick={() => api.cancelAppointment(a.id).then(() => api.appointments().then(setUpcoming))}>Cancel</button>
            )}
            {a.status === 'done' && <Tag color="green">Done</Tag>}
          </div>
        ))}
      </section>

      <section className="block">
        <h3 className="block-title">Invite the agent to a call</h3>
        <div className="form-row">
          <input className="input grow" placeholder="Paste a Teams, Zoom or Google Meet link" value={url}
            onChange={(e) => setUrl(e.target.value)} aria-label="Meeting link" />
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Meeting name" />
          <button className="btn-primary" disabled={!url || busy || !health?.recall}
            onClick={() => run(() => api.joinMeeting(url, title))}>Send agent</button>
        </div>
        {!health?.recall && (
          <p className="muted small">Joining calls needs a Recall.ai key (RECALL_API_KEY). You can upload a transcript below meanwhile.</p>
        )}
      </section>

      <section className="block">
        <h3 className="block-title">Or upload a transcript</h3>
        <p className="muted small">In Teams, open the meeting recap and choose Download transcript (.vtt). Plain “Name: text” lines work too.</p>
        <div className="form-row">
          <input type="file" accept=".vtt,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Transcript file" />
          <button className="btn-secondary" disabled={(!file && !paste.trim()) || busy}
            onClick={() => run(() => api.uploadTranscript(title, file, paste))}>Summarise transcript</button>
        </div>
        <textarea className="input area" rows={3} placeholder={'Name: The API is connected, but I am waiting for test credentials.\nName: I\'ll send them tonight.'}
          value={paste} onChange={(e) => setPaste(e.target.value)} aria-label="Paste transcript" />
      </section>
      {err && <Callout icon="⚠️" color="red">{err}</Callout>}

      <div className="table-wrap">
        <table className="db-table">
          <thead><tr><th>Meeting</th><th>Status</th><th>Date</th><th>Lines</th><th>Flagged</th><th>Slack</th></tr></thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m.id} onClick={() => go(`meetings/${m.id}`)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && go(`meetings/${m.id}`)}>
                <td className="cell-title">{m.title}</td>
                <td><Tag color={STATUS_TAG[m.status][0]}>{STATUS_TAG[m.status][1]}</Tag></td>
                <td className="muted">{fmtDay(m.created_at)}</td>
                <td>{m.stats.utterances ?? '—'}</td>
                <td>{m.stats.flagged ?? '—'}</td>
                <td>{m.slack_ts ? <Tag color="green">Posted</Tag> : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <Empty>No meetings yet. Invite the agent to your next call, or upload last call's transcript.</Empty>}
      </div>
    </>
  )
}

/** The agent's write-up, laid out like a Notion meeting-notes page. */
export function MeetingDoc({ id, tick, go }: { id: number; tick: number; go: (p: string) => void }) {
  const [m, setM] = useState<Meeting | null>(null)
  const [showAll, setShowAll] = useState(false)
  useEffect(() => { api.meeting(id).then(setM).catch(() => setM(null)) }, [id, tick])
  if (!m) return <Empty>Loading meeting…</Empty>

  const s = m.summary
  const utts = m.utterances ?? []
  const visible = showAll ? utts : utts.filter((u) => u.flagged)
  const flagTags = (u: (typeof utts)[number]) =>
    (['blocker', 'update', 'commitment', 'decision'] as const)
      .filter((k) => (u.flags[k] ?? 0) >= 0.5)
      .map((k) => <Tag key={k} color={{ blocker: 'red', update: 'blue', commitment: 'yellow', decision: 'purple' }[k]}>{k}</Tag>)

  return (
    <article className="doc">
      <button className="btn-text small" onClick={() => go('meetings')}>← Meetings</button>
      <PageHeader icon="📄" title={m.title}>
        <dl className="props">
          <dt>Status</dt><dd><Tag color={STATUS_TAG[m.status][0]}>{STATUS_TAG[m.status][1]}</Tag></dd>
          <dt>Date</dt><dd>{fmtDay(m.created_at)}</dd>
          <dt>Source</dt><dd>{m.provider === 'recall' ? 'Agent joined the call' : 'Uploaded transcript'}</dd>
          {m.stats.utterances != null && (<>
            <dt>Triage</dt>
            <dd>{m.stats.flagged} of {m.stats.utterances} lines mattered, picked by {m.stats.triage_engine === 'jev' ? 'Jev' : 'rules'}; summary by {m.stats.summary_engine === 'llm' ? 'the LLM' : 'rules'}</dd>
          </>)}
          <dt>Slack</dt><dd>{m.slack_ts ? 'Posted to the project channel' : <span className="muted">Not posted</span>}</dd>
        </dl>
      </PageHeader>

      {(m.status === 'joining' || m.status === 'in_call') && (
        <Callout icon="🎧" color="blue">
          The agent is {m.status === 'joining' ? 'joining' : 'in'} the call. Lines appear below as people speak.
          <div><button className="btn-secondary small" onClick={() => api.finishMeeting(m.id)}>End and summarise now</button></div>
        </Callout>
      )}
      {m.status === 'processing' && <Callout icon="⏳" color="purple">Writing the summary…</Callout>}
      {m.status === 'failed' && <Callout icon="⚠️" color="red">{m.error}</Callout>}

      {s && (<>
        <Callout icon="💡" color="yellow"><b>{s.headline}</b></Callout>
        {s.summary.length > 0 && <ul className="doc-list">{s.summary.map((b, i) => <li key={i}>{b}</li>)}</ul>}

        <h2 className="doc-h">Decisions</h2>
        {s.decisions.length ? <ul className="doc-list">{s.decisions.map((d, i) => <li key={i}>{d}</li>)}</ul>
          : <Empty>No decisions recorded.</Empty>}

        <h2 className="doc-h">Action items</h2>
        {s.action_items.length ? (
          <ul className="todo">
            {s.action_items.map((a, i) => (
              <li key={i}>
                <input type="checkbox" aria-label={a.what} />
                <span className="mention">@{a.owner}</span> {a.what}
                {a.due && <span className="muted small"> due {a.due}</span>}
              </li>
            ))}
          </ul>
        ) : <Empty>No action items.</Empty>}

        {s.blockers.length > 0 && (<>
          <h2 className="doc-h">Blockers</h2>
          <ul className="doc-list">{s.blockers.map((b, i) => <li key={i}><Tag color="red">blocked</Tag> {b.text}</li>)}</ul>
        </>)}
        {s.risks.length > 0 && (<>
          <h2 className="doc-h">Risks</h2>
          <ul className="doc-list">{s.risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </>)}
        {(m.stats.reviews ?? 0) > 0 && (
          <Callout icon="🙋" color="gray">
            {m.stats.reviews} task update{m.stats.reviews === 1 ? '' : 's'} heard in this meeting {m.stats.reviews === 1 ? 'is' : 'are'} waiting for the lead, who was asked to confirm in chat.
          </Callout>
        )}
      </>)}

      <details className="toggle doc-toggle" open={!s}>
        <summary>Transcript ({utts.length} lines{utts.some((u) => u.flagged) ? `, ${utts.filter((u) => u.flagged).length} flagged` : ''})</summary>
        <label className="muted small check"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Show every line, not just flagged ones</label>
        <ol className="transcript">
          {visible.map((u) => (
            <li key={u.id} className={u.flagged ? 'flagged' : ''}>
              <span className="ts">{mmss(u.start_s)}</span>
              <Person name={u.member ?? u.speaker} size={18} />
              <span className="line">{u.text}</span>
              <span className="line-tags">{flagTags(u)}{u.task_id && <Tag color="gray">{u.task_id}</Tag>}</span>
            </li>
          ))}
          {visible.length === 0 && <li className="muted">Nothing flagged yet.</li>}
        </ol>
      </details>
    </article>
  )
}
