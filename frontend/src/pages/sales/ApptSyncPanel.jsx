/**
 * Calendar sync state for the meetings on a deal.
 *
 * Replaces the "NOT BUILT YET" placeholder that stood here through Checkpoints
 * 1 and 2. Everything below is now real: the states come from actual provider
 * calls, and Retry actually retries.
 *
 * THREE OUTCOMES, THREE DIFFERENT WORDS. The whole reason this panel exists is
 * that these are not the same thing and must never be flattened into a tick:
 *
 *   On their calendar     the event was written to Outlook or Google
 *   Invite sent by email  they have no connected calendar; we emailed an .ics
 *   Reconnect required    their grant is dead — only they can fix it
 *
 * "Invite sent by email" is a SUCCESS. It is styled as one.
 */
import { useState } from 'react'
import { api } from '../../api/client'
import { Card, Chip, Empty, dateTime, wallDateTime } from './parts'

const SYNC_TONE = {
  synced: 'green',
  ics_sent: 'blue',
  not_connected: null,
  pending: null,
  retrying: 'amber',
  failed: 'red',
  reauth_required: 'amber',
}

export default function ApptSyncPanel({ opp, onChanged }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)

  const appts = (opp.appointments || []).filter(a => a.status === 'scheduled')

  async function retry(apptId) {
    setBusy(apptId); setError(null); setNote(null)
    try {
      const r = await api.post('/sales/appointments/' + apptId + '/resync', {})
      const rep = r.sync_report || {}
      setNote(rep.needs_attention
        ? (rep.needs_attention + ' still need attention.')
        : 'All calendars are up to date.')
      if (onChanged) await onChanged()
    } catch (e) { setError(e.message || 'Could not retry the sync.') }
    finally { setBusy(null) }
  }

  async function resend(apptId) {
    setBusy(apptId); setError(null); setNote(null)
    try {
      await api.post('/sales/appointments/' + apptId + '/resend-invitation', {})
      setNote('Invitation re-sent. The prospect’s existing link still works.')
      if (onChanged) await onChanged()
    } catch (e) { setError(e.message || 'Could not re-send the invitation.') }
    finally { setBusy(null) }
  }

  return (
    <Card title="CALENDAR SYNC" sub="Outlook · Google · email invitation">
      {appts.length === 0 && (
        <Empty title="No meetings to sync">
          Once a meeting is booked, each attendee's calendar state appears here.
        </Empty>
      )}

      {appts.map(a => {
        const attention = a.sync_needs_attention || 0
        return (
          <div key={a.id} style={{ paddingBottom: 12, marginBottom: 12,
                                   borderBottom: '1px solid #eef2f5' }}>
            <div className="sw-flex sw-between">
              <b style={{ fontSize: 11 }}>
                {wallDateTime(a.starts_at_local || a.starts_at)}
              </b>
              {attention > 0
                ? <Chip tone="amber">{attention} need{attention === 1 ? 's' : ''} attention</Chip>
                : <Chip tone="green">All set</Chip>}
            </div>

            {a.rescheduled_count > 0 && (
              <div className="sw-subtle" style={{ marginTop: 4 }}>
                Moved {a.rescheduled_count}×
                {a.previous_starts_at
                  ? ' · was ' + wallDateTime(a.previous_starts_at) : ''}
              </div>
            )}

            <div style={{ marginTop: 8 }}>
              {(a.participants || []).map(p => (
                <div key={p.user_id} className="sw-flex sw-between"
                     style={{ padding: '4px 0' }}>
                  <span style={{ fontSize: 11 }}>{p.full_name}</span>
                  <span className="sw-flex" style={{ gap: 6 }}>
                    <Chip tone={SYNC_TONE[p.sync_status]}>{p.sync_label}</Chip>
                  </span>
                </div>
              ))}
            </div>

            {/* The actual provider message, not a generic "sync failed". A rep
                who cannot tell a dead grant from a flaky network cannot act. */}
            {(a.participants || []).filter(p => p.needs_attention && p.sync_error).map(p => (
              <div key={p.user_id + '-err'} className="sw-subtle"
                   style={{ marginTop: 4, color: '#b45309' }}>
                {p.full_name}: {p.sync_error}
              </div>
            ))}

            <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px dashed #eef2f5' }}>
              <div className="sw-flex sw-between">
                <span style={{ fontSize: 11 }}>Prospect invitation</span>
                {a.prospect_invite_error
                  ? <Chip tone="red">Failed</Chip>
                  : a.prospect_invite_sent_at
                    ? <Chip tone="green">Sent</Chip>
                    : <Chip>Not sent</Chip>}
              </div>
              {a.prospect_invite_sent_at && !a.prospect_invite_error && (
                <div className="sw-subtle" style={{ marginTop: 4 }}>
                  {dateTime(a.prospect_invite_sent_at)}
                </div>
              )}
              {a.prospect_invite_error && (
                <div className="sw-subtle" style={{ marginTop: 4, color: '#b45309' }}>
                  {a.prospect_invite_error}
                </div>
              )}
            </div>

            <div className="sw-flex" style={{ gap: 8, marginTop: 10 }}>
              {attention > 0 && (
                <button className="sw-tiny sw-primary" disabled={busy === a.id}
                        onClick={() => retry(a.id)}>
                  {busy === a.id ? 'Retrying…' : 'Retry sync'}
                </button>
              )}
              {a.prospect?.email && (
                <button className="sw-tiny" disabled={busy === a.id}
                        onClick={() => resend(a.id)}>Re-send invitation</button>
              )}
            </div>
          </div>
        )
      })}

      {error && <div className="sw-subtle" style={{ color: '#b45309' }}>{error}</div>}
      {note && <div className="sw-subtle" style={{ color: '#047857' }}>{note}</div>}
    </Card>
  )
}
