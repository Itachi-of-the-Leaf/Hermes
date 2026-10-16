import type { Board } from './api'

/** Quick replies for the "Test without apps" panel, built from the real board instead of fixed names:
 * the first unfinished task that waits on someone else's unfinished task gets a blocked, unblocked, finished story. */
export function presetsFor(board: Board): [string, string, boolean][] {
  const byTitle = new Map(board.tasks.map((t) => [t.title, t]))
  for (const t of board.tasks) {
    if (t.status === 'Complete') continue
    for (const title of t.depends_on_titles) {
      const dep = byTitle.get(title)
      if (dep && dep.status !== 'Complete' && dep.owner_key !== t.owner_key) {
        return [
          [t.owner_key, `It is connected, but I am waiting for ${dep.title}.`, false],
          [dep.owner_key, 'sent them on email', false],
          [t.owner_key, 'tested, all green, PR #14', false],
        ]
      }
    }
  }
  return []
}
