import { Fragment } from 'react'
import { motion } from 'motion/react'
import type { EventRow, Task } from '../api'
import { EngineTag, fmtTime, StatusTag } from '../ui'

const STEP_GAP = 0.38                 // seconds between stages lighting up

function traceLines(trace: EventRow['trace']): string[] {
  if (Array.isArray(trace)) return trace
  try { const v = JSON.parse(trace ?? '[]'); return Array.isArray(v) ? v : [] } catch { return [] }
}

/** The latest decision replayed as the pipeline that made it: heard → understood → gate → result. */
export function AgentPipeline({ tasks }: { tasks: Task[] }) {
  const latest = tasks.filter((t) => t.last_event).sort((a, b) => b.last_event!.id - a.last_event!.id)[0]
  if (!latest) {
    return (
      <section className="block pipeline" aria-label="Latest agent decision">
        <div className="pipeline-head"><span className="block-title">Agent at work</span></div>
        <p className="muted small">Waiting for the first reply. When someone answers a check-in, you will see how the agent read it here.</p>
      </section>
    )
  }
  const ev = latest.last_event!
  const conf = ev.confidence ?? 1
  const checks = traceLines(ev.trace).length
  const steps = [
    { k: 'Heard', body: <><b>{ev.actor === 'bot' ? latest.owner : ev.actor}</b><span className="pstep-quote">“{ev.evidence ?? ev.reason}”</span></> },
    { k: 'Understood', body: <>
        <EngineTag engine={ev.engine} confidence={ev.confidence} />
        <span className="pconf" aria-label={`confidence ${Math.round(conf * 100)}%`}>
          <motion.span initial={{ width: 0 }} animate={{ width: `${Math.round(conf * 100)}%` }}
            transition={{ delay: STEP_GAP + 0.2, duration: 0.7, ease: [0.2, 0.7, 0.2, 1] }} />
        </span>
      </> },
    { k: 'Safety gate', body: <><span className="pstep-text">{ev.reason}</span>{checks > 0 && <span className="muted small">{checks} pipeline step{checks === 1 ? '' : 's'} checked</span>}</> },
    { k: 'Result', body: <><span className="pstep-status"><StatusTag status={ev.old_status} /><span aria-hidden="true">→</span><StatusTag status={ev.new_status} /></span><b>{latest.title}</b></> },
  ]
  return (
    <section className="block pipeline" aria-label="Latest agent decision">
      <div className="pipeline-head">
        <span className="block-title">Agent at work</span>
        <span className="muted small">latest decision · {fmtTime(ev.at)}</span>
      </div>
      <ol className="pipeline-steps" key={ev.id}>
        {steps.map((s, i) => (
          <Fragment key={s.k}>
            <motion.li className="pstep" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * STEP_GAP, duration: 0.4, ease: [0.2, 0.7, 0.2, 1] }}>
              <span className="pstep-k"><span className="pstep-n">{i + 1}</span>{s.k}</span>
              {s.body}
            </motion.li>
            {i < steps.length - 1 && (
              <li className="pconn" aria-hidden="true">
                <motion.span className="pconn-fill" initial={{ scaleX: 0 }} animate={{ scaleX: 1 }}
                  transition={{ delay: i * STEP_GAP + 0.2, duration: 0.3 }} />
                <span className="pconn-dot" style={{ animationDelay: `${i * STEP_GAP + 0.5}s` }} />
              </li>
            )}
          </Fragment>
        ))}
      </ol>
    </section>
  )
}
