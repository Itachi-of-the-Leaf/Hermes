// A small, honest preview of what the agent records from one chat message. The real agent reads with Jev and
// an LLM and validates every guess on the server (backend/app/converse.py); this runs in the browser with rules.

export const TEAM = ['Tejas', 'Soham', 'Samarth', 'Atharva']
const TASKS = [
  { id: 'T4', title: 'Build the API', words: ['api', 'backend'] },
  { id: 'T2', title: 'Finish the designs', words: ['design', 'designs', 'figma', 'mockups'] },
]
const DAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

export type Reading =
  | { kind: 'task'; title: string; owner: string | null; due: Date | null; waitsFor: string | null }
  | { kind: 'meeting'; title: string; at: Date }
  | { kind: 'none' }

export const fmtDay = (d: Date) => d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' }).replace(',', '')
export const fmtTime = (d: Date) => d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

const addDays = (d: Date, n: number) => { const x = new Date(d); x.setDate(x.getDate() + n); x.setHours(0, 0, 0, 0); return x }

/** "by Friday", "tomorrow", "the 20th", "22 Oct", "in 3 days" -> a date that is today or later, plus the words matched. */
export function findDue(text: string, today: Date): { due: Date; match: string } | null {
  const t = text.toLowerCase()
  let m = t.match(/\b(?:by |on |before )?(today|tonight|eod|end of day)\b/)
  if (m) return { due: addDays(today, 0), match: m[0] }
  m = t.match(/\b(?:by |on )?tomorrow\b/)
  if (m) return { due: addDays(today, 1), match: m[0] }
  m = t.match(/\bin (\d{1,2}) days?\b/)
  if (m) return { due: addDays(today, Number(m[1])), match: m[0] }
  m = t.match(new RegExp(`\\b(?:by |on |before |next )?(${DAYS.join('|')})\\b`))
  if (m) {
    const ahead = (DAYS.indexOf(m[1]) - today.getDay() + 7) % 7 || 7
    return { due: addDays(today, ahead), match: m[0] }
  }
  m = t.match(new RegExp(`\\b(?:by |on |before )?(?:the )?(\\d{1,2})(?:st|nd|rd|th)?(?: of)? ?(${MONTHS.join('|')})?[a-z]*\\b(?! ?(?:min|hour|day|am|pm))`))
  if (m && (m[2] || /\d(?:st|nd|rd|th)/.test(m[0]))) {
    const day = Number(m[1])
    let d = new Date(today.getFullYear(), m[2] ? MONTHS.indexOf(m[2]) : today.getMonth(), day)
    if (day < 1 || day > 31) return null
    if (d < addDays(today, 0)) d = m[2] ? new Date(d.getFullYear() + 1, d.getMonth(), day) : new Date(d.getFullYear(), d.getMonth() + 1, day)
    return { due: d, match: m[0] }
  }
  return null
}

function findMeeting(text: string, now: Date): Date | null {
  const t = text.toLowerCase()
  if (!/\b(meeting|meet|stand-?up|sync|call|review)\b/.test(t)) return null
  let m = t.match(/\bin (\d{1,3}) ?(min|mins|minutes|hour|hours|hr|hrs)\b/)
  if (m) return new Date(now.getTime() + Number(m[1]) * (m[2].startsWith('h') ? 60 : 1) * 60_000)
  m = t.match(/\bat (\d{1,2})(?::(\d{2}))? ?(am|pm)?\b/)
  if (m) {
    let h = Number(m[1]) % 24
    if (m[3] === 'pm' && h < 12) h += 12
    if (m[3] === 'am' && h === 12) h = 0
    const d = new Date(now); d.setHours(h, Number(m[2] ?? 0), 0, 0)
    if (/\btomorrow\b/.test(t) || d < now) d.setDate(d.getDate() + 1)
    return d
  }
  return null
}

const SMALL_TALK = /\b(chai|beer|lol|haha|lmao|party|anyone up|bro|gm|good morning|good night)\b/i
const ACTION = /\b(please|pls|can you|could you|need|needs|have to|has to|should|will|i'll|i will|let me|finish|build|make|write|do|set up|setup|send|fix|deploy|design|test|prepare|create|update)\b/i

export function parseMessage(text: string, now: Date = new Date()): Reading {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (clean.length < 4) return { kind: 'none' }
  const at = findMeeting(clean, now)
  if (at) return { kind: 'meeting', title: 'Team meeting', at }
  if (SMALL_TALK.test(clean) && !ACTION.test(clean.replace(SMALL_TALK, ''))) return { kind: 'none' }
  if (!ACTION.test(clean)) return { kind: 'none' }

  const owner = TEAM.find((n) => new RegExp(`\\b${n}\\b`, 'i').test(clean))
    ?? (/\b(i'll|i will|i can|let me|i'm on it)\b/i.test(clean) ? 'You' : null)
  const due = findDue(clean, now)

  let waitsFor: string | null = null
  let rest = clean
  const after = rest.match(/,?\s*\b(?:after|once|when)\b\s+(?:the\s+)?(.+?)(?:\s+(?:is|are)\s+(?:done|ready|in|finished|merged))?(?=[,.!?]|$)/i)
  if (after) {
    const words = after[1].toLowerCase()
    const known = TASKS.find((k) => k.words.some((w) => words.includes(w)))
    waitsFor = known ? `${known.id} ${known.title}` : `${after[1].replace(/\s+(is|are)$/i, '')} (a new task)`
    rest = rest.replace(after[0], '')
  }
  if (due) rest = rest.replace(new RegExp(due.match.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'), '')
  if (owner && owner !== 'You') rest = rest.replace(new RegExp(`@?\\b${owner}\\b,?`, 'i'), '')
  rest = rest
    .replace(/\b(please|pls|can you|could you|would you|kindly|i'll|i will|i can|let me|we need to|need to|you need to|have to)\b/gi, '')
    .replace(/[,.!?]+/g, ' ').replace(/\s+/g, ' ').trim()
  if (rest.length < 3) return { kind: 'none' }
  const title = rest.charAt(0).toUpperCase() + rest.slice(1)
  return { kind: 'task', title, owner, due: due?.due ?? null, waitsFor }
}
