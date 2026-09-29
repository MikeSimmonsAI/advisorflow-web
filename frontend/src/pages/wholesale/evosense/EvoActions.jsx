/* EVOSENSE — WHAT YOU CAN DO (server-computed next actions).
 *
 * Renders GET /wholesale/evosense/properties/{id} -> summary, readiness, actions.
 * Every action comes from the server with enabled / reason_if_disabled /
 * effect_description / endpoint, so this panel never invents a capability:
 * a disabled action says WHY, an enabled one says WHAT HAPPENS AFTER.
 *
 * Manual forms (owner of record, owner phone/email, dismiss reason) post to
 * the endpoint the server named. Nothing here sends a message or records
 * consent.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import './evo-actions.css'

const DISMISS_LABEL = { IGNORE: 'Not worth pursuing', BAD_FIT: 'Bad fit for our buy box',
  WRONG_OWNER: 'Wrong owner', NOT_ACTUALLY_DISTRESSED: 'Not actually distressed' }

function Field({ label, children }) {
  return <label className="evo-act__field"><span>{label}</span>{children}</label>
}

export default function EvoActions({ d, propertyId, busy, act, onPromote }) {
  const [open, setOpen] = useState(null)
  const [owner, setOwner] = useState({ name: '', owner_type: 'individual', mailing_street: '', mailing_zip: '' })
  const [contact, setContact] = useState({ kind: 'phone', value: '', person_name: '' })
  const [dismiss, setDismiss] = useState({ kind: 'IGNORE', reason: '' })
  if (!d || !d.actions) return null
  const s = d.summary || {}
  const r = d.readiness || { required: [], recommended: [] }
  const list = d.actions.filter((a) => a.key !== 'seller_contact')
  const seller = d.actions.find((a) => a.key === 'seller_contact')

  async function run(a) {
    if (a.key === 'promote') return onPromote()
    if (['add_owner', 'add_contact_manual', 'dismiss', 'resolve_identity'].includes(a.key)) {
      setOpen(open === a.key ? null : a.key); return null
    }
    return act(() => api.post(a.endpoint, a.payload || {}), (x) => {
      if (a.key === 'run_enrichment') return `${x.decision || 'Done'}: ${(x.reasons || [])[0] || ''}`
      if (a.key === 'acknowledge_handoff') return 'Hand-off acknowledged — it is yours.'
      return 'Done.'
    })
  }

  function button(a) {
    const cls = `evo-btn ${a.primary && a.enabled ? 'evo-btn--primary' : 'evo-btn--secondary'}`
    if (a.method === 'LINK') return <Link className={cls} to={a.endpoint} data-testid={`act-${a.key}`}>{a.label}</Link>
    if (a.method === 'ANCHOR') return <a className="evo-btn evo-btn--ghost" href={a.endpoint} data-testid={`act-${a.key}`}>{a.label}</a>
    return (
      <button type="button" className={cls} disabled={!a.enabled || busy} data-testid={`act-${a.key}`}
              aria-expanded={['add_owner', 'add_contact_manual', 'dismiss', 'resolve_identity'].includes(a.key) ? open === a.key : undefined}
              onClick={() => run(a)}>{a.label}</button>
    )
  }

  const review = (d.identity_reviews || [])[0]

  return (
    <section className="evo-act" id="actions" aria-label="What you can do">
      <div className="evo-act__summary">
        <div><dt>What was found</dt><dd>{s.found}</dd></div>
        <div><dt>Why it matters</dt><dd>{s.why_it_matters}</dd></div>
        <div><dt>What's missing</dt><dd>{(s.missing || []).length ? (
          <ul>{s.missing.map((m) => <li key={m}>{m}</li>)}</ul>) : 'Nothing essential'}</dd></div>
        <div className="is-next"><dt>Next step</dt><dd><strong>{s.next}</strong>
          {s.after ? <small>After: {s.after}</small> : null}</dd></div>
      </div>

      <div className="evo-act__cols">
        <div>
          <h3 className="evo-act__h">What you can do</h3>
          {d.dismissed ? (
            <p className="evo-act__note">Dismissed ({DISMISS_LABEL[d.dismissed.kind] || d.dismissed.kind}): “{d.dismissed.reason}”.
              The record and its evidence are kept.</p>
          ) : null}
          <ul className="evo-act__list">
            {list.map((a) => (
              <li key={a.key} className={`evo-act__item${a.enabled ? '' : ' is-disabled'}${a.primary && a.enabled ? ' is-primary' : ''}`}>
                <div className="evo-act__btn">{button(a)}</div>
                <div className="evo-act__why">
                  {a.enabled ? <span>{a.effect_description}</span>
                    : <span className="evo-act__blocked"><strong>Can't yet:</strong> {a.reason_if_disabled}</span>}
                </div>

                {open === 'add_owner' && a.key === 'add_owner' ? (
                  <form className="evo-act__form" onSubmit={async (e) => {
                    e.preventDefault()
                    const body = { ...owner, mailing_street: owner.mailing_street || null, mailing_zip: owner.mailing_zip || null }
                    const x = await act(() => api.post(a.endpoint, body), () => 'Owner of record saved (entered by you).')
                    if (x) setOpen(null)
                  }}>
                    <Field label="Owner name (as on the record)">
                      <input required minLength={2} value={owner.name} onChange={(e) => setOwner({ ...owner, name: e.target.value })} data-testid="owner-name" />
                    </Field>
                    <Field label="Owner type">
                      <select value={owner.owner_type} onChange={(e) => setOwner({ ...owner, owner_type: e.target.value })}>
                        {['individual', 'trust', 'llc', 'estate', 'corporation', 'unknown'].map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </Field>
                    <Field label="Mailing street (optional)">
                      <input value={owner.mailing_street} onChange={(e) => setOwner({ ...owner, mailing_street: e.target.value })} />
                    </Field>
                    <Field label="Mailing ZIP (optional)">
                      <input value={owner.mailing_zip} onChange={(e) => setOwner({ ...owner, mailing_zip: e.target.value })} />
                    </Field>
                    <button type="submit" className="evo-btn evo-btn--primary" disabled={busy} data-testid="owner-save">Save owner</button>
                  </form>
                ) : null}

                {open === 'add_contact_manual' && a.key === 'add_contact_manual' ? (
                  <form className="evo-act__form" onSubmit={async (e) => {
                    e.preventDefault()
                    const x = await act(() => api.post(a.endpoint, { ...contact, person_name: contact.person_name || null, role: 'owner' }),
                      () => 'Contact saved (manual, unverified). No consent recorded, nothing sent.')
                    if (x) { setOpen(null); setContact({ ...contact, value: '' }) }
                  }}>
                    <Field label="Type">
                      <select value={contact.kind} onChange={(e) => setContact({ ...contact, kind: e.target.value })}>
                        <option value="phone">Phone</option><option value="email">Email</option>
                      </select>
                    </Field>
                    <Field label={contact.kind === 'phone' ? '10-digit US phone' : 'Email'}>
                      <input required value={contact.value} onChange={(e) => setContact({ ...contact, value: e.target.value })} data-testid="contact-value" />
                    </Field>
                    <Field label="Person's name (optional)">
                      <input value={contact.person_name} onChange={(e) => setContact({ ...contact, person_name: e.target.value })} data-testid="contact-name" />
                    </Field>
                    <p className="evo-act__fine">A number you enter is not permission to text. Consent, DNC and quiet hours still apply.</p>
                    <button type="submit" className="evo-btn evo-btn--primary" disabled={busy} data-testid="contact-save">Save contact</button>
                  </form>
                ) : null}

                {open === 'dismiss' && a.key === 'dismiss' ? (
                  <form className="evo-act__form" onSubmit={async (e) => {
                    e.preventDefault()
                    const x = await act(() => api.post(a.endpoint, dismiss), () => 'Dismissed. Reason recorded.')
                    if (x) setOpen(null)
                  }}>
                    <Field label="Why">
                      <select value={dismiss.kind} onChange={(e) => setDismiss({ ...dismiss, kind: e.target.value })}>
                        {((a.payload && a.payload.kinds) || Object.keys(DISMISS_LABEL)).map((k) => <option key={k} value={k}>{DISMISS_LABEL[k] || k}</option>)}
                      </select>
                    </Field>
                    <Field label="Reason (required)">
                      <input required value={dismiss.reason} onChange={(e) => setDismiss({ ...dismiss, reason: e.target.value })} />
                    </Field>
                    <button type="submit" className="evo-btn evo-btn--secondary" disabled={busy}>Dismiss</button>
                  </form>
                ) : null}

                {open === 'resolve_identity' && a.key === 'resolve_identity' && review ? (
                  <div className="evo-act__form">
                    <p className="evo-act__fine">{review.reason}</p>
                    <button type="button" className="evo-btn evo-btn--secondary" disabled={busy}
                            onClick={() => act(() => api.post(a.endpoint, { action: 'merge', property_id: propertyId }), () => 'Merged into this property.')}>
                      Same property — merge</button>
                    <button type="button" className="evo-btn evo-btn--secondary" disabled={busy}
                            onClick={() => act(() => api.post(a.endpoint, { action: 'new' }), () => 'Kept as a separate property.')}>
                      Different property — keep separate</button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h3 className="evo-act__h">Ready for Deal Operations?</h3>
          <ul className="evo-act__check" data-testid="readiness">
            {r.required.map((x) => (
              <li key={x.key} className={x.met ? 'is-met' : 'is-unmet'}>
                <span aria-hidden="true">{x.met ? '✓' : '✕'}</span>
                <div><strong>{x.label}</strong> <em>required</em>{x.detail ? <small>{x.detail}</small> : null}</div>
              </li>
            ))}
            {r.recommended.map((x) => (
              <li key={x.key} className={x.met ? 'is-met' : 'is-soft'}>
                <span aria-hidden="true">{x.met ? '✓' : '○'}</span>
                <div><strong>{x.label}</strong> <em>recommended</em>{x.detail ? <small>{x.detail}</small> : null}</div>
              </li>
            ))}
          </ul>
          {seller ? <p className="evo-act__fine">{seller.effect_description}</p> : null}
          {d.property && d.property.promoted_deal_id ? (
            <p className="evo-act__fine">Promoted. The deal continues in Deal Operations; this page keeps the discovery record.</p>
          ) : null}
        </div>
      </div>
    </section>
  )
}
