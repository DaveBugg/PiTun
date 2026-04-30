import { useState, useEffect, FormEvent } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import {
  LayoutDashboard,
  Server,
  GitBranch,
  Rss,
  Globe,
  ScrollText,
  ChevronLeft,
  ChevronRight,
  Shield,
  Network,
  Layers,
  Circle,
  BookOpen,
  User,
  LogOut,
  Key,
  X,
  Monitor,
  Activity,
  Settings2,
  Sun,
  Moon,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useAppStore } from '@/store'
import { useSystemStatus } from '@/hooks/useSystem'
import { useEscapeKey } from '@/hooks/useEscapeKey'
import { authApi } from '@/api/client'
import { VersionPopover } from '@/components/VersionPopover'

function getUsername(): string {
  try {
    const token = localStorage.getItem('pitun_token')
    if (!token) return 'admin'
    const payload = JSON.parse(atob(token.split('.')[1]))
    return payload.sub || 'admin'
  } catch {
    return 'admin'
  }
}

const NAV = [
  { to: '/',             icon: LayoutDashboard, label: 'Dashboard'     },
  { to: '/nodes',        icon: Server,          label: 'Nodes'         },
  { to: '/circles',      icon: Circle,          label: 'NodeCircle'    },
  { to: '/routing',      icon: GitBranch,       label: 'Routing'       },
  { to: '/devices',      icon: Monitor,         label: 'Devices'       },
  { to: '/balancers',    icon: Layers,          label: 'Balancers'     },
  { to: '/subscriptions',icon: Rss,             label: 'Subscriptions' },
  { to: '/dns',          icon: Network,         label: 'DNS'           },
  { to: '/geodata',      icon: Globe,           label: 'GeoData'       },
  { to: '/logs',         icon: ScrollText,      label: 'Logs'          },
  { to: '/settings',     icon: Settings2,       label: 'Settings'      },
  { to: '/diagnostics',  icon: Activity,        label: 'Diagnostics'   },
  { to: '/kb',           icon: BookOpen,        label: 'Knowledge Base'},
]

export function Layout() {
  const { sidebarCollapsed, toggleSidebar, lang, setLang, theme, setTheme } = useAppStore()
  const { data: status } = useSystemStatus()

  // Keep `<html data-theme="…">` in sync with the store. main.tsx sets
  // the initial value before first paint; this effect handles live
  // toggles afterwards. Writing to a single root attribute gives every
  // CSS-var-driven style (grays, sidebar gradient, ambient) a single
  // pivot point.
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  const [showChangePw, setShowChangePw] = useState(false)
  const [currentPw, setCurrentPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [pwError, setPwError] = useState('')
  const [pwSuccess, setPwSuccess] = useState('')
  const [pwLoading, setPwLoading] = useState(false)

  // Esc closes the password modal. The hook short-circuits when the modal
  // is hidden so it doesn't interfere with other Esc-using UI.
  useEscapeKey(() => setShowChangePw(false), showChangePw)

  const handleLogout = () => {
    localStorage.removeItem('pitun_token')
    window.location.href = '/login'
  }

  const handleChangePassword = async (e: FormEvent) => {
    e.preventDefault()
    setPwError('')
    setPwSuccess('')

    if (newPw !== confirmPw) {
      setPwError('Passwords do not match')
      return
    }
    if (newPw.length < 8) {
      setPwError('New password must be at least 8 characters')
      return
    }

    setPwLoading(true)
    try {
      await authApi.changePassword({ current_password: currentPw, new_password: newPw })
      setPwSuccess('Password changed successfully')
      setCurrentPw('')
      setNewPw('')
      setConfirmPw('')
      setTimeout(() => {
        setShowChangePw(false)
        setPwSuccess('')
      }, 1500)
    } catch {
      setPwError('Failed to change password. Check your current password.')
    } finally {
      setPwLoading(false)
    }
  }

  const openChangePw = () => {
    setCurrentPw('')
    setNewPw('')
    setConfirmPw('')
    setPwError('')
    setPwSuccess('')
    setShowChangePw(true)
  }

  return (
    // Transparent root lets the body ambient glows + grain show through.
    // Text color inherits here so all pages get gray-100 default without
    // each one respecifying it.
    <div className="flex h-full text-gray-100">
      {/* Backdrop shown when the sidebar is expanded on mobile. Tapping
          it collapses the sidebar (standard mobile pattern). Hidden on
          md+ since the sidebar is static there and doesn't overlay. */}
      {!sidebarCollapsed && (
        <div
          onClick={toggleSidebar}
          className="fixed inset-0 z-30 bg-black/50 md:hidden"
          aria-hidden="true"
        />
      )}

      {/* Sidebar — solid saturated gradient (dark navy → near-black in dark
          theme; soft-slate in light). Reads as a distinct "frame" around
          the translucent cards in main content.

          Responsive layout:
          - Mobile (< md): `fixed` overlay so expanding the sidebar
            covers the page instead of crushing the content column into
            an unreadable 150-pixel-wide strip.
          - Desktop (md+): `static` flex item — sidebar + main split the
            viewport as before. */}
      <aside
        style={{ backgroundImage: 'var(--sidebar-bg)' }}
        className={clsx(
          'fixed inset-y-0 left-0 z-40 md:static md:z-auto',
          'flex flex-col border-r border-gray-800/70 transition-all duration-200',
          sidebarCollapsed ? 'w-16' : 'w-56',
        )}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 px-4 py-4 border-b border-gray-800">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 flex-shrink-0">
            <Shield className="h-5 w-5 text-white" />
          </div>
          {/* `text-gray-100` instead of `text-white` so the logo flips
              to dark on the light theme (white would stay invisible on
              the soft-gray sidebar in light mode). */}
          {!sidebarCollapsed && (
            <span className="text-lg font-bold text-gray-100 tracking-tight">PiTun</span>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 px-2 py-3 space-y-0.5">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              onClick={() => {
                // Auto-collapse the sidebar after a nav tap on mobile
                // — otherwise the sidebar stays overlaying the page the
                // user just navigated to, and they have to tap the
                // backdrop to see anything.
                if (
                  !sidebarCollapsed &&
                  typeof window !== 'undefined' &&
                  window.matchMedia('(max-width: 767px)').matches
                ) {
                  toggleSidebar()
                }
              }}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 rounded-lg px-2 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-brand-600/20 text-brand-400'
                    : 'text-gray-400 hover:bg-gray-800 hover:text-gray-100',
                )
              }
            >
              <Icon className="h-4 w-4 flex-shrink-0" />
              {!sidebarCollapsed && <span>{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Language toggle */}
        <div className={clsx(
          'border-t border-gray-800 px-2 py-1.5',
          sidebarCollapsed ? 'flex flex-col items-center gap-1' : 'flex items-center gap-1',
        )}>
          {/* Theme toggle — moon/sun icon button. Toggles
              `<html data-theme="…">` via the store, which flips every
              CSS var in index.css to its light counterpart. */}
          <button
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            className="rounded p-1 text-gray-600 hover:text-gray-400 hover:bg-gray-800/60 transition-colors"
          >
            {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </button>

          {/* Language selector */}
          {!sidebarCollapsed && (
            <div className="flex items-center gap-1 ml-auto">
              {(['en', 'ru'] as const).map((l) => (
                <button
                  key={l}
                  onClick={() => setLang(l)}
                  className={clsx(
                    'rounded px-2 py-0.5 text-xs font-medium uppercase transition-colors',
                    lang === l
                      ? 'bg-brand-600/30 text-brand-300'
                      : 'text-gray-600 hover:text-gray-400',
                  )}
                >
                  {l}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Status indicator + single-line version trigger.
            The 3 version lines that used to be here (xray / backend /
            frontend) are now collapsed into a `PiTun X.Y.Z ⓘ` button
            that opens <VersionPopover /> with the full snapshot. Saves
            sidebar vertical space and scales to any number of versions
            (nginx, socket-proxy, kernel, alembic rev, geo mtimes…).
            `relative` on the wrapper anchors the popover's absolute
            positioning to this sidebar row. */}
        {!sidebarCollapsed && status && (
          <div className="relative px-4 py-3 border-t border-gray-800">
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <span
                className={clsx(
                  'h-2 w-2 rounded-full',
                  status.running ? 'bg-green-500 animate-pulse' : 'bg-gray-600',
                )}
              />
              {status.running ? `Running \u00b7 ${status.mode}` : 'Stopped'}
            </div>
            <div className="mt-1">
              <VersionPopover shortVersion={status.app_version || __APP_VERSION__} />
            </div>
          </div>
        )}

        {/* User section */}
        <div className={clsx(
          'border-t border-gray-800 px-2 py-2',
          sidebarCollapsed ? 'flex flex-col items-center gap-1' : 'flex items-center gap-2',
        )}>
          {sidebarCollapsed ? (
            <>
              <button
                onClick={openChangePw}
                className="flex items-center justify-center rounded-lg p-2 text-gray-400 hover:bg-gray-800 hover:text-gray-100 transition-colors"
                title="Change Password"
              >
                <Key className="h-4 w-4" />
              </button>
              <button
                onClick={handleLogout}
                className="flex items-center justify-center rounded-lg p-2 text-gray-400 hover:bg-gray-800 hover:text-red-400 transition-colors"
                title="Logout"
              >
                <LogOut className="h-4 w-4" />
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2 flex-1 min-w-0 pl-1">
                <User className="h-4 w-4 text-gray-500 flex-shrink-0" />
                <span className="text-xs text-gray-400 truncate">{getUsername()}</span>
              </div>
              <button
                onClick={openChangePw}
                className="flex items-center justify-center rounded-lg p-1.5 text-gray-500 hover:bg-gray-800 hover:text-gray-300 transition-colors"
                title="Change Password"
              >
                <Key className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={handleLogout}
                className="flex items-center justify-center rounded-lg p-1.5 text-gray-500 hover:bg-gray-800 hover:text-red-400 transition-colors"
                title="Logout"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>

        {/* Collapse toggle */}
        <button
          onClick={toggleSidebar}
          className="flex items-center justify-center py-3 border-t border-gray-800 text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
        >
          {sidebarCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </aside>

      {/* Main content.
          `pl-16` on mobile: reserves the 64px strip (same as `w-16`
          collapsed sidebar) so that content isn't hidden behind the
          fixed icon-only sidebar when the sidebar is collapsed. Expanded
          sidebar just floats on top; the backdrop above catches taps. */}
      <main className="flex-1 overflow-y-auto pl-16 md:pl-0">
        <Outlet />
      </main>

      {/* Change Password Modal */}
      {showChangePw && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="change-pw-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
        >
          <form
            onSubmit={handleChangePassword}
            className="w-full max-w-sm rounded-xl bg-gray-900 border border-gray-800 p-6 space-y-4 shadow-xl"
          >
            <div className="flex items-center justify-between">
              <h2 id="change-pw-title" className="text-lg font-semibold text-gray-100">Change Password</h2>
              <button
                type="button"
                onClick={() => setShowChangePw(false)}
                className="text-gray-500 hover:text-gray-300 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {pwError && (
              <div className="rounded-lg bg-red-900/30 border border-red-700/50 px-3 py-2 text-sm text-red-300">
                {pwError}
              </div>
            )}
            {pwSuccess && (
              <div className="rounded-lg bg-green-900/30 border border-green-700/50 px-3 py-2 text-sm text-green-300">
                {pwSuccess}
              </div>
            )}

            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">Current Password</label>
                <input
                  type="password"
                  value={currentPw}
                  onChange={(e) => setCurrentPw(e.target.value)}
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
                  required
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">New Password</label>
                <input
                  type="password"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
                  required
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">Confirm New Password</label>
                <input
                  type="password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
                  required
                />
              </div>
            </div>

            <div className="flex gap-2 pt-1">
              <button
                type="button"
                onClick={() => setShowChangePw(false)}
                className="flex-1 rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:bg-gray-800 transition-colors"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={pwLoading}
                className="flex-1 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
              >
                {pwLoading ? 'Saving\u2026' : 'Save'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
