/**
 * DEMO BUILD — the workspace the queue and the deal both open into.
 *
 * ===========================================================================
 * WHY THIS IS A PAGE AND NOT A SECTION
 * ===========================================================================
 *
 * "Open demo build" used to expand a collapsed section on the deal screen you
 * were already looking at. That is fine as a disclosure and useless as a
 * destination: there was nowhere for the queue to send anybody, so the queue
 * could not send anybody anywhere.
 *
 * A demo build is a piece of work with its own owner, its own date and its own
 * output. It gets an address. The deal screen keeps its DEMO section — that is
 * the seller's view of the outcome — and its button now routes here, to the
 * builder's view of the work.
 *
 * ===========================================================================
 * DISCOVERY IS THE SOURCE PACKAGE
 * ===========================================================================
 *
 * Everything the seller captured is rendered at the top, read-only, by the
 * server (`discovery_schema.render`). The builder reads it; they never retype
 * it. There is deliberately no editable copy of a discovery answer on this
 * screen — a second place to write the same fact is a second answer to it.
 *
 * ===========================================================================
 * NOTHING HERE IS A SECOND DEMO SYSTEM
 * ===========================================================================
 *
 * The controls are `DemoPanel`, unchanged. The publishing is
 * `DemoSitesPanel`, unchanged. Both are the components the deal screen already
 * renders, and both write through the endpoints that already existed. This
 * page is composition and a route, not a new mechanism.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { api } from '../../api/client'
import SalesShell from './SalesShell'
import DemoPanel from './DemoPanel'
import DemoSitesPanel from './DemoSitesPanel'
import { Card, Chip, Empty, ErrorBar, Info, dateTime, shortDate } from './parts'

const STATUS_TONE = {
  requested: 'amber', in_progress: 'amber', ready: 'green', delivered: 'green',
}

/** Discovery, as a brief. Read-only on purpose — see the module docstring. */
function Handoff({ discovery }) {
  const rows = (discovery && discovery.handoff) || []
  const answered = rows.filter(r => r.value)
  const missing = rows.filter(r => !r.value && r.required)
  const p = (discovery && discovery.progress) || { answered: 0, required: 0 }

  const groups = []
  for (const r of answered) {
    const g = r.group || 'Discovery'
    let bucket = groups.find(x => x.name === g)
    if (!bucket) { bucket = { name: g, rows: [] }; groups.push(bucket) }
    bucket.rows.push(r)
  }

  return (
    <Card
      title="WHAT THE SELLER CAPTURED"
      sub="The brief for this build. You do not need to ask the prospect any of it again."
      right={<Chip tone={p.answered >= p.required && p.required ? 'green' : 'amber'}>
        {p.answered}/{p.required}
      </Chip>}
    >
      {discovery?.completed_at ? (
        <div className="sw-subtle" style={{ marginBottom: 10 }}>
          Discovery completed {dateTime(discovery.completed_at)}
          {discovery.completed_by_name ? ' by ' + discovery.completed_by_name : ''}
        </div>
      ) : null}

      {answered.length === 0 ? (
        <Empty title="Discovery has not been captured yet">
          Nothing was recorded on the discovery call, so there is no brief to
          build from. Ask the seller to complete discovery before starting.
        </Empty>
      ) : groups.map(g => (
        <div key={g.name} style={{ marginBottom: 14 }}>
          <div className="sw-subtle" style={{ fontWeight: 700, letterSpacing: '.05em',
                                              textTransform: 'uppercase',
                                              fontSize: 11, marginBottom: 6 }}>
            {g.name}
          </div>
          {g.rows.map(r => (
            <div key={r.key} style={{ marginBottom: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: '.04em',
                            textTransform: 'uppercase', opacity: 0.6 }}>
                {r.label}
              </div>
              <div style={{ fontSize: 13, whiteSpace: 'pre-wrap' }}>{r.value}</div>
            </div>
          ))}
        </div>
      ))}

      {missing.length ? (
        <div className="sw-note">
          Still unanswered in discovery: {missing.map(m => m.label).join(', ')}.
          Building against gaps is how a demo comes back for rework.
        </div>
      ) : null}
    </Card>
  )
}

export default function DemoBuild() {
  const { oppId } = useParams()
  const nav = useNavigate()

  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.get('/sales/opportunities/' + oppId + '/demo-build')
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e?.detail || e?.message || 'Could not load this demo build.'))
      .finally(() => setLoading(false))
  }, [oppId])

  useEffect(load, [load])

  /* The SAME patch the deal screen uses. A demo build that wrote through its
     own endpoint would be a second way to change one status. */
  const patch = useCallback(async body => {
    setSaving(true)
    setError(null)
    try {
      await api.patch('/sales/opportunities/' + oppId, body)
      load()
    } catch (e) {
      setError(e?.detail || e?.message || 'Could not save that.')
    } finally {
      setSaving(false)
    }
  }, [oppId, load])

  if (loading && !data) {
    return <SalesShell title="Demo build"><div className="sw-subtle">Loading…</div></SalesShell>
  }
  if (!data) {
    return (
      <SalesShell title="Demo build"
        actions={<button className="sw-btn" onClick={() => nav('/sales/demos')}>
          ← Demo queue
        </button>}>
        <ErrorBar error={error} onRetry={load} />
        <Card title="NOT AVAILABLE">
          <Empty title="This demo build is not available">
            The deal does not exist, or it belongs to another representative.
          </Empty>
        </Card>
      </SalesShell>
    )
  }

  const o = data.opportunity
  const d = data.demo
  // DemoPanel and DemoSitesPanel both read an `opp`. Handing them the shape
  // they already expect keeps them untouched — no prop churn, no fork.
  const oppForPanels = {
    id: o.id,
    company_name: o.company_name,
    demo: d,
    can_manage_demo: data.can_manage_demo,
  }

  return (
    <SalesShell
      title={o.company_name}
      subtitle={['Demo build', o.contact_name, o.brand_name]
        .filter(Boolean).join(' · ')}
      actions={
        <>
          <button className="sw-btn" onClick={() => nav('/sales/demos')}>
            ← Demo queue
          </button>
          <button className="sw-btn"
                  onClick={() => nav('/sales/opportunities/' + o.id)}>
            Open full deal
          </button>
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      <Card
        title="THIS BUILD"
        sub="Who it is for, who is building it, and by when"
        right={<span style={{ display: 'flex', gap: 6 }}>
          {d.overdue ? <Chip tone="red">Past due</Chip> : null}
          <Chip tone={STATUS_TONE[d.status]}>{d.status_label}</Chip>
        </span>}
      >
        <div className="sw-infogrid">
          <Info label="COMPANY" value={o.company_name} />
          <Info label="PRIMARY CONTACT" value={o.contact_name || 'Not captured'} />
          <Info label="SALES OWNER" value={o.owner_name || 'Unassigned'} />
          <Info label="BUILDER" value={d.owner_name || 'Unassigned'} />
          <Info label="REQUESTED" value={d.requested_at ? dateTime(d.requested_at) : 'Not requested'} />
          <Info label="TARGET" value={d.due_at ? shortDate(d.due_at) : 'Not set'} />
          <Info label="STAGE" value={o.stage_label} />
          <Info label="INDUSTRY" value={o.industry || '—'} />
        </div>
        {!data.can_manage_demo ? (
          <div className="sw-note">
            You can read this build. Changing the builder, the target date or
            the status belongs to the manager or to whoever is assigned to it.
          </div>
        ) : null}
      </Card>

      <div className="sw-mt"><Handoff discovery={data.discovery} /></div>

      {/* The controls, unchanged from the deal screen. */}
      <div className="sw-mt">
        <DemoPanel opp={oppForPanels} team={data.team} onPatch={patch}
                   saving={saving}
                   onRequest={() => patch({ stage: 'demo_build' })} />
      </div>

      {/* Publishing, unchanged. Publishing the platform slot fills in the demo
          URL and marks the build ready on the server, so `load` refreshes the
          panel above rather than this screen guessing. */}
      <div className="sw-mt">
        <DemoSitesPanel opp={oppForPanels} onChanged={load} />
      </div>
    </SalesShell>
  )
}
