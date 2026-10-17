import type { CSSProperties } from 'react'
import { useEffect, useState } from 'react'
import { Logo } from '../components/Logo'
import { Flow } from './Flow'
import activityImg from './img/activity.jpg'
import forecastImg from './img/forecast.jpg'
import graphImg from './img/graph.jpg'
import tasksImg from './img/tasks.jpg'
import { fmtDay, fmtTime, parseMessage } from './parseMessage'

const REPO = 'https://github.com/Tejas-Santosh-Nalawade/Kurukshetra-Hackathon-2026'
const DASHBOARD = '/dashboard/'

/** Adds `is-in` to every [data-reveal] once it scrolls into view. Without an observer everything is shown at once. */
function useReveal() {
  useEffect(() => {
    const targets = document.querySelectorAll<HTMLElement>('[data-reveal]')
    if (!('IntersectionObserver' in window)) { targets.forEach((el) => el.classList.add('is-in')); return }
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add('is-in'); io.unobserve(e.target) }
    }, { rootMargin: '0px 0px -12% 0px', threshold: 0.1 })
    targets.forEach((el) => io.observe(el))
    const safety = setTimeout(() => targets.forEach((el) => {                // never leave something on screen hidden
      const r = el.getBoundingClientRect()
      if (r.top < innerHeight && r.bottom > 0) el.classList.add('is-in')
    }), 2500)
    return () => { io.disconnect(); clearTimeout(safety) }
  }, [])
}

export function Landing() {
  useReveal()
  return (
    <>
      <header className="bar wrap">
        <a className="wordmark" href="#top" aria-label="Saarathi, back to top"><Logo size={22} />Saarathi</a>
        <nav className="bar-links" aria-label="Sections">
          <a className="link" href="#week">How it works</a>
          <a className="link" href="#try">Try it</a>
          <a className="link" href="#inside">Inside a task</a>
        </nav>
        <a className="button" href={DASHBOARD}>Get started</a>
      </header>
      <main id="top">
        <section className="masthead shutter" aria-label="How a chat message becomes part of the plan">
          <Flow />
        </section>
        <Opening />
        <Week />
        <TryIt />
        <BoardPage />
        <Inside />
        <Close />
      </main>
      <Footer />
    </>
  )
}

function Opening() {
  return (
    <section className="opening wrap axis-grid">
      <p className="side small">Saarathi is Sanskrit for charioteer: the one who keeps the chariot on course so everyone else can do the work.</p>
      <div className="axis">
        <h1 className="display lines">
          <span className="line"><span data-line style={{ '--i': 0 } as CSSProperties}>Chat becomes</span></span>{' '}
          <span className="line"><span data-line style={{ '--i': 1 } as CSSProperties}>the plan.</span></span>
        </h1>
        <p className="lead" data-rise style={{ '--i': 3 } as CSSProperties}>
          Saarathi sits in your team's Telegram group and reads it the way a good project lead would. Who said they would do
          what, by when, and what has to happen first becomes a live task graph, and the agent follows up so nobody has to nag.
        </p>
      </div>
    </section>
  )
}

function Week() {
  return (
    <section className="week wrap axis-grid" id="week">
      <p className="side small">Telegram first. The same agent also works in Slack and Microsoft Teams.</p>
      <div className="axis">
        <h2 className="head" data-reveal>A week in the group</h2>
        <ol className="exchanges">
          <li className="exchange" data-reveal>
            <p className="say">First, someone says what needs doing.</p>
            <div className="call" data-ui><span className="who">Atharva</span>Tejas we need a fully functioning website by the 20th</div>
            <div className="reply" data-ui>
              <span className="who bot">Saarathi</span>
              <span className="typing" aria-hidden="true"><i /><i /><i /></span>
              <span className="reply-text">New task T3, <b>Build a fully functioning website</b> for <b className="mention">@Tejas</b>, due Tue 20 Oct.</span>
              <span className="chip">Undo</span>
            </div>
          </li>
          <li className="exchange" data-reveal>
            <p className="say">Then it checks in before the deadline.</p>
            <div className="call" data-ui><span className="who bot">Saarathi</span>Hi Tejas, how is the website going? It is due Tue 20 Oct.</div>
            <div className="reply" data-ui>
              <span className="who">Tejas</span>
              <span className="typing" aria-hidden="true"><i /><i /><i /></span>
              <span className="reply-text">stuck, waiting for the Figma file from Soham</span>
              <span className="status"><b>Blocked</b> Soham asked to send the Figma file, Tejas told when it lands.</span>
            </div>
          </li>
          <li className="exchange last" data-reveal>
            <p className="say">And the plan keeps itself current.</p>
            <figure className="result">
              <img src={forecastImg} width={2080} height={1004} loading="lazy"
                alt="The Forecast page: a CP-SAT schedule marked optimal, the projected finish, the dependency graph and the schedule table with the website task on the critical path." />
              <figcaption className="small">The real Forecast page. Every reply re-runs a CP-SAT schedule over the dependency graph and shows when the project actually finishes.</figcaption>
            </figure>
          </li>
        </ol>
      </div>
    </section>
  )
}

const SAMPLES = ["I'll do the slides tomorrow", 'Samarth can you set up the database by 22 Oct', 'group meeting in 20 minutes', 'anyone up for chai']

export function TryIt({ now }: { now?: Date }) {
  const [text, setText] = useState('Soham please finish the login page by Friday, after the API is done')
  const [today] = useState(() => now ?? new Date())          // fixed for the visit; the preview is about dates, not seconds
  const reading = parseMessage(text, today)
  return (
    <section className="split" id="try" aria-labelledby="try-a">
      <div className="split-in">
        <label className="split-label small" htmlFor="try-input">Write it the way you would in the group</label>
        <textarea id="try-input" className="try-input" rows={4} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
        <div className="samples" role="group" aria-label="Try another message">
          {SAMPLES.map((s) => <button key={s} type="button" className="sample" onClick={() => setText(s)}>{s}</button>)}
        </div>
        <h2 className="split-head" id="try-a">Type a message</h2>
      </div>
      <div className="split-out">
        <div className="task-card" data-ui aria-live="polite" data-testid="try-out">
          {reading.kind === 'task' && (<>
            <p className="card-kind small">New task</p>
            <p className="card-title">{reading.title}</p>
            <dl className="card-props">
              <dt>Owner</dt><dd className={reading.owner ? undefined : 'missing'}>{reading.owner ?? 'Not said yet, the agent will ask'}</dd>
              <dt>Asked by</dt><dd>You</dd>
              <dt>Due</dt><dd className={reading.due ? undefined : 'missing'}>{reading.due ? fmtDay(reading.due) : 'No date given'}</dd>
              <dt>Waits for</dt><dd className={reading.waitsFor ? undefined : 'missing'}>{reading.waitsFor ?? 'Nothing'}</dd>
            </dl>
          </>)}
          {reading.kind === 'meeting' && (<>
            <p className="card-kind small">Team meeting</p>
            <p className="card-title">{fmtDay(reading.at)}, {fmtTime(reading.at)}</p>
            <dl className="card-props">
              <dt>Who</dt><dd>Everyone</dd>
              <dt>Reminder</dt><dd>{fmtTime(new Date(reading.at.getTime() - 5 * 60_000))}, in the group</dd>
              <dt>Cancel</dt><dd>One tap, from the confirmation</dd>
            </dl>
          </>)}
          {reading.kind === 'none' && (<>
            <p className="card-kind small">Nothing recorded</p>
            <p className="card-title">Small talk stays small talk.</p>
            <p className="card-quiet">The agent only writes something down when a message asks for work, a date or a meeting.</p>
          </>)}
        </div>
        <p className="split-note small">A simplified preview that runs in your browser. The real agent reads with Jev and an LLM, and checks every guess against the team, the tasks and the calendar before it writes anything.</p>
        <h2 className="split-head right" aria-hidden="true">get a task</h2>
      </div>
    </section>
  )
}

function BoardPage() {
  return (
    <section className="board-page" aria-labelledby="board-h">
      <div className="wrap board-intro axis-grid">
        <div className="axis">
          <h2 className="head" id="board-h" data-reveal>What the team sees</h2>
          <p data-reveal>The same dashboard the agent keeps current. These are screenshots of it running for our own team this week.</p>
        </div>
      </div>
      <div className="mosaic">
        <figure className="tile pic wide" data-reveal><img src={tasksImg} width={2080} height={697} loading="lazy" alt="The task board with Pending, In Progress, Blocked and Complete columns; two tasks were closed by chat replies scored by Rules and Jev." /></figure>
        <p className="tile note" data-reveal>The board moves when people reply, not when someone remembers to update it.</p>
        <figure className="tile pic tall" data-reveal><img src={graphImg} width={968} height={840} loading="lazy" alt="The graph page: the project, four people and three tasks; blue lines show owners, pink dashed arrows show who asked for each task." /></figure>
        <p className="tile note dark" data-reveal>Blue lines show who owns a task. Pink arrows show who asked for it.</p>
        <figure className="tile pic wide2" data-reveal><img src={activityImg} width={2080} height={924} loading="lazy" alt="The activity feed: messages in and out of the group, each status change with the model that read it and its confidence." /></figure>
      </div>
    </section>
  )
}

const NOTES: { part: string; side: 'l' | 'r'; name: string; text: string }[] = [
  { part: 'owner', side: 'l', name: 'Owner', text: "Taken from the message: a name, an @username, or “I'll do it”." },
  { part: 'asker', side: 'l', name: 'Asked by', text: 'Whoever wrote the message, drawn as its own line in the graph.' },
  { part: 'due', side: 'l', name: 'Due', text: '“By Friday”, “the 20th” and “kal tak” all become a date, never one in the past.' },
  { part: 'status', side: 'r', name: 'Blocked', text: 'A blocker in a reply marks the task and asks the one person who can clear it.' },
  { part: 'deps', side: 'r', name: 'Waits for', text: '“Once the API is done” links two tasks. Loops are refused.' },
  { part: 'forecast', side: 'r', name: 'Forecast', text: 'CP-SAT plans every open task; a slip raises a follow-up before the deadline, not after it.' },
]

export function Inside() {
  const [on, setOn] = useState<string | null>(null)
  const cls = (part: string) => (on === part ? 'is-on' : undefined)
  const notes = (side: 'l' | 'r') => (
    <ul className={`notes notes-${side}`}>
      {NOTES.filter((n) => n.side === side).map((n) => (
        <li key={n.part} className={`note-item${on === n.part ? ' is-on' : ''}`} tabIndex={0}
          onMouseEnter={() => setOn(n.part)} onMouseLeave={() => setOn(null)} onFocus={() => setOn(n.part)} onBlur={() => setOn(null)}>
          <b>{n.name}</b>{n.text}
        </li>
      ))}
    </ul>
  )
  return (
    <section className="inside wrap" id="inside" aria-labelledby="inside-h">
      <h2 className="head inside-head" id="inside-h" data-reveal>One task, fully read</h2>
      <div className="plate">
        {notes('l')}
        <div className={`panel${on ? ' has-focus' : ''}`} data-ui data-testid="panel">
          <p className="panel-id small">T5</p>
          <p className="panel-title">Connect the payment API</p>
          <dl className="panel-props">
            <dt className={cls('status')}>Status</dt><dd className={cls('status')}><span className="pill blocked">Blocked</span></dd>
            <dt className={cls('owner')}>Owner</dt><dd className={cls('owner')}>Samarth</dd>
            <dt className={cls('asker')}>Asked by</dt><dd className={cls('asker')}>Tejas</dd>
            <dt className={cls('due')}>Due</dt><dd className={cls('due')}>Thu 22 Oct</dd>
            <dt className={cls('deps')}>Waits for</dt><dd className={cls('deps')}>T4 Build the API</dd>
            <dt className={cls('forecast')}>Forecast</dt><dd className={cls('forecast')}>Finishes Thu 18:40, 5 h late</dd>
          </dl>
          <div className={`panel-blocker ${cls('status') ?? ''}`}>
            <p className="small"><b>Blocker</b></p>
            <p>waiting for test credentials from Soham</p>
          </div>
          <div className={`panel-log ${cls('forecast') ?? ''}`}>
            <p className="small"><b>Next</b></p>
            <p>Asked Soham for the credentials at 14:05. Will check again tomorrow 10:00.</p>
          </div>
        </div>
        {notes('r')}
      </div>
    </section>
  )
}

function Close() {
  return (
    <section className="close" aria-labelledby="close-h">
      <h2 className="close-head" id="close-h" data-reveal>Let the chat<br />plan itself.</h2>
      <div className="close-row">
        <p data-reveal>Add the bot to your Telegram group, tap /join, and keep talking the way you already do.</p>
        <a className="button big" href={DASHBOARD} data-reveal>Get started</a>
      </div>
    </section>
  )
}

function Footer() {
  return (
    <footer className="foot">
      <p className="foot-name" aria-hidden="true">Saarathi</p>
      <div className="foot-row wrap">
        <ul className="foot-places">
          <li><a className="link" href={REPO}>Source on GitHub</a></li>
          <li><a className="link" href={DASHBOARD}>The dashboard</a></li>
          <li><a className="link" href={`${REPO}/tree/main/docs`}>How it works, in detail</a></li>
        </ul>
        <div className="foot-cols">
          <ul><li><b>Built by</b></li><li>Tejas</li><li>Soham</li><li>Samarth</li><li>Atharva</li></ul>
          <ul><li><b>Talks to</b></li><li>Telegram</li><li>Slack</li><li>Microsoft Teams</li><li>Meetings, through Recall.ai</li></ul>
          <ul><li><b>Runs on</b></li><li>FastAPI and React</li><li>OR-Tools CP-SAT</li><li>Neo4j</li><li>Jev and an LLM</li></ul>
        </div>
      </div>
      <div className="foot-legal wrap small">
        <p>Saarathi keeps group projects on course.</p>
        <a className="link" href="#top">Back to top</a>
      </div>
    </footer>
  )
}
