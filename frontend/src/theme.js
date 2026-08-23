/**
 * AdvisorFlow Platform Theme Detection
 * ---------------------------------------
 * Reads window.location.hostname at load time and returns the correct
 * brand theme. The backend serves the same React bundle to all domains.
 * This file is the single place that makes it look different per brand.
 *
 * Brand detection order:
 *   1. evosyspro  â†’ app.evosyspro.live  (dark navy, electric blue, green)
 *   2. harmonyhustle â†’ app.harmonyhustle.com (TBD â€” real estate)
 *   3. default    â†’ app.bookaboost.live + localhost (BookaBoost existing theme)
 *
 * How it works:
 *   applyTheme() injects a data-theme attribute on <html>.
 *   CSS in index.css uses [data-theme="evosyspro"] overrides.
 *   No JavaScript theme state to manage â€” pure CSS variable overrides.
 */

export const THEMES = {
  BOOKABOOST: 'bookaboost',
  EVOSYSPRO: 'evosyspro',
  HARMONYHUSTLE: 'harmonyhustle',
  ADVISORFLOW: 'advisorflow',
}

/**
 * Detect which brand theme to use based on the current hostname.
 * Safe to call in SSR/test environments where window may not exist.
 */
export function detectTheme() {
  if (typeof window === 'undefined') return THEMES.BOOKABOOST

  const host = window.location.hostname.toLowerCase()

  if (host.includes('advisorflow')) return THEMES.ADVISORFLOW
  if (host.includes('evosyspro')) return THEMES.EVOSYSPRO
  if (host.includes('harmonyhustle')) return THEMES.HARMONYHUSTLE

  return THEMES.BOOKABOOST
}

/**
 * Apply the detected theme to the document root.
 * Sets data-theme attribute + updates the browser tab title + injects favicon.
 */
export function applyTheme(theme) {
  if (typeof document === 'undefined') return

  document.documentElement.setAttribute('data-theme', theme)

  const titles = {
    [THEMES.ADVISORFLOW]:  'AdvisorFlow',
    [THEMES.EVOSYSPRO]:    'EvoSys Pro',
    [THEMES.HARMONYHUSTLE]: 'Harmony Hustle',
    [THEMES.BOOKABOOST]:   'BookaBoost',
  }
  document.title = titles[theme] || 'BookaBoost'

  // Inject per-brand favicon as inline SVG data URI so every brand gets
  // a tab icon regardless of whether a hosted image exists.
  const favicons = {
    [THEMES.EVOSYSPRO]:     "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23087cff'/%3E%3Ctext x='16' y='22' font-family='Arial,sans-serif' font-size='18' font-weight='700' fill='white' text-anchor='middle'%3EE%3C/text%3E%3C/svg%3E",
    [THEMES.BOOKABOOST]:    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23c9973d'/%3E%3Ctext x='16' y='22' font-family='Arial,sans-serif' font-size='13' font-weight='700' fill='white' text-anchor='middle'%3EBB%3C/text%3E%3C/svg%3E",
    [THEMES.ADVISORFLOW]:   "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23f59e0b'/%3E%3Ctext x='16' y='22' font-family='Arial,sans-serif' font-size='14' font-weight='700' fill='white' text-anchor='middle'%3EAF%3C/text%3E%3C/svg%3E",
    [THEMES.HARMONYHUSTLE]: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%2310b981'/%3E%3Ctext x='16' y='22' font-family='Arial,sans-serif' font-size='13' font-weight='700' fill='white' text-anchor='middle'%3EHH%3C/text%3E%3C/svg%3E",
  }
  let link = document.querySelector("link[rel~='icon']")
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.href = favicons[theme] || favicons[THEMES.BOOKABOOST]
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
 * Brand config per theme â€” logos, display names, support emails, colors.
 * Used in Layout.jsx to render the correct sidebar logo and brand name.
 */
export const BRAND_CONFIG = {
  [THEMES.ADVISORFLOW]: {
    displayName: 'AdvisorFlow',
    shortName: 'AF',
    supportEmail: 'mike@simmonsstrong.com',
    accentColor: '#f59e0b',
  },
  [THEMES.BOOKABOOST]: {
    displayName: 'BookaBoost',
    shortName: 'BB',
    supportEmail: 'support@bookaboost.live',
    accentColor: '#c9973d',
  },
  [THEMES.EVOSYSPRO]: {
    displayName: 'EvoSys Pro',
    shortName: 'E',
    supportEmail: 'support@evosyspro.live',
    accentColor: '#087cff',
    logoUrl: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyMjAgNjAiPjxyZWN0IHg9IjAiIHk9IjQiIHdpZHRoPSI1MiIgaGVpZ2h0PSI1MiIgcng9IjEwIiBmaWxsPSIjMDg3Y2ZmIi8+PHJlY3QgeD0iMTMiIHk9IjE2IiB3aWR0aD0iMjYiIGhlaWdodD0iNSIgcng9IjIuNSIgZmlsbD0id2hpdGUiLz48cmVjdCB4PSIxMyIgeT0iMjciIHdpZHRoPSIyMCIgaGVpZ2h0PSI1IiByeD0iMi41IiBmaWxsPSJ3aGl0ZSIvPjxyZWN0IHg9IjEzIiB5PSIzOSIgd2lkdGg9IjI2IiBoZWlnaHQ9IjUiIHJ4PSIyLjUiIGZpbGw9IndoaXRlIi8+PHRleHQgeD0iNjQiIHk9IjI4IiBmb250LWZhbWlseT0iQXJpYWwsSGVsdmV0aWNhLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTgiIGZvbnQtd2VpZ2h0PSI3MDAiIGZpbGw9IiMwODdjZmYiPkV2b1N5czwvdGV4dD48dGV4dCB4PSI2NCIgeT0iNTAiIGZvbnQtZmFtaWx5PSJBcmlhbCxIZWx2ZXRpY2Esc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9IjQwMCIgZmlsbD0iIzdhYjNmZiIgbGV0dGVyLXNwYWNpbmc9IjEiPlBSTzwvdGV4dD48L3N2Zz4=',
  },
  [THEMES.HARMONYHUSTLE]: {
    displayName: 'Harmony Hustle',
    shortName: 'HH',
    supportEmail: 'support@harmonyhustle.com',
    accentColor: '#10b981',
  },
}
