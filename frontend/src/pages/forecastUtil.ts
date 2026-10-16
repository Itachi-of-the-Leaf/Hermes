import type { GraphData } from '../api'

export const humanMinutes = (m: number) => {
  const h = Math.floor(Math.abs(m) / 60)
  const min = Math.abs(m) % 60
  return h && min ? `${h}h ${min}m` : h ? `${h}h` : `${min} min`
}

/** Dependency layers: a task sits one column right of its deepest prerequisite. */
export function layers(g: GraphData): Record<string, number> {
  const deps: Record<string, string[]> = {}
  g.nodes.forEach((n) => { deps[n.id] = [] })
  g.edges.forEach((e) => deps[e.target]?.push(e.source))
  const depth: Record<string, number> = {}
  const visit = (id: string, seen: string[]): number => {
    if (depth[id] != null) return depth[id]
    if (seen.includes(id)) return 0                      // cycle: the server flags it; do not loop forever
    depth[id] = 1 + Math.max(-1, ...deps[id].map((d) => visit(d, [...seen, id])))
    return depth[id]
  }
  g.nodes.forEach((n) => visit(n.id, []))
  return depth
}
