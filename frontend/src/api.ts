// Typed client for the FastAPI backend + a live-update hook over Server-Sent Events.
import { useCallback, useEffect, useRef, useState } from 'react'

export type Status = 'Pending' | 'In Progress' | 'Blocked' | 'Complete'
export interface Appointment {
  id: number
  title: string
  starts_at: string
  status: 'scheduled' | 'done'
  remind_min: number
  created_by: string | null
  source_text: string
  attendees: string[]
}

export const STATUSES: Status[] = ['Pending', 'In Progress', 'Blocked', 'Complete']
export type ChannelName = 'telegram' | 'slack' | 'teams'

export interface EventRow {
  id: number
  task_id: string
  old_status: Status
  new_status: Status
  reason: string
  engine: string
  confidence: number | null
  evidence: string | null
  actor: string
  reverted: boolean
  at: string
  trace?: string[] | string
}

export interface Task {
  id: string
  title: string
  owner: string
  owner_key: string
  status: Status
  progress_pct: number | null
  deadline: string | null
  eta: string | null
  blocker_text: string | null
  blocker_type: string | null
  blocked_by: string | null
  blocker_resolved: boolean
  proof_url: string | null
  depends_on: string                 // prerequisite ids, comma separated ("T1,T3")
  depends_on_titles: string[]
  at_risk: string | null
  last_event: EventRow | null
}

export interface Member { id: number; key: string; name: string; role: string; connected: boolean; channels: ChannelName[] }

export interface Board {
  tasks: Task[]
  members: Member[]
  counts: Record<string, number>
  pending_reviews: number
}

export interface Health {
  env: string
  role: string
  mode: string
  telegram: boolean
  bot_username: string | null
  slack: boolean
  teams: boolean
  recall: boolean
  jev: boolean
  jev_model: string | null
  llm: boolean
  llm_model: string | null
  slack_summary_channel: boolean
  slack_project_channel?: boolean
  neo4j?: boolean
}

export interface Activity {
  type: 'event' | 'message'
  at: string
  task: string | null
  task_id: string | null
  text: string
  engine?: string
  confidence?: number | null
  actor?: string
  reverted?: boolean
  direction?: 'in' | 'out'
  kind?: string
  who?: string
}

export interface Review {
  id: number
  task: string
  member: string | null
  text: string | null
  source: 'chat' | 'meeting'
  at: string
  proposal: { status: Status; engine: string; confidence: number; trace: string[] }
  reasons: string[]
}

export interface TimelineItem extends Partial<EventRow> {
  type: 'event' | 'message'
  at: string
  text?: string
  who?: string
  direction?: 'in' | 'out'
  kind?: string
  channel?: string
}

export interface TeamMember {
  key: string
  name: string
  email: string | null
  role: string
  preferred: ChannelName | null
  channels: ChannelName[]
  telegram_link: string | null
}

export interface MeetingSummary {
  headline: string
  summary: string[]
  decisions: string[]
  action_items: { owner: string; what: string; due: string | null; task_id: string | null }[]
  blockers: { task_id: string | null; text: string; owner: string | null }[]
  risks: string[]
}

export interface Utterance {
  id: number
  speaker: string
  member: string | null
  text: string
  start_s: number
  task_id: string | null
  flagged: boolean
  triaged: boolean
  flags: Partial<Record<'update' | 'blocker' | 'commitment' | 'decision' | 'task_conf', number>> & { engine?: string }
}

export interface Meeting {
  id: number
  title: string
  meeting_url: string | null
  provider: 'recall' | 'upload'
  status: 'created' | 'joining' | 'in_call' | 'ending' | 'processing' | 'done' | 'failed'
  error: string | null
  summary: MeetingSummary | null
  stats: { utterances?: number; flagged?: number; triage_engine?: string; summary_engine?: string; reviews?: number; reduction?: number }
  slack_ts: string | null
  created_at: string
  ended_at: string | null
  utterances?: Utterance[]
}

export interface ForecastTask {
  id: string
  title: string
  owner: string | null
  status: Status | null
  start: string
  end: string
  duration_min: number
  deadline: string | null
  late_min: number
  slack_min: number | null
  critical: boolean
  notes: string[]
  priority: number | null
}

export interface RiskEvent {
  risk_type: string
  severity: 'LOW' | 'MEDIUM' | 'HIGH'
  task_id: string | null
  summary: string
  evidence: string[]
  attribution: 'direct' | 'possible' | 'none'
  delay_minutes: number
  confidence: string
}

export interface Forecast {
  status: string
  solver?: string
  confidence?: 'HIGH' | 'MEDIUM' | 'LOW'
  version?: string
  computed_at?: string
  project_end?: string | null
  previous_project_end?: string | null
  total_late_min?: number
  assumptions?: string[]
  note?: string
  solve_ms?: number
  trigger?: string
  tasks: ForecastTask[]
  risks: RiskEvent[]
  analysis: { bottlenecks?: { task_id: string; waiting_behind: number }[]; critical_path?: string[]; issues?: string[] }
}

export interface GraphData {
  nodes: { id: string; title: string; owner: string | null; status: Status; end: string | null; late_min: number; critical: boolean; blocked: boolean }[]
  edges: { source: string; target: string }[]
}

export interface NetNode {
  id: string                       // "Task:T2", "Person:3", "Project:main"
  type: 'Project' | 'Person' | 'Task' | string
  name?: string
  status?: Status
  role?: string
  blocker?: string
  deadline?: string
  forecast_end?: string
  late_minutes?: number
  priority_score?: number
}

export interface NetEdge { source: string; target: string; type: 'OWNS' | 'REQUESTED' | 'DEPENDS_ON' | 'HAS_TASK' | string }

export interface NetworkData {
  source: 'neo4j' | 'database'
  browser: string | null
  error?: string
  nodes: NetNode[]
  edges: NetEdge[]
}

export interface NewTask {
  title: string
  owner_key: string
  deadline?: string
  est_minutes?: number
  depends_on: string[]
}

export interface AgentActionRow {
  id: number
  created_at: string
  type: string
  risk_type: string
  task: string | null
  task_id: string
  recipient: string | null
  text: string
  status: 'pending' | 'sent' | 'failed' | 'unreachable' | 'committed' | 'resolved' | 'expired'
  channel: string | null
  commitment_at: string | null
  response_text: string | null
  evidence: string[]
  severity: string | null
}

// Where the backend lives. Empty in development (Vite proxies /api to localhost:8000); the production build
// points at the hosted API (frontend/.env.production), which allows this site's origin through CORS.
export const API_BASE = (import.meta.env.VITE_API_URL ?? '').replace(/\/+$/, '')

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData
  const r = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers: isForm ? init?.headers : { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail ?? detail } catch { /* not JSON */ }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

const post = <T>(path: string, body: unknown = {}) => req<T>(path, { method: 'POST', body: JSON.stringify(body) })

export const api = {
  health: () => req<Health>('/health'),
  board: () => req<Board>('/board'),
  activity: () => req<Activity[]>('/activity'),
  reviews: () => req<Review[]>('/reviews'),
  team: () => req<TeamMember[]>('/team'),
  setPreferred: (key: string, channel: ChannelName) => post(`/team/${key}/preferred`, { channel }),
  timeline: (id: string) => req<{ items: TimelineItem[] }>(`/tasks/${id}/timeline`),
  checkin: (task_id?: string) => post<{ sent: number }>('/checkin', { task_id }),
  undo: (eventId: number) => post('/events/' + eventId + '/undo'),
  resolveReview: (id: number, verdict: string) => post(`/reviews/${id}/resolve`, { verdict }),
  reset: () => post('/reset'),
  meetings: () => req<Meeting[]>('/meetings'),
  appointments: () => req<Appointment[]>('/appointments'),
  cancelAppointment: (id: number) => req<{ ok: boolean }>(`/appointments/${id}`, { method: 'DELETE' }),
  meeting: (id: number) => req<Meeting>(`/meetings/${id}`),
  joinMeeting: (meeting_url: string, title: string) => post<Meeting>('/meetings', { meeting_url, title }),
  finishMeeting: (id: number) => post(`/meetings/${id}/finish`),
  uploadTranscript: (title: string, file: File | null, text: string) => {
    const fd = new FormData()
    fd.append('title', title)
    if (file) fd.append('file', file)
    else fd.append('text', text)
    return req<Meeting>('/meetings/upload', { method: 'POST', body: fd })
  },
  network: () => req<NetworkData>('/intel/network'),
  createTask: (t: NewTask) => post<{ id: string; title: string }>('/tasks', t),
  addDependency: (id: string, depends_on: string) => post<{ ok: boolean }>(`/tasks/${id}/dependencies`, { depends_on }),
  removeDependency: (id: string, dep: string) => req<{ ok: boolean }>(`/tasks/${id}/dependencies/${dep}`, { method: 'DELETE' }),
  inferDependencies: () => post<{ linked: string[] }>('/tasks/infer-dependencies'),
  deleteTask: (id: string) => req<{ ok: boolean }>(`/tasks/${id}`, { method: 'DELETE' }),
  forecast: () => req<Forecast>('/intel/forecast'),
  graph: () => req<GraphData>('/intel/graph'),
  agentActions: () => req<AgentActionRow[]>('/intel/actions'),
  replan: (send: boolean) => post<{ actions: { text: string; recipient: string; type: string }[] }>(`/intel/replan?send=${send}`),
  devMessage: (member_key: string, text: string, group = false) =>
    post<{ outbox: { to: string; text: string }[] }>('/dev/message', { member_key, text, group }),
}

/** Loads shared data once, then refetches whenever the server pushes a change. */
export function useLiveData() {
  const [board, setBoard] = useState<Board | null>(null)
  const [activity, setActivity] = useState<Activity[]>([])
  const [reviews, setReviews] = useState<Review[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [live, setLive] = useState(false)
  const [tick, setTick] = useState(0)          // bumps on every server event; pages refetch their own data
  const timer = useRef<number | undefined>(undefined)

  const refresh = useCallback(async () => {
    try {
      const [b, a, r] = await Promise.all([api.board(), api.activity(), api.reviews()])
      setBoard(b)
      setActivity(a)
      setReviews(r)
      setTick((t) => t + 1)
    } catch {
      /* backend restarting; the stream reconnects on its own */
    }
  }, [])

  useEffect(() => {
    refresh()
    api.health().then(setHealth).catch(() => setHealth(null))
    const es = new EventSource(`${API_BASE}/api/stream`)
    es.onopen = () => setLive(true)
    es.onerror = () => setLive(false)
    es.onmessage = () => {
      window.clearTimeout(timer.current)            // several events per reply: coalesce into one refetch
      timer.current = window.setTimeout(refresh, 120)
    }
    return () => es.close()
  }, [refresh])

  return { board, activity, reviews, health, live, tick, refresh }
}

/** Minimal hash router: #/tasks, #/meetings/3 … */
export function useRoute(): [string[], (path: string) => void] {
  const read = () => (window.location.hash.replace(/^#\/?/, '') || 'tasks').split('/')
  const [parts, setParts] = useState(read)
  useEffect(() => {
    const on = () => setParts(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return [parts, (path: string) => { window.location.hash = '/' + path }]
}
