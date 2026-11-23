import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'

import { SearchableSelect } from '@/components/SearchableSelect'

const OPTIONS = [
  { value: '', label: 'None (single tunnel)' },
  { value: '1', label: 'Alpha node', hint: 'vless · 10.0.0.1' },
  { value: '2', label: 'Beta node', hint: 'trojan · 10.0.0.2' },
  { value: '3', label: 'Gamma node (offline)', hint: 'ss · 10.0.0.3' },
]

function Harness({ onChange = vi.fn() }: { onChange?: (v: string) => void }) {
  const [value, setValue] = useState('1')
  return (
    <SearchableSelect
      options={OPTIONS}
      value={value}
      onChange={(v) => {
        setValue(v)
        onChange(v)
      }}
      placeholder="Pick a node"
    />
  )
}

describe('<SearchableSelect>', () => {
  it('shows the selected option label on the closed button', () => {
    render(<Harness />)
    expect(screen.getByRole('button', { name: /Alpha node/ })).toBeInTheDocument()
  })

  it('filters options by label and hint substring', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: /Alpha node/ }))
    const input = screen.getByPlaceholderText('Search…')
    fireEvent.change(input, { target: { value: 'trojan' } })
    expect(screen.getByRole('button', { name: /Beta node/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Gamma node/ })).not.toBeInTheDocument()
  })

  it('selects an option on click and closes the list', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /Alpha node/ }))
    fireEvent.click(screen.getByRole('button', { name: /Gamma node/ }))
    expect(onChange).toHaveBeenCalledWith('3')
    expect(screen.queryByPlaceholderText('Search…')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Gamma node/ })).toBeInTheDocument()
  })

  it('selects the highlighted option with Enter', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /Alpha node/ }))
    const input = screen.getByPlaceholderText('Search…')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('2')
  })

  it('reports no matches for an unknown query', () => {
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: /Alpha node/ }))
    fireEvent.change(screen.getByPlaceholderText('Search…'), { target: { value: 'zzz' } })
    expect(screen.getByText('Nothing matches')).toBeInTheDocument()
  })
})
