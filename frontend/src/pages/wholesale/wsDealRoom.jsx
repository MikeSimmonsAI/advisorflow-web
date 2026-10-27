/* DEAL ROOM (staff side) - one tab on the Buyers screen (#415).
 *
 * The same records the rest of Wholesale owns: invitations link to buyers,
 * listings wrap deals whose investor package is already approved, holds are
 * decided by an owner or manager, and every portal conversation is a buyer
 * conversation. Nothing here emails an investor by itself: a practice
 * (.invalid) invitation shows its link to staff only.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Alert, Drawer, Empty, Panel, Seg, Skeleton, Tag } from './ds/ds'
import { errText } from './wsShared'

const DRS = '/wholesale/deal-room'
const usd = (v) => (v === null || v === undefined ? '—' : '$' + Math.round(Number(v)).toLocaleString('en-US'))
const words = (s) => (s || '').toString().replace(/_/g, ' ')
const cap = (s) => { const w = words(s); return w ? w[0].toUpperCase() + w.slice(1) : '' }
const when = (iso) => (iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—')
const STATE_TONE = { draft: undefined, approved: 'info', published: 'live', exclusive_review: 'estimate', expired: undefined,
  withdrawn: undefined, closed: undefined }
const NEXT = { draft: ['approve', 'withdraw'], approved: ['publish', 'redraft', 'withdraw'], published: ['withdraw', 'close'],
  exclusive_review: ['withdraw', 'close'], expired: ['publish', 'redraft', 'close'], withdrawn: ['publish', 'redraft', 'close'], closed: [] }
const ACTION_LABEL = { approve: 'Approve', publish: 'Publish', withdraw: 'Withdraw', close: 'Mark closed', redraft: 'Back to draft' }

function Left({ endsAt }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])
  if (!endsAt) return null
  const s = Math.max(0, Math.floor((new Date(endsAt).getTime() - now) / 1000))
  const d = Math.floor(s / 86400); const h = Math.floor((s % 86400) / 3600); const m = Math.floor((s % 3600) / 60)
  return <span className="dr-left" role="timer">{s ? `${d ? d + 'd ' : ''}${h}h ${String(m).padStart(2, '0')}m left` : 'Ending'}</span>
}

export default function DealRoomTab({ includeTest, onOpenBuyer }) {
  const [view, setView] = useState(() => { try { return sessionStorage.getItem('dr.view') || 'holds' } catch { return 'holds' } })
  const [err, setErr] = useState(null)
  const [ok, setOk] = useState(null)
  const [bump, setBump] = useState(0)
  useEffect(() => { try { sessionStorage.setItem('dr.view', view) } catch { /* private */ } }, [view])
  const done = useCallback((m) => { setErr(null); if (m) setOk(m); setBump((x) => x + 1) }, [])
  const fail = useCallback((e) => { setOk(null); setErr(errText(e)) }, [])
  const p = { includeTest, bump, done, fail, onOpenBuyer }
  return (
    <div className="dr">
      <Alert>{err}</Alert>
      <Alert kind="ok">{ok}</Alert>
      <div className="dr-bar">
        <Seg label="Deal Room" value={view} onChange={setView}
             items={[{ key: 'holds', label: 'Exclusive reviews' }, { key: 'inbox', label: 'Investor messages' },
                     { key: 'listings', label: 'Listings' }, { key: 'invites', label: 'Invitations' }]} />
        <span className="evo-muted evo-small">Investors sign in at /investors. Only approved, published deal packages can be listed.</span>
      </div>
      {view === 'holds' ? <Holds {...p} /> : null}
      {view === 'inbox' ? <Inbox {...p} /> : null}
      {view === 'listings' ? <Listings {...p} /> : null}
      {view === 'invites' ? <Invites {...p} /> : null}
    </div>
  )
}

// ── exclusive reviews ──────────────────────────────────────────────────────
function Holds({ includeTest, bump, done, fail, onOpenBuyer }) {
  const [d, setD] = useState(null)
  const [preset, setPreset] = useState({})
  useEffect(() => { api.get(`${DRS}/holds?include_test=${includeTest}`).then(setD).catch(fail) }, [includeTest, bump, fail])
  const decide = async (h, decision) => {
    const pr = preset[h.id] || '48h'
    const body = { decision }
    if (decision === 'approve' || decision === 'extend') {
      if (pr === 'custom') {
        const hrs = Number(window.prompt('How many hours? (max 720)', '36'))
        if (!hrs) return
        body.minutes = Math.round(hrs * 60)
      } else body.preset = pr
    }
    try { await api.post(`${DRS}/holds/${h.id}`, body); done(`Exclusive review ${decision === 'approve' ? 'approved' : decision + 'd'}.`) } catch (e) { fail(e) }
  }
  if (!d) return <Skeleton rows={3} />
  const req = d.holds.filter((h) => h.status === 'requested')
  const act = d.holds.filter((h) => h.status === 'active')
  const presets = [...(d.presets || []), 'custom']
  return (
    <div className="bi-grid">
      <Panel title="Waiting for a decision" count={req.length} hint="Only Michael Simmons, Michael Schlueter or a manager can approve.">
        {req.length ? <ul className="bi-list">{req.map((h) => (
          <li key={h.id} className="dr-row">
            <div><b><button type="button" className="evo-link" onClick={() => onOpenBuyer && onOpenBuyer(h.buyer_id)}>{h.buyer_name}</button></b>
              <div className="evo-small evo-muted">{h.address || 'Listing'} · asked {when(h.requested_at)}</div>
              {h.request_note ? <div className="evo-small">&ldquo;{h.request_note}&rdquo;</div> : null}</div>
            <div className="dr-actions">
              <label className="evo-sr" htmlFor={`pr-${h.id}`}>Length</label>
              <select id={`pr-${h.id}`} className="evo-input dr-sel" value={preset[h.id] || '48h'} onChange={(e) => setPreset({ ...preset, [h.id]: e.target.value })}>
                {presets.map((x) => <option key={x} value={x}>{x === 'custom' ? 'Custom…' : x}</option>)}
              </select>
              <button className="evo-btn evo-btn--sm evo-btn--primary" onClick={() => decide(h, 'approve')}>Approve</button>
              <button className="evo-btn evo-btn--sm" onClick={() => decide(h, 'decline')}>Decline</button>
            </div>
          </li>))}</ul> : <Empty title="No requests">Investors ask from an opportunity page.</Empty>}
      </Panel>
      <Panel title="Active now" count={act.length} hint={d.notice}>
        {act.length ? <ul className="bi-list">{act.map((h) => (
          <li key={h.id} className="dr-row">
            <div><b>{h.buyer_name}</b> <Tag kind="estimate"><Left endsAt={h.ends_at} /></Tag>
              <div className="evo-small evo-muted">{h.address || 'Listing'} · ends {when(h.ends_at)}{h.extended_count ? ` · extended ${h.extended_count}×` : ''}</div></div>
            <div className="dr-actions">
              <select className="evo-input dr-sel" aria-label="Extend by" value={preset[h.id] || '24h'} onChange={(e) => setPreset({ ...preset, [h.id]: e.target.value })}>
                {presets.map((x) => <option key={x} value={x}>{x === 'custom' ? 'Custom…' : x}</option>)}
              </select>
              <button className="evo-btn evo-btn--sm" onClick={() => decide(h, 'extend')}>Extend</button>
              <button className="evo-btn evo-btn--sm" onClick={() => decide(h, 'release')}>End now</button>
            </div>
          </li>))}</ul> : <Empty title="No active holds">Approved reviews hide the deal from other investors until they end.</Empty>}
      </Panel>
    </div>
  )
}

// ── investor messages ──────────────────────────────────────────────────────
function Inbox({ includeTest, bump, done, fail }) {
  const [d, setD] = useState(null)
  const [open, setOpen] = useState(null)
  useEffect(() => { api.get(`${DRS}/inbox?include_test=${includeTest}`).then(setD).catch(fail) }, [includeTest, bump, fail])
  if (!d) return <Skeleton rows={4} />
  return (
    <Panel title="Investor conversations" count={d.threads.length} flush>
      {d.threads.length ? (
        <table className="evo-table">
          <thead><tr><th>Investor</th><th>Conversation</th><th>Last message</th><th>Handled by</th><th /></tr></thead>
          <tbody>{d.threads.map((t) => (
            <tr key={t.id} className="bi-row" tabIndex={0} onClick={() => setOpen(t.id)} onKeyDown={(e) => { if (e.key === 'Enter') setOpen(t.id) }}>
              <td><b>{t.buyer_name}</b> {t.is_test ? <Tag>Practice</Tag> : null}</td>
              <td>{cap(t.kind)}{t.status === 'handed_off' ? <> <Tag kind="danger">Asked for a person</Tag></> : null}</td>
              <td className="evo-small"><div className="dr-clip">{t.last}</div><span className="evo-muted">{cap(t.last_role)} · {when(t.last_message_at)}</span></td>
              <td>{t.ai_enabled ? <Tag kind="info">Concierge</Tag> : <Tag kind="live">Team</Tag>}</td>
              <td>{t.unread ? <Tag kind="danger">{t.unread} new</Tag> : null}</td>
            </tr>))}</tbody>
        </table>
      ) : <Empty title="No conversations yet">Each investor gets a concierge thread when they join.</Empty>}
      {open ? <ThreadDrawer id={open} onClose={() => setOpen(null)} done={done} fail={fail} /> : null}
    </Panel>
  )
}

function ThreadDrawer({ id, onClose, done, fail }) {
  const [t, setT] = useState(null)
  const [room, setRoom] = useState(null)
  const [text, setText] = useState('')
  const [n, setN] = useState(0)
  useEffect(() => {
    api.get(`${DRS}/threads/${id}`).then((x) => {
      setT(x)
      if (x.buyer_id) api.get(`${DRS}/buyers/${x.buyer_id}`).then(setRoom).catch(() => setRoom(null))
    }).catch(fail)
  }, [id, n, fail])
  const act = async (what, body) => {
    try { await api.post(`${DRS}/threads/${id}/${what}`, body || {}); setText(''); setN((x) => x + 1); done(null) } catch (e) { fail(e) }
  }
  const review = async (fid, status) => {
    const body = { status }
    if (status === 'accepted') {
      const exp = window.prompt('Proof of funds valid until (YYYY-MM-DD) - leave blank if this is not proof of funds', '')
      if (exp) body.pof_expires = exp
    }
    try { await api.post(`${DRS}/files/${fid}/review`, body); setN((x) => x + 1); done('File reviewed.') } catch (e) { fail(e) }
  }
  return (
    <Drawer open title={t ? `${cap(t.kind)} conversation` : 'Conversation'} sub={t && (t.ai_enabled ? 'Concierge is answering' : 'Team is answering')} onClose={onClose} wide
            footer={t ? (
              <div className="dr-compose">
                <label className="evo-sr" htmlFor="dr-text">Message</label>
                <textarea id="dr-text" className="evo-input" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder="Write to the investor, or an internal note" />
                <div className="dr-actions">
                  <button className="evo-btn evo-btn--primary evo-btn--sm" disabled={!text.trim()} onClick={() => act('reply', { text })}>Send to investor</button>
                  <button className="evo-btn evo-btn--sm" disabled={!text.trim()} onClick={() => act('note', { text })}>Internal note</button>
                  {t.ai_enabled ? <button className="evo-btn evo-btn--sm" onClick={() => act('takeover')}>Take over</button>
                    : <button className="evo-btn evo-btn--sm" onClick={() => act('return-to-ai')}>Return to concierge</button>}
                </div>
                <p className="evo-small evo-muted">Sending takes the conversation over from the concierge. Internal notes are never shown to the investor.</p>
              </div>) : null}>
      {!t ? <Skeleton rows={4} /> : (
        <>
          {t.handoff_reason ? <Alert>{t.handoff_reason}</Alert> : null}
          <ol className="dr-msgs">
            {(t.transcript || []).map((m, i) => (
              <li key={i} className={`dr-msg dr-msg--${m.role}`}>
                <span className="evo-small evo-muted">{({ internal: 'Internal note', ai: 'Concierge', buyer: 'Investor', staff: 'Team', system: 'Deal Room' })[m.role] || cap(m.role)}{m.by ? ` · ${m.by}` : ''} · {when(m.at)}</span>
                <div>{m.text}</div>
              </li>))}
          </ol>
          {room ? (
            <div className="bi-sec">
              <h3 className="bi-h">Investor</h3>
              {(room.accounts || []).map((a) => (
                <div key={a.id} className="evo-small">{a.legal_name || a.email} {a.company_legal_name ? `· ${a.company_legal_name}` : ''} ·{' '}
                  {a.identity_complete ? <Tag kind="info">Identity complete</Tag> : <Tag>Identity incomplete</Tag>} {a.status !== 'active' ? <Tag kind="danger">{cap(a.status)}</Tag> : null}</div>
              ))}
              <h3 className="bi-h">Files</h3>
              {(room.files || []).length ? <ul className="bi-list">{room.files.map((f) => (
                <li key={f.id} className="dr-row">
                  <div><b>{f.name}</b><div className="evo-small evo-muted">{cap(f.category)} · {Math.round(f.size / 1024)} KB · {f.scan_detail || f.scan_status}</div></div>
                  <div className="dr-actions">
                    <Tag kind={f.review_status === 'accepted' ? 'live' : f.review_status === 'rejected' ? 'danger' : undefined}>{cap(f.review_status || 'shared')}</Tag>
                    {f.from_you && f.review_status === 'pending' ? <>
                      <button className="evo-btn evo-btn--sm" onClick={() => review(f.id, 'accepted')}>Accept</button>
                      <button className="evo-btn evo-btn--sm" onClick={() => review(f.id, 'rejected')}>Reject</button></> : null}
                  </div>
                </li>))}</ul> : <p className="evo-small evo-muted">No files.</p>}
              {(room.interest || []).length ? <><h3 className="bi-h">Responses</h3>
                <ul className="bi-list">{room.interest.map((o, i) => <li key={i}>{cap(o.status)}{o.offer ? ` · ${usd(o.offer)}` : ''}<span className="evo-small evo-muted">{when(o.at)}</span></li>)}</ul></> : null}
            </div>
          ) : null}
        </>
      )}
    </Drawer>
  )
}

// ── listings ───────────────────────────────────────────────────────────────
function Listings({ includeTest, bump, done, fail }) {
  const [d, setD] = useState(null)
  const [aud, setAud] = useState(null)
  useEffect(() => { api.get(`${DRS}/listings?include_test=${includeTest}`).then(setD).catch(fail) }, [includeTest, bump, fail])
  const add = async (dealId) => { try { await api.post(`${DRS}/deals/${dealId}/listing`, {}); done('Added as a draft listing.') } catch (e) { fail(e) } }
  const go = async (l, action) => {
    if (action === 'publish' && !window.confirm('Publish this listing to the selected investors?')) return
    try { await api.post(`${DRS}/listings/${l.id}/${action}`, {}); done(`${ACTION_LABEL[action]}: done.`) } catch (e) { fail(e) }
  }
  const setAudience = async (l, audience) => { try { await api.patch(`${DRS}/listings/${l.id}`, { audience }); done('Audience saved.') } catch (e) { fail(e) } }
  const preview = async (l) => { try { setAud({ l, rows: (await api.get(`${DRS}/listings/${l.id}/audience`)).investors }) } catch (e) { fail(e) } }
  if (!d) return <Skeleton rows={4} />
  return (
    <>
      <Panel title="Deal Room listings" count={d.listings.length} flush>
        {d.listings.length ? (
          <table className="evo-table">
            <thead><tr><th>Property</th><th>State</th><th>Who sees it</th><th>Interest</th><th>Actions</th></tr></thead>
            <tbody>{d.listings.map((l) => {
              const active = (l.holds || []).find((h) => h.status === 'active')
              return (
                <tr key={l.id}>
                  <td><b>{l.address}</b> {l.is_test ? <Tag>Practice</Tag> : null}<div className="evo-small evo-muted">Published {when(l.published_at)}</div></td>
                  <td><Tag kind={STATE_TONE[l.state]}>{cap(l.state)}</Tag>{active ? <div className="evo-small">{active.buyer_name} · <Left endsAt={active.ends_at} /></div> : null}</td>
                  <td>
                    <select className="evo-input dr-sel" aria-label="Audience" value={l.audience} onChange={(e) => setAudience(l, e.target.value)}>
                      <option value="matched">Matched investors (score ≥ {l.min_match_score})</option>
                      <option value="all">All Deal Room investors</option>
                      <option value="selected">Selected investors</option>
                    </select>
                    <button type="button" className="evo-link evo-small" onClick={() => preview(l)}>Preview audience</button>
                  </td>
                  <td className="evo-small">{(l.submissions || []).length ? l.submissions.map((s, i) => <div key={i}>{s.buyer_name || 'Investor'}: {cap(s.status)}{s.offer ? ` ${usd(s.offer)}` : ''}</div>) : '—'}</td>
                  <td><div className="dr-actions">{(NEXT[l.state] || []).map((a) => (
                    <button key={a} className={`evo-btn evo-btn--sm${a === 'publish' || a === 'approve' ? ' evo-btn--primary' : ''}`} onClick={() => go(l, a)}>{ACTION_LABEL[a]}</button>))}</div></td>
                </tr>)
            })}</tbody>
          </table>
        ) : <Empty title="Nothing listed yet">Add a deal whose investor package is approved and published.</Empty>}
      </Panel>
      <Panel title="Ready to add" count={(d.available || []).length} hint="Deals with an approved, published investor package.">
        {(d.available || []).length ? <ul className="bi-list">{d.available.map((a) => (
          <li key={a.deal_id}><span><b>{a.address || 'Deal'}</b> {a.is_test ? <Tag>Practice</Tag> : null} <span className="evo-muted evo-small">{usd(a.asking_price)}</span></span>
            <button className="evo-btn evo-btn--sm" onClick={() => add(a.deal_id)}>Add to Deal Room</button></li>))}</ul>
          : <p className="evo-small evo-muted">No other published packages. Approve and publish a deal package from the deal first.</p>}
      </Panel>
      {aud ? (
        <Drawer open title="Who would see this" sub={aud.l.address} onClose={() => setAud(null)}>
          <ul className="bi-list">{aud.rows.map((r) => (
            <li key={r.buyer_id}><span>{r.name}</span><span>{r.match !== null && r.match !== undefined ? `${r.match}%` : '—'} {r.eligible ? <Tag kind="live">Sees it</Tag> : <Tag>Hidden</Tag>}</span></li>))}</ul>
          {!aud.rows.length ? <p className="evo-small evo-muted">No investors have joined the Deal Room yet.</p> : null}
        </Drawer>
      ) : null}
    </>
  )
}

// ── invitations ────────────────────────────────────────────────────────────
function Invites({ includeTest, bump, done, fail }) {
  const [d, setD] = useState(null)
  const [f, setF] = useState({ name: '', email: '', company: '', is_test: true })
  const [link, setLink] = useState(null)
  useEffect(() => { api.get(`${DRS}/invites?include_test=${includeTest}`).then(setD).catch(fail) }, [includeTest, bump, fail])
  const create = async (e) => {
    e.preventDefault()
    try { const r = await api.post(`${DRS}/invites`, f); setLink(r); setF({ name: '', email: '', company: '', is_test: f.is_test }); done('Invitation created.') } catch (x) { fail(x) }
  }
  const resend = async (i) => { try { setLink(await api.post(`${DRS}/invites/${i.id}/resend`, {})); done('New link issued; the old one no longer works.') } catch (x) { fail(x) } }
  const revoke = async (i) => { if (!window.confirm(`Revoke ${i.name}'s invitation?`)) return; try { await api.post(`${DRS}/invites/${i.id}/revoke`, {}); done('Invitation revoked.') } catch (x) { fail(x) } }
  const copy = async () => { try { await navigator.clipboard.writeText(link.link); done('Link copied.') } catch { /* select manually */ } }
  return (
    <div className="bi-grid">
      <Panel title="Invite an investor" hint="Practice invitations use a .invalid address and are never emailed.">
        <form onSubmit={create} className="dr-form">
          <label className="bi-filter">Name<input className="evo-input" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="bi-filter">Email<input className="evo-input" required type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
          <label className="bi-filter">Company (optional)<input className="evo-input" value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></label>
          <label className="evo-small"><input type="checkbox" checked={f.is_test} onChange={(e) => setF({ ...f, is_test: e.target.checked })} /> Practice invitation</label>
          <button className="evo-btn evo-btn--primary">Create invitation</button>
        </form>
        {link ? (
          <div className="dr-link" role="status">
            <div className="evo-small"><b>{link.name}</b> · {words(link.delivery)}</div>
            <code>{link.link}</code>
            <div className="dr-actions"><button className="evo-btn evo-btn--sm" onClick={copy}>Copy link</button>
              <span className="evo-small evo-muted">Shown once. It works one time and expires {when(link.expires_at)}.</span></div>
          </div>
        ) : null}
      </Panel>
      <Panel title="Invitations" count={d ? d.invites.length : null} flush>
        {!d ? <Skeleton rows={3} /> : d.invites.length ? (
          <table className="evo-table">
            <thead><tr><th>Investor</th><th>Status</th><th>Sent</th><th /></tr></thead>
            <tbody>{d.invites.map((i) => (
              <tr key={i.id}>
                <td><b>{i.name}</b>{i.is_test ? <> <Tag>Practice</Tag></> : null}<div className="evo-small evo-muted">{i.email}</div></td>
                <td><Tag kind={i.status === 'accepted' ? 'live' : i.status === 'pending' ? 'info' : undefined}>{cap(i.status)}</Tag></td>
                <td className="evo-small">{i.send_count}× · {when(i.last_sent_at)}</td>
                <td><div className="dr-actions">{i.status === 'pending' || i.status === 'expired' ? <>
                  <button className="evo-btn evo-btn--sm" onClick={() => resend(i)}>New link</button>
                  {i.status === 'pending' ? <button className="evo-btn evo-btn--sm" onClick={() => revoke(i)}>Revoke</button> : null}</> : null}</div></td>
              </tr>))}</tbody>
          </table>
        ) : <Empty title="No invitations yet" />}
      </Panel>
    </div>
  )
}
