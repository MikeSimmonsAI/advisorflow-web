import { useState } from 'react'
import { api } from '../api/client'

/**
 * VoiceCampaign — Bulk outbound AI call campaign launcher.
 * Used on both Leads page and AI Hub.
 *
 * Props:
 *   selectedLeads: array of lead objects { id, first_name, last_name, phone, tier }
 *   onClose: function to close the modal
 *   onSuccess: function called when campaign fires successfully
 */
export default function VoiceCampaign({ selectedLeads = [], onClose, onSuccess }) {
  const [campaignName, setCampaignName] = useState(`AI Call Campaign — ${new Date().toLocaleDateString()}`)
  const [concurrent, setConcurrent] = useState(5)
  const [scheduleEnabled, setScheduleEnabled] = useState(false)
  const [scheduledAt, setScheduledAt] = useState('')
  const [windowStart, setWindowStart] = useState('09:00')
  const [windowEnd, setWindowEnd] = useState('17:00')
  const [launching, setLaunching] = useState(false)
  const [error, setError] = useState('')
  const [launched, setLaunched] = useState(null)

  const validLeads = selectedLeads.filter(l => l.phone)
  const noPhoneCount = selectedLeads.length - validLeads.length

  async function handleLaunch() {
    if (!campaignName.trim()) { setError('Campaign name required'); return }
    if (validLeads.length === 0) { setError('No leads with phone numbers selected'); return }
    setLaunching(true)
    setError('')
    try {
      const result = await api.post('/voice/campaigns', {
        name: campaignName,
        lead_ids: validLeads.map(l => l.id),
        concurrent_calls: concurrent,
        scheduled_at: scheduleEnabled && scheduledAt ? scheduledAt : null,
        call_window_start: windowStart,
        call_window_end: windowEnd,
      })
      setLaunched(result)
      if (onSuccess) onSuccess(result)
    } catch (e) {
      setError(e.message || 'Failed to launch campaign')
    } finally {
      setLaunching(false)
    }
  }

  if (launched) {
    return (
      <div style={styles.overlay} onClick={onClose}>
        <div style={styles.modal} onClick={e => e.stopPropagation()}>
          <div style={styles.successIcon}>🚀</div>
          <h2 style={styles.successTitle}>Campaign Launched</h2>
          <p style={styles.successText}>
            {launched.total_leads} AI calls queued — {concurrent} at a time.
            {launched.skipped > 0 && ` ${launched.skipped} leads skipped (DNC or no phone).`}
          </p>
          <div style={styles.successMeta}>
            <div style={styles.metaRow}>
              <span>Campaign</span>
              <strong>{campaignName}</strong>
            </div>
            <div style={styles.metaRow}>
              <span>Total calls</span>
              <strong>{launched.total_leads}</strong>
            </div>
            <div style={styles.metaRow}>
              <span>Concurrency</span>
              <strong>{concurrent} at a time</strong>
            </div>
            <div style={styles.metaRow}>
              <span>Status</span>
              <strong style={{ color: '#1ef0a8' }}>{launched.scheduled_at ? `Scheduled` : 'Running now'}</strong>
            </div>
            {launched.scheduled_at && (
              <div style={styles.metaRow}>
                <span>Starts at</span>
                <strong>{new Date(launched.scheduled_at).toLocaleString()}</strong>
              </div>
            )}
          </div>
          <p style={styles.successNote}>
            Track progress in AI Hub → Voice Calls tab. You'll receive a 🔥 alert email for any escalations.
          </p>
          <button style={styles.btnPrimary} onClick={onClose}>Done</button>
        </div>
      </div>
    )
  }

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.modal} onClick={e => e.stopPropagation()}>

        <div style={styles.modalHeader}>
          <div>
            <div style={styles.modalEyebrow}>📞 AI Voice</div>
            <h2 style={styles.modalTitle}>Bulk Call Campaign</h2>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>×</button>
        </div>

        {/* Lead summary */}
        <div style={styles.leadSummary}>
          <div style={styles.summaryItem}>
            <span style={styles.summaryNum}>{selectedLeads.length}</span>
            <span style={styles.summaryLabel}>Selected</span>
          </div>
          <div style={styles.summaryItem}>
            <span style={{ ...styles.summaryNum, color: '#1ef0a8' }}>{validLeads.length}</span>
            <span style={styles.summaryLabel}>Will call</span>
          </div>
          {noPhoneCount > 0 && (
            <div style={styles.summaryItem}>
              <span style={{ ...styles.summaryNum, color: '#f87171' }}>{noPhoneCount}</span>
              <span style={styles.summaryLabel}>No phone</span>
            </div>
          )}
        </div>

        {noPhoneCount > 0 && (
          <div style={styles.warning}>
            ⚠️ {noPhoneCount} lead{noPhoneCount > 1 ? 's' : ''} will be skipped — no phone number on file.
          </div>
        )}

        {/* Campaign name */}
        <div style={styles.field}>
          <label style={styles.label}>Campaign name</label>
          <input
            style={styles.input}
            value={campaignName}
            onChange={e => setCampaignName(e.target.value)}
            placeholder="e.g. Pre-Need Follow-Up July 2026"
          />
        </div>

        {/* Concurrency */}
        <div style={styles.field}>
          <label style={styles.label}>
            Simultaneous calls
            <span style={styles.labelNote}> — {concurrent} at a time ({Math.ceil(validLeads.length / concurrent)} batches)</span>
          </label>
          <div style={styles.concurrencyRow}>
            {[1, 2, 3, 5, 10].map(n => (
              <button
                key={n}
                style={{ ...styles.concurrencyBtn, ...(concurrent === n ? styles.concurrencyBtnActive : {}) }}
                onClick={() => setConcurrent(n)}
              >{n}</button>
            ))}
          </div>
          <p style={styles.fieldNote}>
            Pay-as-you-go Twilio accounts support up to 5 concurrent calls by default.
          </p>
        </div>

        {/* Call window */}
        <div style={styles.field}>
          <label style={styles.label}>Call window (CST)</label>
          <div style={styles.row}>
            <select style={styles.select} value={windowStart} onChange={e => setWindowStart(e.target.value)}>
              {['08:00','09:00','10:00','11:00','12:00'].map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <span style={{ color: 'var(--text-tertiary)', fontSize: 13 }}>to</span>
            <select style={styles.select} value={windowEnd} onChange={e => setWindowEnd(e.target.value)}>
              {['15:00','16:00','17:00','18:00','19:00','20:00'].map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
          <p style={styles.fieldNote}>Calls outside this window will be held and fired when the window opens.</p>
        </div>

        {/* Schedule toggle */}
        <div style={styles.field}>
          <label style={{ ...styles.checkRow }}>
            <input
              type="checkbox"
              checked={scheduleEnabled}
              onChange={e => setScheduleEnabled(e.target.checked)}
              style={{ accentColor: 'var(--accent)' }}
            />
            <span style={styles.label}>Schedule for later</span>
          </label>
          {scheduleEnabled && (
            <input
              style={{ ...styles.input, marginTop: 8 }}
              type="datetime-local"
              value={scheduledAt}
              onChange={e => setScheduledAt(e.target.value)}
            />
          )}
        </div>

        {error && <div style={styles.error}>{error}</div>}

        {/* Preview */}
        <div style={styles.preview}>
          <div style={styles.previewTitle}>What happens when a lead picks up:</div>
          <div style={styles.previewScript}>
            "Hi, is this [first name]? This is an AI assistant calling on behalf of
            [advisor name] at [business name]. Is it alright if I speak with you in English
            for just a moment? ..."
          </div>
          <div style={styles.previewNote}>
            AI reads lead tier, history, and appointment type to personalize each call.
            Voicemails left automatically. Escalations paused and flagged to you.
          </div>
        </div>

        <div style={styles.actions}>
          <button style={styles.btnSecondary} onClick={onClose}>Cancel</button>
          <button
            style={{ ...styles.btnPrimary, opacity: launching ? 0.7 : 1 }}
            onClick={handleLaunch}
            disabled={launching || validLeads.length === 0}
          >
            {launching ? '⏳ Launching…' : `📞 Call ${validLeads.length} Lead${validLeads.length !== 1 ? 's' : ''}`}
          </button>
        </div>

      </div>
    </div>
  )
}

const styles = {
  overlay: {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 9999, padding: 20,
  },
  modal: {
    background: 'var(--bg-card, #1a1a2e)', border: '1px solid var(--border-subtle)',
    borderRadius: 16, padding: 28, width: '100%', maxWidth: 520,
    maxHeight: '90vh', overflowY: 'auto',
  },
  modalHeader: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20,
  },
  modalEyebrow: { fontSize: 11, fontWeight: 700, color: 'var(--accent)', letterSpacing: '0.08em', textTransform: 'uppercase', marginBottom: 4 },
  modalTitle: { fontSize: 22, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.02em', margin: 0 },
  closeBtn: { background: 'none', border: 'none', color: 'var(--text-tertiary)', fontSize: 24, cursor: 'pointer', lineHeight: 1, padding: '0 4px' },
  leadSummary: { display: 'flex', gap: 16, marginBottom: 16, padding: '16px', background: 'rgba(47,182,255,0.06)', borderRadius: 10, border: '1px solid rgba(47,182,255,0.15)' },
  summaryItem: { display: 'flex', flexDirection: 'column', alignItems: 'center', flex: 1 },
  summaryNum: { fontSize: 28, fontWeight: 900, color: 'var(--text-primary)', lineHeight: 1 },
  summaryLabel: { fontSize: 11, color: 'var(--text-tertiary)', marginTop: 4, textTransform: 'uppercase', letterSpacing: '0.06em' },
  warning: { background: 'rgba(251,146,60,0.1)', border: '1px solid rgba(251,146,60,0.3)', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#fb923c', marginBottom: 16 },
  field: { marginBottom: 18 },
  label: { display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 },
  labelNote: { fontWeight: 400, textTransform: 'none', letterSpacing: 0, color: 'var(--text-tertiary)' },
  input: { width: '100%', background: 'var(--bg-input, #0f0f1a)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: '12px 14px', fontSize: 14, color: 'var(--text-primary)', outline: 'none' },
  select: { flex: 1, background: 'var(--bg-input, #0f0f1a)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: '10px 12px', fontSize: 13, color: 'var(--text-primary)', outline: 'none' },
  row: { display: 'flex', gap: 10, alignItems: 'center' },
  fieldNote: { fontSize: 11, color: 'var(--text-tertiary)', marginTop: 6, lineHeight: 1.5 },
  concurrencyRow: { display: 'flex', gap: 8, marginBottom: 6 },
  concurrencyBtn: { width: 48, height: 40, border: '1px solid var(--border-subtle)', borderRadius: 8, background: 'transparent', color: 'var(--text-secondary)', fontSize: 15, fontWeight: 700, cursor: 'pointer' },
  concurrencyBtnActive: { background: 'var(--accent)', borderColor: 'var(--accent)', color: '#fff' },
  checkRow: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' },
  error: { background: 'rgba(248,113,113,0.1)', border: '1px solid rgba(248,113,113,0.3)', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#f87171', marginBottom: 16 },
  preview: { background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-subtle)', borderRadius: 10, padding: 16, marginBottom: 20 },
  previewTitle: { fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 },
  previewScript: { fontSize: 13, color: 'var(--text-secondary)', fontStyle: 'italic', lineHeight: 1.6, marginBottom: 8 },
  previewNote: { fontSize: 11, color: 'var(--text-tertiary)', lineHeight: 1.5 },
  actions: { display: 'flex', gap: 10, justifyContent: 'flex-end' },
  btnPrimary: { background: 'var(--accent, #2fb6ff)', color: '#fff', border: 'none', borderRadius: 8, padding: '13px 24px', fontSize: 14, fontWeight: 700, cursor: 'pointer' },
  btnSecondary: { background: 'transparent', color: 'var(--text-secondary)', border: '1px solid var(--border-subtle)', borderRadius: 8, padding: '13px 20px', fontSize: 14, cursor: 'pointer' },
  successIcon: { fontSize: 48, textAlign: 'center', marginBottom: 16 },
  successTitle: { fontSize: 24, fontWeight: 800, color: 'var(--text-primary)', textAlign: 'center', marginBottom: 8 },
  successText: { fontSize: 15, color: 'var(--text-secondary)', textAlign: 'center', lineHeight: 1.6, marginBottom: 20 },
  successMeta: { background: 'rgba(255,255,255,0.04)', borderRadius: 10, padding: 16, marginBottom: 16 },
  metaRow: { display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--border-subtle)', fontSize: 13, color: 'var(--text-secondary)' },
  successNote: { fontSize: 12, color: 'var(--text-tertiary)', textAlign: 'center', lineHeight: 1.6, marginBottom: 20 },
}
