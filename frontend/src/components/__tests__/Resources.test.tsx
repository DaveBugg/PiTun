/**
 * What PiTun uses (per part) and the optional limits form.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  resourcesApi: { processes: vi.fn(), limits: vi.fn(), saveLimits: vi.fn() },
}))

import { ProcessesCard } from '@/components/ProcessesCard'
import { ResourceLimitsForm } from '@/components/ResourceLimitsForm'
import { resourcesApi } from '@/api/client'
import type { ProcessesResponse, ResourceLimitsResponse } from '@/types'

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

const PROCS: ProcessesResponse = {
  processes: [
    { name: 'pitun-backend', kind: 'container', cpu_percent: 12.3, mem_mb: 310, mem_limit_mb: 512, note: 'running' },
    { name: 'xray', kind: 'process', cpu_percent: 8.1, mem_mb: 120, mem_limit_mb: null, note: '9 threads' },
    { name: 'pitun-nginx', kind: 'container', cpu_percent: 0.2, mem_mb: 9, mem_limit_mb: null, note: 'running' },
  ],
  host: { mem_total_mb: 4096, cpu_count: 4 },
  docker_error: null,
}

describe('<ProcessesCard>', () => {
  beforeEach(() => vi.mocked(resourcesApi.processes).mockReset().mockResolvedValue(PROCS))

  it('shows each part with its CPU, memory and limit', async () => {
    render(wrap(<ProcessesCard />))
    expect(await screen.findByText('xray')).toBeInTheDocument()
    expect(screen.getByText('12.3%')).toBeInTheDocument()
    expect(screen.getByText('310 MB')).toBeInTheDocument()
    expect(screen.getByText(/512 MB \(61%\)/)).toBeInTheDocument()       // 310 of 512
    expect(screen.getAllByText(/^none$|^нет$/).length).toBe(2)           // the two unlimited parts
  })

  it('says why containers are missing instead of hiding it', async () => {
    vi.mocked(resourcesApi.processes).mockResolvedValue({ ...PROCS, processes: PROCS.processes.slice(1, 2), docker_error: 'Docker is not reachable from the backend' })
    render(wrap(<ProcessesCard />))
    expect(await screen.findByText(/Docker is not reachable/)).toBeInTheDocument()
  })
})

const LIMITS: ResourceLimitsResponse = {
  limits: {
    backend: { mem_mb: null, cpus: null }, frontend: { mem_mb: null, cpus: null },
    nginx: { mem_mb: null, cpus: null }, naive: { mem_mb: null, cpus: null }, xray_mem_mb: null,
  },
  effective: { backend: [{ container: 'pitun-backend', mem_mb: null, cpus: null }], frontend: [], nginx: [], naive: [] },
  docker_error: null,
  host: { mem_mb: 4096, cpus: 4 },
  targets: {
    backend: { label: 'Backend + xray (one container)', min_mb: 256 },
    frontend: { label: 'Web UI', min_mb: 32 },
    nginx: { label: 'nginx', min_mb: 32 },
    naive: { label: 'NaiveProxy sidecars (each)', min_mb: 64 },
  },
  xray_min_mb: 64,
}

describe('<ResourceLimitsForm>', () => {
  beforeEach(() => {
    vi.mocked(resourcesApi.limits).mockReset().mockResolvedValue(LIMITS)
    vi.mocked(resourcesApi.saveLimits).mockReset().mockResolvedValue({
      limits: { ...LIMITS.limits, backend: { mem_mb: 1024, cpus: null } },
      results: [{ target: 'backend', container: 'pitun-backend', ok: true, error: null }],
      docker_error: null, xray_restart_needed: false,
    })
  })

  it('starts with nothing limited', async () => {
    render(wrap(<ResourceLimitsForm />))
    const mem = await screen.findByLabelText('Backend + xray (one container) memory')
    expect(mem).toHaveValue(null)
    expect(screen.getByText(/Off by default|По умолчанию выключено/)).toBeInTheDocument()
  })

  it('sends only what was typed and null for the rest', async () => {
    const user = userEvent.setup()
    render(wrap(<ResourceLimitsForm />))
    await user.type(await screen.findByLabelText('Backend + xray (one container) memory'), '1024')
    await user.click(screen.getByRole('button', { name: /^Apply$|^Применить$/ }))
    await waitFor(() => expect(resourcesApi.saveLimits).toHaveBeenCalled())
    expect(vi.mocked(resourcesApi.saveLimits).mock.calls[0][0]).toEqual({
      backend: { mem_mb: 1024, cpus: null }, frontend: { mem_mb: null, cpus: null },
      nginx: { mem_mb: null, cpus: null }, naive: { mem_mb: null, cpus: null }, xray_mem_mb: null,
    })
    expect(await screen.findByText(/Applied|Применено/)).toBeInTheDocument()
  })

  it('removes every limit in one click', async () => {
    const user = userEvent.setup()
    render(wrap(<ResourceLimitsForm />))
    await user.click(await screen.findByRole('button', { name: /Remove all limits|Снять все лимиты/ }))
    await waitFor(() => expect(resourcesApi.saveLimits).toHaveBeenCalled())
    const sent = vi.mocked(resourcesApi.saveLimits).mock.calls[0][0]
    expect(Object.values(sent).every((v) => v === null || (v as { mem_mb: null; cpus: null }).mem_mb === null)).toBe(true)
  })

  it('reports a part Docker refused', async () => {
    vi.mocked(resourcesApi.saveLimits).mockResolvedValue({
      limits: LIMITS.limits,
      results: [{ target: 'nginx', container: 'pitun-nginx', ok: false, error: 'Docker refused the update' }],
      docker_error: null, xray_restart_needed: false,
    })
    const user = userEvent.setup()
    render(wrap(<ResourceLimitsForm />))
    await user.click(await screen.findByRole('button', { name: /^Apply$|^Применить$/ }))
    expect(await screen.findByText(/pitun-nginx — Docker refused the update/)).toBeInTheDocument()
  })
})
