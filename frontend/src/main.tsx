import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ConfirmProvider } from './components/ConfirmModal'
import { useAppStore } from './store'
import './index.css'

// Apply persisted theme before the first paint so the initial render
// uses the right colors (avoids a dark-to-light flash on reload for
// light-theme users). `useAppStore.persist.hasHydrated()` is true
// synchronously in dev; on the first real load hydration runs as part
// of `create`, so reading state here gives us what's stored.
const initialTheme = useAppStore.getState().theme
document.documentElement.setAttribute('data-theme', initialTheme)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 2,
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      refetchOnMount: false,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
        <ConfirmProvider>
          <App />
        </ConfirmProvider>
      </QueryClientProvider>
    </BrowserRouter>
  </StrictMode>,
)
