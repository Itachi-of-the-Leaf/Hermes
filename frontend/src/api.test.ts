import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

const reply = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('GETs under /api with a JSON content type', async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(200, { ok: true }))
    vi.stubGlobal('fetch', f)
    await api.health()
    expect(f.mock.calls[0][0]).toBe('/api/health')
    expect(f.mock.calls[0][1]?.headers).toMatchObject({ 'Content-Type': 'application/json' })
  })

  it('POSTs JSON bodies', async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(200, { sent: 4 }))
    vi.stubGlobal('fetch', f)
    expect(await api.checkin('T2')).toEqual({ sent: 4 })
    const [url, init] = f.mock.calls[0]
    expect(url).toBe('/api/checkin')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual({ task_id: 'T2' })
  })

  it('surfaces the server detail message on errors', async () => {
    vi.stubGlobal('fetch', vi.fn(() => reply(400, { detail: 'Set RECALL_API_KEY' })))
    await expect(api.joinMeeting('https://x', 't')).rejects.toThrow('Set RECALL_API_KEY')
  })

  it('falls back to the status text when the error is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('boom', { status: 502, statusText: 'Bad Gateway' }))))
    await expect(api.board()).rejects.toThrow('Bad Gateway')
  })

  it('uploads transcripts as multipart without forcing a JSON content type', async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(200, { id: 1 }))
    vi.stubGlobal('fetch', f)
    await api.uploadTranscript('Sync', null, 'Kabir: done')
    const init = f.mock.calls[0][1]
    expect(init?.body).toBeInstanceOf(FormData)
    const fd = init?.body as FormData
    expect(fd.get('text')).toBe('Kabir: done')
    expect(fd.get('title')).toBe('Sync')
    expect(init?.headers).toBeUndefined()
  })

  it('prefers the file over text when both are given', async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(200, { id: 1 }))
    vi.stubGlobal('fetch', f)
    await api.uploadTranscript('Sync', new File(['WEBVTT'], 't.vtt'), 'ignored')
    const fd = f.mock.calls[0][1]?.body as FormData
    expect(fd.get('file')).toBeInstanceOf(File)
    expect(fd.get('text')).toBeNull()
  })

  it('builds the resolve and undo URLs', async () => {
    const f = vi.fn((_u: string, _i?: RequestInit) => reply(200, { ok: true }))
    vi.stubGlobal('fetch', f)
    await api.resolveReview(3, 'Blocked')
    await api.undo(7)
    expect(f.mock.calls.map((c) => c[0])).toEqual(['/api/reviews/3/resolve', '/api/events/7/undo'])
  })
})
