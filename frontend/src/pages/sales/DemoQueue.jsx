/**
 * DEMOS TO BUILD — the work queue.
 *
 * ===========================================================================
 * WHAT THIS FIXES
 * ===========================================================================
 *
 * Discovery → Request Demo → the count on /sales/proposals goes up → and then
 * nothing. "Custom Demo Requests — 3" was a number with no rows behind it, because
 * the only thing the server sent was an integer per rep. There was no way to
 * reach the individual demo that had just been requested, from anywhere.
 *
 * This is that list. Every row is a real job with a real destination: opening
 * one lands in the builder workspace for that deal, with discovery already
 * handed over.
 *
 * ===========================================================================
 * NO NEW PRIORITY SYSTEM
 * ===========================================================================
 *
 * The order is the server's, and the server's order is the one the platform
 * already used for this list: soonest target date first, a job with no target
 * last. The one-line reason on a row is `attention`, which every card in this
 * workspace already shows. Nothing here invents a severity.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'

import { api } from '../../api/client'
import SalesShell from './SalesShell'
import { Card, Chip, Empty, ErrorBar, dateTime, shortDate } from './parts'

const STATUS_TONE = {
  requested: 'amber', in_progress: 'amber', ready: 'green', delivered: 'green',
}

export function DemoJobRow({ job, onOpen }) {
  const disc = job.discovery_required
    ? job.discovery_answered + '/' + job.discovery_required + ' discovery'
    : 'no discovery captured'
  return (
    <button className="sw-qrow" onClick={() => onOpen(job.opportunity_id)}>
      <span>
        <b>{job.company_name}</b>
        <span className="sw-why">
          {[job.contact_name,
            'Builder: ' + (job.builder_name || 'Unassigned'),
            job.due_at ? 'target ' + shortDate(job.due_at) : 'no target set',
            disc,
            job.requirements_captured ? null : 'no requirements captured',
          ].filter(Boolean).join(' · ')}
        </span>
        <span className="sw-who">
          Sales owner: {job.sales_owner_name || 'Unassigned'}
          {job.requested_at ? ' · requested ' + dateTime(job.requested_at) : ''}
        </span>
      </span>
      <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        {job.overdue ? <Chip tone="red">Past due</Chip> : null}
        {!job.discovery_complete && !['ready', 'delivered'].includes(job.demo_status) ? <Chip tone="amber">Discovery incomplete</Chip> : null}
        <Chip tone={STATUS_TONE[job.demo_status]}>{job.demo_status_label}</Chip>
      </span>
    </button>
  )
}

export default function DemoQueue() {
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const builder = params.get('builder') || ''
  const [showDone, setShowDone] = useState(false)

  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(() => {
    setLoading(true)
    const q = new URLSearchParams()
    if (builder) q.set('builder', builder)
    if (showDone) q.set('include_done', 'true')
    api.get('/sales/demo-queue' + (q.toString() ? '?' + q.toString() : ''))
      .then(d => { setData(d); setError(null) })
      .catch(e => setError(e?.detail || e?.message || 'Could not load the demo queue.'))
      .finally(() => setLoading(false))
  }, [builder, showDone])

  useEffect(load, [load])

  const setBuilder = v => {
    const next = new URLSearchParams(params)
    if (v) next.set('builder', v); else next.delete('builder')
    setParams(next, { replace: true })
  }

  const open = id => nav('/sales/demo-build/' + id)

  if (loading && !data) {
    return <SalesShell title="Custom Demo Requests"><div className="sw-subtle">Loading…</div></SalesShell>
  }

  const s = (data && data.summary) || { total: 0, by_builder: [] }
  const jobs = (data?.jobs || []).filter(job => showDone || !['ready', 'delivered'].includes(job.demo_status))

  return (
    <SalesShell
      title="Custom Demo Requests"
      subtitle={data?.brand_sales_org?.name
        ? data.brand_sales_org.name + ' — custom demos linked to deals'
        : 'Custom demos linked to deals'}
      actions={
        <>
          <button className="sw-btn" onClick={() => nav('/sales/proposals')}>
            ← Demos / Proposals
          </button>
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      <div className="sw-flex" style={{ flexWrap: 'wrap', marginBottom: 12 }}>
        <select aria-label="Filter by builder" className="sw-select" value={builder}
                onChange={e => setBuilder(e.target.value)}>
          <option value="">All builders</option>
          <option value="unassigned">Unassigned</option>
          <option value="me">Assigned to me</option>
          {(s.by_builder || []).filter(b => b.user_id).map(b => (
            <option key={b.user_id} value={b.user_id}>{b.name}</option>
          ))}
        </select>
      </div>

      <Card
        title={builder ? 'FILTERED' : 'ALL DEMO BUILDS'}
        sub="Open one to go straight into building it — discovery comes with it"
        right={
          <label className="sw-subtle" style={{ display: 'flex', gap: 6,
                                                alignItems: 'center', fontSize: 12 }}>
            <input type="checkbox" checked={showDone}
                   onChange={e => setShowDone(e.target.checked)} />
            Include ready and delivered
          </label>
        }
      >
        {builder ? (
          <div className="sw-note">
            Filtered.{' '}
            <button className="sw-tiny" onClick={() => setBuilder('')}>Show everyone</button>
          </div>
        ) : null}

        {jobs.length === 0 ? (
          <Empty title="Nothing waiting">
            No deal is sitting in demo build. Requesting a demo from a deal puts
            it here.
          </Empty>
        ) : jobs.map(j => (
          <DemoJobRow key={j.opportunity_id} job={j} onOpen={open} />
        ))}
      </Card>
    </SalesShell>
  )
}
