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
 * The brand the HOSTNAME names, or null when the host is not a brand domain
 * (localhost, a preview host, an IP). Unlike detectTheme() this never guesses:
 * null means "the address bar does not say", which is exactly when the
 * signed-in workspace's own platform should decide (Phase 7.2).
 */
export function hostTheme() {
  if (typeof window === 'undefined') return null
  const host = window.location.hostname.toLowerCase()
  if (host.includes('advisorflow') && !host.includes('onrender')) return THEMES.ADVISORFLOW
  if (host.includes('evosyspro')) return THEMES.EVOSYSPRO
  if (host.includes('harmonyhustle')) return THEMES.HARMONYHUSTLE
  if (host.includes('bookaboost')) return THEMES.BOOKABOOST
  return null
}

/**
 * The theme the application shell should wear for this workspace.
 *
 * A brand domain decides its own chrome, always. Only when the host names no
 * brand does the workspace's platform (GET /branding/org -> platform) decide,
 * and only when neither says anything does the historical default apply.
 */
export function shellTheme(branding) {
  return shellThemeSource(branding).theme
}

/**
 * The shell theme AND where it came from - the answer the permanent
 * brand-resolution gate checks (scripts/review/brand_gate.py):
 *
 *   'host'       a brand domain decided (app.evosyspro.live, ...)
 *   'workspace'  the authenticated organization's platform decided
 *   'default'    NOTHING said anything and the historical hostname default was
 *                used. On a non-brand host with a signed-in workspace this is a
 *                FALLBACK, and the gate fails it.
 */
export function shellThemeSource(branding) {
  const fromHost = hostTheme()
  if (fromHost) return { theme: fromHost, source: 'host' }
  const fromWorkspace = branding && branding.platform && branding.platform.theme
  if (fromWorkspace && BRAND_CONFIG[fromWorkspace]) return { theme: fromWorkspace, source: 'workspace' }
  return { theme: detectTheme(), source: 'default' }
}

/**
 * The brand's commercial name for a product/module (EvoSysPro -> "EvoSys
 * Wholesale"), from the workspace's platform as the server states it. null when
 * the brand has not named one - callers then show the neutral module name and
 * never borrow another brand's product name.
 */
export function productName(branding, module) {
  const p = branding && branding.platform
  return (p && p.products && p.products[module]) || null
}

/**
 * Apply the workspace platform's theme to the document when the host is not a
 * brand domain. A no-op on every brand domain.
 */
export function applyWorkspaceTheme(branding) {
  if (typeof document === 'undefined' || hostTheme()) return
  const p = branding && branding.platform
  if (!p || !p.theme || !BRAND_CONFIG[p.theme]) return
  applyTheme(p.theme)
  if (p.accent_color) {
    document.documentElement.style.setProperty('--brand-platform-accent', p.accent_color)
  }
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
  // The cached platform row wins over hostname sniffing: it is what the
  // database actually says this host is, and applying it synchronously here is
  // what removes the flash the bootstrap literal exists to prevent.
  const cached = getCachedBrand()
  const theme = (cached && cached.theme) || detectTheme()
  applyTheme(theme)
  if (cached) applyBrandPayload(cached)
  // The signed-in workspace's platform, from the last /branding/org answer,
  // so a non-brand host (localhost) paints the right brand on the first frame.
  try {
    const org = JSON.parse(localStorage.getItem('af_branding') || 'null')
    applyWorkspaceTheme(org)
  } catch { /* storage blocked */ }
  return theme
}

/**
 * BOOTSTRAP ONLY - the platform row is the source of truth.
 *
 * These literals used to be one of four unsynchronised copies of the same brand
 * data. They are now a FIRST-PAINT FALLBACK and nothing more: `hydrateBrand()`
 * below fetches GET /branding, which reads the platform row, and caches the
 * answer so every load after the first is database-driven.
 *
 * They cannot simply be deleted. A network fetch cannot beat the first frame,
 * and a flash of the wrong brand - a funeral home's staff seeing another
 * company's name and colours for 300ms on every load - is worse than carrying a
 * bootstrap literal. Adding a brand no longer requires editing this table; a
 * brand missing from it renders from its row on the first paint after the
 * cache warms, and correctly from then on.
 *
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
    websiteUrl: 'https://bookaboost.live',
  },
  [THEMES.EVOSYSPRO]: {
    displayName: 'EvoSys Pro',
    shortName: 'E',
    supportEmail: 'support@evosyspro.live',
    accentColor: '#087cff',
    logoUrl: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyMjAgNjAiPjxyZWN0IHg9IjAiIHk9IjQiIHdpZHRoPSI1MiIgaGVpZ2h0PSI1MiIgcng9IjEwIiBmaWxsPSIjMDg3Y2ZmIi8+PHJlY3QgeD0iMTMiIHk9IjE2IiB3aWR0aD0iMjYiIGhlaWdodD0iNSIgcng9IjIuNSIgZmlsbD0id2hpdGUiLz48cmVjdCB4PSIxMyIgeT0iMjciIHdpZHRoPSIyMCIgaGVpZ2h0PSI1IiByeD0iMi41IiBmaWxsPSJ3aGl0ZSIvPjxyZWN0IHg9IjEzIiB5PSIzOSIgd2lkdGg9IjI2IiBoZWlnaHQ9IjUiIHJ4PSIyLjUiIGZpbGw9IndoaXRlIi8+PHRleHQgeD0iNjQiIHk9IjI4IiBmb250LWZhbWlseT0iQXJpYWwsSGVsdmV0aWNhLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMTgiIGZvbnQtd2VpZ2h0PSI3MDAiIGZpbGw9IiMwODdjZmYiPkV2b1N5czwvdGV4dD48dGV4dCB4PSI2NCIgeT0iNTAiIGZvbnQtZmFtaWx5PSJBcmlhbCxIZWx2ZXRpY2Esc2Fucy1zZXJpZiIgZm9udC1zaXplPSIxNCIgZm9udC13ZWlnaHQ9IjQwMCIgZmlsbD0iIzdhYjNmZiIgbGV0dGVyLXNwYWNpbmc9IjEiPlBSTzwvdGV4dD48L3N2Zz4=',
    websiteUrl: 'https://evosyspro.live',
  },
  [THEMES.HARMONYHUSTLE]: {
    displayName: 'Harmony Hustle',
    shortName: 'HH',
    supportEmail: 'support@harmonyhustle.com',
    accentColor: '#10b981',
    websiteUrl: 'https://harmonyhustle.com',
  },
}

// ---------------------------------------------------------------------------
// DATABASE-DRIVEN BRANDING
// ---------------------------------------------------------------------------

const BRAND_CACHE_KEY = 'af_platform_brand'

/** The cached platform brand, or null. Never throws - storage can be blocked. */
export function getCachedBrand() {
  try {
    const raw = localStorage.getItem(BRAND_CACHE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

/**
 * Apply a brand payload from GET /branding to the document.
 *
 * Only writes what the payload actually carries. A brand whose row has no logo
 * must render with no logo, never with another brand's - the same rule the
 * backend's public_identity applies to customer-facing pages.
 */
export function applyBrandPayload(brand) {
  if (!brand || typeof document === 'undefined') return

  if (brand.theme) document.documentElement.setAttribute('data-theme', brand.theme)
  if (brand.documentTitle) document.title = brand.documentTitle

  if (brand.faviconUrl) {
    let link = document.querySelector("link[rel~='icon']")
    if (!link) {
      link = document.createElement('link')
      link.rel = 'icon'
      document.head.appendChild(link)
    }
    link.href = brand.faviconUrl
  }

  if (brand.accentColor) {
    document.documentElement.style.setProperty('--brand-platform-accent', brand.accentColor)
  }
}

/**
 * Fetch the brand for this hostname and cache it.
 *
 * Called once after mount. The cached copy is applied synchronously on the NEXT
 * load, before this fetch resolves, which is what makes the database the source
 * of truth without reintroducing a flash.
 */
export async function hydrateBrand(apiBase) {
  if (typeof window === 'undefined') return null
  try {
    const base = apiBase || ''
    const res = await fetch(base + '/branding', { credentials: 'omit' })
    if (!res.ok) return null
    const brand = await res.json()
    if (!brand || !brand.brand) return null
    try {
      localStorage.setItem(BRAND_CACHE_KEY, JSON.stringify(brand))
    } catch {
      /* storage blocked - the fetch still themed this page */
    }
    applyBrandPayload(brand)
    // On a host that is not a brand domain (localhost), the signed-in
    // workspace's platform outranks whatever this host-level answer said, in
    // whichever order the two arrive. No-op on a brand domain.
    try { applyWorkspaceTheme(JSON.parse(localStorage.getItem('af_branding') || 'null')) } catch { /* storage blocked */ }
    return brand
  } catch {
    return null
  }
}

/**
 * The brand values a component should render.
 *
 * Cached platform row first, bootstrap literal second - so Layout, Login and
 * Billing stop reading a compiled-in table and start reading the database,
 * without any of them needing to know that is what changed.
 */
export function resolveBrand(theme) {
  const cached = getCachedBrand() || {}
  const boot = BRAND_CONFIG[theme] || BRAND_CONFIG[THEMES.BOOKABOOST] || {}
  return {
    displayName:  cached.displayName  || boot.displayName,
    shortName:    cached.shortName    || boot.shortName,
    supportEmail: cached.supportEmail || boot.supportEmail,
    accentColor:  cached.accentColor  || boot.accentColor,
    websiteUrl:   cached.websiteUrl   || boot.websiteUrl,
    logoUrl:      cached.logoUrl      || boot.logoUrl,
    tagline:      cached.tagline      || boot.tagline || null,
    source:       cached.brand ? 'database' : 'bootstrap',
  }
}
