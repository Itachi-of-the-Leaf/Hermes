import { describe, expect, it } from 'vitest'
import { findDue, fmtDay, parseMessage } from './parseMessage'

const NOW = new Date(2026, 9, 17, 10, 0)            // Saturday 17 Oct 2026, 10:00

describe('parseMessage (the landing page preview)', () => {
  it('reads owner, due date and what the task waits for', () => {
    const r = parseMessage('Soham please finish the login page by Friday, after the API is done', NOW)
    expect(r).toMatchObject({ kind: 'task', title: 'Finish the login page', owner: 'Soham', waitsFor: 'T4 Build the API' })
    expect(r.kind === 'task' && fmtDay(r.due!)).toBe('Fri 23 Oct')
  })

  it('treats "I\'ll" as the writer taking the task', () => {
    const r = parseMessage("I'll do the slides tomorrow", NOW)
    expect(r).toMatchObject({ kind: 'task', title: 'Do the slides', owner: 'You' })
    expect(r.kind === 'task' && fmtDay(r.due!)).toBe('Sun 18 Oct')
  })

  it('understands written dates and never returns one in the past', () => {
    expect(fmtDay(findDue('by 22 Oct', NOW)!.due)).toBe('Thu 22 Oct')
    expect(fmtDay(findDue('by the 20th', NOW)!.due)).toBe('Tue 20 Oct')
    expect(fmtDay(findDue('by the 5th', NOW)!.due)).toBe('Thu 5 Nov')
    expect(findDue('in 20 minutes', NOW)).toBeNull()
  })

  it('recognises a meeting announcement', () => {
    const r = parseMessage('group meeting in 20 minutes', NOW)
    expect(r.kind).toBe('meeting')
    expect(r.kind === 'meeting' && r.at.getTime() - NOW.getTime()).toBe(20 * 60_000)
  })

  it('leaves small talk alone', () => {
    expect(parseMessage('anyone up for chai', NOW).kind).toBe('none')
    expect(parseMessage('lol', NOW).kind).toBe('none')
  })

  it('names an unknown prerequisite as a new task', () => {
    const r = parseMessage('Atharva write the report after the survey is done', NOW)
    expect(r).toMatchObject({ kind: 'task', owner: 'Atharva', waitsFor: 'survey (a new task)' })
  })
})
