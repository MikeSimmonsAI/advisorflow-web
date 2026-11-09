import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// build-info.json: which commit this build came from, so "is my change live?"
// is answered by what is actually being served (the VOLT development relay,
// #571, checks it before calling a change deployed). Render provides
// RENDER_GIT_COMMIT at build time; elsewhere it says "unknown". Nothing secret.
function buildInfo() {
  return {
    name: 'build-info',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'build-info.json',
        source: JSON.stringify({
          commit: process.env.RENDER_GIT_COMMIT || process.env.GIT_COMMIT || 'unknown',
          built_at: new Date().toISOString(),
        }),
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), buildInfo()],
  server: {
    port: 5173,
  },
})
