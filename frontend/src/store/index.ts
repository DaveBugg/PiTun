import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { SystemStatus, Node } from '@/types'

interface AppState {
  // System
  status: SystemStatus | null
  setStatus: (s: SystemStatus) => void

  // Active node (cached locally for instant display)
  activeNode: Node | null
  setActiveNode: (n: Node | null) => void

  // Sidebar collapse — desktop-only narrow/wide toggle (icon-only vs
  // icon+label). Persisted across sessions.
  sidebarCollapsed: boolean
  toggleSidebar: () => void

  // Mobile menu — separate from `sidebarCollapsed` because the two
  // semantics differ: on desktop the sidebar is always present (just
  // narrow or wide); on mobile (since v1.3.0-beta.6) the sidebar is
  // hidden entirely by default and slides in as an overlay drawer
  // when this flag is true. NOT persisted — opens fresh on every
  // page load (the floating home button is the entry point).
  mobileMenuOpen: boolean
  setMobileMenuOpen: (v: boolean) => void

  // Theme (future)
  theme: 'dark' | 'light'
  setTheme: (t: 'dark' | 'light') => void

  // Language
  lang: 'en' | 'ru'
  setLang: (l: 'en' | 'ru') => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      status: null,
      setStatus: (status) => set({ status }),

      activeNode: null,
      setActiveNode: (activeNode) => set({ activeNode }),

      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

      mobileMenuOpen: false,
      setMobileMenuOpen: (mobileMenuOpen) => set({ mobileMenuOpen }),

      theme: 'dark',
      setTheme: (theme) => set({ theme }),

      lang: 'en',
      setLang: (lang) => set({ lang }),
    }),
    {
      name: 'pitun-app-store',
      partialize: (s) => ({ sidebarCollapsed: s.sidebarCollapsed, theme: s.theme, lang: s.lang }),
    }
  )
)
