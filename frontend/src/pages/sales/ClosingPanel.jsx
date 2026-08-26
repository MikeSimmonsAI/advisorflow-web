/**
 * The Closing workspace for one deal.
 *
 * WARNINGS FIRST, deliberately. A closing screen that leads with status tells a
 * rep what they already knew; "sent four days ago, never opened" tells them
 * what to do this afternoon. Everything below the warnings is the supporting
 * detail they need once they have decided to act.
 *
 * Every value here is real — assembled server-side from the proposal, the
 * portal events and the appointments. Nothing is estimated.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import { Card, Chip, Info, Empty, ErrorBar, dateTime, wallDateTime } from './parts'

const STATUS_TONE = {
  draft: null, internal_review: null, ready: 'blue', sent: 'blue',
  viewed: 'amber', accepted: 'green', declined: 'red',
  change_requested: 'amber', expired: 'red', superseded: null,
}

function money(v, cur) {
  if (v === null || v === undefined) return '—'
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency: cur || 'USD', maximumFractionDigits: 0,
    }).format(v)
  } catch { return (cur || 'USD') + ' ' + v }
}

const WARN_STYLE = {
  red:   { background: '#ffeff1', border: '1px solid #efc0c6', color: '#9d3f4b' },
  amber: { background: '#fff6e9', border: '1px solid #f2d5aa', color: '#9e6722' },
}

export default function ClosingPanel({ opp }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setError(null)
    try { setData(await api.get('/sales/opportunities/' + opp.id + '/closing')) }
    catch (e) { setError(e.message || 'Could not load the closing view.') }
  }, [opp.id])

  useEffect(() => { load() }, [load])

  if (!data) {
    return <Card title="CLOSING"><div className="sw-subtle">Loading…</div></Card>
  }

  const p = data.proposal
  const actionable = (data.warnings || []).filter(w => w.level)
  const notes = (data.warnings || []).filter(w => !w.level)

  return (
    <Card title="CLOSING"
          sub="What stands between this deal and Won"
          right={actionable.length > 0
            ? <Chip tone={actionable.some(w => w.level === 'red') ? 'red' : 'amber'}>
                {actionable.length} to deal with
              </Chip>
            : <Chip tone="green">Nothing outstanding</Chip>}>
      <ErrorBar error={error} onRetry={load} />

      {/* ── warnings ─────────────────────────────────────────────────────── */}
      {actionable.map((w, i) => (
        <div key={i} style={{ ...WARN_STYLE[w.level], borderRadius: 8,
                              padding: '10px 12px', marginBottom: 8 }}>
          <b style={{ fontSize: 11 }}>{w.text}</b>
          {w.action && (
            <div style={{ fontSize: 11, marginTop: 3, opacity: 0.85 }}>{w.action}</div>
          )}
        </div>
      ))}
      {notes.map((w, i) => (
        <div key={'n' + i} className="sw-subtle" style={{ marginBottom: 8 }}>{w.text}</div>
      ))}

      {/* ── the proposal ─────────────────────────────────────────────────── */}
      {!p ? (
        <Empty title="No proposal yet">
          Create one from the Proposal panel — it prefills from this deal.
        </Empty>
      ) : (
        <>
          <div className="sw-flex sw-between" style={{ margin: '14px 0 6px' }}>
            <b style={{ fontSize: 12 }}>
              {p.proposal_number}{p.version > 1 ? ' · v' + p.version : ''}
            </b>
            <Chip tone={STATUS_TONE[p.status]}>{p.status_label}</Chip>
          </div>
          <div className="sw-infogrid">
            <Info label="AMOUNT" value={money(p.amount, p.currency)} />
            <Info label="EXPIRES" value={p.expires_at ? dateTime(p.expires_at) : '—'} />
            <Info label="SENT" value={p.sent_at ? dateTime(p.sent_at) : 'Not sent'} />
            <Info label="FIRST OPENED"
                  value={p.first_viewed_at ? dateTime(p.first_viewed_at) : 'Never'} />
          </div>
          {p.customer_response_note && (
            <div style={{ background: '#f8fafc', border: '1px solid #e5e7eb',
                          borderRadius: 8, padding: 10, marginTop: 10 }}>
              <b style={{ fontSize: 11 }}>They said:</b>
              <div className="sw-subtle" style={{ marginTop: 4 }}>
                “{p.customer_response_note}”
              </div>
            </div>
          )}
        </>
      )}

      {/* ── portal ───────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid #eef2f5' }}>
        <div className="sw-flex sw-between">
          <b style={{ fontSize: 11 }}>DEAL ROOM</b>
          {data.portal.opened
            ? <Chip tone="green">Opened</Chip>
            : <Chip>Never opened</Chip>}
        </div>
        {data.portal.last_activity ? (
          <div className="sw-subtle" style={{ marginTop: 6 }}>
            Last activity: {data.portal.last_activity.label}
            {data.portal.last_activity.detail ? ' — ' + data.portal.last_activity.detail : ''}
            {' · ' + data.portal.last_activity.ago}
          </div>
        ) : (
          <div className="sw-subtle" style={{ marginTop: 6 }}>
            {p?.sent_at ? 'Nothing yet since it was sent.' : 'Nothing sent yet.'}
          </div>
        )}
        {data.portal.event_count > 0 && (
          <div className="sw-subtle" style={{ marginTop: 3 }}>
            {data.portal.event_count} recorded action
            {data.portal.event_count === 1 ? '' : 's'}
          </div>
        )}
      </div>

      {/* ── meetings ─────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid #eef2f5' }}>
        <b style={{ fontSize: 11 }}>MEETINGS</b>
        <div className="sw-flex sw-between" style={{ padding: '6px 0' }}>
          <span className="sw-subtle">Last</span>
          <span style={{ fontSize: 11 }}>
            {data.last_meeting
              ? (data.last_meeting.meeting_type || 'Meeting') + ' · '
                + wallDateTime(data.last_meeting.starts_at)
              : 'None yet'}
          </span>
        </div>
        <div className="sw-flex sw-between" style={{ padding: '6px 0' }}>
          <span className="sw-subtle">Next</span>
          <span className="sw-flex" style={{ gap: 8 }}>
            <span style={{ fontSize: 11 }}>
              {data.next_meeting
                ? (data.next_meeting.meeting_type || 'Meeting') + ' · '
                  + wallDateTime(data.next_meeting.starts_at)
                : 'Nothing booked'}
            </span>
            {/* Join straight from the closing screen — the closing call is
                exactly the meeting a rep is most likely to be late to. */}
            {data.next_meeting?.video?.join_url && (
              <a className="sw-tiny sw-primary" href={data.next_meeting.video.join_url}
                 target="_blank" rel="noopener noreferrer"
                 style={{ textDecoration: 'none' }}>Join</a>
            )}
          </span>
        </div>
      </div>

      {/* ── people and next step ─────────────────────────────────────────── */}
      <div style={{ marginTop: 16, paddingTop: 12, borderTop: '1px solid #eef2f5' }}>
        <div className="sw-infogrid">
          <Info label="SALESPERSON" value={data.salesperson?.full_name} />
          <Info label="MANAGER" value={data.manager?.full_name} />
        </div>
        <div className="sw-field" style={{ marginTop: 10 }}>
          <label>NEXT ACTION</label>
          <div style={{ fontSize: 12, fontWeight: 700 }}>
            {data.next_action || '— none set —'}
          </div>
          {data.next_action_due_at && (
            <div className="sw-subtle" style={{ marginTop: 3 }}>
              Due {dateTime(data.next_action_due_at)}
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}
