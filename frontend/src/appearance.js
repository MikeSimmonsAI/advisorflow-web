/**
 * APPEARANCE — ONE LIGHT THEME. There is no longer a day/night choice.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHY THE TOGGLE WAS REMOVED (spec §66, 2026-09-28)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The Light / Dark / System control produced a "mixed theme" platform: God
 * Mode was permanently light, the tenant app defaulted dark, the Executive and
 * Sales surfaces each carried their own dark palette, and every new page had
 * to be designed twice or it looked broken in one of them. The platform now
 * ships ONE polished light theme; its tokens live in styles/appearance.css
 * (the documented design-token layer) and do not depend on any attribute.
 *
 * WHAT THIS FILE STILL DOES
 *   - Writes data-appearance="light" on <html> and color-scheme: light, so
 *     native widgets (select lists, date pickers, scrollbars) render light and
 *     any legacy selector keyed on the attribute resolves to the light branch.
 *   - Clears the stale `af_appearance` localStorage key left behind by the old
 *     toggle, so nobody carries a dead "dark" preference around.
 *
 * The exported names are kept (as light-only, no-op-safe functions) so any
 * importer that has not been updated yet keeps compiling and simply gets light.
 * `theme.js` still owns the white-label BRAND (data-theme); that is unchanged.
 */

const KEY = 'af_appearance'

export const LIGHT = 'light'
/** Retained for import compatibility only. Dark is no longer offered. */
export const DARK = 'dark'
/** Retained for import compatibility only. System is no longer offered. */
export const SYSTEM = 'system'

/** The only appearance the platform offers. */
export const APPEARANCES = [{ value: LIGHT, label: 'Light' }]

function clearStalePreference() {
  try { localStorage.removeItem(KEY) } catch (_) { /* blocked storage - fine */ }
}

/** Always light. */
export function systemAppearance() { return LIGHT }

/** Always light. Any stored value from the old toggle is ignored. */
export function getAppearancePreference() { return LIGHT }

/** Always light. */
export function effectiveAppearance() { return LIGHT }

/** Write the (only) appearance to the DOM. Argument ignored on purpose. */
export function applyAppearance(_ignored) {
  if (typeof document === 'undefined') return
  const root = document.documentElement
  root.setAttribute('data-appearance', LIGHT)
  root.style.colorScheme = LIGHT
}

/** No-op-safe: whatever is asked for, light is applied. Returns 'light'. */
export function setAppearancePreference(_ignored) {
  clearStalePreference()
  applyAppearance(LIGHT)
  return LIGHT
}

/** Boot: clear the stale key, apply light. Returns a no-op cleanup. */
export function initAppearance() {
  clearStalePreference()
  applyAppearance(LIGHT)
  return () => {}
}
