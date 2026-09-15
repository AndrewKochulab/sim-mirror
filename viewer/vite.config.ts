// SPDX-License-Identifier: Apache-2.0
// The library build -- `dist/index.js` for `npm i @andrewkochulab/sim-mirror` -- and the tests.
import { defineConfig } from 'vitest/config'

export default defineConfig({
  build: {
    lib: { entry: 'src/index.ts', formats: ['es'], fileName: () => 'index.js' },
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts', 'standalone/**/*.test.ts'],
    setupFiles: ['./test-support/setup.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text-summary', 'text', 'json-summary'],
      reportsDirectory: './coverage',
      include: ['src/**/*.ts', 'standalone/**/*.ts'],
      // `standalone/start.ts` is the page's one-line entry: it calls `start()`, which the tests cover.
      exclude: ['**/*.test.ts', '**/*.d.ts', 'standalone/start.ts'],
      // Every file on its own, never an average: a new module arriving untested fails here.
      thresholds: { lines: 99, statements: 99, functions: 99, branches: 98, perFile: true },
    },
  },
})
