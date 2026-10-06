/**
 * GodEmailDiagnostics — outbound sender + inbound reply mailboxes.
 *
 * 2026-09-29: an Atlantis email went out From support@evosyspro.live and the
 * reply sat in that Outlook mailbox, never reaching EvoSys, because nothing
 * read it. This screen connects that mailbox (Microsoft sign-in, done by the
 * owner on Microsoft's own page), shows what each poll did, and lists every
 * message the poller looked at with the reason it was or was not attached.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'

const OUTCOME = {
  matched: ['Attached to lead', '#15803d'],
  no_lead: ['No matching lead', '#b45309'],
  ambiguous: ['Ambiguous — not attached', '#b45309'],
  own_mail: ['Own mail — skipped', '#64748b'],
  error: ['Error — will retry', '#b91c1c'],
}

function fmt(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

const card = { background: 'var(--surface-card, #fff)', border: '1px solid var(--border-subtle, #e5e7eb)',
  borderRadius: 12, padding: 18, marginBottom: 16 }

export default function GodEmailDiagnostics() {
  const [params] = useSearchParams()
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(null)
  const [note, setNote] = useState(null)

  const load = useCallback(() => {
    api.get('/god/email/inbound-mailboxes?limit=100')
      .then(d => { setData(d); setErr(null) })
      .catch(e => setErr(e.message || 'Could not load'))
  }, [])
  useEffect(() => { load() }, [load])

  useEffect(() => {
    const ok = params.get('mailbox_connected')
    const bad = params.get('mailbox_error')
    if (ok) setNote({ ok: true, text: `Connected ${ok}. Replies are read every 5 minutes; press "Check now" to read immediately.` })
    if (bad) setNote({ ok: false, text: `The mailbox was not connected (${bad}).` })
  }, [params])

  async function connect() {
    setBusy('connect')
    try {
      const r = await api.post('/god/email/inbound-mailboxes/connect', {})
      window.location.href = r.authorization_url
    } catch (e) {
      setNote({ ok: false, text: e.message || 'Could not start the Microsoft sign-in.' })
      setBusy(null)
    }
  }

  async function pollNow(id) {
    setBusy(id)
    try {
      const r = await api.post(`/god/email/inbound-mailboxes/${id}/poll-now`, {})
      const res = r.result || {}
      setNote({ ok: !res.errors, text: `Checked ${res.checked ?? 0} message(s), attached ${res.matched ?? 0} reply(ies)${res.errors ? `, ${res.errors} error(s)` : ''}.` })
      load()
    } catch (e) {
      setNote({ ok: false, text: e.message || 'Poll failed.' })
    } finally { setBusy(null) }
  }

  const boxes = (data && data.mailboxes) || []
  const recent = (data && data.recent) || []

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <h1 style={{ margin: '0 0 4px' }}>Email diagnostics</h1>
      <p style={{ marginTop: 0, color: 'var(--text-secondary, #64748b)' }}>
        Where workspace email is sent from, and whether replies to that address come back into EvoSys.
      </p>
      {note ? (
        <div role="status" style={{ ...card, padding: 12, background: note.ok ? '#ecfdf5' : '#fef2f2',
          borderColor: note.ok ? '#a7f3d0' : '#fecaca' }}>{note.text}</div>
      ) : null}
      {err ? <div style={{ ...card, color: '#b91c1c' }}>{err}</div> : null}

      <div style={card}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Reply mailboxes</h2>
          <button className="btn btn--primary" onClick={connect} disabled={busy === 'connect'}
                  data-testid="connect-mailbox">
            {busy === 'connect' ? 'Opening Microsoft…' : 'Connect a mailbox'}
          </button>
        </div>
        <p style={{ color: 'var(--text-secondary, #64748b)', fontSize: 13 }}>
          Connect the address workspaces send from (for example <b>support@evosyspro.live</b>). On Microsoft's page, pick
          that mailbox's account. EvoSys gets read and write access to that mailbox (Mail.ReadWrite): it reads replies
          from the Inbox and, only after a reply has been processed, files it into its location's Outlook folder. It
          never sends, deletes or creates folders from here.
        </p>
        {data && boxes.length === 0 ? (
          <div style={{ padding: 12, background: '#fffbeb', borderRadius: 8 }}>
            No reply mailbox is connected, so replies to provider-sent email are <b>not</b> reaching EvoSys.
          </div>
        ) : null}
        {boxes.map(b => (
          <div key={b.id} style={{ borderTop: '1px solid var(--border-subtle, #e5e7eb)', paddingTop: 12, marginTop: 12 }}
               data-testid="mailbox-row">
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <b>{b.address}</b>{' '}
                <span style={{ color: b.last_status === 'ok' || b.last_status === 'connected' ? '#15803d' : '#b91c1c' }}>
                  · {b.is_active ? (b.last_status || 'not polled yet') : 'disabled'}
                </span>
                <div style={{ fontSize: 13, color: 'var(--text-secondary, #64748b)' }}>
                  Last checked {fmt(b.last_polled_at)} · read {b.last_checked ?? '—'} · attached {b.last_matched ?? '—'} ·
                  cursor {fmt(b.cursor_received_at)}
                </div>
                <div style={{ fontSize: 13 }}>
                  Routes replies for: {b.routes_to.length ? b.routes_to.map(o => o.name || o.id).join(', ') : <i>no workspace sends from this address</i>}
                </div>
                {b.last_error ? <div style={{ fontSize: 13, color: '#b91c1c' }}>{b.last_error}</div> : null}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn--secondary" onClick={() => pollNow(b.id)} disabled={!!busy}>
                  {busy === b.id ? 'Checking…' : 'Check now'}
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      <div style={card}>
        <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>Inbound log</h2>
        {recent.length === 0 ? <div style={{ color: 'var(--text-secondary, #64748b)' }}>Nothing read yet.</div> : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead><tr style={{ textAlign: 'left' }}>
                <th>Received</th><th>From</th><th>Subject</th><th>Outcome</th><th>Workspace</th><th>Detail</th>
              </tr></thead>
              <tbody>
                {recent.map(r => {
                  const [label, color] = OUTCOME[r.outcome] || [r.outcome, '#334155']
                  return (
                    <tr key={r.id} style={{ borderTop: '1px solid var(--border-subtle, #e5e7eb)' }}>
                      <td style={{ padding: '6px 8px 6px 0', whiteSpace: 'nowrap' }}>{fmt(r.received_at)}</td>
                      <td>{r.from || '—'}</td>
                      <td>{r.subject || '—'}</td>
                      <td style={{ color, fontWeight: 600 }}>{label}</td>
                      <td>{r.organization || '—'}</td>
                      <td>{r.detail || ''}{r.lead_id ? <> <Link to={`/leads/${r.lead_id}`}>lead →</Link></> : null}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
