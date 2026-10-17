import { motion } from 'motion/react'
import type { ForecastTask } from '../api'
import { fmtDay, fmtTime } from '../ui'

const ms = (iso: string) => new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime()

/** The CP-SAT schedule as a Gantt chart: one bar per task, deadlines as ticks, a line for now. */
export function Timeline({ tasks, now = Date.now() }: { tasks: ForecastTask[]; now?: number }) {
  if (tasks.length === 0) return null
  const ends = tasks.map((t) => ms(t.end))
  const t0 = Math.min(now, ...tasks.map((t) => ms(t.start)))
  const last = Math.max(...ends)
  const deadlines = tasks.filter((t) => t.deadline).map((t) => ms(t.deadline!)).filter((d) => d <= last + (last - t0))
  const t1 = Math.max(last, ...deadlines) + (last - t0) * 0.04 || t0 + 3600_000
  const pct = (t: number) => `${(((t - t0) / (t1 - t0)) * 100).toFixed(2)}%`
  const ticks = Array.from({ length: 4 }, (_, i) => t0 + ((t1 - t0) * (i + 1)) / 4)     // the start is marked by "now"

  return (
    <div className="gantt" role="img" aria-label="Forecast timeline">
      <div className="gantt-axis">
        <span />
        <div className="gantt-scale">
          {ticks.map((t) => (
            <span key={t} style={{ left: pct(t) }}>{fmtDay(new Date(t).toISOString())} {fmtTime(new Date(t).toISOString())}</span>
          ))}
        </div>
      </div>
      {tasks.map((t, i) => {
        const late = t.late_min > 0
        return (
          <div key={t.id} className="gantt-row">
            <span className="gantt-label" title={t.title}><b>{t.title}</b><span className="muted small">{t.owner ?? ''}</span></span>
            <div className="gantt-track">
              <motion.span
                className={`gantt-bar${t.critical ? ' critical' : ''}${late ? ' late' : ''}${t.status === 'Blocked' ? ' blocked' : ''}`}
                style={{ left: pct(ms(t.start)), width: `max(6px, calc(${pct(ms(t.end))} - ${pct(ms(t.start))}))`, originX: 0 }}
                initial={{ scaleX: 0, opacity: 0.4 }} animate={{ scaleX: 1, opacity: 1 }}
                transition={{ delay: 0.15 + i * 0.07, duration: 0.6, ease: [0.2, 0.7, 0.2, 1] }}
                title={`${t.title}: ${fmtTime(t.start)} to ${fmtTime(t.end)}${late ? `, late ${t.late_min} min` : ''}`} />
              {t.deadline && ms(t.deadline) <= t1 && <span className="gantt-deadline" style={{ left: pct(ms(t.deadline)) }} title={`Deadline ${fmtTime(t.deadline)}`} />}
            </div>
          </div>
        )
      })}
      <div className="gantt-now" style={{ left: `calc(var(--gantt-label) + (100% - var(--gantt-label)) * ${((now - t0) / (t1 - t0)).toFixed(4)})` }}>
        <span>now</span>
      </div>
      <p className="gantt-legend small muted">
        <i className="lg critical" /> critical path <i className="lg" /> other work <i className="lg late" /> late <i className="lg tick" /> deadline
      </p>
    </div>
  )
}
