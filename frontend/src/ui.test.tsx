import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { STATUSES } from './api'
import { Callout, Empty, EngineTag, fmtDate, mmss, PageHeader, Person, STATUS_COLOR, StatusTag } from './ui'

afterEach(cleanup)

describe('formatters', () => {
  it('mmss pads seconds and floors fractions', () => {
    expect(mmss(0)).toBe('0:00')
    expect(mmss(65)).toBe('1:05')
    expect(mmss(59.9)).toBe('0:59')
    expect(mmss(3600)).toBe('60:00')
  })
  it('fmtDate is empty for null and a short date otherwise', () => {
    expect(fmtDate(null)).toBe('')
    expect(fmtDate('2026-10-17')).toMatch(/17/)
  })
})

describe('status colours', () => {
  it('every status has a colour', () => {
    for (const s of STATUSES) expect(STATUS_COLOR[s]).toBeTruthy()
  })
  it('blocked is red and complete is green', () => {
    expect(STATUS_COLOR.Blocked).toBe('red')
    expect(STATUS_COLOR.Complete).toBe('green')
  })
  it('StatusTag renders its label with the matching class', () => {
    render(<StatusTag status="Blocked" />)
    expect(screen.getByText('Blocked').closest('.tag')).toHaveClass('tag-red')
  })
})

describe('EngineTag', () => {
  it('model engines are purple, humans pink, rules green', () => {
    const { rerender } = render(<EngineTag engine="jev" confidence={0.912} />)
    expect(screen.getByText('Jev').closest('.tag')).toHaveClass('tag-purple')
    expect(screen.getByText('0.91')).toBeInTheDocument()
    rerender(<EngineTag engine="lead" />)
    expect(screen.getByText('Lead').closest('.tag')).toHaveClass('tag-pink')
    rerender(<EngineTag engine="rules" />)
    expect(screen.getByText('Rules').closest('.tag')).toHaveClass('tag-green')
  })
  it('meeting-sourced engines get the mic marker', () => {
    render(<EngineTag engine="meeting/jev" />)
    expect(screen.getByText(/Meeting/)).toBeInTheDocument()
  })
  it('unknown engines fall back to their own name', () => {
    render(<EngineTag engine="mystery" />)
    expect(screen.getByText('mystery')).toBeInTheDocument()
  })
})

describe('building blocks', () => {
  it('Person shows initial and name', () => {
    render(<Person name="Kabir" />)
    expect(screen.getByText('K')).toBeInTheDocument()
    expect(screen.getByText(/Kabir/)).toBeInTheDocument()
  })
  it('PageHeader renders title, description and children', () => {
    render(<PageHeader icon="✅" title="Tasks" description="All work"><button>Go</button></PageHeader>)
    expect(screen.getByRole('heading', { name: 'Tasks' })).toBeInTheDocument()
    expect(screen.getByText('All work')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Go' })).toBeInTheDocument()
  })
  it('Callout and Empty render their content', () => {
    render(<><Callout icon="ℹ️">hello</Callout><Empty>nothing here</Empty></>)
    expect(screen.getByText('hello')).toBeInTheDocument()
    expect(screen.getByText('nothing here')).toBeInTheDocument()
  })
})
