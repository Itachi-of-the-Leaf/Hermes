import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Board, Task } from '../api'
import { TasksPage } from './Tasks'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const task = (id: string, title: string, owner_key: string, status: Task['status'] = 'Pending'): Task => ({
  id, title, owner: owner_key.toUpperCase(), owner_key, status, progress_pct: null, deadline: null, eta: null, blocker_text: null, blocker_type: null,
  blocked_by: null, blocker_resolved: false, proof_url: null, depends_on: '', depends_on_titles: [], at_risk: null, last_event: null,
})
const members = [
  { id: 1, key: 'ada', name: 'Ada', role: 'lead', connected: false, channels: [] },
  { id: 2, key: 'bo', name: 'Bo', role: 'member', connected: false, channels: [] },
]
const board = (tasks: Task[]): Board => ({ tasks, members, counts: {}, pending_reviews: 0 })

function mock(handler: (url: string, init?: RequestInit) => unknown = () => ({})) {
  const calls: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, init })
    const r = handler(url, init)
    if (r instanceof Response) return Promise.resolve(r)
    return Promise.resolve(new Response(JSON.stringify(r), { status: 200 }))
  }))
  return calls
}

describe('empty project', () => {
  it('says how to add the first task, in the app and in chat', () => {
    mock()
    render(<TasksPage board={board([])} tick={0} />)
    expect(screen.getByText(/No tasks yet/)).toBeInTheDocument()
    expect(screen.getByText(/task: Build the login page @ada due friday est 3h/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New task' })).toBeInTheDocument()
  })
  it('shows no empty board columns when there is nothing to put in them', () => {
    mock()
    render(<TasksPage board={board([])} tick={0} />)
    expect(screen.queryByLabelText('Pending')).not.toBeInTheDocument()
  })
})

describe('New task form', () => {
  it('opens, offers every person and every existing task, and closes again', () => {
    mock()
    render(<TasksPage board={board([task('T1', 'First thing', 'ada')])} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'New task' }))
    const form = screen.getByRole('form', { name: 'New task' })
    expect(form).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Ada' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Bo' })).toBeInTheDocument()
    expect(screen.getByLabelText(/First thing/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('form', { name: 'New task' })).not.toBeInTheDocument()
  })

  it('will not submit without a real title', () => {
    mock()
    render(<TasksPage board={board([])} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'New task' }))
    const create = screen.getByRole('button', { name: 'Create task' })
    expect(create).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ab' } })
    expect(create).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Build it' } })
    expect(create).toBeEnabled()
  })

  it('posts exactly what was entered, converting hours to minutes', async () => {
    const calls = mock(() => ({ id: 'T7', title: 'Write the report' }))
    render(<TasksPage board={board([task('T1', 'First thing', 'ada'), task('T2', 'Second thing', 'ada')])} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'New task' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: '  Write the report ' } })
    fireEvent.change(screen.getByLabelText('Owner'), { target: { value: 'bo' } })
    fireEvent.change(screen.getByLabelText('Due'), { target: { value: '2026-10-30' } })
    fireEvent.change(screen.getByLabelText('Estimate (hours)'), { target: { value: '1.5' } })
    fireEvent.click(screen.getByLabelText(/First thing/))
    fireEvent.click(screen.getByLabelText(/Second thing/))
    fireEvent.click(screen.getByLabelText(/First thing/))                                     // untick again
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/tasks')).toBe(true))
    const body = JSON.parse(calls.find((c) => c.url === '/api/tasks')!.init!.body as string)
    expect(body).toEqual({ title: 'Write the report', owner_key: 'bo', deadline: '2026-10-30', est_minutes: 90, depends_on: ['T2'] })
    expect(await screen.findByText('Created T7 · Write the report')).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'New task' })).not.toBeInTheDocument()
  })

  it('leaves the optional fields out when they are empty (an unknown estimate stays unknown)', async () => {
    const calls = mock(() => ({ id: 'T1', title: 'Just a title' }))
    render(<TasksPage board={board([])} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'New task' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Just a title' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/tasks')).toBe(true))
    const body = JSON.parse(calls.find((c) => c.url === '/api/tasks')!.init!.body as string)
    expect(body.est_minutes).toBeUndefined()
    expect(body.deadline).toBeUndefined()
    expect(body.depends_on).toEqual([])
  })

  it('shows the server reason and keeps the form open when the task is refused', async () => {
    mock(() => new Response(JSON.stringify({ detail: 'unknown prerequisite T9' }), { status: 400 }))
    render(<TasksPage board={board([])} tick={0} />)
    fireEvent.click(screen.getByRole('button', { name: 'New task' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Something' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create task' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('unknown prerequisite T9')
    expect(screen.getByRole('form', { name: 'New task' })).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('Something')
  })
})

describe('deleting a task', () => {
  const open = () => {
    render(<TasksPage board={board([task('T1', 'Doomed task', 'ada')])} tick={0} />)
    fireEvent.click(screen.getByText('Doomed task'))
  }

  it('asks first, then deletes and closes the panel', async () => {
    const calls = mock((url) => (url.includes('timeline') ? { items: [] } : { ok: true }))
    vi.stubGlobal('confirm', vi.fn(() => true))
    open()
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(calls.some((c) => c.url === '/api/tasks/T1' && c.init?.method === 'DELETE')).toBe(true))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('does nothing if you say no', async () => {
    const calls = mock((url) => (url.includes('timeline') ? { items: [] } : { ok: true }))
    vi.stubGlobal('confirm', vi.fn(() => false))
    open()
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }))
    expect(calls.some((c) => c.init?.method === 'DELETE')).toBe(false)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
