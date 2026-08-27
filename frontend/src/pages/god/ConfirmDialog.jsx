/**
 * ConfirmDialog — one confirmation surface for privileged owner actions.
 *
 * The Command Center and the Organizations screen both suspend organizations
 * and both enter tenants. Two dialogs meant two chances for one of them to stop
 * saying what the action actually does.
 */
export default function ConfirmDialog({
  tone = 'blue', eyebrow, title, body, confirmLabel,
  busy, onConfirm, onCancel,
}) {
  const line = tone === 'danger' ? 'rgba(255,93,125,.45)'
    : tone === 'gold' ? 'rgba(255,217,104,.42)'
    : 'rgba(57,189,248,.42)'
  const fg = tone === 'danger' ? '#ff829b' : tone === 'gold' ? '#ffd968' : '#7cc0ff'

  return (
    <div
      role="dialog" aria-modal="true"
      onClick={onCancel}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(1,4,9,.72)', zIndex: 400,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 18,
      }}
    >
      <div
        className="gm-card"
        onClick={e => e.stopPropagation()}
        style={{ borderColor: line, padding: 24, maxWidth: 440, width: '100%' }}
      >
        <div style={{ color: fg, fontSize: 9, fontWeight: 800, letterSpacing: '.12em', marginBottom: 11 }}>
          {eyebrow}
        </div>
        <div style={{ color: '#f1f7ff', fontSize: 15, fontWeight: 600, marginBottom: 9 }}>
          {title}
        </div>
        <div style={{ color: '#7f96ae', fontSize: 11.5, lineHeight: 1.65, marginBottom: 20 }}>
          {body}
        </div>
        <div style={{ display: 'flex', gap: 9 }}>
          <button className="gm-btn" style={{ flex: 1, padding: '9px 0' }}
                  onClick={onCancel} disabled={busy}>
            CANCEL
          </button>
          <button
            className="gm-btn"
            style={{
              flex: 1, padding: '9px 0', color: fg, borderColor: line,
              background: tone === 'danger' ? '#2a1017' : tone === 'gold' ? '#1b1505' : '#0d2a44',
              fontWeight: 800,
            }}
            onClick={onConfirm} disabled={busy}
          >
            {busy ? 'WORKING…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
