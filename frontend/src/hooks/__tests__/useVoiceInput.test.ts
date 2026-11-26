import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

import { useVoiceInput } from '@/hooks/useVoiceInput'
import { assistantApi } from '@/api/client'

vi.mock('@/api/client', () => ({
  assistantApi: { transcribe: vi.fn() },
}))

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

class FakeMediaRecorder {
  state: 'inactive' | 'recording' = 'inactive'
  mimeType = 'audio/webm'
  ondataavailable: ((e: BlobEvent) => void) | null = null
  onstop: (() => void) | null = null

  start = vi.fn(() => {
    this.state = 'recording'
  })

  stop = vi.fn(() => {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.onstop?.()
  })
}

function fakeStream() {
  const tracks = [{ stop: vi.fn() }]
  return {
    stream: { getTracks: () => tracks, getAudioTracks: () => tracks } as unknown as MediaStream,
    tracks,
  }
}

describe('useVoiceInput', () => {
  let getUserMediaDeferreds: Array<ReturnType<typeof deferred<MediaStream>>>
  let recorders: FakeMediaRecorder[]

  beforeEach(() => {
    getUserMediaDeferreds = []
    recorders = []

    const MediaRecorderCtor = vi.fn(function (this: FakeMediaRecorder, _stream: MediaStream) {
      const recorder = new FakeMediaRecorder()
      recorders.push(recorder)
      return recorder
    })
    vi.stubGlobal('MediaRecorder', MediaRecorderCtor as unknown as typeof MediaRecorder)

    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia: vi.fn(() => {
          const d = deferred<MediaStream>()
          getUserMediaDeferreds.push(d)
          return d.promise
        }),
      },
      writable: true,
      configurable: true,
    })
    // vi.mock factory mocks keep call history across tests despite restoreMocks.
    vi.mocked(assistantApi.transcribe).mockClear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('records, stops and transcribes in the normal flow', async () => {
    const onText = vi.fn()
    const { result } = renderHook(() => useVoiceInput('server', 'en-US', onText))
    const { stream, tracks } = fakeStream()

    act(() => {
      result.current.start()
    })
    await act(async () => {
      getUserMediaDeferreds[0].resolve(stream)
    })
    expect(result.current.state).toBe('listening')
    expect(recorders).toHaveLength(1)

    vi.mocked(assistantApi.transcribe).mockResolvedValue('hello world')
    const chunk = new Blob(['x'], { type: 'audio/webm' })
    act(() => {
      recorders[0].ondataavailable?.({ data: chunk } as unknown as BlobEvent)
    })
    await act(async () => {
      recorders[0].stop()
    })

    await waitFor(() => expect(result.current.state).toBe('idle'))
    expect(assistantApi.transcribe).toHaveBeenCalledTimes(1)
    expect(onText).toHaveBeenCalledWith('hello world', true)
    expect(tracks[0].stop).toHaveBeenCalled()
  })

  it('stops a pending start before getUserMedia resolves and releases the stream', async () => {
    const { result } = renderHook(() => useVoiceInput('provider', 'en-US', vi.fn()))

    act(() => {
      result.current.start()
    })
    expect(result.current.state).toBe('listening')
    expect(getUserMediaDeferreds).toHaveLength(1)

    act(() => {
      result.current.stop()
    })
    expect(result.current.state).toBe('idle')

    const { stream, tracks } = fakeStream()
    await act(async () => {
      getUserMediaDeferreds[0].resolve(stream)
    })
    expect(recorders).toHaveLength(0)
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(assistantApi.transcribe).not.toHaveBeenCalled()
  })

  it('releases the stream when unmounted while waiting for getUserMedia', async () => {
    const { result, unmount } = renderHook(() => useVoiceInput('server', 'ru-RU', vi.fn()))

    act(() => {
      result.current.start()
    })
    expect(getUserMediaDeferreds).toHaveLength(1)

    const { stream, tracks } = fakeStream()
    unmount()
    await act(async () => {
      getUserMediaDeferreds[0].resolve(stream)
    })
    expect(recorders).toHaveLength(0)
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(assistantApi.transcribe).not.toHaveBeenCalled()
  })

  it('rapid stop-start does not let the cancelled request create a recorder', async () => {
    const { result } = renderHook(() => useVoiceInput('provider', 'en-US', vi.fn()))

    act(() => {
      result.current.start()
    })
    expect(getUserMediaDeferreds).toHaveLength(1)

    act(() => {
      result.current.stop()
    })
    act(() => {
      result.current.start()
    })
    expect(getUserMediaDeferreds).toHaveLength(2)

    const first = fakeStream()
    await act(async () => {
      getUserMediaDeferreds[0].resolve(first.stream)
    })
    expect(recorders).toHaveLength(0)
    expect(first.tracks[0].stop).toHaveBeenCalled()

    const second = fakeStream()
    await act(async () => {
      getUserMediaDeferreds[1].resolve(second.stream)
    })
    expect(recorders).toHaveLength(1)
    expect(recorders[0].start).toHaveBeenCalledTimes(1)

    vi.mocked(assistantApi.transcribe).mockResolvedValue('hello')
    const chunk = new Blob(['x'], { type: 'audio/webm' })
    act(() => {
      recorders[0].ondataavailable?.({ data: chunk } as unknown as BlobEvent)
    })
    await act(async () => {
      recorders[0].stop()
    })
    await waitFor(() => expect(assistantApi.transcribe).toHaveBeenCalledTimes(1))
  })
})
