import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { NetworkData } from '../api'
import { NetworkPage } from './Network'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const base: NetworkData = {
  source: 'neo4j',
  browser: 'http://localhost:7474',
  nodes: [
    { id: 'Project:main', type: 'Project', name: 'Group project' },
    { id: 'Person:1', type: 'Person', name: 'Tejas', role: 'lead' },
    { id: 'Person:2', type: 'Person', name: 'Soham', role: 'member' },
    { id: 'Task:T1', type: 'Task', name: 'Get credentials', status: 'In Progress', priority_score: 30 },
    { id: 'Task:T2', type: 'Task', name: 'Connect the payment API', status: 'Blocked', blocker: 'waiting for credentials', late_minutes: 45, deadline: '2026-10-17T18:00:00+00:00' },
  ],
  edges: [
    { source: 'Project:main', target: 'Task:T1', type: 'HAS_TASK' }, { source: 'Project:main', target: 'Task:T2', type: 'HAS_TASK' },
    { source: 'Person:1', target: 'Task:T1', type: 'OWNS' }, { source: 'Person:2', target: 'Task:T2', type: 'OWNS' },
    { source: 'Task:T2', target: 'Task:T1', type: 'DEPENDS_ON' },
    { source: 'Person:1', target: 'Task:T2', type: 'REQUESTED' },
  ],
}

function mock(data: NetworkData | Error) {
  const f = vi.fn(() => data instanceof Error ? Promise.reject(data) : Promise.resolve(new Response(JSON.stringify(data), { status: 200 })))
  vi.stubGlobal('fetch', f)
  return f
}

describe('NetworkPage', () => {
  it('shows every node: the project, each person and each task', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    expect(await screen.findByText('Live from Neo4j')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-node="Project"]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-node="Person"]')).toHaveLength(2)
    expect(container.querySelectorAll('[data-node="Task"]')).toHaveLength(2)
    for (const n of ['Project Group project', 'Person Tejas', 'Person Soham', 'Task Get credentials', 'Task Connect the payment API']) expect(screen.getByRole('button', { name: n })).toBeInTheDocument()
    expect(screen.getByText('Connect the payment')).toBeInTheDocument()                    // long names wrap, they are not cut off
    expect(screen.getByText('API')).toBeInTheDocument()
  })

  it('draws every relationship, with arrows only on dependencies', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    expect(container.querySelectorAll('path[data-edge="OWNS"]')).toHaveLength(2)
    expect(container.querySelectorAll('path[data-edge="HAS_TASK"]')).toHaveLength(2)
    const dep = container.querySelectorAll('path[data-edge="DEPENDS_ON"]')
    expect(dep).toHaveLength(1)
    expect(dep[0]).toHaveAttribute('marker-end')
  })

  it('draws who asked for a task in its own colour and names them in the details', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    const req = container.querySelectorAll('path[data-edge="REQUESTED"]')
    expect(req).toHaveLength(1)
    expect(req[0]).toHaveAttribute('stroke', 'var(--edge-requested)')
    expect(req[0]).toHaveAttribute('marker-end', 'url(#net-arrow-req)')
    fireEvent.click(container.querySelector('g[aria-label="Task Connect the payment API"]')!)
    expect(screen.getByText('Asked by')).toBeInTheDocument()
  })

  it('counts nodes and relationships in plain words', async () => {
    mock(base)
    render(<NetworkPage tick={0} />)
    expect(await screen.findByText(/5 nodes \(1 project, 2 persons, 2 tasks\) · 6 relationships/)).toBeInTheDocument()
  })

  it('links to the Neo4j browser when there is one', async () => {
    mock(base)
    render(<NetworkPage tick={0} />)
    expect(await screen.findByRole('link', { name: 'Open in Neo4j' })).toHaveAttribute('href', 'http://localhost:7474')
  })

  it('says plainly when it is reading the database instead of Neo4j', async () => {
    mock({ ...base, source: 'database', browser: null })
    render(<NetworkPage tick={0} />)
    expect(await screen.findByText('From the project database')).toBeInTheDocument()
    expect(screen.getByText(/Neo4j is not connected/)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open in Neo4j' })).not.toBeInTheDocument()
  })

  it('explains a Neo4j outage and still shows the graph', async () => {
    mock({ ...base, source: 'database', error: 'Neo4j unavailable: cannot connect' })
    const { container } = render(<NetworkPage tick={0} />)
    expect(await screen.findByText(/Neo4j unavailable: cannot connect/)).toBeInTheDocument()
    expect(container.querySelectorAll('[data-node]')).toHaveLength(5)
  })

  it('clicking a node shows its details and relationships; clicking again hides them', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    fireEvent.click(container.querySelector('[data-node="Task"][aria-label="Task Connect the payment API"]')!)
    const panel = await screen.findByLabelText('Node details')
    expect(panel).toHaveTextContent('Blocked')
    expect(panel).toHaveTextContent('waiting for credentials')
    expect(panel).toHaveTextContent('depends on → Get credentials')
    expect(panel).toHaveTextContent('Soham owns this')
    fireEvent.click(container.querySelector('[data-node="Task"][aria-label="Task Connect the payment API"]')!)
    expect(screen.queryByLabelText('Node details')).not.toBeInTheDocument()
  })

  it('a person node lists what they own', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    fireEvent.click(container.querySelector('[data-node="Person"][aria-label="Person Tejas"]')!)
    expect(await screen.findByLabelText('Node details')).toHaveTextContent('owns → Get credentials')
  })

  it('can hide the project links without hiding any node', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    fireEvent.click(screen.getByLabelText(/project links/))
    expect(container.querySelectorAll('path[data-edge="HAS_TASK"]')).toHaveLength(0)
    expect(container.querySelectorAll('[data-node]')).toHaveLength(5)
  })

  it('flags blocked tasks and late tasks on the graph itself', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    expect(container.querySelector('[aria-label="Task Connect the payment API"] rect')).toHaveAttribute('stroke', 'var(--dot-red)')
    expect(screen.getByText(/late 45 min/)).toBeInTheDocument()
  })

  it('an empty project shows the project and people with a hint on how to add tasks', async () => {
    mock({ ...base, nodes: base.nodes.slice(0, 3), edges: [] })
    const { container } = render(<NetworkPage tick={0} />)
    expect(await screen.findByText(/No tasks yet, so only the project and its people/)).toBeInTheDocument()
    expect(container.querySelectorAll('[data-node]')).toHaveLength(3)
  })

  it('reloads when the live tick changes', async () => {
    const f = mock(base)
    const { rerender } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    rerender(<NetworkPage tick={1} />)
    await waitFor(() => expect(f).toHaveBeenCalledTimes(2))
  })

  it('scales to fit the page instead of clipping nodes at the edge', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    const svg = container.querySelector('svg[data-width]')!
    expect(svg).toHaveAttribute('width', '100%')
    expect(svg.getAttribute('viewBox')).toMatch(/^0 0 \d+ \d+$/)
  })

  it('legend chips show and hide each kind of relationship', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    fireEvent.click(screen.getByLabelText(/owns/))
    expect(container.querySelectorAll('path[data-edge="OWNS"]')).toHaveLength(0)
    fireEvent.click(screen.getByLabelText(/owns/))
    expect(container.querySelectorAll('path[data-edge="OWNS"]')).toHaveLength(2)
  })

  it('hovering a node lights up its neighbours and fades the rest', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    fireEvent.mouseEnter(container.querySelector('g[aria-label="Person Soham"]')!)
    expect(container.querySelector('.net-stage')).toHaveClass('has-focus')
    expect(container.querySelector('g[aria-label="Task Connect the payment API"]')).toHaveClass('is-near')
    expect(container.querySelector('g[aria-label="Task Get credentials"]')).not.toHaveClass('is-near')
  })

  it('a relationship in the details jumps to the other node', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    fireEvent.click(container.querySelector('g[aria-label="Person Soham"]')!)
    fireEvent.click(screen.getByRole('button', { name: /owns → Connect the payment API/ }))
    expect(await screen.findByLabelText('Node details')).toHaveTextContent('waiting for credentials')
  })

  it('zooms in and back to fit', async () => {
    mock(base)
    const { container } = render(<NetworkPage tick={0} />)
    await screen.findByText('Live from Neo4j')
    const svg = container.querySelector('svg[data-width]') as SVGSVGElement
    fireEvent.click(screen.getByLabelText('Zoom in'))
    expect(svg.style.width).toMatch(/px$/)
    fireEvent.click(screen.getByRole('button', { name: 'Fit' }))
    expect(svg.style.width).toBe('')
  })

  it('tells you when the backend cannot be reached', async () => {
    mock(new Error('refused'))
    render(<NetworkPage tick={0} />)
    expect(await screen.findByText(/Could not load the graph/)).toBeInTheDocument()
  })
})
