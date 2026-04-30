import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config'

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      globals: true,
      environment: 'jsdom',
      setupFiles: ['./src/test-setup.ts'],
      css: false,
      // `include` matches __tests__ dirs co-located with the code as well
      // as *.test.ts anywhere under src/.
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      restoreMocks: true,
    },
    // `__APP_VERSION__` is injected by vite.config at build time but
    // vitest uses this file instead — wire the same define so components
    // that read it render identically in tests.
    define: {
      __APP_VERSION__: JSON.stringify('test'),
    },
  }),
)
