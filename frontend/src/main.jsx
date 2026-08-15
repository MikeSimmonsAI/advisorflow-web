import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'
import { initTheme } from './theme.js'

// Detect hostname and apply the correct brand theme BEFORE React renders.
// This sets data-theme on <html> so CSS variable overrides kick in immediately
// with no flash of wrong-brand styling.
initTheme()

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
