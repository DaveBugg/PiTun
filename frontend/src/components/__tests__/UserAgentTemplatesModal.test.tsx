/**
 * UserAgentTemplatesModal: the table + editor behind the subscription UA
 * dropdown. Key behaviours worth pinning:
 *   - table renders key / UA / header-count / usage, and badges built-ins
 *   - create and edit round-trip the right payload, including headers
 *   - blank header rows are dropped, not sent as ""
 *   - client-side validation blocks the CRLF + non-ASCII cases BEFORE a
 *     request goes out (the backend also rejects them, but the operator
 *     should see it next to the field)
 *   - delete escalates a 409 into a second, explicit force confirmation
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AxiosError, AxiosHeaders } from 'axios'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  uaTemplatesApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}))

// The component reaches for `useConfirm()`. Stub the provider surface with
// a queue we control per test, so a confirm resolves synchronously instead
// of needing a click on the real dialog.
const confirmMock = vi.fn()
vi.mock('@/components/ConfirmModal', () => ({
  useConfirm: () => confirmMock,
}))

import { UserAgentTemplatesModal } from '@/components/UserAgentTemplatesModal'
import { uaTemplatesApi } from '@/api/client'
import type { UserAgentTemplate } from '@/types'

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

const BUILTIN: UserAgentTemplate = {
  id: 1, key: 'clash', name: 'Clash.Meta', user_agent: 'clash.meta/1.18.0',
  headers: {}, description: 'Panels serve Clash YAML.', builtin: true,
  order: 20, usage_count: 2,
}

const CUSTOM: UserAgentTemplate = {
  id: 2, key: 'panel-x', name: 'Panel X', user_agent: 'PanelX/1.2.3',
  headers: { 'X-Api-Key': 'secret', Referer: 'https://panel.example' },
  description: null, builtin: false, order: 5, usage_count: 0,
}

/** Build an AxiosError the way the delete-guard path expects to see it. */
function conflict(detail: string): AxiosError {
  const err = new AxiosError('Request failed with status code 409')
  err.response = {
    status: 409, statusText: 'Conflict', data: { detail },
    headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() },
  }
  return err
}

describe('<UserAgentTemplatesModal>', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(uaTemplatesApi.list).mockResolvedValue([CUSTOM, BUILTIN])
    confirmMock.mockResolvedValue(true)
  })

  // ── List ────────────────────────────────────────────────────────────────

  it('renders one row per template, ordered by `order`', async () => {
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))

    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())
    const rows = screen.getAllByRole('row').slice(1) // drop the header row
    expect(rows).toHaveLength(2)
    // CUSTOM has order 5, BUILTIN order 20 — custom first.
    expect(within(rows[0]).getByText('panel-x')).toBeInTheDocument()
    expect(within(rows[1]).getByText('clash')).toBeInTheDocument()
  })

  it('badges the seeded templates and shows their usage count', async () => {
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))

    await waitFor(() => expect(screen.getByText('Clash.Meta')).toBeInTheDocument())
    const builtinRow = screen.getByText('clash').closest('tr')!
    expect(within(builtinRow).getByText(/default/i)).toBeInTheDocument()
    expect(within(builtinRow).getByText('2')).toBeInTheDocument()
  })

  it('shows a header count badge only for templates that carry headers', async () => {
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))

    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())
    expect(within(screen.getByText('panel-x').closest('tr')!).getByText('+2')).toBeInTheDocument()
    // The built-in has none — em-dash placeholders instead.
    expect(
      within(screen.getByText('clash').closest('tr')!).queryByText(/^\+\d/),
    ).not.toBeInTheDocument()
  })

  it('renders an empty state when there are no templates', async () => {
    vi.mocked(uaTemplatesApi.list).mockResolvedValue([])
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))

    await waitFor(() =>
      expect(screen.getByText(/no templates yet/i)).toBeInTheDocument(),
    )
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  // ── Create ──────────────────────────────────────────────────────────────

  it('creates a template with the typed values', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.create).mockResolvedValue(CUSTOM)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await user.type(screen.getByLabelText(/^name$/i), 'New Panel')
    await user.type(screen.getByLabelText(/^key$/i), 'new-panel')
    await user.type(screen.getByLabelText(/^user-agent$/i), 'NewPanel/2.0')
    await user.click(screen.getByRole('button', { name: /create/i }))

    await waitFor(() => expect(uaTemplatesApi.create).toHaveBeenCalledOnce())
    expect(uaTemplatesApi.create).toHaveBeenCalledWith({
      key: 'new-panel',
      name: 'New Panel',
      user_agent: 'NewPanel/2.0',
      headers: {},
      description: null,
      order: 100,
    })
  })

  it('lowercases the key on the way out', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.create).mockResolvedValue(CUSTOM)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await user.type(screen.getByLabelText(/^name$/i), 'Mixed')
    await user.type(screen.getByLabelText(/^key$/i), 'MiXeD')
    await user.type(screen.getByLabelText(/^user-agent$/i), 'Mixed/1.0')
    await user.click(screen.getByRole('button', { name: /create/i }))

    await waitFor(() => expect(uaTemplatesApi.create).toHaveBeenCalledOnce())
    expect(vi.mocked(uaTemplatesApi.create).mock.calls[0][0].key).toBe('mixed')
  })

  it('sends custom headers added through the editor', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.create).mockResolvedValue(CUSTOM)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await user.type(screen.getByLabelText(/^name$/i), 'Hdr')
    await user.type(screen.getByLabelText(/^key$/i), 'hdr')
    await user.type(screen.getByLabelText(/^user-agent$/i), 'Hdr/1.0')

    await user.click(screen.getByRole('button', { name: /add header/i }))
    await user.type(screen.getByLabelText(/header 1 name/i), 'X-Api-Key')
    await user.type(screen.getByLabelText(/header 1 value/i), 'tok')
    // A second row left blank — must NOT reach the payload.
    await user.click(screen.getByRole('button', { name: /add header/i }))

    await user.click(screen.getByRole('button', { name: /create/i }))

    await waitFor(() => expect(uaTemplatesApi.create).toHaveBeenCalledOnce())
    expect(vi.mocked(uaTemplatesApi.create).mock.calls[0][0].headers)
      .toEqual({ 'X-Api-Key': 'tok' })
  })

  it('keeps an empty header value — it is the documented way to drop a header', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.create).mockResolvedValue(CUSTOM)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await user.type(screen.getByLabelText(/^name$/i), 'Drop')
    await user.type(screen.getByLabelText(/^key$/i), 'drop')
    await user.type(screen.getByLabelText(/^user-agent$/i), 'Drop/1.0')
    await user.click(screen.getByRole('button', { name: /add header/i }))
    await user.type(screen.getByLabelText(/header 1 name/i), 'Accept-Encoding')
    await user.click(screen.getByRole('button', { name: /create/i }))

    await waitFor(() => expect(uaTemplatesApi.create).toHaveBeenCalledOnce())
    expect(vi.mocked(uaTemplatesApi.create).mock.calls[0][0].headers)
      .toEqual({ 'Accept-Encoding': '' })
  })

  it('removes a header row when its × is clicked', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /edit panel x/i }))
    expect(screen.getByLabelText(/header 2 name/i)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /remove header 1/i }))
    expect(screen.queryByLabelText(/header 2 name/i)).not.toBeInTheDocument()
  })

  // ── Edit ────────────────────────────────────────────────────────────────

  it('prefills the editor from the row and PATCHes the change', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.update).mockResolvedValue(CUSTOM)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /edit panel x/i }))
    expect(screen.getByLabelText(/^key$/i)).toHaveValue('panel-x')
    expect(screen.getByLabelText(/^user-agent$/i)).toHaveValue('PanelX/1.2.3')
    expect(screen.getByLabelText(/header 1 name/i)).toHaveValue('X-Api-Key')

    await user.clear(screen.getByLabelText(/^user-agent$/i))
    await user.type(screen.getByLabelText(/^user-agent$/i), 'PanelX/9.9.9')
    await user.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(uaTemplatesApi.update).toHaveBeenCalledOnce())
    const [id, payload] = vi.mocked(uaTemplatesApi.update).mock.calls[0]
    expect(id).toBe(2)
    expect(payload.user_agent).toBe('PanelX/9.9.9')
    // Headers round-trip untouched.
    expect(payload.headers).toEqual(CUSTOM.headers)
  })

  it('warns that renaming a key will re-point its subscriptions', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Clash.Meta')).toBeInTheDocument())

    // BUILTIN has usage_count 2.
    await user.click(screen.getByRole('button', { name: /edit clash\.meta/i }))
    expect(screen.queryByText(/will be re-pointed/i)).not.toBeInTheDocument()

    await user.clear(screen.getByLabelText(/^key$/i))
    await user.type(screen.getByLabelText(/^key$/i), 'clash-meta')
    expect(screen.getByText(/2 subscription\(s\) will be re-pointed/i)).toBeInTheDocument()
  })

  it('surfaces a backend error message instead of closing the editor', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.create).mockRejectedValue(
      conflict("A User-Agent template with key 'dupe' already exists"),
    )
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await user.type(screen.getByLabelText(/^name$/i), 'Dupe')
    await user.type(screen.getByLabelText(/^key$/i), 'dupe')
    await user.type(screen.getByLabelText(/^user-agent$/i), 'Dupe/1.0')
    await user.click(screen.getByRole('button', { name: /create/i }))

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/already exists/i),
    )
    // Still on the form, values intact.
    expect(screen.getByLabelText(/^key$/i)).toHaveValue('dupe')
  })

  // ── Client-side validation ──────────────────────────────────────────────

  async function fillAndSubmit(
    user: ReturnType<typeof userEvent.setup>,
    over: { ua?: string; key?: string; header?: [string, string] },
  ) {
    await user.click(screen.getByRole('button', { name: /^add$/i }))
    await user.type(screen.getByLabelText(/^name$/i), 'V')
    await user.type(screen.getByLabelText(/^key$/i), over.key ?? 'v')
    await user.type(screen.getByLabelText(/^user-agent$/i), over.ua ?? 'V/1.0')
    if (over.header) {
      await user.click(screen.getByRole('button', { name: /add header/i }))
      await user.type(screen.getByLabelText(/header 1 name/i), over.header[0])
      if (over.header[1]) {
        await user.type(screen.getByLabelText(/header 1 value/i), over.header[1])
      }
    }
    await user.click(screen.getByRole('button', { name: /create/i }))
  }

  it('blocks a non-ASCII User-Agent before any request goes out', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await fillAndSubmit(user, { ua: 'Панель/1.0' })

    expect(screen.getByRole('alert')).toHaveTextContent(/ascii/i)
    expect(uaTemplatesApi.create).not.toHaveBeenCalled()
  })

  it('blocks an invalid key before any request goes out', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await fillAndSubmit(user, { key: 'bad key' })

    expect(screen.getByRole('alert')).toHaveTextContent(/key must/i)
    expect(uaTemplatesApi.create).not.toHaveBeenCalled()
  })

  it('blocks a header name the transport owns', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await fillAndSubmit(user, { header: ['User-Agent', 'sneaky'] })

    expect(screen.getByRole('alert')).toHaveTextContent(/cannot be overridden/i)
    expect(uaTemplatesApi.create).not.toHaveBeenCalled()
  })

  it('blocks a malformed header name', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await fillAndSubmit(user, { header: ['X Bad', 'v'] })

    expect(screen.getByRole('alert')).toHaveTextContent(/invalid header name/i)
    expect(uaTemplatesApi.create).not.toHaveBeenCalled()
  })

  it('blocks a non-ASCII header value', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await fillAndSubmit(user, { header: ['X-Ok', 'значение'] })

    expect(screen.getByRole('alert')).toHaveTextContent(/ascii/i)
    expect(uaTemplatesApi.create).not.toHaveBeenCalled()
  })

  // ── Delete ──────────────────────────────────────────────────────────────

  it('deletes after a single confirmation when nothing uses the template', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.delete).mockResolvedValue(undefined as never)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /delete panel x/i }))

    await waitFor(() => expect(uaTemplatesApi.delete).toHaveBeenCalledOnce())
    expect(uaTemplatesApi.delete).toHaveBeenCalledWith(2, false)
    expect(confirmMock).toHaveBeenCalledTimes(1)
  })

  it('does nothing when the confirmation is declined', async () => {
    const user = userEvent.setup()
    confirmMock.mockResolvedValue(false)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /delete panel x/i }))

    expect(uaTemplatesApi.delete).not.toHaveBeenCalled()
  })

  it('escalates a 409 into an explicit force confirmation', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.delete)
      .mockRejectedValueOnce(conflict("Template 'clash' is used by 2 subscription(s): A, B."))
      .mockResolvedValueOnce(undefined as never)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Clash.Meta')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /delete clash\.meta/i }))

    await waitFor(() => expect(uaTemplatesApi.delete).toHaveBeenCalledTimes(2))
    expect(uaTemplatesApi.delete).toHaveBeenNthCalledWith(1, 1, false)
    expect(uaTemplatesApi.delete).toHaveBeenNthCalledWith(2, 1, true)
    // The second dialog quotes the backend's message so the operator sees
    // exactly which subscriptions are affected.
    expect(confirmMock).toHaveBeenCalledTimes(2)
    expect(confirmMock.mock.calls[1][0].body).toMatch(/used by 2 subscription/i)
  })

  it('does not force-delete when the second confirmation is declined', async () => {
    const user = userEvent.setup()
    confirmMock.mockResolvedValueOnce(true).mockResolvedValueOnce(false)
    vi.mocked(uaTemplatesApi.delete).mockRejectedValueOnce(conflict('in use'))
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Clash.Meta')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /delete clash\.meta/i }))

    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(2))
    expect(uaTemplatesApi.delete).toHaveBeenCalledTimes(1)
  })

  it('warns that a deleted built-in will not come back', async () => {
    const user = userEvent.setup()
    vi.mocked(uaTemplatesApi.delete).mockResolvedValue(undefined as never)
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Clash.Meta')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /delete clash\.meta/i }))

    expect(confirmMock.mock.calls[0][0].body).toMatch(/built-in/i)
  })

  // ── Navigation ──────────────────────────────────────────────────────────

  it('returns to the list from the editor without saving', async () => {
    const user = userEvent.setup()
    render(wrap(<UserAgentTemplatesModal onClose={() => {}} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /^add$/i }))
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(uaTemplatesApi.create).not.toHaveBeenCalled()
  })

  it('closes via the Close button', async () => {
    const user = userEvent.setup()
    const onClose = vi.fn()
    render(wrap(<UserAgentTemplatesModal onClose={onClose} />))
    await waitFor(() => expect(screen.getByText('Panel X')).toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: /close/i }))
    expect(onClose).toHaveBeenCalledOnce()
  })
})
