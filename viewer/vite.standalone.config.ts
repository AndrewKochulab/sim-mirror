// SPDX-License-Identifier: Apache-2.0
// The standalone pages the daemon serves -- /viewer/<scope> and /embed/<scope> -- built into the Python package and
// committed, so an install from a git tag needs no Node. Names carry no hash, so a rebuild that changed nothing diffs
// clean in CI; the pages are never cached (`server/pages.py`).
import { resolve } from 'node:path'
import { defineConfig } from 'vite'

const here = import.meta.dirname

export default defineConfig({
  root: resolve(here, 'standalone'),
  base: '/viewer-assets/',
  build: {
    outDir: resolve(here, '../src/sim_mirror/server/static/viewer'),
    emptyOutDir: true,
    assetsDir: '',
    rollupOptions: {
      input: { index: resolve(here, 'standalone/index.html'), embed: resolve(here, 'standalone/embed.html') },
      output: { entryFileNames: '[name].js', chunkFileNames: '[name].js', assetFileNames: '[name][extname]' },
    },
  },
})
