/**
 * PortCheck: chips per port from `GET /xui/servers/{id}/ports`; only a
 * free port can be picked, and a failed SSH probe is surfaced instead of
 * silently passing the panel-only verdict off as complete.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  xuiApi: { checkPorts: vi.fn() },
}))

import { PortCheck } from '@/components/PortCheck'
import { xuiApi } from '@/api/client'
import type { XuiPortCheck } from '@/types'

const RESULT: XuiPortCheck = {
  xui_server_id: 3,
  panel_error: null,
  ssh_error: null,
  ports: [
    { port: 443, status: 'taken', reason: 'inbound #4 test', listeners: ['xray'], ufw: 'closed',
      inbounds: [{ id: 4, remark: 'test', protocol: 'vless', enabled: true, known_here: false }] },
    { port: 8443, status: 'reserved', reason: 'panel HTTPS (nginx / ACME)', listeners: ['nginx'], ufw: 'allow', inbounds: [] },
    { port: 2443, status: 'free', reason: null, listeners: [], ufw: 'closed', inbounds: [] },
  ],
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('<PortCheck>', () => {
  beforeEach(() => {
    vi.mocked(xuiApi.checkPorts).mockReset()
  })

  it('checks the popular ports and picks only a free one', async () => {
    vi.mocked(xuiApi.checkPorts).mockResolvedValue(RESULT)
    const onPick = vi.fn()
    const user = userEvent.setup()
    render(wrap(<PortCheck serverId={3} onPick={onPick} />))

    const free = await screen.findByRole('button', { name: /^2443 ✓$/ })
    expect(xuiApi.checkPorts).toHaveBeenCalledWith(3, [443, 8443, 2443], false)

    await user.click(screen.getByRole('button', { name: /^443 ✗$/ }))
    await user.click(screen.getByRole('button', { name: /^8443 ✗$/ }))
    expect(onPick).not.toHaveBeenCalled()

    await user.click(free)
    expect(onPick).toHaveBeenCalledWith(2443)
  })

  it('explains the selected port, including foreign inbounds', async () => {
    vi.mocked(xuiApi.checkPorts).mockResolvedValue(RESULT)
    render(wrap(<PortCheck serverId={3} selected={443} />))
    await waitFor(() => expect(screen.getByText(/не из этого PiTun|not managed by this PiTun/)).toBeInTheDocument())
  })

  it('warns when the SSH layer did not run', async () => {
    vi.mocked(xuiApi.checkPorts).mockResolvedValue({ ...RESULT, ssh_error: 'no SSH credentials on the Server row' })
    render(wrap(<PortCheck serverId={3} />))
    await waitFor(() => expect(screen.getByText(/no SSH credentials/)).toBeInTheDocument())
  })

  it('renders nothing without a server', () => {
    const { container } = render(wrap(<PortCheck serverId={null} />))
    expect(container).toBeEmptyDOMElement()
    expect(xuiApi.checkPorts).not.toHaveBeenCalled()
  })
})
