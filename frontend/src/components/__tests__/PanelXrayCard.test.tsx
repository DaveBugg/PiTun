/**
 * PanelXrayCard: shows the core running in a panel, offers only versions the
 * panel can take, installs after a confirmation and proposes a roll-back when
 * the new core does not come up.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  xuiApi: { panelXray: vi.fn(), updatePanelXray: vi.fn() },
}))
vi.mock('@/components/ConfirmModal', () => ({ useConfirm: () => async () => true }))

import { PanelXrayCard } from '@/components/PanelXrayCard'
import { xuiApi } from '@/api/client'
import type { XuiXrayInfo } from '@/types'

const v = (version: string, extra: Partial<XuiXrayInfo['versions'][0]> = {}) => ({
  version, tag: 'v' + version, newer: false, current: false, blocked: null, ...extra,
})

const INFO: XuiXrayInfo = {
  panel_version: '3.11.0',
  xray_version: '26.10.21',
  xray_state: 'running',
  xray_error: null,
  versions: [v('26.10.31', { newer: true }), v('26.10.21', { current: true }), v('26.7.28')],
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('<PanelXrayCard>', () => {
  beforeEach(() => {
    vi.mocked(xuiApi.panelXray).mockReset().mockResolvedValue(INFO)
    vi.mocked(xuiApi.updatePanelXray).mockReset()
  })

  it('shows the running core and the panel version', async () => {
    render(wrap(<PanelXrayCard serverId={3} />))
    expect(await screen.findByText('26.10.21')).toBeInTheDocument()
    expect(screen.getByText('3.11.0')).toBeInTheDocument()
    expect(screen.getByText(/running|работает/)).toBeInTheDocument()
  })

  it('does not offer the version that is already running', async () => {
    render(wrap(<PanelXrayCard serverId={3} />))
    await screen.findByText('26.10.21')
    const options = screen.getAllByRole('option').map((o) => o.textContent ?? '')
    expect(options.some((o) => o.startsWith('26.10.31'))).toBe(true)
    expect(options.some((o) => o.startsWith('26.10.21'))).toBe(false)
  })

  it('disables a version the panel is too old for', async () => {
    vi.mocked(xuiApi.panelXray).mockResolvedValue({
      ...INFO,
      panel_version: '3.6.0',
      xray_version: '26.7.28',
      versions: [v('26.10.31', { newer: true, blocked: 'Update the panel to 3.8.0 or newer first.' }), v('26.7.28', { current: true })],
    })
    render(wrap(<PanelXrayCard serverId={3} />))
    const opt = await screen.findByRole('option', { name: /26\.10\.31/ })
    expect(opt).toBeDisabled()
    expect(screen.getByText(/3\.8\.0/)).toBeInTheDocument()
  })

  it('installs the picked version after confirmation', async () => {
    vi.mocked(xuiApi.updatePanelXray).mockResolvedValue({
      ok: true, changed: true, xray_version: '26.10.31', previous_version: '26.10.21', message: 'xray 26.10.31 is running.',
    })
    const user = userEvent.setup()
    render(wrap(<PanelXrayCard serverId={3} />))
    await screen.findByText('26.10.21')
    await user.selectOptions(screen.getByRole('combobox'), '26.10.31')
    await user.click(screen.getByRole('button', { name: /Install|Установить/ }))
    await waitFor(() => expect(xuiApi.updatePanelXray).toHaveBeenCalledWith(3, '26.10.31'))
    expect(await screen.findByText(/xray 26\.10\.31 is running/)).toBeInTheDocument()
  })

  it('proposes a roll-back when the new core did not start', async () => {
    vi.mocked(xuiApi.updatePanelXray)
      .mockResolvedValueOnce({
        ok: false, changed: true, xray_version: '26.10.31', previous_version: '26.10.21',
        message: 'xray did not come up on 26.10.31. Roll back by installing 26.10.21.',
      })
      .mockResolvedValueOnce({ ok: true, changed: true, xray_version: '26.10.21', message: 'xray 26.10.21 is running.' })
    const user = userEvent.setup()
    render(wrap(<PanelXrayCard serverId={3} />))
    await screen.findByText('26.10.21')
    await user.selectOptions(screen.getByRole('combobox'), '26.10.31')
    await user.click(screen.getByRole('button', { name: /Install|Установить/ }))
    await user.click(await screen.findByRole('button', { name: /Roll back to 26\.10\.21|Откатить до 26\.10\.21/ }))
    await waitFor(() => expect(xuiApi.updatePanelXray).toHaveBeenLastCalledWith(3, '26.10.21'))
  })
})
