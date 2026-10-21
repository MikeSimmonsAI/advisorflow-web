/**
 * Reports — "Your progress at a glance." (Lane F, #281)
 *
 * The rep's own snapshot, from the same real data My Day uses
 * (/sales/my-day and the rep's open deals). Team reporting for managers is a
 * separate, still-undecided screen and is not implied here.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import { money } from './parts'
import { SIMPLE_COLUMNS, groupForSimpleBoard, followUpList } from './simpleSales'
import { ErrorLine, Skeleton } from './SimpleParts'

export default function MyResults() {
  return (
    <SalesShell title="Reports" subtitle="Your progress at a glance.">
      <Body />
    </SalesShell>
  )
}

function Body() {
  const nav = useNavigate()
  const [day, setDay] = useState(null)
  const [deals, setDeals] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const [d, o] = await Promise.all([api.get('/sales/my-day'), api.get('/sales/opportunities?include_lost=true')])
      setDay(d); setDeals(o)
    } catch (e) { setError(e.message || 'Could not load your results.') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  const mine = useMemo(() => {
    if (!deals) return { stages: [], lost: [] }
    const keep = c => !deals.is_manager || c.owner_user_id === deals.viewer_user_id
    return {
      stages: (deals.stages || []).map(s => ({ ...s, opportunities: (s.opportunities || []).filter(keep) })),
      lost: (deals.lost || []).filter(keep),
    }
  }, [deals])
  const g = useMemo(() => groupForSimpleBoard(mine), [mine])
  const open = mine.stages.flatMap(s => s.opportunities).filter(c => c.status === 'open')
  const due = followUpList(open, 'today')
  const m = day?.metrics || {}

  if (loading && !day) return <div className="ss-panel"><Skeleton rows={5} /></div>
  return (
    <>
      <ErrorLine error={error} onRetry={load} />
      <div className="ss-sections">
        <div><h2>My Results</h2><p>Your own deals and meetings, live.</p></div>
        <button type="button" className="ss-btn ss-white" onClick={() => nav('/sales')}>Back to My Day</button>
      </div>
      <div className="ss-stats">
        <Stat n={open.length} name="Open deals" />
        <Stat n={due.length} name="To contact today" />
        <Stat n={(day?.upcoming_appointments || []).length} name="Upcoming meetings" detail={(day?.upcoming_appointments || []).length >= 12 ? 'showing the next 12' : null} />
        <Stat n={m.won_this_month || 0} name="Won this month" detail={m.won_value_this_month ? money(m.won_value_this_month) : null} />
      </div>
      <section className="ss-panel">
        <div className="ss-panelhd"><div><h3>Where your deals stand</h3><p>The four pipeline columns, plus closed deals</p></div></div>
        <div className="ss-upnext">
          {SIMPLE_COLUMNS.map(c => (
            <div key={c.key} className="ss-compact">
              <strong style={{ minWidth: 140 }}>{c.label}</strong>
              <span className="ss-sp">{c.hint}</span>
              <strong>{g[c.key].length}</strong>
            </div>
          ))}
          <div className="ss-compact"><strong style={{ minWidth: 140 }}>Lost</strong><span className="ss-sp">Closed without a sale</span><strong>{g.lost.length}</strong></div>
        </div>
      </section>
      <p className="ss-footnote">
        Earnings are on{' '}
        <button type="button" className="ss-link" onClick={() => nav('/sales/my-compensation')}>My Compensation</button>.
      </p>
    </>
  )
}

function Stat({ n, name, detail }) {
  return (
    <div className="ss-stat" style={{ cursor: 'default' }}>
      <div className="ss-num">{n}</div>
      <div className="ss-name">{name}</div>
      {detail && <div className="ss-detail ss-calm">{detail}</div>}
    </div>
  )
}
