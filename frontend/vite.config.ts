import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import pkg from './package.json' assert { type: 'json' }

export default defineConfig({
  plugins: [react()],
  // Expose the frontend package.json version as `__APP_VERSION__` at
  // compile time so the sidebar can display it alongside the xray and
  // backend versions. Bump via the standard `npm version` flow or by
  // editing package.json manually (we do manual bumps in this repo).
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_API_BASE_URL?.replace('/api', '') || 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // 700 KB cap — the index chunk after vendor split should sit
    // comfortably under this. Anything bigger means we accidentally
    // pulled a heavyweight dep into the app code.
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        // Function form (vs. the object form) actually matches by
        // resolved module id, so e.g. `react` from a transitive
        // dependency lands in the `react` chunk too. The object form
        // we used before only matched the literal entry points and
        // left the actual react runtime in the main bundle, which is
        // why `react.js` was only 30 bytes and `index.js` was 1.25MB.
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          // Order matters — react-dom contains 'react' substring,
          // so check the more-specific name first.
          if (id.includes('node_modules/react-dom/') || id.includes('node_modules/react/') ||
              id.includes('node_modules/scheduler/')) {
            return 'react'
          }
          if (id.includes('node_modules/react-router')) return 'router'
          if (id.includes('node_modules/@tanstack/')) return 'query'
          if (id.includes('node_modules/qrcode.react/') ||
              id.includes('node_modules/qr.js/')) {
            return 'qrcode'
          }
          if (id.includes('node_modules/lucide-react/')) return 'icons'
          if (id.includes('node_modules/axios/')) return 'axios'
          // Everything else — Tailwind runtime helpers, clsx, polyfills —
          // stays in a single `vendor` chunk so we don't end up with
          // dozens of tiny files.
          return 'vendor'
        },
      },
    },
  },
})
