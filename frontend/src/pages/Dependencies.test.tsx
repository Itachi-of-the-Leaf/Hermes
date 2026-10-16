import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Board, Task } from '../api'
import { TasksPage } from './Tasks'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const task = (id: string, title: string, depends_on = ''): Task => ({
  id, title, owner: 'Ada', owner_key: 'ada', status: 'Pending', progress_pct: null, deadline: null, eta: null, blocker_text: null, blocker_type: null,
  blocked_by: null, blocker_resolved: false, proof_url: null, depends_on, depends_on_titles: [], at_risk: null, last_event: null,
})
const members = [{ id: 1, key: 'ada', name: 'Ada', role: 'lead', connected: false, channels: [] }]
const board = (...tasks: Task[]): Board => ({ tasks, members, counts: {}, pending_reviews: 0 })

function mock(handler: (url: string, init?: RequestInit) => unknown = () => ({ items: [] })) {
  const calls: { url: string; method: string }[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method ?? 'GET' })
    const r = handler(url, init)
    return Promise.resolve(r instanceof Response ? r : new Response(JSON.stringify(r), { status: 200 }))
  }))
  return calls
}

const open = (title: string) => fireEvent.click(screen.getAllByText(title)[0])

describe('prerequisites on a task', () => {
  it('lists what the task waits for, by title', async () => {
    mock()
    render(<TasksPage board={board(task('T1', 'Backend API'), task('T2', 'Dashboard', 'T1'))} tick={0} />)
    open('Dashboard')
    expect(await screen.findByRole('button', { name: 'Remove prerequisite Backend API' })).toBeInTheDocument()
  })

  it('says so plainly when it waits for nothing', async () => {
    mock()
    render(<TasksPage board={board(task('T1', 'Backend API'))} tick={0} />)
    open('Backend API')
    expect(await screen.findByText('Nothing')).toBeInTheDocument()
  })

  it('removing one calls the API', async () => {
    const calls = mock()
    render(<TasksPage board={board(task('T1', 'Backend API'), task('T2', 'Dashboard', 'T1'))} tick={0} />)
    open('Dashboard')
    fireEvent.click(await screen.findByRole('button', { name: 'Remove prerequisite Backend API' }))
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/tasks/T2/dependencies/T1', method: 'DELETE' }))
  })

  it('adding one offers every other task that is not already a prerequisite, never itself', async () => {
    const calls = mock()
    render(<TasksPage board={board(task('T1', 'Backend API'), task('T2', 'Dashboard', 'T1'), task('T3', 'Design review'))} tick={0} />)
    open('Dashboard')
    const select = await screen.findByLabelText('Add prerequisite')
    const options = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(options).toEqual(['+ add prerequisite', 'Design review'])
    fireEvent.change(select, { target: { value: 'T3' } })
    await waitFor(() => expect(calls).toContainEqual({ url: '/api/tasks/T2/dependencies', method: 'POST' }))
  })

  it('shows why a prerequisite was refused (for example a loop)', async () => {
    mock((url, init) => init?.method === 'POST' && url.includes('dependencies')
      ? new Response(JSON.stringify({ detail: 'Backend API already waits for Dashboard, so this would be a loop' }), { status: 400 }) : { items: [] })
    render(<TasksPage board={board(task('T1', 'Backend API', 'T2'), task('T2', 'Dashboard'))} tick={0} />)
    open('Dashboard')
    fireEvent.change(await screen.findByLabelText('Add prerequisite'), { target: { value: 'T1' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('would be a loop')
  })

  it('offers no picker when there is nothing left to add', async () => {
    mock()
    render(<TasksPage board={board(task('T1', 'Backend API'), task('T2', 'Dashboard', 'T1'))} tick={0} />)
    open('Dashboard')
    await screen.findByRole('button', { name: 'Remove prerequisite Backend API' })
    expect(screen.queryByLabelText('Add prerequisite')).not.toBeInTheDocument()
  })
})

describe('Link dependencies', () => {
  it('is disabled with fewer than two tasks', () => {
    mock()
    render(<TasksPage board={board(task('T1', 'Only one'))} tick={0} />)
    expect(screen.getByRole('button', { name: 'Link dependencies' })).toBeDisabled()
  })

  it('asks the agent and reports what it linked', async () => {
    const calls = mock((url) => (url.includes('infer') ? { linked: ['Dashboard now waits for Backend API'] } : {}))
    render(<TasksPage board={board(task('T1', 'Backend API'), task('T2', 'Dashboard'))} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'Link dependencies' }))
    expect(await screen.findByText(/Linked 1 dependency: Dashboard now waits for Backend API/)).toBeInTheDocument()
    expect(calls).toContainEqual({ url: '/api/tasks/infer-dependencies', method: 'POST' })
  })

  it('says so when nothing is missing', async () => {
    mock(() => ({ linked: [] }))
    render(<TasksPage board={board(task('T1', 'A thing'), task('T2', 'Another thing'))} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'Link dependencies' }))
    expect(await screen.findByText('No obvious dependencies are missing.')).toBeInTheDocument()
  })

  it('shows the reason when it cannot run (for example no LLM configured)', async () => {
    mock(() => new Response(JSON.stringify({ detail: 'An LLM is needed to infer dependencies' }), { status: 400 }))
    render(<TasksPage board={board(task('T1', 'A thing'), task('T2', 'Another thing'))} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'Link dependencies' }))
    expect(await screen.findByText(/An LLM is needed/)).toBeInTheDocument()
  })
})
