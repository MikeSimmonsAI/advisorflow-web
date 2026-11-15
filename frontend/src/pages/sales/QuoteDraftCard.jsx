import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

/** The deal's saved Deal Builder quote, shown above the proposal editor. */
export default function QuoteDraftCard({ oppId }) {
  const [d, setD] = useState(undefined)
  useEffect(() => {
    let on = true
    api.get('/sales/opportunities/' + oppId + '/quote-draft')
      .then(r => { if (on) setD(r) }).catch(() => { if (on) setD(null) })
    return () => { on = false }
  }, [oppId])
  if (d === undefined || d === null) return null
  const q = d.draft
  const money = n => '$' + Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
  return (
    <div className="sw-card" style={{ padding: 14, marginBottom: 12, border: '1px solid #dce5ea', borderRadius: 10, background: '#f7fbfa' }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'space-between' }}>
        <div>
          <b>Deal Builder quote</b>
          {q ? (
            <div style={{ fontSize: 13, color: '#425466' }}>
              {q.plan_name} · {money(q.setup_total)} one-time · {money(q.monthly_total)}/month
              {q.lines?.length ? ' · ' + q.lines.length + ' add-on' + (q.lines.length === 1 ? '' : 's') : ''}
              {' · revision ' + q.revision}
              {d.proposal && (d.proposal.editable
                ? ' · used by proposal ' + d.proposal.number + ' v' + d.proposal.version
                : ' · proposal ' + d.proposal.number + ' v' + d.proposal.version + ' was already ' + d.proposal.status)}
            </div>
          ) : (
            <div style={{ fontSize: 13, color: '#425466' }}>No quote built for this deal yet.</div>
          )}
        </div>
        {d.can_edit
          ? <Link className="sw-btn" to={'/sales/opportunities/' + oppId + '/quote'}>{q ? 'Open and revise quote' : 'Build Quote'}</Link>
          : q ? <Link className="sw-btn" to={'/sales/opportunities/' + oppId + '/quote'}>View quote</Link> : null}
      </div>
    </div>
  )
}
