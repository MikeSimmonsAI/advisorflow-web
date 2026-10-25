/* ACQUISITION READINESS - read-only views over existing machinery.
 *
 *   ReadyToRun        Command Center: can outreach run on its own, what is in
 *                     the way, and who needs contact data first
 *   FirstContactPlan  Property: which first contact is permitted now, the
 *                     approved wording, and why the other channels are not
 *
 * Nothing here sends, buys or changes anything.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { Panel, Tag } from '../ds/ds'

export function ReadyToRun() {
  const [r, setR] = useState(null)
  const [q, setQ] = useState(null)
  useEffect(() => {
    api.get('/wholesale/evosense/automation/readiness').then(setR).catch(() => setR(null))
    api.get('/wholesale/evosense/enrichment/queue?limit=10').then(setQ).catch(() => setQ(null))
  }, [])
  if (!r) return null
  return (
    <Panel title="Ready to run?" action={<Link className="evo-link" to="/wholesale/evosense/controls">Controls</Link>}>
      <p className="evo-small" style={{ marginTop: 0 }}>
        <b>{r.eligible_for_outreach_now}</b> owners can be contacted on a permitted channel now ·{' '}
        <b>{r.consents_confirmed}</b> confirmed text permissions ·{' '}
        contact lookups {r.enrichment_operational ? 'available' : 'not connected'}
      </p>
      {r.blockers?.length ? (
        <ul className="evo-small" style={{ margin: '0 0 8px', paddingLeft: 18 }}>
          {r.blockers.map((b) => <li key={b}>{b}</li>)}
        </ul>
      ) : <p className="evo-small">Nothing is blocking automated outreach.</p>}
      {q && q.rows?.length ? (
        <>
          <p className="evo-small" style={{ margin: '8px 0 4px' }}><b>Need contact data first</b> ({q.needing_contact} in all)</p>
          <ol className="evo-small" style={{ margin: 0, paddingLeft: 18 }}>
            {q.rows.map((x) => (
              <li key={x.property_id}>
                <Link className="evo-feedlink" to={`/wholesale/evosense/property/${x.property_id}`}>{x.address}</Link>
                {x.priority != null ? ` · priority ${x.priority}` : ''}{x.sale_deadline ? ` · sale ${x.sale_deadline}` : ''}
              </li>
            ))}
          </ol>
        </>
      ) : null}
      <p className="evo-small evo-muted" style={{ marginTop: 8 }}>{r.notice}</p>
    </Panel>
  )
}

export function FirstContactPlan({ propertyId }) {
  const [open, setOpen] = useState(false)
  const [p, setP] = useState(null)
  useEffect(() => {
    if (open && !p) api.get(`/wholesale/evosense/properties/${propertyId}/first-contact`).then(setP).catch(() => setP(null))
  }, [open, p, propertyId])
  return (
    <Panel title="First contact" id="first-contact" hint="What may be said, on which channel - nothing is sent from here">
      <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? 'Hide' : 'Show'} the first-contact plan</button>
      {open && p ? (
        <div className="evo-small" style={{ marginTop: 10 }}>
          <div className="evo-chips" style={{ flexWrap: 'wrap', gap: 6 }}>
            {p.channels.map((c) => (
              <Tag key={c.channel} kind={c.permitted ? 'live' : 'estimate'} title={(c.why || []).join(' · ')}>
                {c.channel.toUpperCase()}: {c.permitted ? 'permitted' : 'not permitted'}
              </Tag>
            ))}
          </div>
          {p.channels.filter((c) => !c.permitted && c.why?.length).map((c) => (
            <p key={c.channel} style={{ margin: '6px 0 0' }}>{c.channel.toUpperCase()}: {c.why.join(' · ')}</p>
          ))}
          <p style={{ margin: '10px 0 4px' }}><b>Call talk track</b></p>
          <ol style={{ margin: 0, paddingLeft: 18 }}>{p.wording.call_track.map((t) => <li key={t}>{t}</li>)}</ol>
          <p style={{ margin: '10px 0 4px' }}><b>Rules on every call</b></p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{p.wording.call_rules.map((t) => <li key={t}>{t}</li>)}</ul>
          <p style={{ margin: '10px 0 4px' }}><b>After the call: permission link</b></p>
          <p style={{ margin: 0 }}>{p.wording.permission_link}</p>
          <p className="evo-muted" style={{ marginTop: 8 }}>{p.notice}</p>
        </div>
      ) : null}
    </Panel>
  )
}
