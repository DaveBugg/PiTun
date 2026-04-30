import { useState, FormEvent } from 'react'
import { authApi } from '@/api/client'

export function Login() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const data = await authApi.login({ username, password })
      localStorage.setItem('pitun_token', data.access_token)
      window.location.href = '/'
    } catch {
      setError('Invalid username or password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 flex items-center justify-center p-4">
      <form onSubmit={handleSubmit} className="w-full max-w-sm space-y-5">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-gray-100">PiTun</h1>
          <p className="text-sm text-gray-500 mt-1">Sign in to manage your proxy</p>
        </div>

        {error && (
          <div className="rounded-lg bg-red-900/30 border border-red-700/50 px-4 py-2.5 text-sm text-red-300">
            {error}
          </div>
        )}

        <div className="space-y-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">Username</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              autoFocus
              required
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1.5">Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
              required
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          {loading ? 'Signing in\u2026' : 'Sign In'}
        </button>

        <p className="text-center text-xs text-gray-600">
          Default: admin / password
        </p>
      </form>
    </div>
  )
}
