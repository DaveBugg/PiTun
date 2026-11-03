/**
 * PanelUpdateCard: appears only when the panel has a newer release, confirms
 * (the database copy is named in the warning), and shows the outcome.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  xuiApi: { panelUpdateInfo: vi.fn(), updatePanel: vi.fn() },
}))
const confirmMock = vi.fn(async (_o: unknown) => true)
vi.mock('@/components/ConfirmModal', () => ({ useConfirm: () => confirmMock }))

import { PanelUpdateCard } from '@/components/PanelUpdateCard'
import { xuiApi } from '@/api/client'

const INFO = { supported: true, channel: 'stable', current: '3.6.0', latest: '3.11.0', update_available: true }

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('<PanelUpdateCard>', () => {
  beforeEach(() => {
    confirmMock.mockClear().mockResolvedValue(true)
    vi.mocked(xuiApi.panelUpdateInfo).mockReset().mockResolvedValue(INFO)
    vi.mocked(xuiApi.updatePanel).mockReset().mockResolvedValue({
      ok: true, changed: true, previous_version: '3.6.0', panel_version: '3.11.0',
      backup: '/etc/x-ui/x-ui.db.pitun-pre-update-1', message: 'Panel updated to 3.11.0.',
    })
  })

  it('offers the update when a newer release exists', async () => {
    render(wrap(<PanelUpdateCard serverId={1} />))
    expect(await screen.findByText('3.11.0')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Update panel|Обновить панель/ })).toBeInTheDocument()
  })

  it('stays out of the way when the panel is current', async () => {
    vi.mocked(xuiApi.panelUpdateInfo).mockResolvedValue({ ...INFO, current: '3.11.0', update_available: false })
    const { container } = render(wrap(<PanelUpdateCard serverId={1} />))
    await waitFor(() => expect(xuiApi.panelUpdateInfo).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('updates only after the confirmation, which mentions the database copy', async () => {
    const user = userEvent.setup()
    render(wrap(<PanelUpdateCard serverId={1} />))
    await user.click(await screen.findByRole('button', { name: /Update panel|Обновить панель/ }))
    await waitFor(() => expect(xuiApi.updatePanel).toHaveBeenCalledWith(1))
    expect(JSON.stringify(confirmMock.mock.calls[0][0])).toMatch(/database|базу/)
    expect(await screen.findByText('Panel updated to 3.11.0.')).toBeInTheDocument()
  })

  it('does nothing when the confirmation is declined', async () => {
    confirmMock.mockResolvedValue(false)
    const user = userEvent.setup()
    render(wrap(<PanelUpdateCard serverId={1} />))
    await user.click(await screen.findByRole('button', { name: /Update panel|Обновить панель/ }))
    expect(xuiApi.updatePanel).not.toHaveBeenCalled()
  })
})
