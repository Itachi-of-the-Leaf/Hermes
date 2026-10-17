import { useEffect, useLayoutEffect, useRef, useState } from 'react'

// The masthead: real lines from our team's group chat flow through the agent into what it records.
const MESSAGES = [
  { id: 'm1', who: 'Atharva', text: 'Tejas we need a fully functioning website by the 20th' },
  { id: 'm2', who: 'Soham', text: 'Group meeting in 20 minutes, Tejas make sure everyone is there' },
  { id: 'm3', who: 'Samarth', text: "I'll build the API once the designs are in" },
]
const OUTPUTS = [
  { id: 'o1', from: 'm1', title: 'Build the website', meta: 'Tejas, due Tue 20 Oct, asked by Atharva' },
  { id: 'o2', from: 'm2', title: 'Team meeting at 05:10', meta: 'everyone, reminder 5 minutes before' },
  { id: 'o3', from: 'm3', title: 'Build the API', meta: 'Samarth, waits for the designs' },
  { id: 'o4', from: null, title: 'Finish the designs', meta: 'Soham, in progress' },
]

type Path = { id: string; d: string; hot: boolean }

const reducedMotion = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Message i lights up, the agent reads it, output i lands; then the next one. */
function useCycle(stages: number, ms: number) {
  const [step, setStep] = useState(-1)
  useEffect(() => {
    if (reducedMotion()) return
    const start = setTimeout(() => setStep(0), 1600)            // after the shutter has opened
    const tick = setInterval(() => setStep((s) => (s < 0 ? s : (s + 1) % stages)), ms)
    return () => { clearTimeout(start); clearInterval(tick) }
  }, [stages, ms])
  return step
}

export function Flow() {
  const box = useRef<HTMLDivElement>(null)
  const [paths, setPaths] = useState<Path[]>([])
  const [size, setSize] = useState({ w: 0, h: 0 })
  const step = useCycle(MESSAGES.length * 3, 1100)
  const active = step < 0 ? -1 : Math.floor(step / 3)
  const phase = step < 0 ? -1 : step % 3                         // 0: message, 1: agent, 2: output

  // connectors are measured from the laid-out nodes, so they follow any width (stacked on a phone)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const draw = () => {
      const base = el.getBoundingClientRect()
      const r = (id: string) => {
        const n = el.querySelector<HTMLElement>(`[data-flow="${id}"]`)
        if (!n || n.offsetParent === null) return null
        const b = n.getBoundingClientRect()
        return { l: b.left - base.left, r: b.right - base.left, t: b.top - base.top, b: b.bottom - base.top, cx: b.left - base.left + b.width / 2, cy: b.top - base.top + b.height / 2 }
      }
      const agent = r('agent')
      if (!agent) return
      const stacked = el.clientWidth < 700
      const link = (a: NonNullable<ReturnType<typeof r>>, b: NonNullable<ReturnType<typeof r>>) => {
        if (stacked) {
          const y = (a.b + b.t) / 2
          return `M${a.cx},${a.b} C${a.cx},${y} ${b.cx},${y} ${b.cx},${b.t}`
        }
        const x = (a.r + b.l) / 2
        return `M${a.r},${a.cy} C${x},${a.cy} ${x},${b.cy} ${b.l},${b.cy}`
      }
      const out: Path[] = []
      MESSAGES.forEach((m, i) => { const a = r(m.id); if (a) out.push({ id: `in-${m.id}`, d: link(a, agent), hot: i === active && phase === 0 }) })
      OUTPUTS.forEach((o, i) => {
        const b = r(o.id)
        if (b && o.from) out.push({ id: `out-${o.id}`, d: link(agent, b), hot: i === active && phase >= 1 })
      })
      const o3 = r('o3'), o4 = r('o4')                          // "waits for": the API task depends on the designs
      if (o3 && o4 && !stacked) {
        const x = o3.r + 20
        out.push({ id: 'dep', d: `M${o3.r},${o3.cy} C${x},${o3.cy} ${x},${o4.cy} ${o4.r},${o4.cy}`, hot: false })
      }
      setPaths(out)
      setSize({ w: el.clientWidth, h: el.clientHeight })
    }
    draw()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(draw) : null
    ro?.observe(el)
    return () => ro?.disconnect()
  }, [active, phase])

  const moving = !reducedMotion()
  return (
    <div className="flow" ref={box} data-ui>
      <svg className="flow-lines" aria-hidden="true" viewBox={`0 0 ${size.w || 1} ${size.h || 1}`}>
        <defs>
          <marker id="flow-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L8,4 L0,8 z" className="flow-arrow" />
          </marker>
        </defs>
        {paths.map((p) => (
          <path key={p.id} id={`flow-${p.id}`} d={p.d} className={p.hot ? 'hot' : undefined} data-path={p.id}
            markerEnd={p.id === 'dep' ? 'url(#flow-arrow)' : undefined} />
        ))}
        {moving && paths.filter((p) => p.id !== 'dep').map((p, i) => (
          <circle key={`dot-${p.id}`} r="3.5" className="flow-dot" visibility="hidden">
            <set attributeName="visibility" to="visible" begin={`${(i * 0.45).toFixed(2)}s`} />
            <animateMotion dur="2.6s" repeatCount="indefinite" begin={`${(i * 0.45).toFixed(2)}s`}>
              <mpath href={`#flow-${p.id}`} />
            </animateMotion>
          </circle>
        ))}
      </svg>
      <ol className="flow-col flow-in" aria-label="Messages in the group">
        {MESSAGES.map((m, i) => (
          <li key={m.id} className={`node msg${i === active && phase === 0 ? ' is-hot' : ''}`} data-flow={m.id}>
            <span className="who">{m.who}</span>{m.text}
          </li>
        ))}
      </ol>
      <div className="flow-col flow-mid">
        <div className={`node agent${phase === 1 ? ' is-busy' : ''}`} data-flow="agent">
          <strong>Saarathi</strong>
          <span>reads every message, checks each guess, plans the week</span>
        </div>
      </div>
      <ol className="flow-col flow-out" aria-label="What the agent records">
        {OUTPUTS.map((o, i) => (
          <li key={o.id} className={`node out${o.from ? '' : ' quiet'}${i === active && phase === 2 ? ' is-new' : ''}`} data-flow={o.id}>
            <strong>{o.title}</strong><span>{o.meta}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
