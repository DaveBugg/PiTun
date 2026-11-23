/**
 * Regression guard for the wave-1 split-harden fix at
 * frontend/src/components/RuleEditor.tsx lines 46-57.
 *
 * Before the fix:
 *   - `'node:'`    → customNode = `''`    (render-safe by accident)
 *   - `'node:abc'` → customNode = `'abc'` (submitted NaN id later)
 *   - `'node:5'`   → customNode = `'5'`   (ok)
 *
 * After the fix (regex `^node:(\d+)$`):
 *   - `'node:'`    → `''`
 *   - `'node:abc'` → `''`   ← the actual regression we're locking in
 *   - `'node:5'`   → `'5'`
 *
 * We verify by rendering the editor with the given initial action and
 * reading the SearchableSelect button bound to `customNode`: a parsed
 * id shows the node's name, the empty fallback shows the placeholder.
 */
import { describe, it, expect, vi } from 'vitest'
import { render } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  balancersApi: { list: vi.fn().mockResolvedValue([]) },
}))

import { RuleEditor } from '@/components/RuleEditor'

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('<RuleEditor> action parsing', () => {
  const nodeOptions = [
    { id: 5, name: 'Node Five' },
    { id: 9, name: 'Node Nine' },
  ]

  it('extracts the node id from a well-formed "node:5"', () => {
    const { getByRole } = render(
      wrap(
        <RuleEditor
          initial={{ action: 'node:5' }}
          nodeOptions={nodeOptions}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />,
      ),
    )
    expect(getByRole('button', { name: 'Node Five' })).toBeInTheDocument()
  })

  it('falls back to empty on malformed "node:abc" (THE regression)', () => {
    const { getByRole, queryByRole } = render(
      wrap(
        <RuleEditor
          // Cast: these are deliberately malformed strings that the type
          // system forbids but reality can produce (corrupted DB row,
          // old migration artefact). We're asserting the parser tolerates
          // them without blowing up the form.
          initial={{ action: 'node:abc' as never }}
          nodeOptions={nodeOptions}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />,
      ),
    )
    expect(getByRole('button', { name: 'Select node…' })).toBeInTheDocument()
    expect(queryByRole('button', { name: 'Node Five' })).not.toBeInTheDocument()
  })

  it('falls back to empty on bare "node:"', () => {
    const { getByRole, queryByRole } = render(
      wrap(
        <RuleEditor
          initial={{ action: 'node:' as never }}
          nodeOptions={nodeOptions}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />,
      ),
    )
    expect(getByRole('button', { name: 'Select node…' })).toBeInTheDocument()
    expect(queryByRole('button', { name: 'Node Nine' })).not.toBeInTheDocument()
  })
})
