import { describe, expect, it } from 'vitest'
import type { NetEdge, NetNode, NetworkData } from '../api'
import { edgePath, layoutNetwork, NODE_H, NODE_W, wrapName } from './networkLayout'

const node = (id: string, type: string, extra: Partial<NetNode> = {}): NetNode => ({ id, type, name: id, ...extra })
const data = (nodes: NetNode[], edges: NetEdge[] = []): NetworkData => ({ source: 'database', browser: null, nodes, edges })

const sample = () => data(
  [node('Project:main', 'Project'), node('Person:1', 'Person'), node('Person:2', 'Person'),
   node('Task:T1', 'Task'), node('Task:T2', 'Task'), node('Task:T3', 'Task'), node('Task:T4', 'Task')],
  [
    { source: 'Task:T2', target: 'Task:T1', type: 'DEPENDS_ON' },     // T2 waits for T1
    { source: 'Task:T3', target: 'Task:T2', type: 'DEPENDS_ON' },     // T3 waits for T2
    { source: 'Person:1', target: 'Task:T1', type: 'OWNS' }, { source: 'Person:2', target: 'Task:T2', type: 'OWNS' },
    { source: 'Person:1', target: 'Task:T3', type: 'OWNS' }, { source: 'Person:2', target: 'Task:T4', type: 'OWNS' },
  ],
)

describe('layoutNetwork', () => {
  it('gives every node its own position, so none is hidden', () => {
    const d = sample()
    const { pos } = layoutNetwork(d)
    expect(Object.keys(pos).sort()).toEqual(d.nodes.map((n) => n.id).sort())
    const spots = Object.values(pos).map((p) => `${p.x},${p.y}`)
    expect(new Set(spots).size).toBe(spots.length)
  })

  it('no two boxes overlap', () => {
    const { pos } = layoutNetwork(sample())
    const boxes = Object.values(pos)
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j]
      const overlap = a.x < b.x + NODE_W && b.x < a.x + NODE_W && a.y < b.y + NODE_H && b.y < a.y + NODE_H
      expect(overlap).toBe(false)
    }
  })

  it('puts the project and people in the first column and tasks to the right by prerequisite depth', () => {
    const { pos } = layoutNetwork(sample())
    expect(pos['Project:main'].x).toBe(pos['Person:1'].x)
    expect(pos['Person:2'].x).toBe(pos['Person:1'].x)
    expect(pos['Task:T1'].x).toBeGreaterThan(pos['Person:1'].x)
    expect(pos['Task:T2'].x).toBeGreaterThan(pos['Task:T1'].x)              // waits for T1
    expect(pos['Task:T3'].x).toBeGreaterThan(pos['Task:T2'].x)              // waits for T2
    expect(pos['Task:T4'].x).toBe(pos['Task:T1'].x)                          // waits for nothing
  })

  it('stacks the project above the people', () => {
    const { pos } = layoutNetwork(sample())
    expect(pos['Project:main'].y).toBeLessThan(pos['Person:1'].y)
    expect(pos['Person:1'].y).toBeLessThan(pos['Person:2'].y)
  })

  it('orders tasks in a column by their owner to keep lines short', () => {
    const { pos } = layoutNetwork(sample())
    expect(pos['Task:T1'].y).toBeLessThan(pos['Task:T4'].y)                  // owner 1 before owner 2
  })

  it('sizes the canvas to contain every box', () => {
    const { pos, width, height } = layoutNetwork(sample())
    for (const p of Object.values(pos)) {
      expect(p.x + NODE_W).toBeLessThanOrEqual(width)
      expect(p.y + NODE_H).toBeLessThanOrEqual(height)
    }
  })

  it('handles an empty project: just the project and the people', () => {
    const l = layoutNetwork(data([node('Project:main', 'Project'), node('Person:1', 'Person')]))
    expect(Object.keys(l.pos)).toHaveLength(2)
    expect(l.width).toBeGreaterThan(NODE_W)
  })

  it('handles no data at all', () => {
    const l = layoutNetwork(data([]))
    expect(l.pos).toEqual({})
    expect(l.height).toBeGreaterThan(0)
  })

  it('survives a prerequisite cycle without looping forever', () => {
    const d = data([node('Task:A', 'Task'), node('Task:B', 'Task')],
      [{ source: 'Task:A', target: 'Task:B', type: 'DEPENDS_ON' }, { source: 'Task:B', target: 'Task:A', type: 'DEPENDS_ON' }])
    expect(() => layoutNetwork(d)).not.toThrow()
    expect(Object.keys(layoutNetwork(d).pos)).toHaveLength(2)
  })

  it('places node types it does not know about instead of dropping them', () => {
    const l = layoutNetwork(data([node('Resource:r1', 'Resource'), node('Person:1', 'Person')]))
    expect(l.pos['Resource:r1']).toBeDefined()
  })

  it('ignores edges that point at nodes that are not there', () => {
    const d = data([node('Task:T1', 'Task')], [{ source: 'Task:T1', target: 'Task:GONE', type: 'DEPENDS_ON' }])
    expect(() => layoutNetwork(d)).not.toThrow()
  })
})

describe('edgePath', () => {
  it('leaves the right side of the left box and enters the left side of the right box', () => {
    expect(edgePath({ x: 0, y: 0 }, { x: 300, y: 100 })).toMatch(new RegExp(`^M${NODE_W},${NODE_H / 2} C`))
    expect(edgePath({ x: 0, y: 0 }, { x: 300, y: 100 })).toMatch(new RegExp(`300,${100 + NODE_H / 2}$`))
  })
  it('runs backwards (a task pointing at its prerequisite) from the left side to the right side', () => {
    expect(edgePath({ x: 300, y: 0 }, { x: 0, y: 100 })).toMatch(new RegExp(`^M300,${NODE_H / 2} C.* ${NODE_W},${100 + NODE_H / 2}$`))
  })
})

describe('wrapName', () => {
  it('leaves short names alone', () => expect(wrapName('Design the slides')).toEqual(['Design the slides']))
  it('breaks a long name at a word boundary instead of cutting it off', () => {
    expect(wrapName('Connect the payment API')).toEqual(['Connect the payment', 'API'])
    expect(wrapName('Get sandbox test credentials')).toEqual(['Get sandbox test', 'credentials'])
  })
  it('keeps every word when it fits on two lines', () => {
    const [a, b] = wrapName('Record the final demo video')
    expect(`${a} ${b}`).toBe('Record the final demo video')
  })
  it('only truncates (with an ellipsis) when even two lines are not enough', () => {
    const [a, b] = wrapName('An extremely long task title that keeps going and going and going well past two lines')
    expect(a.length).toBeLessThanOrEqual(22)
    expect(b!.endsWith('…')).toBe(true)
  })
  it('splits a single enormous word rather than overflowing the box', () => {
    const [a] = wrapName('Supercalifragilisticexpialidocious')
    expect(a.length).toBeLessThanOrEqual(22)
  })
})
