import { useEffect, useState } from 'react'
import {
  APPEARANCES, SYSTEM, getAppearancePreference, setAppearancePreference,
  effectiveAppearance,
} from '../appearance'

/**
 * THE APPEARANCE CONTROL — Light / Dark / System.
 *
 * A three-way segmented control rather than a two-state switch, because System
 * is a real third answer and a toggle cannot express it. A toggle would also
 * lie: flipped to "dark" while the OS is light, it would look identical to a
 * System preference that had resolved to dark, and the user could not tell
 * which they had chosen.
 *
 * `compact` renders icon-only for a collapsed rail; the accessible name still
 * carries the full word, so a screen reader never hears just a glyph.
 */
const ICONS = {
  light: 'M12 3v1.5M12 19.5V21M4.2 4.2l1.1 1.1M18.7 18.7l1.1 1.1M3 12h1.5M19.5 12H21M4.2 19.8l1.1-1.1M18.7 5.3l1.1-1.1',
  dark: 'M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z',
  system: 'M3 5h18v11H3zM8 20h8M12 16v4',
}

export default function AppearanceToggle({ compact = false, label = true }) {
  const [pref, setPref] = useState(() => getAppearancePreference())
  const [effective, setEffective] = useState(() => effectiveAppearance())

  // Keep the "System · currently dark" hint honest if the OS flips while the
  // control is on screen.
  useEffect(() => {
    if (pref !== SYSTEM) return
    const id = setInterval(() => setEffective(effectiveAppearance()), 2000)
    return () => clearInterval(id)
  }, [pref])

  function choose(value) {
    setPref(value)
    setEffective(setAppearancePreference(value))
  }

  return (
    <div>
      {label && !compact && (
        <div style={{ fontSize: 11, letterSpacing: '0.08em', fontWeight: 700,
                      color: 'var(--text-tertiary)', marginBottom: 6 }}>
          APPEARANCE
        </div>
      )}
      <div role="radiogroup" aria-label="Appearance"
        style={{ display: 'inline-flex', padding: 2, gap: 2, borderRadius: 8,
                 background: 'var(--bg-field-soft)',
                 border: '1px solid var(--border-subtle)' }}>
        {APPEARANCES.map(opt => {
          const on = pref === opt.value
          return (
            <button key={opt.value} role="radio" aria-checked={on}
              aria-label={opt.label}
              title={opt.value === SYSTEM
                ? `Follow the operating system (currently ${effective})`
                : opt.label}
              onClick={() => choose(opt.value)}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: compact ? '5px 7px' : '5px 11px',
                borderRadius: 6, cursor: 'pointer', fontFamily: 'inherit',
                fontSize: 12, fontWeight: on ? 700 : 500,
                border: '1px solid ' + (on ? 'var(--border-strong)' : 'transparent'),
                background: on ? 'var(--bg-card)' : 'transparent',
                color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
              }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="1.9" strokeLinecap="round"
                   strokeLinejoin="round" aria-hidden="true">
                <path d={ICONS[opt.value]} />
                {opt.value === 'light' && <circle cx="12" cy="12" r="3.4" />}
              </svg>
              {/* NOT ICON-ONLY BY DEFAULT. Three small glyphs are genuinely
                  ambiguous — a sun, a moon and a monitor all mean "display
                  something" until you have learned this particular control. */}
              {!compact && opt.label}
            </button>
          )
        })}
      </div>
      {pref === SYSTEM && !compact && (
        <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 5 }}>
          Following your system · currently {effective}
        </div>
      )}
    </div>
  )
}
