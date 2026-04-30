/**
 * Login smoke: renders the form, accepts input, submits through the
 * mocked auth API, and stores the returned token.
 *
 * NB: the form's <label>s aren't yet associated via htmlFor/id — a real
 * a11y gap that's worth fixing separately. For now queries rely on role
 * for the username (textbox) and a type=password querySelector for the
 * password, which matches how the form renders.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('@/api/client', () => ({
  authApi: { login: vi.fn() },
}))

import { Login } from '@/pages/Login'
import { authApi } from '@/api/client'

const fields = (container: HTMLElement) => {
  const inputs = container.querySelectorAll('input')
  const username = inputs[0] as HTMLInputElement
  const password = container.querySelector('input[type="password"]') as HTMLInputElement
  return { username, password }
}

describe('<Login>', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { href: '', assign: vi.fn() },
    })
  })

  it('renders heading, two inputs, and a submit button', () => {
    const { container } = render(<Login />)
    expect(screen.getByText('PiTun')).toBeInTheDocument()
    const { username, password } = fields(container)
    expect(username).toBeInTheDocument()
    expect(password).toBeInTheDocument()
    expect(password.type).toBe('password')
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument()
  })

  it('calls authApi.login with typed credentials and stores token', async () => {
    vi.mocked(authApi.login).mockResolvedValueOnce({
      access_token: 'test-token',
      token_type: 'bearer',
    })

    const user = userEvent.setup()
    const { container } = render(<Login />)
    const { username, password } = fields(container)

    await user.type(username, 'admin')
    await user.type(password, 'secret')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(authApi.login).toHaveBeenCalledWith({
        username: 'admin',
        password: 'secret',
      })
    })
    expect(localStorage.getItem('pitun_token')).toBe('test-token')
  })

  it('shows an error when login rejects and does NOT store a token', async () => {
    vi.mocked(authApi.login).mockRejectedValueOnce(new Error('bad creds'))

    const user = userEvent.setup()
    const { container } = render(<Login />)
    const { username, password } = fields(container)

    await user.type(username, 'admin')
    await user.type(password, 'wrong')
    await user.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByText(/invalid username or password/i)).toBeInTheDocument()
    expect(localStorage.getItem('pitun_token')).toBeNull()
  })
})
