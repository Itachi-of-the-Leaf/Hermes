// Shared building blocks of the Saarathi dashboard.
import type { ReactNode } from 'react'
import type { ChannelName, Status } from './api'
import BlurText from './components/reactbits/BlurText'

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

/** Message text as the dashboard shows it: the bot's emoji (🆕, ✅, 👋…) dropped, spacing tidied. */
export const plain = (text: string | null | undefined) =>
  (text ?? '').replace(/[\p{Extended_Pictographic}\u{1F1E6}-\u{1F1FF}\u{FE0F}\u{20E3}]/gu, '').replace(/\s{2,}/g, ' ').trim()

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

const PALETTE = ['#22396F', '#3D5A99', '#5B6FA8', '#7A5A2E', '#8E3B5F', '#4f7a3f']   // navy family plus warm notes; white initials

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
      {engine.startsWith('meeting') && 'Meeting '}
      {label}
      {confidence != null && <span className="tag-num">{confidence.toFixed(2)}</span>}
    </Tag>
  )
}

export const CHANNEL_LABEL: Record<ChannelName, string> = { telegram: 'Telegram', slack: 'Slack', teams: 'Teams' }

export function PageHeader({ icon, title, description, children }: {
  icon?: string
  title: string
  description?: ReactNode
  children?: ReactNode
}) {
  return (
    <header className="page-header" data-icon={icon}>
      {/* React Bits BlurText: the title resolves word by word when the page opens */}
      <h1 className="page-title"><BlurText key={title} text={title} trigger="load" animateBy="words" direction="bottom" delay={90} /></h1>
      {description && <p className="page-desc">{description}</p>}
      {children}
    </header>
  )
}

export function Callout({ icon, color = 'gray', children }: { icon?: string; color?: string; children: ReactNode }) {
  return (
    <div className={`callout callout-${color}`} data-icon={icon}>
      <div>{children}</div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}
