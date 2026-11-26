import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

const createMock = vi.fn()

vi.mock('@/api/client', () => ({
  endpointsApi: {
    list: vi.fn().mockResolvedValue([]),
    create: (...a: unknown[]) => createMock(...a),
    update: vi.fn(),
    delete: vi.fn(),
    restart: vi.fn(),
  },
  balancersApi: { list: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/hooks/useNodes', () => ({
  useNodes: () => ({
    data: [
      { id: 1, name: 'Main node', protocol: 'vless', address: '10.0.0.1', is_online: true, enabled: true },
      { id: 2, name: 'Taken node', protocol: 'vless', address: '10.0.0.2', is_online: true, enabled: true },
      { id: 3, name: 'Free node', protocol: 'trojan', address: '10.0.0.3', is_online: true, enabled: true },
      { id: 4, name: 'Dead node', protocol: 'ss', address: '10.0.0.4', is_online: false, enabled: true },
      { id: 5, name: 'Disabled node', protocol: 'vless', address: '10.0.0.5', is_online: false, enabled: false },
    ],
  }),
}))

vi.mock('@/hooks/useSystem', () => ({
  useSystemStatus: () => ({ data: { active_node_id: 1 } }),
}))

vi.mock('@/hooks/useRoutingSets', () => ({
  useRoutingSets: () => ({ data: [{ id: 7, name: 'Kids' }] }),
}))

import { ProxyEndpointsSection } from '@/components/ProxyEndpointsSection'
import { endpointsApi } from '@/api/client'
import { ConfirmProvider } from '@/components/ConfirmModal'

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={qc}>
      <ConfirmProvider>{ui}</ConfirmProvider>
    </QueryClientProvider>
  )
}

async function openForm() {
  render(wrap(<ProxyEndpointsSection />))
  await waitFor(() => screen.getByText('Add endpoint'))
  const addButtons = screen.getAllByText('Add endpoint')
  fireEvent.click(addButtons[0])
  await screen.findByPlaceholderText('work-laptop')
}

describe('<ProxyEndpointsSection> form', () => {
  beforeEach(() => {
    createMock.mockReset().mockResolvedValue({})
    vi.mocked(endpointsApi.list).mockResolvedValue([
      {
        id: 9, name: 'ep-taken', enabled: true, socks_port: 1081,
        mode: 'global', node_id: 2, balancer_id: null, routing_set_id: null,
        order: 1, running: true, node_name: 'Taken node',
      },
    ] as never)
  })

  it('blocks submit without a name and without an egress', async () => {
    await openForm()
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByText('Name is required')).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('hides the main-flow and other-endpoint nodes from the picker', async () => {
    await openForm()
    fireEvent.change(screen.getByPlaceholderText('work-laptop'), { target: { value: 'ep-x' } })
    fireEvent.click(screen.getByRole('button', { name: /Select node or balancer/ }))
    expect(screen.queryByRole('button', { name: /Main node/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Taken node/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Free node/ })).toBeInTheDocument()
    // Offline nodes hidden by default (the shared hide-unavailable toggle).
    expect(screen.queryByRole('button', { name: /Dead node/ })).not.toBeInTheDocument()
  })

  it('creates with auto port and node id payload', async () => {
    await openForm()
    fireEvent.change(screen.getByPlaceholderText('work-laptop'), { target: { value: 'ep-x' } })
    fireEvent.click(screen.getByRole('button', { name: /Select node or balancer/ }))
    fireEvent.click(screen.getByRole('button', { name: /Free node/ }))
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(createMock).toHaveBeenCalled())
    expect(createMock).toHaveBeenCalledWith(expect.objectContaining({
      name: 'ep-x',
      socks_port: 0,
      mode: 'global',
      node_id: 3,
      balancer_id: null,
    }))
  })

  it('requires a routing set in routing_set mode', async () => {
    await openForm()
    fireEvent.change(screen.getByPlaceholderText('work-laptop'), { target: { value: 'ep-x' } })
    fireEvent.click(screen.getByRole('button', { name: /Select node or balancer/ }))
    fireEvent.click(screen.getByRole('button', { name: /Free node/ }))
    fireEvent.change(screen.getByDisplayValue(/Global/), { target: { value: 'routing_set' } })
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByText('Pick a routing set')).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('keeps the stale current node visible and labeled when editing', async () => {
    vi.mocked(endpointsApi.list).mockResolvedValue([
      {
        id: 9, name: 'ep-main', enabled: true, socks_port: 1081,
        mode: 'global', node_id: 1, balancer_id: null, routing_set_id: null,
        order: 1, running: true, node_name: 'Main node',
      },
      {
        id: 12, name: 'ep-taken', enabled: true, socks_port: 1082,
        mode: 'global', node_id: 2, balancer_id: null, routing_set_id: null,
        order: 2, running: true, node_name: 'Taken node',
      },
      {
        id: 10, name: 'ep-dead', enabled: true, socks_port: 1083,
        mode: 'global', node_id: 4, balancer_id: null, routing_set_id: null,
        order: 3, running: false, node_name: 'Dead node',
      },
      {
        id: 11, name: 'ep-disabled', enabled: true, socks_port: 1084,
        mode: 'global', node_id: 5, balancer_id: null, routing_set_id: null,
        order: 4, running: false, node_name: 'Disabled node',
      },
    ] as never)
    render(wrap(<ProxyEndpointsSection />))
    await waitFor(() => screen.getByText('ep-main'))

    // Main-flow node used by this endpoint stays visible and labeled.
    fireEvent.click(screen.getAllByText('Edit')[0])
    expect(screen.getByRole('button', { name: /Main node \(current\)/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Main node \(current\)/ }))
    expect(screen.getAllByRole('button', { name: /Main node \(current\)/ })).toHaveLength(2)

    // Other used/disabled nodes stay hidden.
    expect(screen.queryByRole('button', { name: /Taken node/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Disabled node/ })).not.toBeInTheDocument()

    // Offline current node is also labeled.
    fireEvent.click(screen.getByText('Cancel'))
    await waitFor(() => expect(screen.queryByText('Edit endpoint')).not.toBeInTheDocument())
    fireEvent.click(screen.getAllByText('Edit')[2])
    expect(screen.getByRole('button', { name: /Dead node \(current\)/ })).toBeInTheDocument()

    // Disabled current node is also labeled.
    fireEvent.click(screen.getByText('Cancel'))
    await waitFor(() => expect(screen.queryByText('Edit endpoint')).not.toBeInTheDocument())
    fireEvent.click(screen.getAllByText('Edit')[3])
    expect(screen.getByRole('button', { name: /Disabled node \(current\)/ })).toBeInTheDocument()
  })

  it('blocks non-numeric and out-of-range ports before submitting', async () => {
    const user = userEvent.setup()
    await openForm()
    fireEvent.change(screen.getByPlaceholderText('work-laptop'), { target: { value: 'ep-x' } })
    fireEvent.click(screen.getByRole('button', { name: /Select node or balancer/ }))
    fireEvent.click(screen.getByRole('button', { name: /Free node/ }))

    const portInput = screen.getByPlaceholderText(/auto/) as HTMLInputElement
    await user.type(portInput, '10.5')
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByText(/integer from 1 to 65535/)).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()

    await user.clear(portInput)
    await user.type(portInput, '70000')
    fireEvent.click(screen.getByText('Save'))
    expect(await screen.findByText(/integer from 1 to 65535/)).toBeInTheDocument()
    expect(createMock).not.toHaveBeenCalled()
  })

  it('submits with a valid explicit port', async () => {
    await openForm()
    fireEvent.change(screen.getByPlaceholderText('work-laptop'), { target: { value: 'ep-x' } })
    fireEvent.click(screen.getByRole('button', { name: /Select node or balancer/ }))
    fireEvent.click(screen.getByRole('button', { name: /Free node/ }))
    fireEvent.change(screen.getByPlaceholderText(/auto/), { target: { value: '1500' } })
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(createMock).toHaveBeenCalled())
    expect(createMock).toHaveBeenCalledWith(expect.objectContaining({
      name: 'ep-x',
      socks_port: 1500,
      mode: 'global',
      node_id: 3,
      balancer_id: null,
    }))
  })
})
