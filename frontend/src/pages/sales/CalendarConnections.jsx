/**
 * Calendar connections — Microsoft 365, Google Calendar, or neither.
 *
 * THE POINT OF THIS PANEL IS HONESTY. It reports what is actually true rather
 * than what would look tidiest:
 *
 *  · "Connected for email, not calendar" is shown as its own state, because a
 *    user who consented before calendar permission was requested has a live
 *    token that email works with and calendar does not. Showing that as
 *    CONNECTED would mean every sync silently 403s while the UI says fine.
 *  · Not connecting a calendar is a legitimate choice, presented as a
 *    supported way of working — invitations by email — and never as an error.
 *  · "Test connection" performs a REAL read. A green tick that only proves a
 *    row exists in our own database is worth nothing.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import { Card, Chip, ErrorBar, dateTime } from './parts'

// No tone at all is the neutral grey chip. "Not connected" is deliberately
// neutral rather than red: it is a choice, not a fault.
const TONE = {
  connected: 'green',
  reconnect_required: 'amber',
  not_connected: null,
}

const STATE_TEXT = {
  connected: 'Connected',
  reconnect_required: 'Reconnect required',
  not_connected: 'Not connected',
}

export default function CalendarConnections() {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setError(null)
    try { setData(await api.get('/sales/calendar/connections')) }
    catch (e) { setError(e.message || 'Could not load calendar connections.') }
  }, [])

  useEffect(() => { load() }, [load])

  async function test(provider) {
    setBusy(provider); setNote(null); setError(null)
    try {
      const r = await api.post('/sales/calendar/connections/' + provider + '/test', {})
      setNote({ ok: r.ok, text: r.message })
      await load()
    } catch (e) { setError(e.message || 'Could not test the connection.') }
    finally { setBusy(null) }
  }

  async function disconnect(provider, label) {
    // Destructive enough to deserve a question, mild enough not to need a modal.
    if (!window.confirm(
      'Disconnect ' + label + '?\n\n' +
      'New meetings will be emailed to you as calendar invitations instead, and ' +
      'your existing commitments will no longer be considered when the team looks ' +
      'for a shared time. Meetings already on your calendar stay there.')) return
    setBusy(provider); setNote(null); setError(null)
    try {
      const r = await api.post('/sales/calendar/connections/' + provider + '/disconnect', {})
      setNote({ ok: true, text: r.note })
      await load()
    } catch (e) { setError(e.message || 'Could not disconnect.') }
    finally { setBusy(null) }
  }

  return (
    <Card title="CALENDAR CONNECTIONS"
          sub="Connect a calendar and your meetings appear on it automatically">
      <ErrorBar error={error} onRetry={load} />
      {!data && <div className="sw-subtle">Loading…</div>}

      {data && data.connections.map(c => (
        <div key={c.provider}
             style={{ padding: '10px 0', borderBottom: '1px solid #eef2f5' }}>
          <div className="sw-flex sw-between">
            <div>
              <b style={{ fontSize: 12 }}>{c.label}</b>
              {c.account_email && (
                <div className="sw-subtle">{c.account_email}</div>
              )}
            </div>
            <Chip tone={TONE[c.state]}>
              {STATE_TEXT[c.state] || c.state}
            </Chip>
          </div>

          {c.detail && (
            <div className="sw-subtle" style={{ marginTop: 6 }}>{c.detail}</div>
          )}
          {/* The last real error, verbatim. A user who is told only "sync
              failed" cannot tell a dead grant from a bad afternoon. */}
          {c.last_error && c.state !== 'connected' && (
            <div className="sw-subtle" style={{ marginTop: 6, color: '#b45309' }}>
              Last error: {c.last_error}
            </div>
          )}
          {c.state === 'connected' && c.last_sync_at && (
            <div className="sw-subtle" style={{ marginTop: 6 }}>
              {/* dateTime, not new Date(): the API sends naive UTC with no
                  suffix, which the browser would otherwise read as local. */}
              Last read {dateTime(c.last_sync_at)}
            </div>
          )}

          <div className="sw-flex" style={{ gap: 8, marginTop: 8 }}>
            {c.state !== 'connected' && c.connect_url && (
              <a className="sw-btn sw-primary" href={c.connect_url}
                 style={{ textDecoration: 'none' }}>
                {c.has_token ? 'Reconnect' : 'Connect'} {c.label}
              </a>
            )}
            {c.has_token && (
              <button className="sw-tiny" disabled={busy === c.provider}
                      onClick={() => test(c.provider)}>
                {busy === c.provider ? 'Checking…' : 'Test connection'}
              </button>
            )}
            {c.has_token && (
              <button className="sw-tiny" disabled={busy === c.provider}
                      onClick={() => disconnect(c.provider, c.label)}>
                Disconnect
              </button>
            )}
          </div>
        </div>
      ))}

      {note && (
        <div className="sw-subtle"
             style={{ marginTop: 10, color: note.ok ? '#047857' : '#b45309' }}>
          {note.text}
        </div>
      )}

      {/* Working without a connected calendar is a supported way of working,
          not a misconfiguration. Said plainly so nobody feels broken. */}
      {data && data.uses_email_fallback && (
        <div style={{ marginTop: 12, padding: 10, background: '#f8fafc',
                      border: '1px solid #e5e7eb', borderRadius: 6 }}>
          <b style={{ fontSize: 11 }}>Currently: invitations by email</b>
          <div className="sw-subtle" style={{ marginTop: 4 }}>
            {data.fallback_explainer}
          </div>
        </div>
      )}
    </Card>
  )
}
