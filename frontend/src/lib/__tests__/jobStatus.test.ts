/**
 * A deploy whose script exits non-zero still finalizes the JOB as
 * `succeeded` — the runner returns `{status: 'failed'}` as its result
 * instead of raising. The tasks page projected that; the deploy and
 * uninstall modals didn't, so a broken install showed a green
 * "Install succeeded". The projection now lives in one place.
 */
import { describe, it, expect } from 'vitest'

import { effectiveJobStatus } from '@/lib/jobStatus'

const base = { id: 'j1', kind: 'deploy', status: 'succeeded' } as never

describe('effectiveJobStatus', () => {
  it('reports a failed script as failed despite a succeeded job row', () => {
    expect(effectiveJobStatus({
      ...(base as object), status: 'succeeded',
      result: { status: 'failed', error: 'certbot failed' },
    } as never)).toBe('failed')
  })

  it('treats deployed_no_uri as failed too', () => {
    expect(effectiveJobStatus({
      ...(base as object), status: 'succeeded',
      result: { status: 'deployed_no_uri' },
    } as never)).toBe('failed')
  })

  it('leaves a genuinely successful deploy alone', () => {
    expect(effectiveJobStatus({
      ...(base as object), status: 'succeeded',
      result: { status: 'deployed', node_id: 4 },
    } as never)).toBe('succeeded')
  })

  it('passes through when there is no result yet', () => {
    expect(effectiveJobStatus({ ...(base as object), status: 'running' } as never))
      .toBe('running')
    expect(effectiveJobStatus({ ...(base as object), status: 'cancelled' } as never))
      .toBe('cancelled')
  })

  it('ignores a non-object result', () => {
    expect(effectiveJobStatus({
      ...(base as object), status: 'succeeded', result: 'ok',
    } as never)).toBe('succeeded')
  })
})
