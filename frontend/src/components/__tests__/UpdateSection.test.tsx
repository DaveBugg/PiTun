/**
 * The update panel has two behaviours that look like bugs unless they
 * are pinned down:
 *
 *  - a failed status poll WHILE an update is running means the backend is
 *    restarting itself (that is the update working), so it must render as
 *    "restarting", never as an error;
 *  - "could not reach GitHub" must never render as "you are up to date" —
 *    on this box a dead tunnel takes GitHub with it, and quietly claiming
 *    the latest version would hide exactly the failure worth seeing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  updateApi: { check: vi.fn(), status: vi.fn(), start: vi.fn() },
}))

vi.mock('@/components/ConfirmModal', () => ({
  // Auto-confirm: the dialog itself is covered by its own tests.
  useConfirm: () => () => Promise.resolve(true),
}))

import { UpdateSection } from '@/components/UpdateSection'
import { updateApi } from '@/api/client'

const IDLE = {
  state: 'idle' as const, pct: 0, step: '', message: '',
  ok: null, request_pending: false,
}

function wrap(ui: ReactNode) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

beforeEach(() => {
  sessionStorage.clear()
  vi.mocked(updateApi.status).mockResolvedValue({ ...IDLE })
})

describe('UpdateSection', () => {
  it('does not call GitHub until the user asks', async () => {
    render(wrap(<UpdateSection />))
    await waitFor(() => expect(updateApi.status).toHaveBeenCalled())
    expect(updateApi.check).not.toHaveBeenCalled()
  })

  it('offers the update and names the route that answered', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.4.7', latest: 'v1.4.8', update_available: true,
      network_path: 'active node', notes: null, error: null,
    })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))

    expect(await screen.findByText('v1.4.8')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /update now/i })).toBeInTheDocument()
    expect(screen.getByText(/active node/)).toBeInTheDocument()
  })

  it('reports an unreachable check instead of claiming up-to-date', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.4.7', latest: null, update_available: false,
      network_path: 'unreachable',
      error: 'GitHub is unreachable. If the kill switch is armed over a dead tunnel, fix the active node first.',
    })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))

    expect(await screen.findByText(/GitHub is unreachable/i)).toBeInTheDocument()
    expect(screen.queryByText(/latest version/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /update now/i })).not.toBeInTheDocument()
  })

  it('shows up-to-date with a re-install escape hatch', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.4.8', latest: 'v1.4.8', update_available: false,
      network_path: 'direct', error: null,
    })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))

    expect(await screen.findByText(/latest version/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /re-install/i })).toBeInTheDocument()
  })

  it('sends force:true for a re-install', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.4.8', latest: 'v1.4.8', update_available: false,
      network_path: 'direct', error: null,
    })
    vi.mocked(updateApi.start).mockResolvedValue({
      ...IDLE, state: 'queued', request_pending: true,
    })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))
    await userEvent.click(await screen.findByRole('button', { name: /re-install/i }))

    await waitFor(() => expect(updateApi.start).toHaveBeenCalledWith({ force: true }))
  })

  it('renders progress from the agent status file', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({
      state: 'running', pct: 70, step: 'images',
      message: 'Loading pre-built Docker images',
      ok: null, from: '1.4.7', to: 'v1.4.8', request_pending: false,
    })
    render(wrap(<UpdateSection />))

    expect(await screen.findByText(/Loading pre-built Docker images/)).toBeInTheDocument()
    expect(screen.getByText('70%')).toBeInTheDocument()
  })

  it('treats a dropped poll during an update as a restart, not an error', async () => {
    // First poll: running. Then the backend goes away — that IS the
    // update replacing this container.
    vi.mocked(updateApi.status)
      .mockResolvedValueOnce({
        state: 'running', pct: 60, step: 'apply', message: 'Applying',
        ok: null, request_pending: false,
      })
      .mockRejectedValue(new Error('Network Error'))

    render(wrap(<UpdateSection />))
    await screen.findByText('Applying')
    expect(await screen.findByText(/restarting services/i, {}, { timeout: 4000 }))
      .toBeInTheDocument()
    expect(screen.queryByText(/Network Error/)).not.toBeInTheDocument()
  })

  it('says the agent is missing when a request is never picked up', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({
      ...IDLE, state: 'queued', message: 'Waiting for the update agent',
      request_pending: true,
    })
    render(wrap(<UpdateSection />))

    expect(await screen.findByText(/agent is not installed/i)).toBeInTheDocument()
  })

  it('reports a failed update and that the rollback happened', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({
      state: 'failed', pct: 100, step: 'health',
      message: 'backend did not become healthy',
      ok: false, from: '1.4.7', to: 'v1.4.8', request_pending: false,
    })
    render(wrap(<UpdateSection />))

    expect(await screen.findByText(/backend did not become healthy/)).toBeInTheDocument()
    // The snapshot is taken, not auto-restored — promising a rollback
    // that never happens is worse than saying where the file is.
    expect(screen.getByText(/data-backup-pre-\*\.db/)).toBeInTheDocument()
    expect(screen.queryByText(/restored automatically/i)).not.toBeInTheDocument()
  })

  it('surfaces a check failure instead of failing silently', async () => {
    vi.mocked(updateApi.check).mockRejectedValue(new Error('boom'))
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))

    expect(await screen.findByText('boom')).toBeInTheDocument()
  })

  it('warns that a downgrade takes this very panel away', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.4.8', latest: 'v1.4.7', update_available: false,
      network_path: 'active node', error: null,
      target_lacks_update_ui: true, update_ui_since: '1.4.8',
    })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))

    // Visible without opening the dialog: the panel about to vanish is
    // the one the operator is looking at.
    const warning = await screen.findByText(/has no in-UI updater/i)
    expect(warning).toBeInTheDocument()
    expect(warning.textContent).toMatch(/pitun-update\.sh --force/)
  })

  it('stays quiet when the target keeps the updater', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.4.8', latest: 'v1.5.0', update_available: true,
      network_path: 'direct', error: null,
      target_lacks_update_ui: false, update_ui_since: '1.4.8',
    })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))

    await screen.findByRole('button', { name: /update now/i })
    expect(screen.queryByText(/has no in-UI updater/i)).not.toBeInTheDocument()
  })
})

// The last run's result is kept on disk for good; the banner must not be.
describe('UpdateSection result banner', () => {
  const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
  const base = { pct: 100, step: 'done', message: '', ok: true, request_pending: false, from: '1.6.6', to: '1.7.1' }

  beforeEach(() => {
    localStorage.clear()
  })

  it('shows a fresh success', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({ ...base, state: 'done', updated_at: ago(60_000) })
    render(wrap(<UpdateSection />))
    expect(await screen.findByText('1.7.1')).toBeInTheDocument()
  })

  it('hides an old success', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({ ...base, state: 'done', updated_at: ago(3 * 3600_000) })
    render(wrap(<UpdateSection />))
    await waitFor(() => expect(updateApi.status).toHaveBeenCalled())
    expect(screen.queryByText('1.7.1')).not.toBeInTheDocument()
  })

  it('keeps a failure however old until it is closed, and keeps it closed', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({
      ...base, state: 'failed', ok: false, message: 'Installer exited with code 1', updated_at: ago(3 * 3600_000),
    })
    const user = userEvent.setup()
    const first = render(wrap(<UpdateSection />))
    expect(await screen.findByText(/Installer exited with code 1/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Dismiss|Закрыть/ }))
    expect(screen.queryByText(/Installer exited with code 1/)).not.toBeInTheDocument()
    first.unmount()
    render(wrap(<UpdateSection />))
    await waitFor(() => expect(vi.mocked(updateApi.status).mock.calls.length).toBeGreaterThan(1))
    expect(screen.queryByText(/Installer exited with code 1/)).not.toBeInTheDocument()
  })
})

describe('UpdateSection right after "Update now"', () => {
  it('keeps showing "starting" while the status file still holds the previous run', async () => {
    const old = {
      state: 'done' as const, pct: 100, step: 'done', message: 'Updated', ok: true,
      from: '1.6.6', to: '1.7.1', updated_at: new Date(Date.now() - 3600_000).toISOString(),
      request_pending: false,
    }
    vi.mocked(updateApi.status).mockResolvedValue(old)
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.7.1', latest: 'v1.7.2', update_available: true,
      network_path: 'direct', notes: null, error: null,
    })
    // The agent consumed the request but has not written anything yet: still the old run.
    vi.mocked(updateApi.start).mockResolvedValue({ ...old })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))
    await userEvent.click(await screen.findByRole('button', { name: /update now/i }))

    expect(await screen.findByText(/Starting the update|Запуск обновления/)).toBeInTheDocument()
    // Not offered again while it starts.
    expect(screen.getByRole('button', { name: /update now/i })).toBeDisabled()
  })
})

describe('UpdateSection launch edge cases', () => {
  const OLD = {
    state: 'done' as const, pct: 100, step: 'done', message: 'Updated', ok: true,
    from: '1.6.6', to: '1.7.1', updated_at: new Date(Date.now() - 3600_000).toISOString(),
    request_pending: false,
  }
  const CHECK = {
    current: '1.7.1', latest: 'v1.7.2', update_available: true,
    network_path: 'direct', notes: null, error: null,
  }

  it('pins the version the user was shown', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({ ...OLD })
    vi.mocked(updateApi.check).mockResolvedValue({ ...CHECK })
    vi.mocked(updateApi.start).mockResolvedValue({ ...OLD })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))
    await userEvent.click(await screen.findByRole('button', { name: /update now/i }))
    await waitFor(() => expect(updateApi.start).toHaveBeenCalledWith({ force: false, version: 'v1.7.2' }))
  })

  it('does not drop the progress box when the agent answers "nothing to do"', async () => {
    vi.mocked(updateApi.status).mockResolvedValue({ ...OLD })
    vi.mocked(updateApi.check).mockResolvedValue({ ...CHECK })
    vi.mocked(updateApi.start).mockResolvedValue({ ...OLD })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))
    await userEvent.click(await screen.findByRole('button', { name: /update now/i }))
    expect(await screen.findByText(/Starting the update|Запуск обновления/)).toBeInTheDocument()
    vi.mocked(updateApi.status).mockResolvedValue({
      ...IDLE, state: 'idle', pct: 100, step: 'up-to-date', message: 'Already on 1.7.1',
      updated_at: new Date().toISOString(),
    })
    // The answer is shown, not swallowed.
    expect(await screen.findByText('Already on 1.7.1', {}, { timeout: 5000 })).toBeInTheDocument()
  }, 10000)

  it('still shows the update as running after a reload while the backend is down', async () => {
    sessionStorage.setItem('pitun.update.launch', JSON.stringify({ since: Date.now(), stamp: OLD.updated_at }))
    vi.mocked(updateApi.status).mockRejectedValue(new Error('Network Error'))
    render(wrap(<UpdateSection />))
    expect(await screen.findByText(/Restarting services|Перезапуск сервисов/)).toBeInTheDocument()
  })

  it('shows why the update could not be started', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({ ...CHECK })
    vi.mocked(updateApi.start).mockRejectedValue(new Error('boom'))
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))
    await userEvent.click(await screen.findByRole('button', { name: /update now/i }))
    expect(await screen.findByText(/boom|Could not start the update|Не удалось/)).toBeInTheDocument()
  })
})

describe('UpdateSection with an unreadable status mid-update', () => {
  const OLD = {
    state: 'done' as const, pct: 100, step: 'done', message: 'Updated', ok: true,
    from: '1.6.6', to: '1.7.1', updated_at: new Date(Date.now() - 3600_000).toISOString(),
    request_pending: false,
  }

  it('keeps the last known step on screen instead of dropping the progress', async () => {
    vi.mocked(updateApi.check).mockResolvedValue({
      current: '1.7.1', latest: 'v1.7.2', update_available: true,
      network_path: 'direct', notes: null, error: null,
    })
    vi.mocked(updateApi.start).mockResolvedValue({
      ...OLD, state: 'queued', pct: 0, updated_at: new Date().toISOString(), request_pending: true,
    })
    vi.mocked(updateApi.status).mockResolvedValue({ ...OLD })
    render(wrap(<UpdateSection />))
    await userEvent.click(screen.getByRole('button', { name: /check for updates/i }))
    await userEvent.click(await screen.findByRole('button', { name: /update now/i }))
    vi.mocked(updateApi.status).mockResolvedValue({
      ...OLD, state: 'running', pct: 45, message: 'Preparing host', updated_at: new Date().toISOString(),
    })
    expect(await screen.findByText('Preparing host', {}, { timeout: 5000 })).toBeInTheDocument()

    // The status file turns unreadable for a few seconds (this is what the colour codes did).
    vi.mocked(updateApi.status).mockResolvedValue({
      state: 'unknown', pct: 0, step: '', message: 'status file is not valid JSON',
      ok: null, request_pending: false,
    })
    await new Promise((r) => setTimeout(r, 2600))
    expect(screen.getByText('Preparing host')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /update now/i })).toBeDisabled()

    vi.mocked(updateApi.status).mockResolvedValue({
      ...OLD, state: 'done', to: '1.7.2', updated_at: new Date().toISOString(),
    })
    expect(await screen.findByText(/Updated to|Обновлено до/, {}, { timeout: 5000 })).toBeInTheDocument()
  }, 20000)
})
