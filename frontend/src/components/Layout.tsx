import { useState, useEffect, Fragment, FormEvent } from 'react'
import { Link, NavLink, Outlet } from 'react-router-dom'
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
  Scale,
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
  Cloud,
  Menu,
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
  { to: '/routing',      icon: GitBranch,       label: 'Routing'       },
  { to: '/nodes',        icon: Server,          label: 'Nodes'         },
  { to: '/circles',      icon: Circle,          label: 'NodeCircle'    },
  { to: '/balancers',    icon: Scale,           label: 'Balancers'     },
  { to: '/subscriptions',icon: Rss,             label: 'Subscriptions' },
  { to: '/servers',      icon: Cloud,           label: 'Servers'       },
  { to: '/xui',          icon: Layers,          label: 'X-ui'          },
  { to: '/chains',       icon: GitBranch,       label: 'Chains'        },
  { to: '/dns',          icon: Network,         label: 'DNS'           },
  { to: '/geodata',      icon: Globe,           label: 'GeoData'       },
  { to: '/devices',      icon: Monitor,         label: 'Devices'       },
  { to: '/logs',         icon: ScrollText,      label: 'Logs'          },
  { to: '/diagnostics',  icon: Activity,        label: 'Diagnostics'   },
  { to: '/settings',     icon: Settings2,       label: 'Settings'      },
  { to: '/kb',           icon: BookOpen,        label: 'Knowledge Base'},
]

// Routes after which a thin separator line groups the nav visually.
const DIVIDE_AFTER = new Set(['/routing', '/subscriptions', '/chains', '/devices', '/settings'])

export function Layout() {
  const {
    sidebarCollapsed, toggleSidebar,
    mobileMenuOpen, setMobileMenuOpen,
    lang, setLang, theme, setTheme,
  } = useAppStore()
  const { data: status } = useSystemStatus()
  const t = (en: string, ru: string) => (lang === 'ru' ? ru : en)

  // Always start with the mobile drawer closed on each fresh load —
  // not persisted in store. Defensive reset in case a prior version
  // of the store had it lingering. Cheap, runs once.
  useEffect(() => {
    setMobileMenuOpen(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Esc closes the mobile menu drawer too — same affordance as the
  // tap-the-backdrop pattern. Hooked here so it doesn't fight with
  // the password modal's own Esc handler (that one short-circuits
  // when its modal is hidden).
  useEscapeKey(() => setMobileMenuOpen(false), mobileMenuOpen)

  // Track viewport size so the sidebar's bottom-section render
  // branches (language toggle / status+version / user section) can
  // pick "expanded" layout on mobile regardless of the desktop-only
  // `sidebarCollapsed` flag. The drawer on mobile is always w-72,
  // so it shouldn't display the icon-only collapsed variants.
  const [isMobileViewport, setIsMobileViewport] = useState<boolean>(() =>
    typeof window !== 'undefined' &&
    window.matchMedia('(max-width: 767px)').matches
  )
  useEffect(() => {
    if (typeof window === 'undefined') return
    const mql = window.matchMedia('(max-width: 767px)')
    const handler = (e: MediaQueryListEvent) => setIsMobileViewport(e.matches)
    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])
  const effectiveCollapsed = isMobileViewport ? false : sidebarCollapsed

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
    //
    // `overflow-x-hidden` clips the off-canvas mobile drawer's
    // pre-translation footprint — without this, the sidebar at
    // `-translate-x-full` still contributes to the page's scrollable
    // width on some browsers, manifesting as a parasitic horizontal
    // scroll on every page (no real overflowing content). Desktop is
    // unaffected because the sidebar there is `static` and lives
    // inside the flex row normally.
    <div className="flex h-full text-gray-100 overflow-x-hidden">
      {/* Mobile-only backdrop. Visible when the off-canvas drawer is
          open, tapping it slides the drawer back out. Desktop sidebar
          is `static` and doesn't need a backdrop. */}
      {mobileMenuOpen && (
        <div
          onClick={() => setMobileMenuOpen(false)}
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-xs md:hidden"
          aria-hidden="true"
        />
      )}

      {/* Sidebar — solid saturated gradient (dark navy → near-black in
          dark theme; soft-slate in light).

          Responsive layout (since v1.3.0-beta.6):
          - **Mobile (< md):** completely hidden by default; slides in as
            a full-height off-canvas drawer when `mobileMenuOpen=true`
            (entry point: floating home button bottom-right). Always
            full-width-ish (`w-72`) when open — no narrow icon-only
            mode on mobile, since the user explicitly tapped the
            menu and wants labels.
          - **Desktop (md+):** static flex item; `sidebarCollapsed`
            toggles between icon-only (`w-16`) and labeled (`w-56`).
            The mobile drawer flag has no effect at this breakpoint. */}
      <aside
        className={clsx(
          // Base — flex column, themed panel surface + right border.
          'flex flex-col border-r border-gray-800/70 transition-all duration-200',
          // Mobile (< md): fixed overlay, animated slide-in/out via
          // translate-x. Width fixed at w-72 for legibility.
          'fixed inset-y-0 left-0 z-40 w-72',
          mobileMenuOpen ? 'translate-x-0' : '-translate-x-full',
          // Desktop (md+): static, narrow/wide based on store flag.
          'md:static md:z-auto md:translate-x-0',
          sidebarCollapsed ? 'md:w-16' : 'md:w-56',
        )}
      >
        {/* Logo */}
        <div className="flex items-center gap-2 px-4 py-4 border-b border-gray-800">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 shrink-0">
            <Shield className="h-5 w-5 text-white" />
          </div>
          {/* `text-gray-100` instead of `text-white` so the logo flips
              to dark on the light theme (white would stay invisible on
              the soft-gray sidebar in light mode). */}
          {/* Logo label — hidden only when desktop sidebar is in
              icon-only collapsed state. On mobile the drawer is
              always expanded width, so the label always shows. */}
          <span
            className={clsx(
              'text-lg font-bold text-gray-100 tracking-tight',
              sidebarCollapsed && 'md:hidden',
            )}
          >
            PiTun
          </span>
        </div>

        {/* Nav */}
        <nav className="flex-1 min-h-0 overflow-y-auto px-2 py-3 space-y-0.5">
          {NAV.map(({ to, icon: Icon, label }) => (
            <Fragment key={to}>
            <NavLink
              to={to}
              end={to === '/'}
              onClick={() => {
                // On mobile, close the off-canvas drawer after the
                // user picks a destination — otherwise the drawer
                // stays overlaying the page they just navigated to.
                // Desktop sidebar is always present, no auto-collapse.
                if (
                  typeof window !== 'undefined' &&
                  window.matchMedia('(max-width: 767px)').matches
                ) {
                  setMobileMenuOpen(false)
                }
              }}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 rounded-lg px-2 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-600/20 dark:text-brand-300'
                    : 'text-gray-400 hover:bg-gray-800 hover:text-gray-100',
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {/* Same rule as the logo: label disappears only when
                  desktop sidebar is collapsed; mobile drawer always
                  shows labels. */}
              <span className={clsx(sidebarCollapsed && 'md:hidden')}>{label}</span>
            </NavLink>
            {DIVIDE_AFTER.has(to) && (
              <div className="my-1.5 mx-2 border-t border-gray-800/60" aria-hidden="true" />
            )}
            </Fragment>
          ))}
        </nav>

        {/* Language toggle */}
        <div className={clsx(
          'border-t border-gray-800 px-2 py-1.5',
          effectiveCollapsed ? 'flex flex-col items-center gap-1' : 'flex items-center gap-1',
        )}>
          {/* Theme toggle — moon/sun icon button. Toggles
              `<html data-theme="…">` via the store, which flips every
              CSS var in index.css to its light counterpart. */}
          <button
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
            title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
            className="rounded-sm p-1 text-gray-600 hover:text-gray-400 hover:bg-gray-800/60 transition-colors"
          >
            {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </button>

          {/* Language selector */}
          {!effectiveCollapsed && (
            <div className="flex items-center gap-1 ml-auto">
              {(['en', 'ru'] as const).map((l) => (
                <button
                  key={l}
                  onClick={() => setLang(l)}
                  className={clsx(
                    'rounded-sm px-2 py-0.5 text-xs font-medium uppercase transition-colors',
                    lang === l
                      ? 'bg-brand-50 text-brand-700 dark:bg-brand-600/30 dark:text-brand-300'
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
        {!effectiveCollapsed && status && (
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
          effectiveCollapsed ? 'flex flex-col items-center gap-1' : 'flex items-center gap-2',
        )}>
          {effectiveCollapsed ? (
            <>
              <button
                onClick={openChangePw}
                className="flex items-center justify-center rounded-lg p-2 text-gray-400 hover:bg-gray-800 hover:text-gray-100 transition-colors"
                title={t('Change Password', 'Смена пароля')}
              >
                <Key className="h-4 w-4" />
              </button>
              <button
                onClick={handleLogout}
                className="flex items-center justify-center rounded-lg p-2 text-gray-400 hover:bg-gray-800 hover:text-red-400 transition-colors"
                title={t('Logout', 'Выход')}
              >
                <LogOut className="h-4 w-4" />
              </button>
            </>
          ) : (
            <>
              <div className="flex items-center gap-2 flex-1 min-w-0 pl-1">
                <User className="h-4 w-4 text-gray-500 shrink-0" />
                <span className="text-xs text-gray-400 truncate">{getUsername()}</span>
              </div>
              <button
                onClick={openChangePw}
                className="flex items-center justify-center rounded-lg p-1.5 text-gray-500 hover:bg-gray-800 hover:text-gray-300 transition-colors"
                title={t('Change Password', 'Смена пароля')}
              >
                <Key className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={handleLogout}
                className="flex items-center justify-center rounded-lg p-1.5 text-gray-500 hover:bg-gray-800 hover:text-red-400 transition-colors"
                title={t('Logout', 'Выход')}
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>

        {/* Collapse toggle — desktop-only (mobile drawer doesn't have
            a narrow icon-mode, the user explicitly tapped the FAB to
            see the full menu). Replaced on mobile by an X button at
            the top-right of the drawer header for explicit close. */}
        <button
          onClick={toggleSidebar}
          className="hidden md:flex items-center justify-center py-3 border-t border-gray-800 text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {sidebarCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>

        {/* Mobile-only Close button at the top of the drawer for
            users who don't realise tapping the backdrop / Esc closes
            it. Same affordance, more discoverable on a phone where
            the backdrop area is small. */}
        <button
          onClick={() => setMobileMenuOpen(false)}
          aria-label="Close menu"
          className="md:hidden absolute top-3 right-3 rounded-full p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </aside>

      {/* Main content.
          On mobile (since v1.3.0-beta.6) the sidebar is fully off-
          canvas, so main content uses the entire viewport width — no
          `pl-16` reservation. Bottom padding leaves room for the
          floating home button (4rem ≈ 64px) on mobile so the
          page-level scroll doesn't hide its last row under the
          button. Desktop sidebar is `static` and inline; main flex-1
          takes the rest naturally.

          `min-w-0` is the classic flex-pitfall fix: a `flex-1` item's
          default `min-width: auto` lets the item grow to fit the
          intrinsic width of any wide child (long URLs, tables,
          unbreakable mono spans), so a single mono span past the
          viewport causes main itself to scroll horizontally even
          though the root has `overflow-x-hidden`. Setting
          `min-width: 0` lets main shrink to its parent's allotted
          width and child overflows are contained by the explicit
          `overflow-x-hidden` below. */}
      <main className="flex-1 min-w-0 overflow-x-hidden overflow-y-auto pb-20 md:pb-0 bg-gray-900 dark:bg-surface">
        {/* Validation-error banner — shown at the top of every page when
            the most recent xray config write failed validation. The hint
            string is composed by `config_gen._explain_xray_stderr()` and
            stored in the `last_xray_validation_error` Settings key.
            Tapping "Routing" jumps to the rules table where v1.2.7's
            self-healing banner identifies the offending rule(s). */}
        {status?.last_xray_validation_error && (
          <div
            role="alert"
            className="mx-4 mt-4 mb-0 rounded-lg border border-red-200 dark:border-red-700/60 bg-red-50 dark:bg-red-950/40 px-4 py-3 text-sm text-red-800 dark:text-red-200"
          >
            <div className="flex items-start gap-3">
              <span className="text-lg leading-none mt-0.5">⚠</span>
              <div className="flex-1 min-w-0">
                <div className="font-semibold text-red-100">
                  {t('Xray configuration validation failed', 'Валидация конфигурации Xray не прошла')}
                </div>
                <div className="mt-1 leading-snug wrap-break-word">
                  {status.last_xray_validation_error}
                </div>
                <div className="mt-2 flex gap-3 text-xs">
                  <Link to="/routing" className="text-red-700 dark:text-red-300 hover:text-red-100 underline">
                    {t('Open Routing →', 'Открыть Routing →')}
                  </Link>
                </div>
              </div>
            </div>
          </div>
        )}
        <Outlet />
      </main>

      {/* Floating menu button — mobile-only entry point for the
          off-canvas drawer above. Bottom-right, semi-transparent so
          it doesn't fight content underneath, larger touch target
          (min 48px) for accessibility. Hidden on desktop where the
          sidebar is always visible inline. Hidden also when the
          drawer is open (the drawer's own X button + tapping
          backdrop are the close affordances — keeping the FAB
          visible would be redundant + would overlap the drawer's
          right edge on small screens).

          We chose a Menu (hamburger) icon over Home because the
          button's job is "open navigation," not "go home." */}
      <button
        type="button"
        onClick={() => setMobileMenuOpen(true)}
        aria-label={t('Open menu', 'Открыть меню')}
        className={clsx(
          'fixed bottom-4 right-4 z-30 md:hidden',
          'flex items-center justify-center',
          'h-12 w-12 rounded-full',
          'bg-brand-50 dark:bg-brand-600/85 text-white shadow-lg backdrop-blur-sm',
          'hover:bg-brand-500 active:scale-95 transition-all',
          'border border-brand-400/40',
          mobileMenuOpen && 'opacity-0 pointer-events-none',
        )}
      >
        <Menu className="h-5 w-5" />
      </button>

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
              <div className="rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-sm text-red-700 dark:text-red-300">
                {pwError}
              </div>
            )}
            {pwSuccess && (
              <div className="rounded-lg bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-700/50 px-3 py-2 text-sm text-green-700 dark:text-green-300">
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
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
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
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
                  required
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">Confirm New Password</label>
                <input
                  type="password"
                  value={confirmPw}
                  onChange={(e) => setConfirmPw(e.target.value)}
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
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
