import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
// AFTER index.css, deliberately. appearance.css is the light design-token layer
// (documented at its top); it redefines the tokens index.css declares.
import './styles/appearance.css'
// THE GOD CONTROL PLANE'S PALETTE, third and last of the three colour layers.
//
// index.css declares the tenant tokens, appearance.css redefines the neutral
// ones per light/dark, and this declares the `--gm-*` / `--go-*` set that God
// Mode paints from. It is imported here rather than from a God component so the
// order is explicit and cannot be changed by a page happening to import a sheet
// first. Every selector inside is anchored to .gm-shell / .gm-scope / .go-scope,
// so loading it app-wide paints nothing outside God Mode.
import './pages/god/godTokens.css'
import { initTheme, hydrateBrand } from './theme.js'
import { initAppearance } from './appearance.js'

// Detect hostname and apply the correct brand theme BEFORE React renders.
// This sets data-theme on <html> so CSS variable overrides kick in immediately
// with no flash of wrong-brand styling.
// Build: 2026-08-20 — force cache-bust for bookaboost cream/gold theme
initTheme()
// One light theme (no day/night toggle since 2026-09-28): writes
// data-appearance="light" + color-scheme and clears the stale af_appearance key.
initAppearance()
// Refresh the brand from the platform row. Async on purpose: the cached
// copy already themed this paint, so this only matters for the next one.
hydrateBrand(import.meta.env.VITE_API_BASE_URL || 'https://advisorflow-backend.onrender.com')

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
