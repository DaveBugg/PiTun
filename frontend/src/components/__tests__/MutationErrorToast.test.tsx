/**
 * The global toast must fire on a FRESH failure only.
 *
 * The first version keyed off "some mutation in the cache currently holds
 * an error", which is a state, not an event: a failed mutation lingers
 * until garbage collection, so any later cache activity could re-surface
 * it. Keying off the error ACTION instead makes one failure produce
 * exactly one toast, which is what these tests pin down.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider, useMutation } from '@tanstack/react-query'
import { AxiosError, AxiosHeaders } from 'axios'
import type { ReactNode } from 'react'

import { MutationErrorToast } from '@/components/MutationErrorToast'

let qc: QueryClient

function wrap(ui: ReactNode) {
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

function notFound(): AxiosError {
  const err = new AxiosError('Request failed with status code 404')
  err.response = {
    status: 404,
    statusText: 'Not Found',
    data: { detail: 'Rule not found' },
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  }
  return err
}

/** Two buttons: one always fails, one always succeeds. */
function Harness() {
  const failing = useMutation({ mutationFn: async () => { throw notFound() } })
  const ok = useMutation({ mutationFn: async () => 'fine' })
  return (
    <>
      <button onClick={() => failing.mutate()}>fail</button>
      <button onClick={() => ok.mutate()}>succeed</button>
      <MutationErrorToast />
    </>
  )
}

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
})

describe('MutationErrorToast', () => {
  it('shows the backend message when a mutation fails', async () => {
    const user = userEvent.setup()
    render(wrap(<Harness />))

    await user.click(screen.getByText('fail'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Rule not found')
  })

  it('does not resurrect a stale error on a later successful action', async () => {
    const user = userEvent.setup()
    render(wrap(<Harness />))

    await user.click(screen.getByText('fail'))
    await screen.findByRole('alert')

    // Dismiss, then do something entirely unrelated that succeeds.
    await user.click(screen.getByLabelText('Dismiss error'))
    expect(screen.queryByRole('alert')).toBeNull()

    await user.click(screen.getByText('succeed'))
    await waitFor(() => expect(qc.getMutationCache().getAll().length).toBeGreaterThan(1))
    // The old failure must stay dismissed.
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows a second, genuinely new failure', async () => {
    const user = userEvent.setup()
    render(wrap(<Harness />))

    await user.click(screen.getByText('fail'))
    await screen.findByRole('alert')
    await user.click(screen.getByLabelText('Dismiss error'))
    expect(screen.queryByRole('alert')).toBeNull()

    await user.click(screen.getByText('fail'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Rule not found')
  })
})
