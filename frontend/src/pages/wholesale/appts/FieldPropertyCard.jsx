/* THE FIELD PROPERTY CARD (build list #11). What a field specialist or
 * canvasser sees for a property on one of their appointments: one screen, in
 * the order they need it at the door. No scores, no money, no admin buttons -
 * the server refuses those to a field login anyway. */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { EvoApp, Panel, Tag } from '../ds/ds'
import PropertyPhoto from '../evosense/PropertyPhoto'
import { ApptCard } from './WholesaleAppointments'
import './appts.css'

function Links({ links }) {
  if (!links) return null
  const items = [[links.maps_url, 'Google Maps'], [links.street_view_url, 'Street View'],
    [links.zillow_url, 'Zillow'], [links.redfin_url, 'Redfin'],
    [links.county_record_url, links.county_record_label || 'County record']].filter(([u]) => u)
  if (!items.length) return null
  return (
    <div className="ap-actions">
      {items.map(([u, l]) => <a key={u} className="evo-btn evo-btn--ghost evo-btn--sm" href={u} target="_blank" rel="noreferrer">{l} ↗</a>)}
    </div>
  )
}

export default function FieldPropertyCard({ d, propertyId }) {
  const [appts, setAppts] = useState(null)
  useEffect(() => {
    api.get(`/wholesale/appointments?when=all&property=${encodeURIComponent(propertyId)}`)
      .then((r) => setAppts(r.items || [])).catch(() => setAppts([]))
  }, [propertyId])
  const p = d.property
  const ph = (d.facts && d.facts.physical) || {}
  const place = [p.city, p.state, p.zip_code].filter(Boolean).join(', ')
  const live = (d.contacts || []).filter((c) => c.status === 'active' || !c.status)
  const phones = live.filter((c) => c.kind === 'phone').slice(0, 2)
  const email = live.find((c) => c.kind === 'email')
  const owners = (d.owners || []).filter((o) => o.name)
  const signals = (d.signals || []).filter((s) => s.scored !== false)
  const size = [ph.bedrooms ? `${ph.bedrooms} bd` : null, ph.bathrooms ? `${ph.bathrooms} ba` : null,
    ph.square_feet ? `${Number(ph.square_feet).toLocaleString()} sq ft` : null,
    ph.year_built ? `built ${ph.year_built}` : null].filter(Boolean).join(' · ')
  const replace = (a) => setAppts((xs) => xs.map((x) => (x.id === a.id ? a : x)))

  return (
    <EvoApp world="operations">
      <Link className="evo-crumb" to="/wholesale/appointments">← My appointments</Link>
      <div className="fc-grid">
        <div className="fc-photo"><PropertyPhoto propertyId={propertyId} address={p.address} isTest={p.is_test} /></div>
        <div className="fc-head">
          <h1 className="fc-addr">{p.address}{p.unit ? ` #${p.unit}` : ''}</h1>
          <div className="evo-muted">{place}</div>
          {size ? <div className="fc-size">{size}</div> : null}
          {p.is_test ? <Tag kind="sandbox">Test record</Tag> : null}
          <Links links={d.links} />
        </div>
      </div>

      <Panel title="Who you are meeting">
        {owners.length ? owners.map((o, i) => (
          <div key={i} className="fc-owner"><strong>{o.name}</strong>{o.owner_type_label ? <span className="evo-muted"> · {o.owner_type_label}</span> : null}</div>
        )) : <p className="evo-muted" style={{ margin: 0 }}>No owner name on record.</p>}
        <div className="ap-actions" style={{ marginTop: 10 }}>
          {phones.map((c) => (p.is_test || c.is_test
            ? <span key={c.value} className="evo-btn evo-btn--secondary is-static">{c.value} (test)</span>
            : <a key={c.value} className="evo-btn evo-btn--primary" href={`tel:${c.value}`}>Call {c.value}</a>))}
          {email ? <a className="evo-btn evo-btn--ghost" href={`mailto:${email.value}`}>{email.value}</a> : null}
          {!phones.length && !email ? <span className="evo-muted">No phone or email on file - ask the office.</span> : null}
        </div>
      </Panel>

      <Panel title="Why we are here">
        {signals.length ? (
          <ul className="fc-why">{signals.map((s) => <li key={s.signal_type}><strong>{s.label}</strong>{s.value ? ` - ${s.value}` : ''}</li>)}</ul>
        ) : <p className="evo-muted" style={{ margin: 0 }}>No reason on file. Your appointment notes say why you are going.</p>}
      </Panel>

      <Panel title="Your appointment">
        {appts === null ? <p className="evo-muted">Loading…</p> : !appts.length
          ? <p className="evo-muted" style={{ margin: 0 }}>No appointment of yours here.</p>
          : <ul className="ap-list">{appts.map((a) => <ApptCard key={a.id} a={a} isField reps={[]} onChanged={replace} />)}</ul>}
      </Panel>
    </EvoApp>
  )
}
