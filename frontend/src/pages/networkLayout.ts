import type { NetworkData } from '../api'

export const NODE_W = 178
export const NODE_H = 60
export const GAP_X = 72
export const GAP_Y = 16
const PAD = 12

export interface Layout {
  pos: Record<string, { x: number; y: number }>
  width: number
  height: number
}

/**
 * Left to right: the project and its people, then tasks in columns by how many prerequisites sit before them.
 * Every node gets its own spot, so nothing is hidden behind anything else. Prerequisite cycles (the server
 * flags them) are tolerated: a task inside one is placed one column after the point where the cycle was entered.
 */
export function layoutNetwork(data: NetworkData): Layout {
  const prereq: Record<string, string[]> = {}
  data.nodes.forEach((n) => { prereq[n.id] = [] })
  data.edges.filter((e) => e.type === 'DEPENDS_ON').forEach((e) => prereq[e.source]?.push(e.target))

  const depth: Record<string, number> = {}
  const visit = (id: string, trail: string[]): number => {
    if (depth[id] != null) return depth[id]
    if (trail.includes(id)) return 0
    depth[id] = 1 + Math.max(-1, ...(prereq[id] ?? []).map((p) => visit(p, [...trail, id])))
    return depth[id]
  }

  const people = data.nodes.filter((n) => n.type === 'Person')
  const tasks = data.nodes.filter((n) => n.type === 'Task')
  const others = data.nodes.filter((n) => n.type !== 'Person' && n.type !== 'Task' && n.type !== 'Project')
  const project = data.nodes.filter((n) => n.type === 'Project')
  tasks.forEach((t) => visit(t.id, []))

  const ownerRow: Record<string, number> = {}
  people.forEach((p, i) => { ownerRow[p.id] = i })
  const ownerOf: Record<string, string> = {}
  data.edges.filter((e) => e.type === 'OWNS').forEach((e) => { ownerOf[e.target] = e.source })

  const pos: Record<string, { x: number; y: number }> = {}
  const colX = (c: number) => PAD + c * (NODE_W + GAP_X)
  const rowY = (r: number) => PAD + r * (NODE_H + GAP_Y)

  let row = 0
  project.forEach((n) => { pos[n.id] = { x: colX(0), y: rowY(row++) } })
  people.forEach((n) => { pos[n.id] = { x: colX(0), y: rowY(row++) } })
  others.forEach((n) => { pos[n.id] = { x: colX(0), y: rowY(row++) } })
  let rows = row

  const byCol: Record<number, typeof tasks> = {}
  tasks.forEach((t) => { (byCol[depth[t.id]] ??= []).push(t) })
  Object.entries(byCol).forEach(([c, list]) => {
    list.sort((a, b) => (ownerRow[ownerOf[a.id]] ?? 99) - (ownerRow[ownerOf[b.id]] ?? 99) || a.id.localeCompare(b.id, undefined, { numeric: true }))
    list.forEach((t, i) => { pos[t.id] = { x: colX(1 + Number(c)), y: rowY(i) } })
    rows = Math.max(rows, list.length)
  })

  const cols = 1 + (tasks.length ? Math.max(...tasks.map((t) => depth[t.id])) + 1 : 0)
  return { pos, width: PAD * 2 + cols * NODE_W + (cols - 1) * GAP_X, height: PAD * 2 + Math.max(rows, 1) * NODE_H + (Math.max(rows, 1) - 1) * GAP_Y }
}

/** Break a name over at most two lines at word boundaries, so long names stay readable instead of being cut off. */
export function wrapName(name: string, width = 22): [string, string?] {
  if (name.length <= width) return [name]
  const words = name.split(/\s+/)
  let first = ''
  let i = 0
  while (i < words.length && (first + ' ' + words[i]).trim().length <= width) first = (first + ' ' + words[i++]).trim()
  if (!first) { first = words[0].slice(0, width); words[0] = words[0].slice(width); i = 0 }
  const rest = words.slice(i).join(' ')
  return [first, rest.length > width * 1.25 ? rest.slice(0, Math.round(width * 1.25) - 1) + '…' : rest || undefined]
}

/** A smooth path from one box to another, leaving and entering on the sides that face each other. */
export function edgePath(a: { x: number; y: number }, b: { x: number; y: number }): string {
  const forward = a.x <= b.x
  const x1 = forward ? a.x + NODE_W : a.x
  const x2 = forward ? b.x : b.x + NODE_W
  const y1 = a.y + NODE_H / 2
  const y2 = b.y + NODE_H / 2
  const k = Math.max(24, Math.abs(x2 - x1) / 2)
  const d = forward ? k : -k
  return `M${x1},${y1} C${x1 + d},${y1} ${x2 - d},${y2} ${x2},${y2}`
}
