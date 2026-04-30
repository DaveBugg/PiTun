/**
 * StatusBadge: three semantic states (unknown / offline / online) + a
 * latency threshold ladder. If any of these break the UI goes from
 * "green 50ms" to "red 500ms" silently.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StatusBadge } from '@/components/StatusBadge'

describe('<StatusBadge>', () => {
  it('renders "Unknown" when online is undefined', () => {
    render(<StatusBadge />)
    expect(screen.getByText('Unknown')).toBeInTheDocument()
  })

  it('renders "Offline" with red tone when online=false', () => {
    const { container } = render(<StatusBadge online={false} />)
    expect(screen.getByText('Offline')).toBeInTheDocument()
    // The outer <span> carries the color class
    const badge = container.querySelector('span') as HTMLElement
    expect(badge.className).toMatch(/text-red-/)
  })

  it('shows latency in ms and uses green tone below 100ms', () => {
    const { container } = render(<StatusBadge online={true} latency={42} />)
    expect(screen.getByText('42 ms')).toBeInTheDocument()
    const badge = container.querySelector('span') as HTMLElement
    expect(badge.className).toMatch(/text-green-/)
  })

  it('uses yellow tone between 100 and 300ms', () => {
    const { container } = render(<StatusBadge online={true} latency={200} />)
    const badge = container.querySelector('span') as HTMLElement
    expect(badge.className).toMatch(/text-yellow-/)
  })

  it('uses red tone above 300ms', () => {
    const { container } = render(<StatusBadge online={true} latency={500} />)
    const badge = container.querySelector('span') as HTMLElement
    expect(badge.className).toMatch(/text-red-/)
  })

  it('falls back to "Online" label when latency missing', () => {
    render(<StatusBadge online={true} />)
    expect(screen.getByText('Online')).toBeInTheDocument()
  })
})
