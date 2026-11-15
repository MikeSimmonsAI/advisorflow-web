// Google Sheets mirror status for ONE VOLT business (recruiting or consumer).
// Shown only to the owner and VOLT's named operators: the server answers 403
// to everyone else and the panel then renders nothing. No credentials are ever
// sent to the browser - only the service account's email (to share with).
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

const ct = (iso) => (iso ? new Date(iso).toLocaleString('en-US', {
  timeZone: 'America/Chicago', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
}) : '—')
const HEALTH = {
  ok: ['Syncing', '#0a7a4f'], waiting: ['Connected · waiting for the first record', '#0a7a4f'],
  retrying: ['Retrying failed records', '#b26b00'], broken: ['Not updating - action needed', '#b42318'],
  paused: ['Paused', '#b26b00'], off: ['Sync is switched off on the server', '#596a75'],
  needs_credential: ['Waiting for the Google service account', '#b42318'], not_configured: ['Not set up', '#596a75'],
}

export function useSheetsStatus(orgId) {
  const [s, setS] = useState(null)
  const load = useCallback(() => api.get(`/volt/ops/${orgId}/sheets/status`).then(setS).catch(() => setS(false)), [orgId])
  useEffect(() => { load() }, [load])
  return [s, load]
}

export function OpenSheetLink({ orgId, business, className = 'v-btn v-btn--line v-btn--sm' }) {
  const [s] = useSheetsStatus(orgId)
  const b = s && s.businesses.find((x) => x.business === business)
  if (!b || !b.spreadsheet_url) return null
  return <a className={className} href={b.spreadsheet_url} target="_blank" rel="noreferrer" data-open-sheet={business}>Open Google Sheet ↗</a>
}

export default function SheetsPanel({ orgId, business, isOwner = false }) {
  const [s, load] = useSheetsStatus(orgId)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  if (!s) return null
  const b = s.businesses.find((x) => x.business === business)
  if (!b) return null
  const [label, color] = HEALTH[b.health] || [b.health, '#596a75']
  const act = (path, body, done) => {
    setBusy(true); setNote('')
    api.post(`/volt/ops/${orgId}/sheets/${business}/${path}`, body || {})
      .then((r) => { setNote(done(r)); load() })
      .catch((e) => setNote(e?.message || 'Something went wrong.'))
      .finally(() => setBusy(false))
  }
  return (
    <section className="v-panel" data-sheets-panel={business} aria-labelledby={`sheets-${business}`}>
      <div className="v-row"><h3 id={`sheets-${business}`} style={{ margin: 0 }}>Google Sheets · {b.label}</h3>
        <span className="v-tag" style={{ background: color, color: '#fff' }} data-sheets-health={b.health}>{label}</span></div>
      <p className="v-small v-muted" style={{ marginTop: 6 }}>A live, private copy of every real {business === 'consumer' ? 'lead' : 'submitted application'} in
        the &ldquo;{b.tab}&rdquo; tab. EvoSys is the record; the Sheet follows within about 15 seconds. Test data is never copied.</p>
      {b.last_error_text && <div className="v-alert v-alert--err" role="alert" data-sheets-error>{b.last_error_text} <span className="v-small">({ct(b.last_error_at)})</span></div>}
      {b.health === 'needs_credential' && <p className="v-small">The server is waiting for its Google service-account key (VOLT_SHEETS_SERVICE_ACCOUNT_JSON).</p>}
      {s.service_account && <p className="v-small">Share the spreadsheet with <code>{s.service_account}</code> as <b>Editor</b> (only this spreadsheet).</p>}
      <table className="v-table" style={{ marginTop: 8 }}><tbody>
        <tr><th scope="row">Spreadsheet</th><td>{b.spreadsheet_url ? <a href={b.spreadsheet_url} target="_blank" rel="noreferrer">Open Google Sheet ↗</a> : 'Not set'}</td></tr>
        <tr><th scope="row">Last successful sync</th><td data-sheets-last-success>{ct(b.last_success_at)}</td></tr>
        <tr><th scope="row">Waiting / failed / copied</th><td data-sheets-counts>{b.pending} / {b.failed} / {b.synced}{b.oldest_waiting_at ? ` · oldest waiting since ${ct(b.oldest_waiting_at)}` : ''}</td></tr>
        <tr><th scope="row">Last connection check</th><td>{b.verified_at ? `${ct(b.verified_at)} · ${b.verified_ok ? 'OK' : 'failed'}` : 'Not checked yet'}</td></tr>
        <tr><th scope="row">Missed-update sweep</th><td>{b.last_reconcile_at ? `${ct(b.last_reconcile_at)} · ${b.last_reconcile_count} re-queued` : 'Not run yet'} (automatic every 30 minutes)</td></tr>
      </tbody></table>
      {b.configured && <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
        <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={busy} onClick={() => act('verify', null, (r) => (r.ok ? 'Connection OK.' : r.text || 'Connection failed.'))} data-sheets-verify>Check connection</button>
        <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={busy || !b.failed} onClick={() => act('retry', null, (r) => `${r.requeued} record(s) queued again.`)} data-sheets-retry>Retry failed</button>
        <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={busy} onClick={() => act('reconcile', null, (r) => `${r.queued} recent record(s) queued to re-check.`)} data-sheets-reconcile>Re-check recent changes</button>
        {isOwner && <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={busy}
          onClick={() => { if (window.confirm('Copy EVERY existing real record into the Google Sheet? Do this once, after checking who the spreadsheet is shared with.')) act('reconcile', { full: true }, (r) => `${r.queued} record(s) queued for the first copy.`) }} data-sheets-backfill>Copy all existing records</button>}
        <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={busy} onClick={() => act('pause', { paused: !b.paused }, () => (b.paused ? 'Resumed.' : 'Paused. Changes keep queueing and will copy when resumed.'))} data-sheets-pause>{b.paused ? 'Resume' : 'Pause'}</button>
      </div>}
      {note && <p className="v-small" role="status" data-sheets-note>{note}</p>}
    </section>
  )
}
