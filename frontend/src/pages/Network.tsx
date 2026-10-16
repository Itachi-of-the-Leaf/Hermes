import { useCallback, useEffect, useState } from 'react'
import type { NetNode, NetworkData, Status } from '../api'
import { api } from '../api'
import { Callout, Empty, fmtTime, PageHeader, Tag } from '../ui'
import { edgePath, layoutNetwork, NODE_H, NODE_W, wrapName } from './networkLayout'

const STATUS_FILL: Record<Status, string> = { Pending: 'var(--c-gray)', 'In Progress': 'var(--blue-bg)', Blocked: 'var(--red-bg)', Complete: 'var(--green-bg)' }
const EDGE_LABEL: Record<string, string> = { OWNS: 'owns', DEPENDS_ON: 'depends on', HAS_TASK: 'has task' }

const fill = (n: NetNode) => (n.type === 'Task' && n.status ? STATUS_FILL[n.status] : n.type === 'Person' ? 'var(--purple-bg)' : 'var(--ink)')
const textColor = (n: NetNode) => (n.type === 'Project' ? 'var(--page)' : 'currentColor')
const subtitle = (n: NetNode) =>
  n.type === 'Task' ? `${n.id.replace('Task:', '')} · ${n.status ?? ''}${n.late_minutes ? ` · late ${n.late_minutes} min` : ''}`
    : n.type === 'Person' ? (n.role ?? 'member') : 'project'

export function NetworkPage({ tick }: { tick: number }) {
  const [data, setData] = useState<NetworkData | null>(null)
  const [failed, setFailed] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [showProject, setShowProject] = useState(true)

  const load = useCallback(() => {
    api.network().then((d) => { setData(d); setFailed(false) }).catch(() => setFailed(true))
  }, [])
  useEffect(load, [load, tick])

  if (failed && !data) return <Empty>Could not load the graph. Is the backend running?</Empty>
  if (!data) return <Empty>Loading the graph…</Empty>

  const { pos, width, height } = layoutNetwork(data)
  const counts = data.nodes.reduce<Record<string, number>>((a, n) => ({ ...a, [n.type]: (a[n.type] ?? 0) + 1 }), {})
  const sel = data.nodes.find((n) => n.id === selected) ?? null
  const links = sel ? data.edges.filter((e) => e.source === sel.id || e.target === sel.id) : []
  const nameOf = (id: string) => data.nodes.find((n) => n.id === id)?.name ?? id
  const edges = data.edges.filter((e) => pos[e.source] && pos[e.target] && (showProject || e.type !== 'HAS_TASK'))
  const empty = data.nodes.filter((n) => n.type === 'Task').length === 0

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
          <label className="small muted check"><input type="checkbox" checked={showProject} onChange={(e) => setShowProject(e.target.checked)} /> project links</label>
        </div>
      </PageHeader>

      {data.error && <Callout icon="⚠️" color="orange">{data.error}. Showing the project database instead.</Callout>}
      {data.source === 'database' && !data.error && (
        <Callout icon="ℹ️" color="gray">Neo4j is not connected. Set <code>NEO4J_URI</code>, <code>NEO4J_USER</code> and <code>NEO4J_PASSWORD</code> in <code>backend/.env</code> (a local Docker container or a free Aura instance) and this page reads the graph from it.</Callout>
      )}
      {empty && <Empty>No tasks yet, so only the project and its people are in the graph. Add a task from the Tasks page, or send <code>task: Build the login page @name due friday</code> in Telegram or Slack.</Empty>}

      <div className="table-wrap" role="img" aria-label="Project graph">
        <svg viewBox={`0 0 ${width} ${height}`} width="100%" style={{ display: 'block', maxWidth: width, height: 'auto' }} data-width={width}>
          <defs>
            <marker id="net-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" fill="currentColor" opacity="0.55" />
            </marker>
          </defs>
          {edges.map((e, i) => {
            const hot = sel && (e.source === sel.id || e.target === sel.id)
            const style = e.type === 'OWNS' ? { stroke: 'var(--dot-blue)', opacity: hot ? 0.95 : 0.55, dash: undefined }
              : e.type === 'DEPENDS_ON' ? { stroke: 'currentColor', opacity: hot ? 0.9 : 0.5, dash: undefined }
              : { stroke: 'currentColor', opacity: hot ? 0.7 : 0.14, dash: '4 4' }
            return <path key={i} data-edge={e.type} d={edgePath(pos[e.source], pos[e.target])} fill="none" stroke={style.stroke}
              strokeWidth={hot ? 2.4 : 1.5} strokeDasharray={style.dash} opacity={style.opacity} markerEnd={e.type === 'DEPENDS_ON' ? 'url(#net-arrow)' : undefined} />
          })}
          {data.nodes.map((n) => {
            const p = pos[n.id]
            if (!p) return null
            const on = n.id === selected
            return (
              <g key={n.id} data-node={n.type} transform={`translate(${p.x},${p.y})`} style={{ cursor: 'pointer' }} tabIndex={0}
                role="button" aria-label={`${n.type} ${n.name ?? n.id}`}
                onClick={() => setSelected(on ? null : n.id)} onKeyDown={(e) => e.key === 'Enter' && setSelected(on ? null : n.id)}>
                <rect width={NODE_W} height={NODE_H} rx={n.type === 'Person' ? NODE_H / 2 : 7} fill={fill(n)}
                  stroke={on ? 'var(--accent)' : n.status === 'Blocked' ? 'var(--dot-red)' : (n.late_minutes ?? 0) > 0 ? 'var(--orange-fg)' : 'var(--line)'}
                  strokeWidth={on || n.status === 'Blocked' || (n.late_minutes ?? 0) > 0 ? 2.2 : 1} />
                <title>{`${n.name ?? n.id} (${n.type})`}</title>
                {(() => {
                  const [l1, l2] = wrapName(n.name ?? n.id, n.type === 'Person' ? 18 : 22)
                  const x = n.type === 'Person' ? 22 : 10
                  return (<>
                    <text x={x} y={l2 ? 19 : 25} fontSize="12.5" fontWeight="600" fill={textColor(n)}>{l1}</text>
                    {l2 && <text x={x} y="34" fontSize="12.5" fontWeight="600" fill={textColor(n)}>{l2}</text>}
                    <text x={x} y="51" fontSize="11" fill={textColor(n)} opacity="0.66">{subtitle(n)}</text>
                  </>)
                })()}
              </g>
            )
          })}
        </svg>
      </div>

      <p className="small muted">
        Arrows point from a task to its prerequisite. Blue lines: a person owns a task. Dashed: the project has the task.
        Red outline: blocked. Orange: forecast to miss its deadline. Click any node for its details.
      </p>

      {sel && (
        <div className="block" aria-label="Node details">
          <div className="block-title">{sel.name ?? sel.id} <Tag color="gray">{sel.type}</Tag></div>
          <dl className="props">
            {sel.status && (<><dt>Status</dt><dd>{sel.status}</dd></>)}
            {sel.role && (<><dt>Role</dt><dd>{sel.role}</dd></>)}
            {sel.blocker && (<><dt>Blocker</dt><dd>{sel.blocker}</dd></>)}
            {sel.deadline && (<><dt>Deadline</dt><dd>{fmtTime(sel.deadline)}</dd></>)}
            {sel.forecast_end && (<><dt>Forecast end</dt><dd>{fmtTime(sel.forecast_end)}</dd></>)}
            {sel.priority_score != null && (<><dt>Priority</dt><dd>{sel.priority_score} / 100</dd></>)}
            <dt>Relationships</dt>
            <dd>{links.length === 0 ? <span className="muted">none</span> : (
              <ul className="doc-list">
                {links.map((e, i) => (
                  <li key={i}>{e.source === sel.id ? `${EDGE_LABEL[e.type] ?? e.type} → ${nameOf(e.target)}` : `${nameOf(e.source)} ${EDGE_LABEL[e.type] ?? e.type} this`}</li>
                ))}
              </ul>
            )}</dd>
          </dl>
        </div>
      )}
    </>
  )
}
