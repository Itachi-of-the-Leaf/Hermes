import type { CSSProperties } from 'react'
import { useCallback, useEffect, useState } from 'react'
import type { NetEdge, NetNode, NetworkData, Status } from '../api'
import { api } from '../api'
import { Callout, Empty, fmtTime, PageHeader, StatusTag, Tag } from '../ui'
import { edgePath, layoutNetwork, NODE_H, NODE_W, wrapName } from './networkLayout'

const STATUS_DOT: Record<Status, string> = { Pending: 'var(--dot-gray)', 'In Progress': 'var(--dot-blue)', Blocked: 'var(--dot-red)', Complete: 'var(--dot-green)' }
const EDGE_LABEL: Record<string, string> = { OWNS: 'owns', REQUESTED: 'asked for', DEPENDS_ON: 'depends on', HAS_TASK: 'has task' }
const EDGE_KINDS: { type: string; label: string; color: string; dashed?: boolean }[] = [
  { type: 'OWNS', label: 'owns', color: 'var(--dot-blue)' },
  { type: 'REQUESTED', label: 'asked for', color: 'var(--edge-requested)', dashed: true },
  { type: 'DEPENDS_ON', label: 'depends on', color: 'var(--ink)' },
  { type: 'HAS_TASK', label: 'project links', color: 'var(--faint)', dashed: true },
]

const initials = (s: string) => s.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase()
const subtitle = (n: NetNode) =>
  n.type === 'Task' ? `${n.id.replace('Task:', '')} · ${n.status ?? ''}${n.late_minutes ? ` · late ${n.late_minutes} min` : ''}`
    : n.type === 'Person' ? (n.role ?? 'member') : 'project'
const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

function edgeStyle(e: NetEdge) {
  return e.type === 'OWNS' ? { stroke: 'var(--dot-blue)', dash: undefined, marker: undefined }
    : e.type === 'REQUESTED' ? { stroke: 'var(--edge-requested)', dash: '7 5', marker: 'url(#net-arrow-req)' }
    : e.type === 'DEPENDS_ON' ? { stroke: 'currentColor', dash: undefined, marker: 'url(#net-arrow)' }
    : { stroke: 'currentColor', dash: '3 5', marker: undefined }
}

export function NetworkPage({ tick }: { tick: number }) {
  const [data, setData] = useState<NetworkData | null>(null)
  const [failed, setFailed] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [zoom, setZoom] = useState<number | null>(null)          // null: fit to the page

  const load = useCallback(() => {
    api.network().then((d) => { setData(d); setFailed(false) }).catch(() => setFailed(true))
  }, [])
  useEffect(load, [load, tick])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setSelected(null)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (failed && !data) return <Empty>Could not load the graph. Is the backend running?</Empty>
  if (!data) return <Empty>Loading the graph…</Empty>

  const { pos, width, height } = layoutNetwork(data)
  const counts = data.nodes.reduce<Record<string, number>>((a, n) => ({ ...a, [n.type]: (a[n.type] ?? 0) + 1 }), {})
  const sel = data.nodes.find((n) => n.id === selected) ?? null
  const links = sel ? data.edges.filter((e) => e.source === sel.id || e.target === sel.id) : []
  const nameOf = (id: string) => data.nodes.find((n) => n.id === id)?.name ?? id
  const edges = data.edges.filter((e) => pos[e.source] && pos[e.target] && !hidden.has(e.type))
  const empty = data.nodes.filter((n) => n.type === 'Task').length === 0
  const focus = hover ?? selected
  const near = new Set<string>(focus ? [focus] : [])
  if (focus) edges.forEach((e) => { if (e.source === focus) near.add(e.target); if (e.target === focus) near.add(e.source) })
  const moving = !reducedMotion()
  const toggle = (type: string) => setHidden((h) => { const n = new Set(h); if (n.has(type)) n.delete(type); else n.add(type); return n })
  const pick = (id: string) => setSelected((s) => (s === id ? null : id))
  const svgStyle: CSSProperties = zoom == null ? { maxWidth: width } : { width: Math.round(width * zoom), maxWidth: 'none' }

  return (
    <>
      <PageHeader icon="🕸️" title="Graph database"
        description="Every node and relationship the agent reasons over: the project, its people, their tasks, and who waits for whom.">
        <div className="change-line">
          <Tag color={data.source === 'neo4j' ? 'green' : 'yellow'} title={data.source === 'neo4j' ? 'Read live from Neo4j' : 'Neo4j is not connected, so this is built from the project database'}>
            {data.source === 'neo4j' ? 'Live from Neo4j' : 'From the project database'}
          </Tag>
          <span className="muted small">
            {data.nodes.length} nodes ({Object.entries(counts).map(([k, v]) => `${v} ${k.toLowerCase()}${v === 1 ? '' : 's'}`).join(', ')}) · {data.edges.length} relationships
          </span>
          {data.browser && <a className="small" href={data.browser} target="_blank" rel="noreferrer">Open in Neo4j</a>}
        </div>
      </PageHeader>

      {data.error && <Callout icon="⚠️" color="orange">{data.error}. Showing the project database instead.</Callout>}
      {data.source === 'database' && !data.error && (
        <Callout icon="ℹ️" color="gray">Neo4j is not connected. Set <code>NEO4J_URI</code>, <code>NEO4J_USER</code> and <code>NEO4J_PASSWORD</code> in <code>backend/.env</code> (a local Docker container or a free Aura instance) and this page reads the graph from it.</Callout>
      )}
      {empty && <Empty>No tasks yet, so only the project and its people are in the graph. Add a task from the Tasks page, or send <code>task: Build the login page @name due friday</code> in Telegram or Slack.</Empty>}

      <div className="net-toolbar">
        <div className="net-legend" role="group" aria-label="Show relationships">
          {EDGE_KINDS.map((k) => (
            <label key={k.type} className={`net-chip${hidden.has(k.type) ? ' off' : ''}`}>
              <input type="checkbox" checked={!hidden.has(k.type)} onChange={() => toggle(k.type)} />
              <svg width="22" height="8" aria-hidden="true"><line x1="1" y1="4" x2="21" y2="4" stroke={k.color} strokeWidth="2.4" strokeDasharray={k.dashed ? '4 3' : undefined} /></svg>
              {k.label}
            </label>
          ))}
        </div>
        <div className="net-zoom" role="group" aria-label="Zoom">
          <button className="btn-text" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.5, +((z ?? 1) - 0.2).toFixed(1)))}>−</button>
          <button className="btn-text" onClick={() => setZoom(null)} aria-pressed={zoom == null}>Fit</button>
          <button className="btn-text" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(2.4, +((z ?? 1) + 0.2).toFixed(1)))}>+</button>
        </div>
      </div>

      <div className={`net-shell${sel ? ' has-panel' : ''}`}>
        <div className={`net-stage${focus ? ' has-focus' : ''}`} role="img" aria-label="Project graph" onClick={() => setSelected(null)}>
          <svg className="net" viewBox={`0 0 ${width} ${height}`} width="100%" style={svgStyle} data-width={width}>
            <defs>
              <marker id="net-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="currentColor" />
              </marker>
              <marker id="net-arrow-req" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
                <path d="M0,0 L8,4 L0,8 z" fill="var(--edge-requested)" />
              </marker>
            </defs>
            {edges.map((e, i) => {
              const hot = !!focus && (e.source === focus || e.target === focus)
              const s = edgeStyle(e)
              return <path key={i} id={`net-e${i}`} data-edge={e.type} className={`net-edge t-${e.type}${hot ? ' is-hot' : ''}`}
                d={edgePath(pos[e.source], pos[e.target])} fill="none" stroke={s.stroke}
                strokeWidth={hot ? 2.6 : 1.6} strokeDasharray={s.dash} markerEnd={s.marker} />
            })}
            {moving && edges.map((e, i) => {
              const hot = !!focus && (e.source === focus || e.target === focus)
              if (focus ? !hot : e.type === 'HAS_TASK') return null
              return (
                <circle key={`p${i}-${focus ?? ''}`} r={hot ? 4 : 3} className="net-pulse" fill={edgeStyle(e).stroke}>
                  <animateMotion dur={hot ? '1.6s' : '3.2s'} repeatCount="indefinite" begin={`${((i % 7) * 0.4).toFixed(1)}s`}>
                    <mpath href={`#net-e${i}`} />
                  </animateMotion>
                </circle>
              )
            })}
            {data.nodes.map((n, i) => {
              const p = pos[n.id]
              if (!p) return null
              const on = n.id === selected
              const late = (n.late_minutes ?? 0) > 0
              const [l1, l2] = wrapName(n.name ?? n.id, n.type === 'Person' ? 16 : 21)
              const x = n.type === 'Person' ? 52 : 16
              const cls = `net-node t-${n.type}${on ? ' is-on' : ''}${near.has(n.id) ? ' is-near' : ''}`
              return (
                <g key={n.id} data-node={n.type} className={cls} transform={`translate(${p.x},${p.y})`} tabIndex={0}
                  role="button" aria-label={`${n.type} ${n.name ?? n.id}`} aria-pressed={on}
                  style={{ '--i': i } as CSSProperties}
                  onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
                  onFocus={() => setHover(n.id)} onBlur={() => setHover(null)}
                  onClick={(ev) => { ev.stopPropagation(); pick(n.id) }} onKeyDown={(ev) => ev.key === 'Enter' && pick(n.id)}>
                  <rect className="net-card" width={NODE_W} height={NODE_H} rx={n.type === 'Person' ? NODE_H / 2 : 10}
                    stroke={on ? 'var(--accent)' : n.status === 'Blocked' ? 'var(--dot-red)' : late ? 'var(--orange-fg)' : 'var(--line)'}
                    strokeWidth={on || n.status === 'Blocked' || late ? 2.2 : 1} />
                  <title>{`${n.name ?? n.id} (${n.type})`}</title>
                  {n.type === 'Task' && n.status && <rect x="0" y="10" width="4" height={NODE_H - 20} rx="2" fill={STATUS_DOT[n.status]} />}
                  {n.type === 'Person' && (<>
                    <circle cx="30" cy={NODE_H / 2} r="19" className="net-avatar" />
                    <text x="30" y={NODE_H / 2 + 4.5} textAnchor="middle" className="net-initials">{initials(n.name ?? '?')}</text>
                  </>)}
                  <text x={x} y={l2 ? 22 : 28} className="net-name">{l1}</text>
                  {l2 && <text x={x} y="37" className="net-name">{l2}</text>}
                  <text x={x} y={l2 ? 51 : 45} className="net-sub">{subtitle(n)}</text>
                </g>
              )
            })}
          </svg>
        </div>

        {sel && (
          <aside className="net-panel" aria-label="Node details">
            <div className="net-panel-head">
              <span className="small muted">{sel.type}</span>
              <button className="btn-text" aria-label="Close details" onClick={() => setSelected(null)}>×</button>
            </div>
            <h2 className="net-panel-title">{sel.name ?? sel.id}</h2>
            <dl className="props">
              {sel.status && (<><dt>Status</dt><dd><StatusTag status={sel.status} /></dd></>)}
              {sel.role && (<><dt>Role</dt><dd>{sel.role}</dd></>)}
              {sel.type === 'Task' && (() => {
                const asker = data.edges.find((e) => e.type === 'REQUESTED' && e.target === sel.id)
                return asker ? (<><dt>Asked by</dt><dd>{nameOf(asker.source)}</dd></>) : null
              })()}
              {sel.blocker && (<><dt>Blocker</dt><dd>{sel.blocker}</dd></>)}
              {sel.deadline && (<><dt>Deadline</dt><dd>{fmtTime(sel.deadline)}</dd></>)}
              {sel.forecast_end && (<><dt>Forecast end</dt><dd>{fmtTime(sel.forecast_end)}</dd></>)}
              {sel.priority_score != null && (<><dt>Priority</dt><dd>{sel.priority_score} / 100</dd></>)}
            </dl>
            <h3 className="peek-section">Relationships</h3>
            {links.length === 0 ? <p className="muted small">none</p> : (
              <ul className="net-links">
                {links.map((e, i) => {
                  const other = e.source === sel.id ? e.target : e.source
                  return (
                    <li key={i}>
                      <button className="net-link" onClick={() => setSelected(other)} onMouseEnter={() => setHover(other)} onMouseLeave={() => setHover(null)}>
                        <i style={{ background: EDGE_KINDS.find((k) => k.type === e.type)?.color }} aria-hidden="true" />
                        {e.source === sel.id ? `${EDGE_LABEL[e.type] ?? e.type} → ${nameOf(e.target)}` : `${nameOf(e.source)} ${EDGE_LABEL[e.type] ?? e.type} this`}
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </aside>
        )}
      </div>

      <p className="small muted">
        Hover a node to light up its connections, click it for details, and use the chips above to show or hide each kind of link.
        Arrows point from a task to its prerequisite and from the person who asked for a task. Red outline: blocked. Orange: forecast to miss its deadline.
      </p>
    </>
  )
}
