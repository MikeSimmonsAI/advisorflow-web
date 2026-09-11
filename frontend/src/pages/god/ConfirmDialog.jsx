/**
 * ConfirmDialog — one confirmation surface for privileged owner actions.
 *
 * The Command Center and the Organizations screen both suspend organizations
 * and both enter tenants. Two dialogs meant two chances for one of them to stop
 * saying what the action actually does.
 *
 * THREE THINGS THIS GETS RIGHT THAT THE FIRST LIGHT PASS DID NOT:
 *
 *   A SCRIM IS NOT A TINT. The backdrop's job is to take the page behind it out
 *   of play. The mechanical pass that moved this file onto tokens turned an
 *   opaque dark overlay into a pale blue wash, which left the whole screen
 *   looking washed rather than the dialog looking modal.
 *
 *   ONE PRIMARY. Cancel and Confirm were two outlined boxes of the same weight,
 *   so the dialog asked a question and then gave two identical answers. The
 *   confirm button is filled in the tone of what it is about to do; cancel is
 *   the quiet one.
 *
 *   THE TONE COLOURS THE ACCENTS, NOT THE PROSE. Body copy is body copy.
 *
 * Escape cancels, and focus lands on the confirm button — a dialog a keyboard
 * cannot dismiss is a trap on a screen that suspends customers.
 */
import { useEffect, useRef } from 'react'

export default function ConfirmDialog({
  tone = 'blue', eyebrow, title, body, confirmLabel,
  busy, onConfirm, onCancel,
}) {
  const confirmRef = useRef(null)

  useEffect(() => {
    confirmRef.current?.focus()
    function onKey(e) { if (e.key === 'Escape' && !busy) onCancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [busy, onCancel])

  const accent = tone === 'danger' ? 'var(--gm-red)'
    : tone === 'gold' ? 'var(--gm-gold)'
    : 'var(--gm-blue)'
  const fill = tone === 'danger' ? 'var(--gm-red)'
    : tone === 'gold' ? 'var(--gm-btn-gold)'
    : 'var(--gm-btn-primary)'
  const rule = tone === 'danger' ? 'var(--gm-pill-red-bd)'
    : tone === 'gold' ? 'var(--gm-pill-gold-bd)'
    : 'var(--gm-pill-blue-bd)'

  return (
    <div
      onClick={() => { if (!busy) onCancel() }}
      style={{
        position: 'fixed', inset: 0, background: 'var(--gm-scrim)', zIndex: 400,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 18,
      }}
    >
      <div
        role="dialog" aria-modal="true" aria-label={title}
        className="gm-card"
        onClick={e => e.stopPropagation()}
        style={{ borderColor: rule, borderTop: '3px solid ' + accent,
                 padding: 24, maxWidth: 460, width: '100%',
                 boxShadow: 'var(--gm-shadow-modal)' }}
      >
        <div style={{ color: accent, fontSize: 10.5, fontWeight: 800, letterSpacing: '.1em', marginBottom: 10 }}>
          {eyebrow}
        </div>
        <div style={{ color: 'var(--gm-head)', fontSize: 18, fontWeight: 700, marginBottom: 8,
                      letterSpacing: '-.01em' }}>
          {title}
        </div>
        <div style={{ color: 'var(--gm-dim)', fontSize: 13, lineHeight: 1.65, marginBottom: 20 }}>
          {body}
        </div>
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button className="gm-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            className="gm-btn"
            style={{
              background: fill, borderColor: fill,
              color: tone === 'gold' ? 'var(--gm-btn-gold-fg)' : 'var(--gm-btn-primary-fg)',
              fontWeight: 700,
            }}
            onClick={onConfirm} disabled={busy}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
