import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

const task = (id: string, title: string, owner: string, status: string, extra = {}) => ({
  id, title, owner, owner_key: owner.toLowerCase(), status, progress_pct: null, deadline: '2026-10-17', eta: null,
  blocker_text: null, blocker_type: null, blocked_by: null, blocker_resolved: false, proof_url: null,
  depends_on: '', depends_on_titles: [], at_risk: null, last_event: null, ...extra,
})

const board = {
  tasks: [
    task('T1', 'Design the slides', 'Aanya', 'Complete'),
    task('T2', 'Connect the payment API', 'Kabir', 'Blocked', { blocker_text: 'test credentials', blocked_by: 'Priya' }),
  ],
  members: [
    { id: 1, key: 'aanya', name: 'Aanya', role: 'lead', connected: false, channels: [] },
    { id: 2, key: 'kabir', name: 'Kabir', role: 'member', connected: false, channels: [] },
  ],
  counts: { Complete: 1, Blocked: 1, 'In Progress': 0, Pending: 0 },
  pending_reviews: 0,
}
const health = {
  ok: true, env: 'dev', role: 'all', mode: 'auto', telegram: false, bot_username: null, slack: false,
  teams: false, recall: false, jev: false, jev_model: null, llm: false, llm_model: null,
}

class FakeES {
  onopen: (() => void) | null = null
  onerror = null
  onmessage = null
  close() {}
}

function mockApi(overrides: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = {
    '/api/board': board, '/api/activity': [], '/api/reviews': [], '/api/health': health,
    '/api/meetings': [], '/api/team': [], ...overrides,
  }
  const f = vi.fn((url: string, _init?: RequestInit) => {
    const hit = routes[url]
    return Promise.resolve(hit === undefined ? new Response('{}', { status: 404 }) : new Response(JSON.stringify(hit), { status: 200 }))
  })
  vi.stubGlobal('fetch', f)
  return f
}

const loaded = () => screen.findAllByText('Connect the payment API')

beforeEach(() => {
  vi.stubGlobal('EventSource', FakeES)
  window.location.hash = ''
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('App shell', () => {
  it('shows the connecting hint until the backend answers', () => {
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    render(<App />)
    expect(screen.getByText(/Connecting to the coordinator/)).toBeInTheDocument()
  })

  it('stays on the hint (no crash) when the backend is down', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('refused'))))
    render(<App />)
    await new Promise((r) => setTimeout(r, 20))
    expect(screen.getByText(/Connecting to the coordinator/)).toBeInTheDocument()
  })

  it('renders every task on the default Tasks page', async () => {
    mockApi()
    render(<App />)
    await loaded()
    expect(screen.getAllByText('Design the slides').length).toBeGreaterThan(0)
  })

  it('sidebar lists every section', async () => {
    mockApi()
    render(<App />)
    await loaded()
    for (const l of ['Project tasks', 'Forecast & agent', 'Graph database', 'Meetings', 'Needs your call', 'Activity', 'Team', 'Connections'])
      expect(screen.getAllByText(l).length).toBeGreaterThan(0)
  })

  it('summarises progress in the sidebar pulse', async () => {
    mockApi()
    render(<App />)
    expect(await screen.findByText(/50% done, 1 blocked/)).toBeInTheDocument()
  })

  it('shows the review badge when the lead has items waiting', async () => {
    mockApi({ '/api/reviews': [{ id: 1 }, { id: 2 }] })
    render(<App />)
    await loaded()
    await waitFor(() => expect(document.querySelector('.badge')).toHaveTextContent('2'))
  })

  it('reports rules-only mode with no apps connected', async () => {
    mockApi()
    render(<App />)
    expect(await screen.findByText(/No apps connected/)).toHaveTextContent('using rules only')
  })

  it('hides Reset and the test panel in production', async () => {
    mockApi({ '/api/health': { ...health, env: 'prod' } })
    render(<App />)
    await loaded()
    await waitFor(() => expect(screen.queryByText('Test without apps')).not.toBeInTheDocument())
    expect(screen.queryByText('Reset')).not.toBeInTheDocument()
  })

  it('Send check-ins posts to the API and toasts the count', async () => {
    const f = mockApi({ '/api/checkin': { sent: 3 } })
    render(<App />)
    await loaded()
    fireEvent.click(screen.getByRole('button', { name: 'Send check-ins' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Sent 3 check-ins')
    expect(f).toHaveBeenCalledWith('/api/checkin', expect.objectContaining({ method: 'POST' }))
  })

  it('the test panel sends a message as the chosen member', async () => {
    const f = mockApi({ '/api/dev/message': { outbox: [] } })
    render(<App />)
    await loaded()
    fireEvent.click(screen.getByText('Test without apps'))
    expect(screen.getByLabelText('Send as')).toHaveValue('aanya')                    // defaults to the first person on the board
    fireEvent.change(screen.getByLabelText('Send as'), { target: { value: 'kabir' } })
    const box = screen.getByPlaceholderText('Type a reply')
    fireEvent.change(box, { target: { value: 'stuck' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(f).toHaveBeenCalledWith('/api/dev/message',
      expect.objectContaining({ body: JSON.stringify({ member_key: 'kabir', text: 'stuck', group: false }) })))
  })

  it('ignores blank messages in the test panel', async () => {
    const f = mockApi()
    render(<App />)
    await loaded()
    fireEvent.keyDown(screen.getByPlaceholderText('Type a reply'), { key: 'Enter' })
    expect(f).not.toHaveBeenCalledWith('/api/dev/message', expect.anything())
  })
})

describe('routing', () => {
  it.each([
    ['#/activity', 'Activity'],
    ['#/reviews', 'Needs your call'],
    ['#/integrations', 'Connections'],
    ['#/nonsense', 'Project tasks'],
  ])('%s highlights %s', async (hash, label) => {
    window.location.hash = hash
    mockApi()
    render(<App />)
    await waitFor(() => expect(document.querySelector('.nav-item.on')).toHaveTextContent(label))
  })
})
