import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { Activity, ForecastTask, Task } from '../api'
import { ActivityPage } from '../pages/Workspace'
import { AgentPipeline } from './AgentPipeline'
import { Timeline } from './Timeline'

afterEach(cleanup)

const task = (id: string, extra: Partial<Task> = {}): Task => ({
  id, title: `Task ${id}`, owner: 'Tejas', owner_key: 'tejas', status: 'Pending', progress_pct: null, deadline: null, eta: null,
  blocker_text: null, blocker_type: null, blocked_by: null, blocker_resolved: false, proof_url: null, depends_on: '',
  depends_on_titles: [], at_risk: null, last_event: null, ...extra,
})
const event = (id: number, extra = {}) => ({
  id, task_id: 'T1', old_status: 'Pending' as const, new_status: 'Blocked' as const, reason: 'blocked: Figma file', engine: 'jev',
  confidence: 0.82, evidence: 'stuck, waiting for the Figma file', actor: 'bot', reverted: false, at: '2026-10-17T07:14:00Z',
  trace: ['rules: no exact match', 'jev: waiting_on 0.82', 'gate: waiting on someone → Blocked'], ...extra,
})

describe('AgentPipeline', () => {
  it('replays the most recent decision through every stage', () => {
    render(<AgentPipeline tasks={[
      task('T1', { last_event: event(3) }),
      task('T2', { title: 'Older', last_event: event(1, { evidence: 'old reply' }) }),
    ]} />)
    const panel = screen.getByLabelText('Latest agent decision')
    for (const stage of ['Heard', 'Understood', 'Safety gate', 'Result']) expect(within(panel).getByText(stage)).toBeInTheDocument()
    expect(panel).toHaveTextContent('stuck, waiting for the Figma file')
    expect(panel).toHaveTextContent('3 pipeline steps checked')
    expect(within(panel).getByLabelText('confidence 82%')).toBeInTheDocument()
    expect(panel).not.toHaveTextContent('old reply')
  })

  it('reads a trace stored as JSON text', () => {
    render(<AgentPipeline tasks={[task('T1', { last_event: event(1, { trace: '["a","b"]' }) })]} />)
    expect(screen.getByLabelText('Latest agent decision')).toHaveTextContent('2 pipeline steps checked')
  })

  it('waits politely before the first reply', () => {
    render(<AgentPipeline tasks={[task('T1')]} />)
    expect(screen.getByText(/Waiting for the first reply/)).toBeInTheDocument()
  })
})

describe('Timeline', () => {
  const ft = (id: string, start: string, end: string, extra: Partial<ForecastTask> = {}): ForecastTask => ({
    id, title: `Task ${id}`, owner: 'Soham', status: 'Pending', start, end, duration_min: 60, deadline: null, late_min: 0,
    slack_min: 30, critical: false, notes: [], priority: 10, ...extra,
  })

  it('draws one bar per task and marks late, critical and deadlines', () => {
    const now = Date.parse('2026-10-17T08:00:00Z')
    const { container } = render(<Timeline now={now} tasks={[
      ft('T1', '2026-10-17T08:00:00Z', '2026-10-17T10:00:00Z', { critical: true }),
      ft('T2', '2026-10-17T10:00:00Z', '2026-10-17T14:00:00Z', { late_min: 45, deadline: '2026-10-17T13:00:00Z' }),
    ]} />)
    expect(container.querySelectorAll('.gantt-bar')).toHaveLength(2)
    expect(container.querySelector('.gantt-bar.critical')).toBeInTheDocument()
    expect(container.querySelector('.gantt-bar.late')).toBeInTheDocument()
    expect(container.querySelectorAll('.gantt-deadline')).toHaveLength(1)
    expect(screen.getByText('now')).toBeInTheDocument()
  })

  it('renders nothing without tasks', () => {
    const { container } = render(<Timeline tasks={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('Activity filters', () => {
  const items: Activity[] = [
    { type: 'message', at: '2026-10-17T07:14:03Z', task: null, task_id: null, text: 'stuck on the API', direction: 'in', who: 'Tejas' },
    { type: 'message', at: '2026-10-17T07:14:02Z', task: null, task_id: null, text: 'How is the API going?', direction: 'out', who: 'Tejas' },
    { type: 'event', at: '2026-10-17T07:14:01Z', task: 'Build the API', task_id: 'T2', text: 'Pending → Blocked', engine: 'rules', confidence: 0.75 },
  ]

  it('shows every kind with counts, and narrows to one kind', async () => {
    render(<ActivityPage items={items} />)
    expect(screen.getByRole('tab', { name: /All 3/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('tab', { name: /Status changes 1/ }))
    expect(screen.getByText('Pending → Blocked')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('stuck on the API')).not.toBeInTheDocument())   // rows fade out first
    fireEvent.click(screen.getByRole('tab', { name: /From the agent 1/ }))
    expect(screen.getByText('How is the API going?')).toBeInTheDocument()
  })
})
