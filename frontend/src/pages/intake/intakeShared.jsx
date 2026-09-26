// Shared pieces of the Import Center: the organization banner, number tiles,
// status vocabulary and the staged-row table. Kept in one module so the wizard
// and the ledger cannot describe the same batch in two different words.
import { useEffect, useState } from 'react'
import { api } from '../../api/client'

export const STATUS_LABEL = {
  mapping: 'Mapping fields', processing: 'Analyzing', ready_for_review: 'Ready for review',
  staged: 'Staged — not activated', committing: 'Importing', committed: 'Imported',
  partially_committed: 'Partially imported', failed: 'Failed', cancelled: 'Cancelled',
  rolled_back: 'Rolled back', partially_rolled_back: 'Partially rolled back',
  interrupted: 'Interrupted — resume', archived: 'Archived',
}
export const STATUS_TONE = {
  mapping: 'blue', processing: 'blue', ready_for_review: 'amber', staged: 'purple',
  committing: 'blue', committed: 'green', partially_committed: 'amber', failed: 'red',
  cancelled: 'muted', rolled_back: 'muted', partially_rolled_back: 'amber',
  interrupted: 'red', archived: 'muted',
}
export const INTAKE_LABEL = {
  ready: 'Ready', needs_review: 'Needs review', duplicate: 'Duplicate in file',
  existing_match: 'Existing record', blocked: 'Blocked', needs_enrichment: 'Needs enrichment',
  invalid: 'Invalid', failed: 'Failed', approved: 'Approved', imported: 'Imported',
  skipped: 'Skipped', parsed: 'Parsed',
}
export const INTAKE_TONE = {
  ready: 'green', needs_review: 'amber', duplicate: 'purple', existing_match: 'blue',
  blocked: 'red', needs_enrichment: 'muted', invalid: 'red', failed: 'red', approved: 'green',
  imported: 'green', skipped: 'muted',
}
export const SMS_LABEL = {
  ready: 'SMS ready', pending_validation: 'Pending validation', suppressed: 'Suppressed',
  dnc: 'DNC', landline: 'Landline', invalid: 'Invalid', no_phone: 'No phone',
  opted_out: 'Opted out', review: 'Review',
}
export const EMAIL_LABEL = {
  ready: 'Email ready', pending: 'Pending', invalid: 'Invalid', hard_bounce: 'Hard bounce',
  unsubscribed: 'Unsubscribed', suppressed: 'Suppressed', no_email: 'No email', review: 'Review',
}
export const REASON_LABEL = {
  duplicate_in_file: 'Duplicate of an earlier row',
  possible_duplicate_in_file: 'Possible duplicate in this file',
  existing_record_match: 'Matches a record already in the CRM',
  possible_existing_match: 'Possibly matches a CRM record',
  existing_record_dnc: 'Existing record is Do-Not-Contact',
  unrecognized_classification: 'Classification value not mapped',
  ambiguous_consent: 'Consent value is ambiguous',
  ambiguous_date: 'Date could be day/month or month/day',
  unparsed_date: 'Date not recognized',
  no_identity: 'No name, company, phone, email or ID',
  no_usable_channel: 'No usable phone or email',
  invalid_phone: 'Phone is not a valid number',
  invalid_mobile: 'Mobile is not a valid number',
  shares_phone_with_other_record: 'Shares a phone line with another person',
  phone_match_identity_unconfirmed: 'Same phone, identity not confirmed',
  email_shared_by_different_names: 'Same email, different names',
  zip_leading_zero_restored: 'ZIP leading zero restored',
  nonstandard_zip: 'Non-standard ZIP',
  unrecognized_state: 'State not recognized',
}

export function Pill({ tone = 'blue', children, title }) {
  return <span className={`ic-pill ic-pill--${tone}`} title={title}>{children}</span>
}

export function fmt(n) {
  if (n === null || n === undefined) return '—'
  return Number(n).toLocaleString()
}

export function OrgBanner({ ctx }) {
  if (!ctx) return null
  const owner = ctx.acting_as_platform_owner
  return (
    <div className={`ic-orgbanner ${owner ? 'ic-orgbanner--owner' : ''}`}>
      <div>
        <div className="ic-orgbanner-kicker">IMPORTING INTO</div>
        <div className="ic-orgbanner-name">{ctx.organization_name}</div>
      </div>
      <div className="ic-orgbanner-who">
        <div><span className="ic-muted">Imported by</span> {ctx.actor_name}</div>
        <div>
          <span className="ic-muted">Role</span>{' '}
          {owner ? 'God Admin — acting on behalf of this organization' : roleName(ctx.role)}
        </div>
        <div className="ic-muted ic-small">Every record imported here belongs only to this organization.</div>
      </div>
    </div>
  )
}

function roleName(r) {
  return ({ org_admin: 'Customer Admin', super_admin: 'Super Admin', god_admin: 'God Admin',
            location_admin: 'Location Admin', regional_admin: 'Regional Admin',
            corp_admin: 'Corporate Admin' })[r] || (r || 'User')
}

export function Tile({ label, value, sub, tone = 'blue', onClick, active }) {
  return (
    <button type="button"
            className={`ic-tile ic-tile--${tone} ${onClick ? 'ic-tile--click' : ''} ${active ? 'ic-tile--active' : ''}`}
            onClick={onClick} disabled={!onClick}>
      <div className="ic-tile-value">{fmt(value ?? 0)}</div>
      <div className="ic-tile-label">{label}</div>
      {sub && <div className="ic-tile-sub">{sub}</div>}
    </button>
  )
}

export function errorText(err) {
  if (!err) return ''
  if (err.detail && Array.isArray(err.detail.problems)) return err.detail.problems.join(' ')
  if (err.detail && typeof err.detail === 'object' && err.detail.problems)
    return err.detail.problems.join(' ')
  return err.message || String(err)
}

// ── staged rows ─────────────────────────────────────────────────────────────

export function RowTable({ batchId, category, title, catalog = [], editable = false,
                           onChanged, compact = false }) {
  const [data, setData] = useState(null)
  const [page, setPage] = useState(1)
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(null)
  const [err, setErr] = useState('')
  const perPage = compact ? 10 : 25

  useEffect(() => { setPage(1) }, [category, q])
  useEffect(() => {
    if (!batchId) return
    let live = true
    const params = new URLSearchParams({ page, per_page: perPage })
    if (category) params.set('category', category)
    if (q) params.set('search', q)
    api.get(`/intake/batches/${batchId}/rows?${params}`)
      .then(d => { if (live) setData(d) })
      .catch(e => { if (live) setErr(errorText(e)) })
    return () => { live = false }
  }, [batchId, category, page, q, perPage, busy])

  async function act(row, body) {
    setBusy(row.id); setErr('')
    try {
      await api.patch(`/intake/batches/${batchId}/rows/${row.id}`, body)
      onChanged && onChanged()
    } catch (e) { setErr(errorText(e)) }
    finally { setBusy(null) }
  }

  const rows = data?.rows || []
  const pages = data ? Math.max(1, Math.ceil(data.total / perPage)) : 1
  return (
    <div className="ic-rows">
      <div className="ic-rows-head">
        <div className="ic-rows-title">{title} <span className="ic-muted">{data ? fmt(data.total) : ''} rows</span></div>
        <form onSubmit={e => { e.preventDefault(); setQ(search.trim()) }} className="ic-rows-search">
          <input className="settings-input" placeholder="Search name, company, email, phone, ID"
                 value={search} onChange={e => setSearch(e.target.value)} />
        </form>
      </div>
      {err && <div className="ic-error">{err}</div>}
      <div className="ic-table-wrap">
        <table className="ic-table">
          <thead>
            <tr>
              <th>Row</th><th>Name / Company</th><th>Phone</th><th>Email</th>
              <th>Status</th><th>Classification</th><th>Outreach</th><th>Why</th>
              {editable && <th>Decision</th>}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr><td colSpan={editable ? 9 : 8} className="ic-empty">No rows in this category.</td></tr>
            )}
            {rows.map(r => (
              <tr key={r.id} className={busy === r.id ? 'ic-row--busy' : ''}>
                <td className="mono">{r.row_number}</td>
                <td>
                  <div className="ic-strong">{[r.first_name, r.last_name].filter(Boolean).join(' ') || r.company || '—'}</div>
                  {(r.first_name || r.last_name) && r.company && <div className="ic-muted ic-small">{r.company}</div>}
                  {r.source_record_id && <div className="ic-muted ic-small mono">ID {r.source_record_id}</div>}
                </td>
                <td className="mono ic-small">{r.phone || <span className="ic-muted">{r.phone_raw || '—'}</span>}
                  {r.phone_line_type && r.phone && <div className="ic-muted">{r.phone_line_type}</div>}</td>
                <td className="mono ic-small">{r.email || <span className="ic-muted">{r.email_raw || '—'}</span>}</td>
                <td><Pill tone={INTAKE_TONE[r.intake_status]}>{INTAKE_LABEL[r.intake_status] || r.intake_status}</Pill>
                  {r.dup_of_row && <div className="ic-muted ic-small">of row {r.dup_of_row}</div>}</td>
                <td>
                  {editable && r.intake_status !== 'imported' ? (
                    <select className="filter-select ic-select-sm" value={r.classification || ''}
                            onChange={e => act(r, { classification: e.target.value })}>
                      {catalog.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
                    </select>
                  ) : <span>{labelFor(catalog, r.classification)}</span>}
                  <div className="ic-muted ic-small">
                    {r.creates_lead ? 'Contact + Lead' : 'Contact only'}
                    {r.classification_raw ? ` · “${r.classification_raw}”` : ''}
                  </div>
                </td>
                <td className="ic-small">
                  <div>{SMS_LABEL[r.sms_status] || r.sms_status}</div>
                  <div>{EMAIL_LABEL[r.email_status] || r.email_status}</div>
                </td>
                <td className="ic-small">
                  {(r.reasons || []).slice(0, 3).map((x, i) => (
                    <div key={i}>{REASON_LABEL[x.code] || x.code}{x.detail ? <span className="ic-muted"> — {x.detail}</span> : null}</div>
                  ))}
                </td>
                {editable && (
                  <td className="ic-decide">
                    {r.intake_status === 'imported' ? <span className="ic-muted">Imported</span> : (
                      <>
                        {(r.match_type === 'possible' || r.match_type === 'exact' || r.intake_status === 'duplicate') && (
                          <select className="filter-select ic-select-sm" value={r.duplicate_resolution || 'review'}
                                  onChange={e => act(r, { duplicate_resolution: e.target.value })}>
                            {r.intake_status === 'duplicate'
                              ? <option value="merge">Merge into earlier row</option>
                              : <option value="update_existing">Update existing</option>}
                            <option value="keep_separate">Keep separate</option>
                            <option value="skip_incoming">Skip incoming</option>
                            <option value="review">Decide later</option>
                          </select>
                        )}
                        <div className="ic-decide-btns">
                          {!['approved', 'invalid'].includes(r.intake_status) &&
                            <button className="btn btn--secondary btn--sm" onClick={() => act(r, { approve: true })}>Approve</button>}
                          {r.intake_status !== 'skipped' &&
                            <button className="btn btn--secondary btn--sm" onClick={() => act(r, { skip: true })}>Skip</button>}
                          {['approved', 'skipped'].includes(r.intake_status) &&
                            <button className="btn btn--secondary btn--sm" onClick={() => act(r, { approve: false })}>Undo</button>}
                        </div>
                      </>
                    )}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="ic-pager">
          <button className="btn btn--secondary btn--sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>← Prev</button>
          <span className="ic-muted">Page {page} of {fmt(pages)}</span>
          <button className="btn btn--secondary btn--sm" disabled={page >= pages} onClick={() => setPage(p => p + 1)}>Next →</button>
        </div>
      )}
    </div>
  )
}

function labelFor(catalog, key) {
  const c = catalog.find(x => x.key === key)
  return c ? c.label : (key || '—')
}
