/**
 * `apiErrorText` has to understand every `detail` shape FastAPI actually
 * sends us. The dict shape was the gap: `HTTPException(detail={...})` is
 * what the subscription-refresh 409 and the busy-xray-lock 503 raise, and
 * both were rendered as the generic fallback — dropping the `hint`, which
 * is the only part telling the operator what to do.
 */
import { describe, it, expect } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'

import { apiErrorText } from '@/lib/apiError'

function axiosErr(status: number, data: unknown): AxiosError {
  const err = new AxiosError('Request failed with status code ' + status)
  err.response = {
    status,
    statusText: '',
    data,
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  }
  return err
}

describe('apiErrorText', () => {
  it('uses a plain string detail verbatim', () => {
    expect(apiErrorText(axiosErr(400, { detail: 'Node not found' }), 'fb'))
      .toBe('Node not found')
  })

  it('joins error + hint from a structured 409 (refresh mutex)', () => {
    const err = axiosErr(409, {
      detail: {
        error: 'subscription refresh already in progress',
        subscription_id: 3,
        hint: 'Wait for the previous refresh to finish before retrying.',
      },
    })
    expect(apiErrorText(err, 'fb')).toBe(
      'subscription refresh already in progress — ' +
      'Wait for the previous refresh to finish before retrying.',
    )
  })

  it('renders a 503 lock-busy hint even without an error key', () => {
    const err = axiosErr(503, { detail: { hint: 'xray is busy, retry shortly' } })
    expect(apiErrorText(err, 'fb')).toBe('xray is busy, retry shortly')
  })

  it('names the offending field for a pydantic 422', () => {
    const err = axiosErr(422, {
      detail: [{ loc: ['body', 'port'], msg: 'Input should be a valid integer' }],
    })
    expect(apiErrorText(err, 'fb')).toBe('port: Input should be a valid integer')
  })

  it('falls back to the Error message for a non-axios failure', () => {
    expect(apiErrorText(new Error('Network Error'), 'fb')).toBe('Network Error')
  })

  it('falls back to axios own message when the body carries no detail', () => {
    expect(apiErrorText(axiosErr(500, {}), 'Something broke'))
      .toBe('Request failed with status code 500')
  })

  it('uses the caller fallback when there is nothing usable at all', () => {
    expect(apiErrorText({ weird: true }, 'Something broke')).toBe('Something broke')
  })
})
