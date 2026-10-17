import { useEffect, useState } from 'react'
import type { Board, Task, TimelineItem } from '../api'
import { api, STATUSES } from '../api'
import CountUp from '../components/reactbits/CountUp'
import { Callout, Empty, EngineTag, fmtDate, fmtTime, PageHeader, Person, plain, StatusTag, STATUS_COLOR, Tag } from '../ui'

type View = 'board' | 'table'

export function TasksPage({ board, tick }: { board: Board; tick: number }) {
  const [view, setView] = useState<View>(() => (localStorage.getItem('view') as View) || 'board')
  const [openId, setOpenId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [created, setCreated] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)
  const open = board.tasks.find((t) => t.id === openId) ?? null
  const choose = (v: View) => {
    setView(v)
    try { localStorage.setItem('view', v) } catch { /* private mode */ }
  }
  const blocked = board.tasks.filter((t) => t.status === 'Blocked')

  return (
    <>
      <PageHeader icon="✅" title="Project tasks"
        description="The agent keeps these up to date from replies in Telegram, Slack, Teams and meetings.">
        <div className="review-actions">
          <button className="btn-primary small" onClick={() => { setAdding(!adding); setCreated(null) }}>{adding ? 'Cancel' : 'New task'}</button>
          <button className="btn-secondary small" disabled={linking || board.tasks.length < 2} title="Ask the agent to link the obvious prerequisites between tasks"
            onClick={async () => {
              setLinking(true)
              try {
                const r = await api.inferDependencies()
                setCreated(r.linked.length ? `Linked ${r.linked.length} dependenc${r.linked.length === 1 ? 'y' : 'ies'}: ${r.linked.join('; ')}` : 'No obvious dependencies are missing.')
              } catch (err) {
                setCreated(err instanceof Error ? err.message : 'Could not infer dependencies')
              } finally { setLinking(false) }
            }}>Link dependencies</button>
        </div>
      </PageHeader>
      {created && <Callout icon="✅" color="green">{created}</Callout>}
      {adding && <NewTaskForm board={board} onDone={(msg) => { setAdding(false); setCreated(msg) }} />}
      {blocked.length > 0 && (
        <Callout icon="⛔" color="red">
          {blocked.map((t) => (
            <div key={t.id}>
              <b>{t.title}</b> is blocked on {t.blocker_text}
              {t.blocked_by && !t.blocker_resolved && <>; {t.blocked_by} has been asked</>}
              {t.blocker_resolved && <>; marked sorted, waiting for {t.owner} to confirm</>}.
            </div>
          ))}
        </Callout>
      )}
      <div className="views" role="tablist">
        <button role="tab" aria-selected={view === 'board'} className={view === 'board' ? 'on' : ''} onClick={() => choose('board')}>
          Board
        </button>
        <button role="tab" aria-selected={view === 'table'} className={view === 'table' ? 'on' : ''} onClick={() => choose('table')}>
          Table
        </button>
        <span className="views-count">{board.tasks.length} tasks</span>
      </div>
      {board.tasks.length === 0 ? (
        <Empty>
          No tasks yet. Press <b>New task</b>, or tell the bot in Telegram or Slack, for example{' '}
          <code>task: Build the login page @{board.members[0]?.key ?? 'name'} due friday est 3h</code>. Everything after the title is optional.
        </Empty>
      ) : view === 'board' ? <BoardView board={board} onOpen={setOpenId} /> : <TableView board={board} onOpen={setOpenId} />}
      {open && <SidePeek task={open} board={board} tick={tick} onClose={() => setOpenId(null)} />}
    </>
  )
}

function NewTaskForm({ board, onDone }: { board: Board; onDone: (msg: string) => void }) {
  const [title, setTitle] = useState('')
  const [owner, setOwner] = useState(board.members[0]?.key ?? '')
  const [due, setDue] = useState('')
  const [hours, setHours] = useState('')
  const [deps, setDeps] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await api.createTask({
        title: title.trim(), owner_key: owner, deadline: due || undefined,
        est_minutes: hours ? Math.round(parseFloat(hours) * 60) : undefined, depends_on: deps,
      })
      onDone(`Created ${r.id} · ${r.title}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the task')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="newtask" onSubmit={submit} aria-label="New task">
      <label>Title
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Build the login page" autoFocus required />
      </label>
      <label>Owner
        <select className="input" value={owner} onChange={(e) => setOwner(e.target.value)}>
          {board.members.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
        </select>
      </label>
      <label>Due
        <input className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
      </label>
      <label>Estimate (hours)
        <input className="input" type="number" min="0.25" step="0.25" value={hours} onChange={(e) => setHours(e.target.value)} placeholder="optional" />
      </label>
      {board.tasks.length > 0 && (
        <fieldset className="newtask-deps"><legend>Waits for</legend>
          {board.tasks.map((t) => (
            <label key={t.id} className="check small">
              <input type="checkbox" checked={deps.includes(t.id)}
                onChange={(e) => setDeps(e.target.checked ? [...deps, t.id] : deps.filter((d) => d !== t.id))} /> {t.title}
            </label>
          ))}
        </fieldset>
      )}
      {error && <div className="small" role="alert" style={{ color: 'var(--red-fg)' }}>{error}</div>}
      <div><button className="btn-primary" type="submit" disabled={busy || title.trim().length < 3 || !owner}>Create task</button></div>
    </form>
  )
}

function BoardView({ board, onOpen }: { board: Board; onOpen: (id: string) => void }) {
  return (
    <div className="board">
      {STATUSES.map((st) => {
        const tasks = board.tasks.filter((t) => t.status === st)
        return (
          <section key={st} className="board-col" aria-label={st}>
            <h2 className="board-head">
              <StatusTag status={st} />
              <span className="count"><CountUp to={tasks.length} duration={0.9} trigger="load" /></span>
            </h2>
            {tasks.map((t) => <Card key={t.id} t={t} onOpen={() => onOpen(t.id)} />)}
          </section>
        )
      })}
    </div>
  )
}

function Card({ t, onOpen }: { t: Task; onOpen: () => void }) {
  const [flash, setFlash] = useState(false)
  const lastId = t.last_event?.id
  useEffect(() => {
    if (lastId == null) return
    setFlash(true)
    const id = window.setTimeout(() => setFlash(false), 1800)
    return () => window.clearTimeout(id)
  }, [lastId])

  return (
    <button className={`card ${flash ? 'flash' : ''}`} onClick={onOpen}>
      <span className="card-title">{t.title}</span>
      <span className="card-props">
        <Person name={t.owner} size={18} />
        {t.deadline && <span className="muted small">{fmtDate(t.deadline)}</span>}
      </span>
      {t.status === 'Blocked' && t.blocker_text && (
        <span className="card-props"><Tag color={t.blocker_resolved ? 'green' : 'red'}>
          {t.blocker_resolved ? 'Sorted: ' : 'Waiting on '}{t.blocker_text}
        </Tag></span>
      )}
      {t.at_risk && <span className="card-props"><Tag color="orange" title={t.at_risk}>At risk</Tag></span>}
      {t.progress_pct != null && t.status === 'In Progress' && (
        <span className="progress" aria-label={`${t.progress_pct}% done`}><span style={{ width: `${t.progress_pct}%` }} /></span>
      )}
      {t.last_event?.evidence && (
        <span className="card-quote">
          <EngineTag engine={t.last_event.engine} confidence={t.last_event.confidence} />
          <span>“{t.last_event.evidence}”</span>
        </span>
      )}
    </button>
  )
}

function TableView({ board, onOpen }: { board: Board; onOpen: (id: string) => void }) {
  return (
    <div className="table-wrap">
      <table className="db-table">
        <thead>
          <tr>
            <th>Task</th><th>Status</th><th>Owner</th><th>Due</th><th>Blocker</th><th>Depends on</th><th>Last update</th>
          </tr>
        </thead>
        <tbody>
          {board.tasks.map((t) => (
            <tr key={t.id} onClick={() => onOpen(t.id)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onOpen(t.id)}>
              <td className="cell-title">{t.title}</td>
              <td>
                <StatusTag status={t.status} />
                {t.at_risk && <> <Tag color="orange" title={t.at_risk}>At risk</Tag></>}
              </td>
              <td><Person name={t.owner} size={18} /></td>
              <td className="muted">{fmtDate(t.deadline)}</td>
              <td>{t.blocker_text && t.status === 'Blocked' ? t.blocker_text : <span className="muted">—</span>}</td>
              <td className="muted">{t.depends_on_titles.join(', ') || '—'}</td>
              <td>{t.last_event ? <EngineTag engine={t.last_event.engine} confidence={t.last_event.confidence} /> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SidePeek({ task, board, tick, onClose }: { task: Task; board: Board; tick: number; onClose: () => void }) {
  const [depError, setDepError] = useState<string | null>(null)
  const prereqs = task.depends_on.split(',').filter(Boolean)
  const candidates = board.tasks.filter((t) => t.id !== task.id && !prereqs.includes(t.id))
  const [items, setItems] = useState<TimelineItem[]>([])
  useEffect(() => { api.timeline(task.id).then((r) => setItems(r.items)) }, [task.id, tick])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const lastLive = [...items].reverse().find((i) => i.type === 'event' && !i.reverted)

  return (
    <div className="peek-wrap" onClick={onClose}>
      <aside className="peek" onClick={(e) => e.stopPropagation()} aria-label={task.title} role="dialog">
        <div className="peek-bar">
          <button className="btn-text" onClick={onClose} aria-label="Close">»</button>
          <button className="btn-text" onClick={() => api.checkin(task.id)}>Ask {task.owner} for an update</button>
          <button className="btn-text" onClick={async () => {
            if (window.confirm(`Delete "${task.title}" and its history? Tasks that waited for it will no longer wait.`)) { await api.deleteTask(task.id); onClose() }
          }}>Delete</button>
        </div>
        <h2 className="peek-title">{task.title}</h2>
        <dl className="props">
          <dt>Status</dt><dd><StatusTag status={task.status} /></dd>
          <dt>Owner</dt><dd><Person name={task.owner} /></dd>
          <dt>Due</dt><dd>{fmtDate(task.deadline) || <span className="muted">Empty</span>}</dd>
          <dt>Waits for</dt>
          <dd>
            {prereqs.length === 0 && <span className="muted">Nothing</span>}
            {prereqs.map((id) => (
              <Tag key={id} color="gray">
                {board.tasks.find((t) => t.id === id)?.title ?? id}
                <button className="btn-text small" aria-label={`Remove prerequisite ${board.tasks.find((t) => t.id === id)?.title ?? id}`}
                  onClick={async () => { setDepError(null); try { await api.removeDependency(task.id, id) } catch (e) { setDepError(e instanceof Error ? e.message : 'failed') } }}>×</button>
              </Tag>
            ))}
            {candidates.length > 0 && (
              <select className="input small" aria-label="Add prerequisite" value=""
                onChange={async (e) => { setDepError(null); try { await api.addDependency(task.id, e.target.value) } catch (err) { setDepError(err instanceof Error ? err.message : 'failed') } }}>
                <option value="">+ add prerequisite</option>
                {candidates.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
              </select>
            )}
            {depError && <span className="small" role="alert" style={{ color: 'var(--red-fg)' }}>{depError}</span>}
          </dd>
          {task.blocker_text && task.status === 'Blocked' && (<>
            <dt>Blocker</dt>
            <dd><Tag color={task.blocker_resolved ? 'green' : 'red'}>{task.blocker_text}</Tag>
              {task.blocked_by && <span className="muted small"> asked {task.blocked_by}</span>}</dd>
          </>)}
          {task.at_risk && (<><dt>Risk</dt><dd><Tag color="orange">{task.at_risk}</Tag></dd></>)}
          {task.proof_url && (<><dt>Proof</dt><dd>{task.proof_url}</dd></>)}
        </dl>
        <h3 className="peek-section">History</h3>
        <ol className="history">
          {items.length === 0 && <li className="muted">No messages yet. Ask {task.owner} for an update to start.</li>}
          {items.map((i, idx) =>
            i.type === 'message' ? (
              <li key={`m${idx}`} className={`comment ${i.direction}`}>
                <Person name={i.direction === 'out' ? 'Saarathi' : (i.who ?? '?')} size={20} />
                <span className="muted small">
                  {i.direction === 'out' && `to ${i.who} `}{fmtTime(i.at)}{i.channel ? `, via ${i.channel}` : ''}
                </span>
                <p>{plain(i.text)}</p>
              </li>
            ) : (
              <li key={`e${idx}`} className={`change ${i.reverted ? 'reverted' : ''}`}>
                <div className="change-line">
                  <Tag color={STATUS_COLOR[i.old_status!]}>{i.old_status}</Tag>
                  <span className="muted">→</span>
                  <Tag color={STATUS_COLOR[i.new_status!]}>{i.new_status}</Tag>
                  <EngineTag engine={i.engine!} confidence={i.confidence} />
                  <span className="muted small push">{fmtTime(i.at)}</span>
                </div>
                <div className="muted small">{i.reason}, by {i.actor}</div>
                {Array.isArray(i.trace) && i.trace.length > 0 && (
                  <details className="toggle">
                    <summary>How the agent decided</summary>
                    <ul>{i.trace.map((line, k) => <li key={k}><code>{line}</code></li>)}</ul>
                  </details>
                )}
                {lastLive?.id === i.id && i.engine !== 'undo' && (
                  <button className="btn-text small" onClick={() => api.undo(i.id!)}>Undo this change</button>
                )}
              </li>
            ),
          )}
        </ol>
      </aside>
    </div>
  )
}
