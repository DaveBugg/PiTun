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

  // Sidebar collapse
  sidebarCollapsed: boolean
  toggleSidebar: () => void

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
