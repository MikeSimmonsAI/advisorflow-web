// THE IMPORT BATCH LEDGER.
//
// Every universal import this organization has run: where it came from, who
// ran it and in what capacity, what it did, and - for a committed batch - a
// rollback that says exactly what it can and cannot undo before it does it.
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './intake.css'
import { OrgBanner, Pill, STATUS_LABEL, STATUS_TONE, Tile, errorText, fmt } from './intakeShared'

export default function ImportLedger() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [data, setData] = useState(null)
  const [summary, setSummary] = useState(null)
  const [err, setErr] = useState('')
  const [page, setPage] = useState(1)
  const open = params.get('batch')

  const load = useCallback(() => {
    api.get(`/intake/batches?page=${page}&per_page=25`).then(setData).catch(e => setErr(errorText(e)))
    api.get('/intake/contacts/summary').then(setSummary).catch(() => {})
  }, [page])
  useEffect(() => { load() }, [load])

  const ctx = data?.organization
  return (
    <div className="ic-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Import Ledger</h1>
          <p className="page-subtitle">Every import into this organization, what it did, and how to undo it safely.</p>
        </div>
        <div className="ic-header-actions">
          <button className="btn btn--primary" onClick={() => navigate('/imports/new')}>+ New import</button>
        </div>
      </header>
      {err && <div className="ic-blocker"><div className="ic-blocker-title">Select an organization first</div><p>{err}</p></div>}
      <OrgBanner ctx={ctx} />
      {summary && (
        <div className="ic-tiles ic-summary">
          <Tile label="Contacts" value={summary.contacts} tone="blue" sub="the organization's database" />
          <Tile label="Active leads" value={summary.active_leads} tone="green" sub="real opportunities" />
          <Tile label="Customers" value={summary.customers} tone="purple" />
          <Tile label="Previous customers" value={summary.previous_customers} tone="purple" />
          <Tile label="Renewals" value={summary.renewals} tone="purple" />
          <Tile label="SMS ready" value={summary.sms_ready} tone="green" />
          <Tile label="Email ready" value={summary.email_ready} tone="green" />
          <Tile label="Needs enrichment" value={summary.needs_enrichment} tone="muted" />
        </div>
      )}
      <section className="panel ic-panel">
        <div className="panel-header"><h2 className="panel-title">Batches</h2>
          <span className="panel-count">{fmt(data?.total)}</span></div>
        <div className="ic-table-wrap">
          <table className="ic-table ic-ledger">
            <thead><tr>
              <th>Batch ID</th><th>Date</th><th>File</th><th>Imported by</th><th>Source</th>
              <th>Submitted</th><th>Staged</th><th>Imported</th><th>Updated</th><th>Leads</th>
              <th>Skipped</th><th>Dupes</th><th>Review</th><th>Enrich</th><th>Blocked</th>
              <th>Failed</th><th>List / campaign</th><th>Status</th>
            </tr></thead>
            <tbody>
              {data && data.batches.length === 0 && <tr><td colSpan={18} className="ic-empty">No imports yet.</td></tr>}
              {(data?.batches || []).map(b => (
                <tr key={b.id} className="ic-clickrow" onClick={() => setParams({ batch: b.id })}>
                  <td className="mono ic-strong">{b.batch_code}</td>
                  <td className="ic-small">{b.created_at ? new Date(b.created_at).toLocaleString() : '—'}</td>
                  <td className="ic-small">{b.filename}</td>
                  <td className="ic-small">{b.imported_by}{b.acted_as_platform_owner ? <div className="ic-muted">God Admin (acting)</div> : null}</td>
                  <td className="ic-small">{b.source}</td>
                  <td className="mono">{fmt(b.rows_submitted)}</td>
                  <td className="mono">{fmt(b.rows_staged)}</td>
                  <td className="mono">{fmt(b.counts.imported)}</td>
                  <td className="mono">{fmt(b.counts.existing_updated)}</td>
                  <td className="mono">{fmt(b.counts.leads_created)}</td>
                  <td className="mono">{fmt(b.counts.skipped)}</td>
                  <td className="mono">{fmt(b.counts.duplicates)}</td>
                  <td className="mono">{fmt(b.counts.review)}</td>
                  <td className="mono">{fmt(b.counts.enrichment)}</td>
                  <td className="mono">{fmt(b.counts.blocked)}</td>
                  <td className="mono">{fmt(b.counts.failed)}</td>
                  <td className="ic-small">{[b.list_name, b.campaign_purpose].filter(Boolean).join(' · ') || '—'}</td>
                  <td><Pill tone={STATUS_TONE[b.status]}>{STATUS_LABEL[b.status] || b.status}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data && data.total > 25 && (
          <div className="ic-pager">
            <button className="btn btn--secondary btn--sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
            <span className="ic-muted">Page {page}</span>
            <button className="btn btn--secondary btn--sm" disabled={page * 25 >= data.total} onClick={() => setPage(p => p + 1)}>Next →</button>
          </div>
        )}
      </section>
      {open && <BatchDrawer batchId={open} onClose={() => setParams({})} onChanged={load} />}
    </div>
  )
}

function BatchDrawer({ batchId, onClose, onChanged }) {
  const navigate = useNavigate()
  const [b, setB] = useState(null)
  const [events, setEvents] = useState([])
  const [plan, setPlan] = useState(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    api.get(`/intake/batches/${batchId}`).then(setB).catch(e => setErr(errorText(e)))
    api.get(`/intake/batches/${batchId}/audit`).then(d => setEvents(d.events)).catch(() => {})
  }, [batchId])
  useEffect(() => { load(); setPlan(null) }, [load])

  const canRollback = b && ['committed', 'partially_committed', 'partially_rolled_back'].includes(b.status)
  const inProgress = b && !['committed', 'partially_committed', 'rolled_back', 'partially_rolled_back', 'cancelled'].includes(b.status)

  async function loadPlan() {
    setErr('')
    try { setPlan(await api.get(`/intake/batches/${batchId}/rollback-plan`)) }
    catch (e) { setErr(errorText(e)) }
  }
  async function doRollback() {
    setBusy(true); setErr('')
    try {
      const r = await api.post(`/intake/batches/${batchId}/rollback`, { confirm_batch_code: typed })
      setMsg(`Rollback complete: ${Object.entries(r.report.summary).map(([k, v]) => `${k.replace(':', ' ')} ${v}`).join(', ')}`)
      setPlan(null); load(); onChanged()
    } catch (e) { setErr(errorText(e)) }
    finally { setBusy(false) }
  }

  return (
    <div className="ic-drawer-bg" onClick={onClose}>
      <aside className="ic-drawer" onClick={e => e.stopPropagation()}>
        <button className="ic-drawer-x" onClick={onClose} aria-label="Close">✕</button>
        {!b ? <div className="empty-state">{err || 'Loading…'}</div> : <>
          <div className="ic-kicker">IMPORT BATCH</div>
          <h2 className="mono">{b.batch_code}</h2>
          <Pill tone={STATUS_TONE[b.status]}>{STATUS_LABEL[b.status] || b.status}</Pill>
          <dl className="ic-dl">
            <dt>Organization</dt><dd>{b.context?.organization_name}</dd>
            <dt>File</dt><dd>{b.filename}</dd>
            <dt>Source</dt><dd>{b.source}{b.source_detail ? ` — ${b.source_detail}` : ''}</dd>
            <dt>Imported by</dt><dd>{b.imported_by} · {b.acted_as_platform_owner ? 'God Admin acting on behalf of the organization' : b.role}</dd>
            <dt>Uploaded</dt><dd>{b.created_at ? new Date(b.created_at).toLocaleString() : '—'}</dd>
            <dt>Committed</dt><dd>{b.committed_at ? `${new Date(b.committed_at).toLocaleString()} (${b.commit_mode})` : 'Not committed'}</dd>
            <dt>Rows</dt><dd>{fmt(b.rows_submitted)} submitted · {fmt(b.rows_staged)} staged</dd>
            {b.tags?.length > 0 && <><dt>Tags</dt><dd>{b.tags.join(', ')}</dd></>}
          </dl>
          {inProgress && <button className="btn btn--primary" onClick={() => navigate(`/imports/${b.id}`)}>Continue this import →</button>}
          {b.commit_report && (
            <div className="ic-tiles ic-tiles--sm">
              <Tile label="Contacts created" value={b.commit_report.contacts_created} />
              <Tile label="Updated" value={b.commit_report.contacts_updated} />
              <Tile label="Leads created" value={b.commit_report.leads_created} tone="green" />
              <Tile label="Merged dupes" value={b.commit_report.file_duplicates_merged} tone="purple" />
              <Tile label="Failed" value={b.commit_report.failed} tone="red" />
            </div>
          )}
          {canRollback && (
            <div className="ic-subpanel">
              <h3>Rollback</h3>
              {!plan ? <button className="btn btn--secondary" onClick={loadPlan}>Check what a rollback would do</button> : <>
                <div className={plan.fully_reversible ? 'ic-note' : 'ic-warn'}>
                  {plan.fully_reversible ? 'Everything this batch wrote can be undone.' :
                    'Some records have been worked since the import. They will be archived or kept — never deleted.'}
                </div>
                <ul className="ic-small">
                  {Object.entries(plan.summary).map(([k, v]) => <li key={k}>{k.replace(':', ' → ')}: {fmt(v)}</li>)}
                </ul>
                {plan.items.filter(i => ['keep', 'archive'].includes(i.outcome)).slice(0, 25).map(i => (
                  <div key={i.version_id} className="ic-small ic-muted">{i.target_type} {i.target_id.slice(0, 8)} — {i.outcome}: {i.reason}</div>
                ))}
                <div className="ic-confirm">
                  <div>Type the batch ID <b className="mono">{b.batch_code}</b> to roll back:</div>
                  <input className="settings-input mono" value={typed} onChange={e => setTyped(e.target.value)} />
                  <button className="btn btn--danger" disabled={busy || typed.trim() !== b.batch_code} onClick={doRollback}>
                    {busy ? 'Rolling back…' : 'Roll back this import'}</button>
                </div>
              </>}
            </div>
          )}
          {b.rollback_report && <div className="ic-note">Rolled back {b.rolled_back_at ? new Date(b.rolled_back_at).toLocaleString() : ''}.</div>}
          {msg && <div className="ic-note">{msg}</div>}
          {err && <div className="ic-error">{err}</div>}
          <div className="ic-subpanel">
            <h3>Audit trail</h3>
            {events.length === 0 && <div className="ic-muted ic-small">No events.</div>}
            {events.map((e, i) => (
              <div key={i} className="ic-event">
                <span className="mono ic-small">{e.at ? new Date(e.at).toLocaleString() : ''}</span>
                <span className="ic-strong">{e.action.replace('intake.', '').replace(/_/g, ' ')}</span>
                <span className="ic-muted ic-small">{e.details?.imported_by}{e.details?.acting_as_platform_owner ? ' (God Admin, acting)' : ''}</span>
              </div>
            ))}
          </div>
        </>}
      </aside>
    </div>
  )
}
