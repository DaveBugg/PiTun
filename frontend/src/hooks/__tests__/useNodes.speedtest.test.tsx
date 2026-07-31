/**
 * Speed-test state must live in the query cache, not in page state.
 *
 * The bugs this pins down, all reported as "the speed test disappears /
 * glitches":
 *   - navigating away (or paginating the node out of view) unmounted the
 *     page, so per-`mutate()` callbacks never ran and the result was lost
 *   - starting a second test detached the first one's callbacks, leaving
 *     its row stuck on "testing…" forever
 *   - `mutation.variables` describes only the LAST call, so the spinner
 *     and the disabled button tracked the wrong node
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  nodesApi: {
    speedtest: vi.fn(),
    speedtestAll: vi.fn(),
  },
}))

import {
  useSpeedtest,
  useSpeedtestAll,
  useSpeedResults,
  useSpeedPending,
} from '@/hooks/useNodes'
import { nodesApi } from '@/api/client'

let qc: QueryClient

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>
}

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

beforeEach(() => {
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
})

describe('speed-test result cache', () => {
  it('marks the node pending and writes the result on success', async () => {
    // Deferred, not mockResolvedValue: an already-settled promise races
    // through onMutate → onSettled before the pending state is observable.
    const d = deferred<any>()
    vi.mocked(nodesApi.speedtest).mockReturnValue(d.promise)
    const { result } = renderHook(() => ({
      run: useSpeedtest(),
      results: useSpeedResults(),
      pending: useSpeedPending(),
    }), { wrapper })

    act(() => { result.current.run.mutate(7) })
    await waitFor(() => expect(result.current.pending.data).toContain(7))
    expect(result.current.results.data[7]).toBe('testing…')

    await act(async () => {
      d.resolve({ node_id: 7, node_name: 'n', download_mbps: 42.5 })
      await d.promise
    })
    await waitFor(() => expect(result.current.results.data[7]).toBe('42.5 Mbps'))
    expect(result.current.pending.data).not.toContain(7)
  })

  it('keeps the result after the component that started it unmounts', async () => {
    const d = deferred<{ node_id: number; node_name: string; download_mbps: number }>()
    vi.mocked(nodesApi.speedtest).mockReturnValue(d.promise)

    const started = renderHook(() => useSpeedtest(), { wrapper })
    act(() => { started.result.current.mutate(3) })
    // Operator navigates to another page while the test is still running.
    started.unmount()

    await act(async () => {
      d.resolve({ node_id: 3, node_name: 'n', download_mbps: 12 })
      await d.promise
    })

    // Coming back to the page must show the finished measurement.
    const reopened = renderHook(() => ({
      results: useSpeedResults(), pending: useSpeedPending(),
    }), { wrapper })
    await waitFor(() =>
      expect(reopened.result.current.results.data[3]).toBe('12 Mbps'),
    )
    expect(reopened.result.current.pending.data).not.toContain(3)
  })

  it('tracks two concurrent tests independently', async () => {
    const a = deferred<any>()
    const b = deferred<any>()
    vi.mocked(nodesApi.speedtest).mockImplementation((id: number) =>
      (id === 1 ? a.promise : b.promise),
    )

    const { result } = renderHook(() => ({
      run: useSpeedtest(),
      results: useSpeedResults(),
      pending: useSpeedPending(),
    }), { wrapper })

    act(() => { result.current.run.mutate(1) })
    act(() => { result.current.run.mutate(2) })
    await waitFor(() => {
      expect(result.current.pending.data).toContain(1)
      expect(result.current.pending.data).toContain(2)
    })

    // Node 2 finishes first — node 1 must stay pending, not be abandoned.
    await act(async () => {
      b.resolve({ node_id: 2, node_name: 'b', download_mbps: 5 })
      await b.promise
    })
    await waitFor(() => expect(result.current.results.data[2]).toBe('5 Mbps'))
    expect(result.current.pending.data).toContain(1)
    expect(result.current.results.data[1]).toBe('testing…')

    // …and node 1's result still lands when it eventually returns.
    await act(async () => {
      a.resolve({ node_id: 1, node_name: 'a', download_mbps: 9 })
      await a.promise
    })
    await waitFor(() => expect(result.current.results.data[1]).toBe('9 Mbps'))
    expect(result.current.pending.data).toEqual([])
  })

  it('surfaces a backend error string instead of a bare failure', async () => {
    vi.mocked(nodesApi.speedtest).mockResolvedValue({
      node_id: 4, node_name: 'n', error: 'xray: SOCKS port never opened',
    })
    const { result } = renderHook(() => ({
      run: useSpeedtest(), results: useSpeedResults(),
    }), { wrapper })

    act(() => { result.current.run.mutate(4) })
    await waitFor(() =>
      expect(result.current.results.data[4]).toBe('xray: SOCKS port never opened'),
    )
  })

  it('clears the pending flag when the request itself fails', async () => {
    vi.mocked(nodesApi.speedtest).mockRejectedValue(new Error('network'))
    const { result } = renderHook(() => ({
      run: useSpeedtest(),
      results: useSpeedResults(),
      pending: useSpeedPending(),
    }), { wrapper })

    act(() => { result.current.run.mutate(5) })
    await waitFor(() => expect(result.current.results.data[5]).toBe('error'))
    expect(result.current.pending.data).not.toContain(5)
  })

  it('speedtest-all merges into the same cache without dropping earlier rows', async () => {
    vi.mocked(nodesApi.speedtest).mockResolvedValue({
      node_id: 1, node_name: 'a', download_mbps: 1,
    })
    vi.mocked(nodesApi.speedtestAll).mockResolvedValue([
      { node_id: 2, node_name: 'b', download_mbps: 2 },
      { node_id: 3, node_name: 'c', error: 'timeout' },
    ])

    const { result } = renderHook(() => ({
      one: useSpeedtest(), all: useSpeedtestAll(), results: useSpeedResults(),
    }), { wrapper })

    act(() => { result.current.one.mutate(1) })
    await waitFor(() => expect(result.current.results.data[1]).toBe('1 Mbps'))

    act(() => { result.current.all.mutate() })
    await waitFor(() => expect(result.current.results.data[2]).toBe('2 Mbps'))
    expect(result.current.results.data[3]).toBe('timeout')
    expect(result.current.results.data[1]).toBe('1 Mbps')
  })
})
