import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { Inside, Landing, TryIt } from './Landing'

afterEach(cleanup)
const NOW = new Date(2026, 9, 17, 10, 0)

describe('Landing page', () => {
  it('opens with the flow graph, then the headline and one action', () => {
    render(<Landing />)
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Chat becomes the plan.')
    const flow = screen.getByLabelText('How a chat message becomes part of the plan')
    expect(within(flow).getByText('Saarathi')).toBeInTheDocument()
    expect(within(flow).getByText('Build the website')).toBeInTheDocument()
    const starts = screen.getAllByRole('link', { name: 'Get started' })
    expect(starts).toHaveLength(2)
    starts.forEach((a) => expect(a).toHaveAttribute('href', '/dashboard/'))
  })

  it('shows the workflow at a glance in order', () => {
    const { container } = render(<Landing />)
    const steps = [...container.querySelectorAll('.say')].map((e) => e.textContent)
    expect(steps).toEqual(['First, someone says what needs doing.', 'Then it checks in before the deadline.', 'And the plan keeps itself current.'])
  })

  it('carries real screenshots with descriptions', () => {
    render(<Landing />)
    const imgs = screen.getAllByRole('img')
    expect(imgs.length).toBeGreaterThanOrEqual(4)
    imgs.forEach((i) => expect(i.getAttribute('alt')?.length).toBeGreaterThan(20))
  })
})

describe('Try it', () => {
  it('turns the typed message into a task card', () => {
    render(<TryIt now={NOW} />)
    const card = screen.getByTestId('try-out')
    expect(card).toHaveTextContent('Finish the login page')
    fireEvent.change(screen.getByLabelText('Write it the way you would in the group'), { target: { value: 'Samarth can you set up the database by 22 Oct' } })
    expect(card).toHaveTextContent('Set up the database')
    expect(card).toHaveTextContent('Samarth')
    expect(card).toHaveTextContent('Thu 22 Oct')
  })

  it('sample buttons fill the box, and small talk records nothing', () => {
    render(<TryIt now={NOW} />)
    fireEvent.click(screen.getByRole('button', { name: 'anyone up for chai' }))
    expect(screen.getByTestId('try-out')).toHaveTextContent('Small talk stays small talk.')
    fireEvent.click(screen.getByRole('button', { name: 'group meeting in 20 minutes' }))
    expect(screen.getByTestId('try-out')).toHaveTextContent('Team meeting')
  })
})

describe('Inside a task', () => {
  it('a margin note highlights the part of the panel it explains', () => {
    render(<Inside />)
    const panel = screen.getByTestId('panel')
    fireEvent.mouseEnter(screen.getByText('Waits for', { selector: 'b' }).parentElement!)
    expect(panel).toHaveClass('has-focus')
    expect(within(panel).getByText('T4 Build the API')).toHaveClass('is-on')
  })
})
