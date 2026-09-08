/**
 * APPEARANCE — the light / dark / system preference.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHY THIS IS SEPARATE FROM theme.js
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `theme.js` owns the WHITE-LABEL BRAND: which company's colours and logo this
 * install wears, resolved from the hostname and the server's branding payload.
 * That is a property of the DEPLOYMENT.
 *
 * Appearance is a property of the PERSON. Mike wants a bright interface;
 * somebody else on the same brand may not. Folding the two together would mean
 * either a brand that can only be dark or a person whose choice changes their
 * employer's colours.
 *
 * So they are two attributes on <html> and they compose:
 *
 *     data-theme="evosyspro"   ← the brand   (theme.js)
 *     data-appearance="light"  ← the person  (this file)
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHERE THE PREFERENCE LIVES
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * localStorage, under the same `af_` prefix every other client preference in
 * this codebase uses. There is no per-user preference column on the server and
 * inventing one for a colour scheme would put a schema migration, an endpoint
 * and a round trip in front of a toggle — while still needing a local fallback
 * for the first paint. This follows the established convention rather than
 * adding a hack beside it.
 *
 * SYSTEM IS RESOLVED, NOT STORED AS A THIRD CSS STATE. The stored value may be
 * 'system'; what reaches the DOM is always a concrete 'light' or 'dark'. One
 * place decides, the DOM records what was decided, and a screenshot is never
 * ambiguous about what was actually rendered.
 */

const KEY = 'af_appearance'

export const LIGHT = 'light'
export const DARK = 'dark'
export const SYSTEM = 'system'

export const APPEARANCES = [
  { value: LIGHT, label: 'Light' },
  { value: DARK, label: 'Dark' },
  { value: SYSTEM, label: 'System' },
]

function mediaQuery() {
  if (typeof window === 'undefined' || !window.matchMedia) return null
  return window.matchMedia('(prefers-color-scheme: dark)')
}

/** What the OS is asking for right now. Dark when it will not say. */
export function systemAppearance() {
  const mq = mediaQuery()
  if (!mq) return DARK
  return mq.matches ? DARK : LIGHT
}

/**
 * The stored PREFERENCE, which may be 'system'.
 *
 * Defaults to DARK rather than SYSTEM: this product has shipped dark, and a
 * silent flip to light on every machine set to light would be a redesign
 * nobody asked for. Choosing System is opting IN.
 */
export function getAppearancePreference() {
  try {
    const v = localStorage.getItem(KEY)
    if (v === LIGHT || v === DARK || v === SYSTEM) return v
  } catch (_) { /* private mode, blocked storage - fall through */ }
  return DARK
}

/** The appearance actually in force, with 'system' resolved. */
export function effectiveAppearance() {
  const pref = getAppearancePreference()
  return pref === SYSTEM ? systemAppearance() : pref
}

/** Write it to the DOM. The single place that touches the attribute. */
export function applyAppearance(appearance) {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.setAttribute('data-appearance', appearance)
  // Native controls - scrollbars, date pickers, form widgets - follow this and
  // nothing else. Without it a light page still renders dark OS widgets, which
  // is the most obvious tell of a half-finished light theme.
  root.style.colorScheme = appearance
}

/** Choose, persist, apply. Returns the appearance that ended up in force. */
export function setAppearancePreference(pref) {
  try { localStorage.setItem(KEY, pref) } catch (_) { /* not fatal */ }
  const effective = pref === SYSTEM ? systemAppearance() : pref
  applyAppearance(effective)
  return effective
}

/**
 * Apply at boot and keep following the OS while the preference is 'system'.
 *
 * The listener is only meaningful in SYSTEM mode - somebody on an explicit
 * Light does not want their screen flipping at sunset - so the handler
 * re-reads the preference rather than capturing it.
 */
export function initAppearance() {
  applyAppearance(effectiveAppearance())
  const mq = mediaQuery()
  if (!mq) return () => {}
  const onChange = () => {
    if (getAppearancePreference() === SYSTEM) applyAppearance(systemAppearance())
  }
  if (mq.addEventListener) mq.addEventListener('change', onChange)
  else if (mq.addListener) mq.addListener(onChange)     // older Safari
  return () => {
    if (mq.removeEventListener) mq.removeEventListener('change', onChange)
    else if (mq.removeListener) mq.removeListener(onChange)
  }
}
