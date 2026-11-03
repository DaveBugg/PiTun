/**
 * Domains page: collection switch, search, selecting rows, turning a selection
 * into a rule, and cleaning up.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  domainStatsApi: { list: vi.fn(), updateConfig: vi.fn(), prune: vi.fn(), createRules: vi.fn() },
  routingApi: { listRules: vi.fn() },
  nodesApi: { list: vi.fn() },
  balancersApi: { list: vi.fn() },
}))
vi.mock('@/hooks/useRoutingSets', () => ({ useRoutingSets: () => ({ data: [] }) }))
vi.mock('@/components/ConfirmModal', () => ({ useConfirm: () => async () => true }))

import { DomainStats } from '@/pages/DomainStats'
import { balancersApi, domainStatsApi, nodesApi, routingApi } from '@/api/client'
import type { DomainStatsPage } from '@/types'

const now = new Date().toISOString()
const PAGE: DomainStatsPage = {
  items: [
    { domain: 'www.youtube.com', hits: 40, first_seen: now, last_seen: now, ips: ['203.0.113.46'] },
    { domain: 'i.ytimg.com', hits: 10, first_seen: now, last_seen: now, ips: [] },
  ],
  total: 2,
  summary: { rows: 2, total_hits: 50, approx_bytes: 2048, oldest: now, newest: now, pending: 0, dropped: 0 },
  config: { enabled: true, retention_days: 90, max_rows: 20000 },
  log_level_ok: true,
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

describe('<DomainStats>', () => {
  beforeEach(() => {
    vi.mocked(domainStatsApi.list).mockReset().mockResolvedValue(PAGE)
    vi.mocked(domainStatsApi.updateConfig).mockReset().mockResolvedValue(PAGE.config)
    vi.mocked(domainStatsApi.prune).mockReset().mockResolvedValue({ removed: 3, summary: PAGE.summary })
    vi.mocked(domainStatsApi.createRules).mockReset().mockResolvedValue({ mode: 'new', rule_id: 9, added: 2, already_present: 0 })
    vi.mocked(routingApi.listRules).mockReset().mockResolvedValue([
      { id: 5, name: 'Mine', rule_type: 'domain', match_value: 'example.com', action: 'proxy', order: 10, enabled: true } as never,
    ])
    vi.mocked(nodesApi.list).mockReset().mockResolvedValue([])
    vi.mocked(balancersApi.list).mockReset().mockResolvedValue([])
  })

  it('lists domains with their counters and the disk estimate', async () => {
    render(wrap(<DomainStats />))
    expect(await screen.findByText('www.youtube.com')).toBeInTheDocument()
    expect(screen.getByText('40')).toBeInTheDocument()
    expect(screen.getByText(/2 domains|2 доменов/)).toBeInTheDocument()
  })

  it('warns when xray logs too little for anything to be collected', async () => {
    vi.mocked(domainStatsApi.list).mockResolvedValue({ ...PAGE, log_level_ok: false })
    render(wrap(<DomainStats />))
    expect(await screen.findByText(/“info” or “debug”|«info» или «debug»/)).toBeInTheDocument()
  })

  it('says collection is off instead of showing an empty table without a reason', async () => {
    vi.mocked(domainStatsApi.list).mockResolvedValue({ ...PAGE, items: [], total: 0, config: { ...PAGE.config, enabled: false } })
    render(wrap(<DomainStats />))
    expect(await screen.findByText(/Collection is off|Сбор выключен/)).toBeInTheDocument()
  })

  it('switches collection on', async () => {
    vi.mocked(domainStatsApi.list).mockResolvedValue({ ...PAGE, config: { ...PAGE.config, enabled: false } })
    const user = userEvent.setup()
    render(wrap(<DomainStats />))
    await user.click(await screen.findByRole('checkbox', { name: /Collect domain statistics|Собирать статистику/ }))
    await waitFor(() => expect(domainStatsApi.updateConfig).toHaveBeenCalledWith({ enabled: true }))
  })

  it('sends the search after typing stops', async () => {
    const user = userEvent.setup()
    render(wrap(<DomainStats />))
    await screen.findByText('www.youtube.com')
    await user.type(screen.getByPlaceholderText(/Search a domain|Поиск по домену/), 'tube')
    await waitFor(() =>
      expect(vi.mocked(domainStatsApi.list).mock.calls.some(([p]) => p.q === 'tube')).toBe(true),
    )
  })

  it('creates a rule from the selected domains', async () => {
    const user = userEvent.setup()
    render(wrap(<DomainStats />))
    await user.click(await screen.findByRole('checkbox', { name: 'www.youtube.com' }))
    await user.click(screen.getByRole('checkbox', { name: 'i.ytimg.com' }))
    await user.click(screen.getByRole('button', { name: /Create rule|Создать правило/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /Create rule|Создать правило/ }))
    await waitFor(() => expect(domainStatsApi.createRules).toHaveBeenCalled())
    const body = vi.mocked(domainStatsApi.createRules).mock.calls[0][0]
    expect(body.domains?.sort()).toEqual(['i.ytimg.com', 'www.youtube.com'])
    expect(body).toMatchObject({ use: 'domains', mode: 'new', action: 'proxy' })
    expect(await screen.findByText(/Rule created with 2|Правило создано, записей: 2/)).toBeInTheDocument()
  })

  it('can add the selection to an existing rule', async () => {
    vi.mocked(domainStatsApi.createRules).mockResolvedValue({ mode: 'append', rule_id: 5, added: 1, already_present: 1 })
    const user = userEvent.setup()
    render(wrap(<DomainStats />))
    await user.click(await screen.findByRole('checkbox', { name: 'www.youtube.com' }))
    await user.click(screen.getByRole('button', { name: /Create rule|Создать правило/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /Add to an existing rule|В существующее правило/ }))
    await user.selectOptions(within(dialog).getByRole('combobox'), '5')
    await user.click(within(dialog).getByRole('button', { name: /^Add$|^Добавить$/ }))
    await waitFor(() => expect(domainStatsApi.createRules).toHaveBeenCalled())
    expect(vi.mocked(domainStatsApi.createRules).mock.calls[0][0]).toMatchObject({ mode: 'append', rule_id: 5 })
  })

  it('removes the selected domains', async () => {
    const user = userEvent.setup()
    render(wrap(<DomainStats />))
    await user.click(await screen.findByRole('checkbox', { name: 'i.ytimg.com' }))
    await user.click(screen.getByRole('button', { name: /^Remove$|^Удалить$/ }))
    await waitFor(() => expect(domainStatsApi.prune).toHaveBeenCalledWith({ domains: ['i.ytimg.com'] }))
  })

  it('cleans up by age', async () => {
    const user = userEvent.setup()
    render(wrap(<DomainStats />))
    await user.click(await screen.findByRole('button', { name: /Clean up|Очистка/ }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: /^Remove$|^Удалить$/ }))
    await waitFor(() => expect(domainStatsApi.prune).toHaveBeenCalledWith({ older_than_days: 30 }))
  })
})
