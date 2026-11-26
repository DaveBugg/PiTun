// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { ConfirmProvider } from '@/components/ConfirmModal'
import { AssistantPanel } from '@/components/AssistantPanel'
import { assistantApi } from '@/api/client'
import type { AssistantSettings, AssistantPlan, AssistantPendingPlan } from '@/api/client'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return {
    ...actual,
    assistantApi: {
      settings: vi.fn(),
      patchSettings: vi.fn(),
      test: vi.fn(),
      models: vi.fn(),
      conversations: vi.fn(),
      createConversation: vi.fn(),
      conversation: vi.fn(),
      deleteConversation: vi.fn(),
      send: vi.fn(),
      decide: vi.fn(),
      undo: vi.fn(),
      transcribe: vi.fn(),
      mcpTokens: vi.fn(),
      createMcpToken: vi.fn(),
      revokeMcpToken: vi.fn(),
      pendingPlans: vi.fn(),
      macros: vi.fn(),
      runMacro: vi.fn(),
      facts: vi.fn(),
      addFact: vi.fn(),
      confirmFact: vi.fn(),
      deleteFact: vi.fn(),
      discover: vi.fn(),
    },
  }
})

function makeSettings(overrides: Partial<AssistantSettings> = {}): AssistantSettings {
  return {
    enabled: true,
    provider: 'openrouter',
    model: 'gpt-4o',
    base_url: '',
    default_base_url: '',
    egress: 'active',
    small_mode: 'off',
    small_active: false,
    web_search: false,
    voice_mode: 'off',
    stt_url: '',
    stt_model: '',
    voice_lang: '',
    mcp_enabled: false,
    screenshot_days: 7,
    monthly_token_limit: 0,
    has_key: { openrouter: false, openai: false, anthropic: false, local: false },
    usage: { input: 0, output: 0, cost: 0, requests: 0 },
    providers: [],
    ...overrides,
  }
}

function makePlan(overrides: Partial<AssistantPlan> = {}): AssistantPlan {
  return {
    id: 1,
    conversation_id: 1,
    title: 'Test plan',
    summary: '',
    steps: [{ method: 'POST', path: '/routing/rules' }],
    before: [],
    status: 'pending',
    results: [],
    can_undo: false,
    ...overrides,
  }
}

function makePendingPlan(overrides: Partial<AssistantPendingPlan> = {}): AssistantPendingPlan {
  return {
    id: 1,
    title: 'Test plan',
    status: 'pending',
    conversation_id: 1,
    conversation_title: 'Chat',
    source: 'mcp',
    ...overrides,
  }
}

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={qc}>
      <ConfirmProvider>
        <AssistantPanel />
      </ConfirmProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  Element.prototype.scrollIntoView ??= vi.fn() as unknown as typeof Element.prototype.scrollIntoView
})

describe('AssistantPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  it('shows the composer, new-chat button and example prompts in normal enabled mode', async () => {
    const user = userEvent.setup()
    ;(assistantApi.settings as ReturnType<typeof vi.fn>).mockResolvedValue(makeSettings())
    ;(assistantApi.pendingPlans as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(assistantApi.conversations as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(assistantApi.macros as ReturnType<typeof vi.fn>).mockResolvedValue([])

    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Assistant')).toBeInTheDocument())
    await user.click(screen.getByLabelText('Assistant'))

    await waitFor(() => expect(screen.getByTitle('New chat')).toBeInTheDocument())
    expect(screen.getByPlaceholderText('Ask or describe a change…')).toBeInTheDocument()
    expect(screen.getByLabelText('Send')).toBeInTheDocument()
    expect(
      screen.getByText('Route the living-room TV through the fastest German node'),
    ).toBeInTheDocument()
  })

  it('approving a plan still works in normal enabled mode', async () => {
    const user = userEvent.setup()
    const p = makePlan({ id: 42 })
    ;(assistantApi.settings as ReturnType<typeof vi.fn>).mockResolvedValue(makeSettings())
    ;(assistantApi.pendingPlans as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(assistantApi.conversations as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 1, title: 'Chat', updated_at: '2026-01-01T00:00:00Z' },
    ])
    ;(assistantApi.conversation as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 1,
      title: 'Chat',
      items: [{ kind: 'plan', plan: p }],
    })
    ;(assistantApi.decide as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
    ;(assistantApi.macros as ReturnType<typeof vi.fn>).mockResolvedValue([])

    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Assistant')).toBeInTheDocument())
    await user.click(screen.getByLabelText('Assistant'))

    await waitFor(() => expect(screen.getByText('Test plan')).toBeInTheDocument())
    await user.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(assistantApi.decide).toHaveBeenCalledWith(
        42,
        'approve',
        expect.any(Function),
        expect.any(AbortSignal),
      ),
    )
  })

  it('hides composer, new-chat, examples and quick actions in MCP-only mode', async () => {
    const user = userEvent.setup()
    ;(assistantApi.settings as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeSettings({ enabled: false, mcp_enabled: true }),
    )
    ;(assistantApi.pendingPlans as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(assistantApi.conversations as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(assistantApi.macros as ReturnType<typeof vi.fn>).mockResolvedValue([])

    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Assistant')).toBeInTheDocument())
    await user.click(screen.getByLabelText('Assistant'))

    await waitFor(() =>
      expect(
        screen.getAllByText(
          'MCP-only mode: chat, screenshots, voice and quick actions are disabled; only plan approval is available.',
        ).length,
      ).toBeGreaterThan(0),
    )

    expect(screen.queryByTitle('New chat')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('Ask or describe a change…')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Send')).not.toBeInTheDocument()
    expect(screen.queryByTitle('Quick actions (forms, no model needed)')).not.toBeInTheDocument()
    expect(
      screen.queryByText('Route the living-room TV through the fastest German node'),
    ).not.toBeInTheDocument()
  })

  it('keeps plan approval usable in MCP-only mode', async () => {
    const user = userEvent.setup()
    const p = makePlan({ id: 77, conversation_id: 2 })
    ;(assistantApi.settings as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeSettings({ enabled: false, mcp_enabled: true }),
    )
    ;(assistantApi.pendingPlans as ReturnType<typeof vi.fn>).mockResolvedValue([
      makePendingPlan({ id: 77, conversation_id: 2, source: 'mcp' }),
    ])
    ;(assistantApi.conversations as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: 2, title: 'MCP chat', updated_at: '2026-01-01T00:00:00Z' },
    ])
    ;(assistantApi.conversation as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 2,
      title: 'MCP chat',
      items: [{ kind: 'plan', plan: p }],
    })
    ;(assistantApi.decide as ReturnType<typeof vi.fn>).mockResolvedValue(undefined)
    ;(assistantApi.macros as ReturnType<typeof vi.fn>).mockResolvedValue([])

    renderPanel()

    await waitFor(() => expect(screen.getByLabelText('Assistant')).toBeInTheDocument())
    await user.click(screen.getByLabelText('Assistant'))

    await waitFor(() => expect(screen.getByText('Test plan')).toBeInTheDocument())
    expect(screen.queryByPlaceholderText('Ask or describe a change…')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Apply' }))

    await waitFor(() =>
      expect(assistantApi.decide).toHaveBeenCalledWith(
        77,
        'approve',
        expect.any(Function),
        expect.any(AbortSignal),
      ),
    )
  })
})
