// ONE CONTACT, READ-ONLY, WITH ONE EXPLICIT ACTION.
//
// The drawer shows what GET /intake/contacts/{id} says about a contact and
// offers two writes, each behind a confirm: "Promote to Lead" and "Delete
// contact" (a linked lead is kept; a DNC number stays suppressed). It
// never sends anything, never enrolls anything and never infers consent -
// SMS consent reads "No" unless the server says true.
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { useTerminology } from '../../terminology'
import { TierBadge } from '../../components/StatusBadge'
import {
  classTone, displayName, emailTone, fieldPairs, fieldValue, fmtDate, humanize,
  initials, personName, smsTone,
} from './contactsShared'

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'activity', label: 'Activity' },
  { key: 'related', label: 'Related' },
]

function Row({ label, children }) {
  return (
    <div className="cw-kv">
      <dt>{label}</dt>
      <dd>{children === null || children === undefined || children === '' ? '—' : children}</dd>
    </div>
  )
}

function Section({ title, children, note }) {
  return (
    <section className="cw-dsec">
      <h4>{title}</h4>
      {note ? <p className="cw-dsec-note">{note}</p> : null}
      <dl className="cw-kvs">{children}</dl>
    </section>
  )
}

function FieldBlock({ title, blob }) {
  const pairs = fieldPairs(blob)
  if (!pairs.length) return null
  return (
    <Section title={title}>
      {pairs.map(([k, v]) => <Row key={k} label={humanize(k)}>{fieldValue(v)}</Row>)}
    </Section>
  )
}

function PromoteDialog({ contact, onClose, onPromoted }) {
  const terminology = useTerminology()
  const tiers = terminology.tiers || []
  const [tier, setTier] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  async function submit() {
    setBusy(true); setErr(null)
    const body = {}
    if (tier) body.tier = tier
    if (note.trim()) body.note = note.trim()
    try {
      const r = await api.post(`/intake/contacts/${contact.id}/promote`, body)
      onPromoted({ lead_id: r && r.lead_id, held_over_capacity: !!(r && r.held_over_capacity), tier: r && r.tier })
    } catch (e) {
      const d = e && e.detail
      if (e && e.status === 409) {
        const leadId = (d && typeof d === 'object' && d.lead_id) || null
        onPromoted({ lead_id: leadId, already: true })
        return
      }
      if (e && e.status === 403) setErr('You do not have permission to create leads in this workspace.')
      else if (e && e.status === 402) setErr(e.message || 'Your plan does not allow creating this lead right now.')
      else if (e && e.status === 404) setErr('This contact is not in the current workspace.')
      else if (e && e.status === 422) setErr(typeof d === 'string' ? d : (e.message || 'The lead details were not accepted.'))
      else setErr((e && e.message) || 'Could not promote this contact.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="cw-modal-backdrop" role="presentation" onClick={busy ? undefined : onClose}>
      <div className="cw-modal" role="dialog" aria-modal="true" aria-labelledby="cw-promote-title"
           onClick={e => e.stopPropagation()}>
        <h3 id="cw-promote-title">Promote to Lead</h3>
        <p className="cw-modal-lead">
          <strong>{displayName(contact)}</strong>
          {contact.company && personName(contact) ? <> · {contact.company}</> : null}
        </p>
        <div className="cw-callout">
          This creates one Lead for active sales work. It does not send any message or enroll any follow-up.
        </div>
        <label className="cw-field">
          <span>Tier</span>
          <select value={tier} onChange={e => setTier(e.target.value)} disabled={busy}>
            <option value="">Workspace default (first tier)</option>
            {tiers.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <label className="cw-field">
          <span>Note <em>(optional)</em></span>
          <textarea rows={3} value={note} maxLength={2000} disabled={busy}
                    onChange={e => setNote(e.target.value)}
                    placeholder="Why this contact is ready for sales work" />
        </label>
        {err ? <div className="cw-error" role="alert">{err}</div> : null}
        <div className="cw-modal-actions">
          <button className="cw-btn" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="cw-btn cw-btn--primary" onClick={submit} disabled={busy}>
            {busy ? 'Promoting…' : 'Create one Lead'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function ContactDrawer({ contactId, onClose, onChanged }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState('overview')
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteErr, setDeleteErr] = useState(null)

  const load = useCallback(() => {
    let live = true
    setLoading(true); setErr(null)
    api.get(`/intake/contacts/${contactId}`)
      .then(d => { if (live) setData(d) })
      .catch(e => {
        if (!live) return
        if (e && e.status === 404) setErr('This contact was not found in the current workspace.')
        else if (e && e.status === 403) setErr('You do not have access to the contact database.')
        else setErr((e && e.message) || 'Could not load this contact.')
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [contactId])

  useEffect(() => {
    setTab('overview'); setResult(null); setConfirming(false); setData(null); setDeleteErr(null)
    return load()
  }, [load])

  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape' && !confirming) onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose, confirming])

  async function handleDelete() {
    if (deleting) return
    const name = displayName((data && data.contact) || {})
    if (!window.confirm(`Delete ${name}? This cannot be undone.\n\n`
      + 'If this contact became a lead, the lead is kept. A do-not-contact number stays suppressed.')) return
    setDeleting(true); setDeleteErr(null)
    try {
      await api.delete(`/intake/contacts/${contactId}`)
      onChanged && onChanged()
      onClose()
    } catch (e) {
      if (e && e.status === 403) setDeleteErr('You do not have permission to delete contacts in this workspace.')
      else if (e && e.status === 404) setDeleteErr('This contact is not in the current workspace (it may already be deleted).')
      else setDeleteErr((e && e.message) || 'Could not delete this contact.')
    } finally {
      setDeleting(false)
    }
  }

  function handlePromoted(r) {
    setConfirming(false)
    setResult(r)
    load()
    onChanged && onChanged()
  }

  const c = (data && data.contact) || {}
  const prov = (data && data.provenance) || {}
  const ca = (data && data.contactability) || {}
  const lead = data && data.lead
  const history = (data && Array.isArray(data.history)) ? data.history : []
  const leadId = (lead && lead.id) || c.lead_id || (result && result.lead_id) || null
  const address = [c.street_address, c.address_line2].filter(Boolean).join(', ')
  const cityLine = [c.city, [c.state, c.zip_code].filter(Boolean).join(' ')].filter(Boolean).join(', ')

  return (
    <>
      <div className="cw-drawer-scrim" onClick={onClose} />
      <aside className="cw-drawer" role="dialog" aria-label="Contact detail">
        <div className="cw-drawer-top">
          <span className="cw-drawer-kicker">Contact</span>
          <button className="cw-icon-btn" onClick={onClose} aria-label="Close">×</button>
        </div>

        {loading && !data ? <div className="cw-state">Loading contact…</div> : null}
        {err ? <div className="cw-error" role="alert">{err}</div> : null}

        {data ? (
          <>
            <div className="cw-drawer-id">
              <div className="cw-avatar cw-avatar--lg">{initials(c)}</div>
              <div className="cw-drawer-name">
                <h2>{displayName(c)}</h2>
                {personName(c) && c.company ? <p>{c.company}</p> : null}
                {c.job_title ? <p className="cw-muted">{c.job_title}</p> : null}
                <div className="cw-badges">
                  {c.record_class ? (
                    <span className={`badge badge--${classTone(c.record_class)} cw-class cw-class--${c.record_class}`}>
                      {humanize(c.record_class)}
                    </span>
                  ) : null}
                  {c.needs_enrichment || c.lifecycle === 'needs_enrichment'
                    ? <span className="badge badge--amber">Needs enrichment</span> : null}
                  {leadId ? <span className="badge badge--blue">Lead</span> : null}
                </div>
              </div>
            </div>

            <div className="cw-drawer-actions">
              {leadId ? (
                <Link className="cw-btn cw-btn--primary" to={`/leads/${leadId}`}>Open lead</Link>
              ) : (
                <button className="cw-btn cw-btn--primary" onClick={() => setConfirming(true)}>
                  Promote to Lead
                </button>
              )}
              <button className="cw-btn cw-btn--danger" onClick={handleDelete} disabled={deleting}
                      data-testid="contact-delete">
                {deleting ? 'Deleting…' : 'Delete contact'}
              </button>
            </div>
            {deleteErr ? <div className="cw-error" role="alert">{deleteErr}</div> : null}

            {result ? (
              <div className={`cw-notice ${result.already ? 'cw-notice--info' : 'cw-notice--ok'}`} role="status">
                {result.already
                  ? <>This contact is already a lead.</>
                  : <>Lead created. No message was sent and nothing was enrolled.</>}
                {result.held_over_capacity
                  ? <> It is held over your plan's lead capacity until space is available.</> : null}
                {leadId ? <> <Link to={`/leads/${leadId}`}>Open lead →</Link></> : null}
              </div>
            ) : null}

            <nav className="cw-tabs cw-tabs--drawer" role="tablist">
              {TABS.map(t => (
                <button key={t.key} role="tab" aria-selected={tab === t.key}
                        className={`cw-tab ${tab === t.key ? 'cw-tab--active' : ''}`}
                        onClick={() => setTab(t.key)}>
                  {t.label}{t.key === 'activity' && history.length ? <span className="cw-tab-count">{history.length}</span> : null}
                </button>
              ))}
            </nav>

            <div className="cw-drawer-body">
              {tab === 'overview' ? (
                <>
                  <Section title="Contact information">
                    <Row label="Name">{personName(c) || null}</Row>
                    <Row label="Company">{c.company}</Row>
                    <Row label="Email">
                      {c.email ? <>{c.email} {c.email_status ? <span className={`badge badge--${emailTone(c.email_status)}`}>{humanize(c.email_status)}</span> : null}</> : null}
                    </Row>
                    <Row label="Phone">{c.phone}</Row>
                    <Row label="Mobile">{c.mobile_phone}</Row>
                    <Row label="Owner">{c.owner_name}</Row>
                    <Row label="Last activity">{c.last_activity_date ? fmtDate(c.last_activity_date) : null}</Row>
                    <Row label="Created">{c.created_at ? fmtDate(c.created_at) : null}</Row>
                    <Row label="Updated">{c.updated_at ? fmtDate(c.updated_at) : null}</Row>
                  </Section>

                  <Section title="Address">
                    <Row label="Street">{address}</Row>
                    <Row label="City / State / ZIP">{cityLine}</Row>
                    <Row label="Country">{c.country}</Row>
                  </Section>

                  <Section title="Contactability"
                           note={ca.note || 'Having a phone number is not SMS permission.'}>
                    <Row label="Phone on file">{ca.phone_present === true ? 'Yes' : ca.phone_present === false ? 'No' : null}</Row>
                    <Row label="SMS status">
                      {ca.sms_status ? <span className={`badge badge--${smsTone(ca.sms_status)}`}>{humanize(ca.sms_status)}</span> : null}
                    </Row>
                    <Row label="SMS consent">
                      {ca.sms_consent === true
                        ? <span className="badge badge--green">Yes</span>
                        : <span className="badge badge--neutral">No</span>}
                    </Row>
                    <Row label="Email on file">{ca.email_present === true ? 'Yes' : ca.email_present === false ? 'No' : null}</Row>
                    <Row label="Email status">
                      {ca.email_status ? <span className={`badge badge--${emailTone(ca.email_status)}`}>{humanize(ca.email_status)}</span> : null}
                    </Row>
                  </Section>

                  <FieldBlock title="Custom fields" blob={c.custom_fields} />
                  <FieldBlock title="Industry fields" blob={c.vertical_fields} />
                </>
              ) : null}

              {tab === 'activity' ? (
                history.length ? (
                  <ol className="cw-history">
                    {history.map((h, i) => {
                      const pairs = fieldPairs(h.details)
                      return (
                        <li key={i}>
                          <div className="cw-history-head">
                            <strong>{humanize(String(h.action || '').replace(/\./g, ' '))}</strong>
                            <span>{fmtDate(h.at)}</span>
                          </div>
                          {pairs.length ? (
                            <div className="cw-history-details">
                              {pairs.map(([k, v]) => <span key={k}>{humanize(k)}: {fieldValue(v)}</span>)}
                            </div>
                          ) : null}
                        </li>
                      )
                    })}
                  </ol>
                ) : <div className="cw-state">No recorded activity for this contact yet.</div>
              ) : null}

              {tab === 'related' ? (
                <>
                  <Section title="Lead">
                    {lead ? (
                      <>
                        <Row label="Lead"><Link to={`/leads/${lead.id}`}>Open lead →</Link></Row>
                        <Row label="Status">{lead.status ? humanize(lead.status) : null}</Row>
                        <Row label="Tier">{lead.tier ? <TierBadge tier={lead.tier} /> : null}</Row>
                        <Row label="Created">{lead.created_at ? fmtDate(lead.created_at) : null}</Row>
                      </>
                    ) : leadId ? (
                      <Row label="Lead"><Link to={`/leads/${leadId}`}>Open lead →</Link></Row>
                    ) : (
                      <Row label="Lead">Not promoted</Row>
                    )}
                  </Section>

                  <Section title="Provenance">
                    <Row label="Source">{prov.source ? humanize(prov.source) : null}</Row>
                    <Row label="Source detail">{prov.source_detail}</Row>
                    <Row label="Source system">{prov.source_system}</Row>
                    <Row label={prov.source_system && /hubspot/i.test(prov.source_system) ? 'HubSpot record ID' : 'Source record ID'}>
                      {prov.source_record_id ? <span className="mono">{prov.source_record_id}</span> : null}
                    </Row>
                    <Row label="Batch">{prov.batch_code ? <span className="mono">{prov.batch_code}</span> : null}</Row>
                    <Row label="File">{prov.batch_filename}</Row>
                    <Row label="Imported at">{prov.imported_at ? fmtDate(prov.imported_at) : null}</Row>
                    <Row label="Imported by">{prov.imported_by_name}</Row>
                  </Section>

                  {fieldPairs(c.alternate_source_ids).length ? (
                    <FieldBlock title="Other source IDs" blob={c.alternate_source_ids} />
                  ) : null}
                  <FieldBlock title="Original source fields" blob={c.source_fields} />
                </>
              ) : null}
            </div>
          </>
        ) : null}
      </aside>

      {confirming && data ? (
        <PromoteDialog contact={c} onClose={() => setConfirming(false)} onPromoted={handlePromoted} />
      ) : null}
    </>
  )
}
