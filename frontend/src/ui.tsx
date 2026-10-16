// Shared Notion-style building blocks.
import type { ReactNode } from 'react'
import type { ChannelName, Status } from './api'

export const STATUS_COLOR: Record<Status, string> = {
  Pending: 'gray',
  'In Progress': 'blue',
  Blocked: 'red',
  Complete: 'green',
}

export const fmtTime = (iso: string) =>
  new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  })

export const fmtDay = (iso: string) =>
  new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').toLocaleDateString([], {
    month: 'short',
    day: 'numeric',
  })

export const fmtDate = (d: string | null) =>
  d ? new Date(d + 'T00:00').toLocaleDateString([], { month: 'short', day: 'numeric' }) : ''

export const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export function Tag({ color = 'gray', children, title }: { color?: string; children: ReactNode; title?: string }) {
  return <span className={`tag tag-${color}`} title={title}>{children}</span>
}

export function StatusTag({ status }: { status: Status }) {
  return (
    <Tag color={STATUS_COLOR[status]}>
      <span className="tag-dot" />
      {status}
    </Tag>
  )
}

const PALETTE = ['#E9A0A0', '#9EC1E0', '#A8D5B5', '#E8C77D', '#C9B2E3', '#E8B49A']

export function Person({ name, size = 20 }: { name: string; size?: number }) {
  const color = PALETTE[(name.charCodeAt(0) + name.length) % PALETTE.length]
  return (
    <span className="person">
      <span className="avatar" style={{ width: size, height: size, background: color, fontSize: size * 0.5 }}>
        {name[0]}
      </span>
      {name}
    </span>
  )
}

/** Which part of the decision pipeline acted. Purple = model, green = deterministic, red = human. */
export function EngineTag({ engine, confidence }: { engine: string; confidence?: number | null }) {
  const base = engine.split('/').pop() ?? engine
  const color = base === 'jev' || base === 'llm' ? 'purple' : base === 'lead' || base === 'undo' ? 'pink' : 'green'
  const label = { jev: 'Jev', llm: 'LLM', rules: 'Rules', button: 'Button', lead: 'Lead', undo: 'Undo' }[base] ?? base
  return (
    <Tag color={color} title="Which part of the pipeline decided">
      {engine.startsWith('meeting') && '🎙️ '}
      {label}
      {confidence != null && <span className="tag-num">{confidence.toFixed(2)}</span>}
    </Tag>
  )
}

export const CHANNEL_LABEL: Record<ChannelName, string> = { telegram: 'Telegram', slack: 'Slack', teams: 'Teams' }
export const CHANNEL_ICON: Record<ChannelName, string> = { telegram: '✈️', slack: '💬', teams: '🟪' }

export function PageHeader({ icon, title, description, children }: {
  icon: string
  title: string
  description?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="page-header">
      <div className="page-icon" aria-hidden>{icon}</div>
      <h1 className="page-title">{title}</h1>
      {description && <p className="page-desc">{description}</p>}
      {children}
    </header>
  )
}

export function Callout({ icon, color = 'gray', children }: { icon: string; color?: string; children: ReactNode }) {
  return (
    <div className={`callout callout-${color}`}>
      <span className="callout-icon" aria-hidden>{icon}</span>
      <div>{children}</div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}
