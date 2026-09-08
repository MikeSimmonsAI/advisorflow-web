import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
// AFTER index.css, deliberately. Appearance redefines the neutral tokens that
// index.css declares, so it has to come second or the defaults would win.
import './styles/appearance.css'
import { initTheme, hydrateBrand } from './theme.js'
import { initAppearance } from './appearance.js'

// Detect hostname and apply the correct brand theme BEFORE React renders.
// This sets data-theme on <html> so CSS variable overrides kick in immediately
// with no flash of wrong-brand styling.
// Build: 2026-08-20 — force cache-bust for bookaboost cream/gold theme
initTheme()
// The PERSON's light/dark choice, applied in the same pre-render pass as the
// BRAND's colours so there is no flash of the wrong appearance either. Two
// attributes on <html>, composed: data-theme is the brand, data-appearance is
// the person. Also subscribes to the OS preference while the choice is System.
initAppearance()
// Refresh the brand from the platform row. Async on purpose: the cached
// copy already themed this paint, so this only matters for the next one.
hydrateBrand(import.meta.env.VITE_API_BASE_URL || 'https://advisorflow-backend.onrender.com')

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
