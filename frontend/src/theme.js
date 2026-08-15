/**
 * AdvisorFlow Platform Theme Detection
 * ---------------------------------------
 * Reads window.location.hostname at load time and returns the correct
 * brand theme. The backend serves the same React bundle to all domains.
 * This file is the single place that makes it look different per brand.
 *
 * Brand detection order:
 *   1. evosyspro  → app.evosyspro.live  (dark navy, electric blue, green)
 *   2. harmonyhustle → app.harmonyhustle.com (TBD — real estate)
 *   3. default    → app.bookaboost.live + localhost (BookaBoost existing theme)
 *
 * How it works:
 *   applyTheme() injects a data-theme attribute on <html>.
 *   CSS in index.css uses [data-theme="evosyspro"] overrides.
 *   No JavaScript theme state to manage — pure CSS variable overrides.
 */

export const THEMES = {
  BOOKABOOST: 'bookaboost',
  EVOSYSPRO: 'evosyspro',
  HARMONYHUSTLE: 'harmonyhustle',
}

/**
 * Detect which brand theme to use based on the current hostname.
 * Safe to call in SSR/test environments where window may not exist.
 */
export function detectTheme() {
  if (typeof window === 'undefined') return THEMES.BOOKABOOST

  const host = window.location.hostname.toLowerCase()

  if (host.includes('evosyspro')) return THEMES.EVOSYSPRO
  if (host.includes('harmonyhustle')) return THEMES.HARMONYHUSTLE

  return THEMES.BOOKABOOST
}

/**
 * Apply the detected theme to the document root.
 * Sets data-theme attribute + updates the browser tab title.
 */
export function applyTheme(theme) {
  if (typeof document === 'undefined') return

  document.documentElement.setAttribute('data-theme', theme)

  const titles = {
    [THEMES.EVOSYSPRO]:    'EvoSys Pro',
    [THEMES.HARMONYHUSTLE]: 'Harmony Hustle',
    [THEMES.BOOKABOOST]:   'BookaBoost',
  }
  document.title = titles[theme] || 'BookaBoost'
}

/**
 * One-shot: detect and apply on page load.
 * Called from main.jsx before React renders.
 */
export function initTheme() {
  const theme = detectTheme()
  applyTheme(theme)
  return theme
}

/**
 * Brand config per theme — logos, display names, support emails, colors.
 * Used in Layout.jsx to render the correct sidebar logo and brand name.
 */
export const BRAND_CONFIG = {
  [THEMES.BOOKABOOST]: {
    displayName: 'BookaBoost',
    shortName: 'BB',
    supportEmail: 'support@bookaboost.live',
    accentColor: '#2fb6ff',
  },
  [THEMES.EVOSYSPRO]: {
    displayName: 'EvoSys Pro',
    shortName: 'E',
    supportEmail: 'support@evosyspro.live',
    accentColor: '#087cff',
  },
  [THEMES.HARMONYHUSTLE]: {
    displayName: 'Harmony Hustle',
    shortName: 'HH',
    supportEmail: 'support@harmonyhustle.com',
    accentColor: '#10b981',
  },
}
