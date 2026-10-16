import { describe, expect, it } from 'vitest'
import type { Board, Task } from './api'
import { presetsFor } from './devPresets'

const task = (id: string, title: string, owner_key: string, status: Task['status'], depends_on_titles: string[] = []): Task => ({
  id, title, owner: owner_key, owner_key, status, progress_pct: null, deadline: null, eta: null, blocker_text: null, blocker_type: null,
  blocked_by: null, blocker_resolved: false, proof_url: null, depends_on: '', depends_on_titles, at_risk: null, last_event: null,
})
const board = (tasks: Task[]): Board => ({ tasks, members: [], counts: {}, pending_reviews: 0 })

describe('presetsFor', () => {
  it('builds a blocked / unblocked / finished story from the first task waiting on someone else', () => {
    const p = presetsFor(board([
      task('T1', 'Slides', 'ana', 'Complete'),
      task('T2', 'Connect the API', 'bo', 'Pending', ['Get keys']),
      task('T3', 'Get keys', 'cy', 'In Progress'),
    ]))
    expect(p).toEqual([
      ['bo', 'It is connected, but I am waiting for Get keys.', false],
      ['cy', 'sent them on email', false],
      ['bo', 'tested, all green, PR #14', false],
    ])
  })
  it('uses whatever names the board has, nothing is hard-coded', () => {
    const p = presetsFor(board([task('A', 'First', 'zed', 'Pending', ['Zero']), task('B', 'Zero', 'yan', 'Pending')]))
    expect(p.map((x) => x[0])).toEqual(['zed', 'yan', 'zed'])
  })
  it('skips finished prerequisites and tasks you wait on yourself', () => {
    expect(presetsFor(board([task('A', 'First', 'x', 'Pending', ['Done one']), task('B', 'Done one', 'y', 'Complete')]))).toEqual([])
    expect(presetsFor(board([task('A', 'First', 'x', 'Pending', ['Mine']), task('B', 'Mine', 'x', 'Pending')]))).toEqual([])
  })
  it('offers nothing when no task has a dependency', () => {
    expect(presetsFor(board([task('A', 'Alone', 'x', 'Pending')]))).toEqual([])
    expect(presetsFor(board([]))).toEqual([])
  })
})
