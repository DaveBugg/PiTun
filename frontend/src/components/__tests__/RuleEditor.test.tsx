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
 * reading the <select> that's value-bound to `customNode`.
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

// The editor shows the Target Node select only for node-action rules.
// It always has an initial empty-placeholder <option value="">…</option>
// then one <option value="{id}"> per node in nodeOptions. The select's
// own `value` reflects `form.customNode`.
const nodeSelect = (container: HTMLElement): HTMLSelectElement => {
  const selects = container.querySelectorAll('select')
  // Layout: Rule Type select (index 0), Action select (index 1), Target Node select (index 2)
  return selects[selects.length - 1] as HTMLSelectElement
}

describe('<RuleEditor> action parsing', () => {
  const nodeOptions = [
    { id: 5, name: 'Node Five' },
    { id: 9, name: 'Node Nine' },
  ]

  it('extracts the node id from a well-formed "node:5"', () => {
    const { container } = render(
      wrap(
        <RuleEditor
          initial={{ action: 'node:5' }}
          nodeOptions={nodeOptions}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />,
      ),
    )
    expect(nodeSelect(container).value).toBe('5')
  })

  it('falls back to empty on malformed "node:abc" (THE regression)', () => {
    const { container } = render(
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
    expect(nodeSelect(container).value).toBe('')
  })

  it('falls back to empty on bare "node:"', () => {
    const { container } = render(
      wrap(
        <RuleEditor
          initial={{ action: 'node:' as never }}
          nodeOptions={nodeOptions}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />,
      ),
    )
    expect(nodeSelect(container).value).toBe('')
  })
})
