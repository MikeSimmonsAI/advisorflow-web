/* DEAL ROOM - the owner's management screen (/wholesale/deal-room).
 *
 * Not the investor portal: this is where the owner sets the room up, invites
 * investors, adds deals from approved investor packages, answers messages,
 * reviews documents, decides exclusive reviews and watches activity. Every
 * button calls a real endpoint (/wholesale/deal-room/*, the deal package and
 * approval endpoints, the contract send). Counts are live records.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../../api/client'
import { Alert, Drawer, Empty, EvoApp, Skeleton, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import { errText } from '../wsShared'
import { DealPackagePanel } from '../wsPackage'
import { DRS, InviteDrawer, TimeLeft, cap, day, usd, when, words } from './shared'
import SetupWizard from './Setup'
import OwnerDashboard from './OwnerDashboard'
import './dealroom.css'

const VIEWS = [
  ['overview', 'Overview'], ['investors', 'Investors'], ['listings', 'Listings'], ['holds', 'Exclusive reviews'],
  ['messages', 'Messages'], ['documents', 'Documents & signatures'], ['activity', 'Activity'], ['settings', 'Settings'],
]
const STATE_TONE = { draft: undefined, approved: 'info', published: 'live', exclusive_review: 'estimate', expired: undefined,
  withdrawn: undefined, closed: undefined }
const NEXT = { draft: ['approve', 'withdraw'], approved: ['publish', 'redraft', 'withdraw'], published: ['withdraw', 'close'],
  exclusive_review: ['withdraw', 'close'], expired: ['publish', 'redraft', 'close'], withdrawn: ['publish', 'redraft', 'close'], closed: [] }
const ACTION_LABEL = { approve: 'Approve', publish: 'Publish', withdraw: 'Withdraw', close: 'Mark closed', redraft: 'Back to draft' }

export default function DealRoom() {
  const { view: pathView } = useParams()
  const [params] = useSearchParams()
  const nav = useNavigate()
  const asked = pathView || params.get('view') || 'overview'
  const view = VIEWS.some(([k]) => k === asked) ? asked : 'overview'
  const go = useCallback((v, extra = {}) => {
    const q = new URLSearchParams(extra).toString()
    nav(v === 'overview' ? '/wholesale/deal-room' : `/wholesale/deal-room/${v}${q ? '?' + q : ''}`)
    window.scrollTo && window.scrollTo({ top: 0 })
  }, [nav])
  const [practice, setPractice] = useState(() => { try { return localStorage.getItem('drm.practice') !== '0' } catch { return true } })
  useEffect(() => { try { localStorage.setItem('drm.practice', practice ? '1' : '0') } catch { /* private */ } }, [practice])
  const [market, setMarket] = useState('')
  const [ov, setOv] = useState(null)
  const [err, setErr] = useState(null)
  const [ok, setOk] = useState(null)
  const [bump, setBump] = useState(0)
  const [inviting, setInviting] = useState(false)
  const [adding, setAdding] = useState(false)
  const [pv, setPv] = useState(null)
  const done = useCallback((m) => { setErr(null); if (m) setOk(m); setBump((x) => x + 1) }, [])
  const fail = useCallback((e) => { setOk(null); setErr(errText(e)) }, [])
  useEffect(() => {
    api.get(`${DRS}/overview?include_test=${practice}`).then(setOv).catch(fail)
  }, [practice, bump, fail])
  const active = ov && ov.status === 'active'
  const preview = async (l) => { try { setPv({ l, d: await api.get(`${DRS}/listings/${l.id}/preview`) }) } catch (e) { fail(e) } }
  const p = { practice, bump, done, fail, go, ov, active }
  const markets = (ov && ov.markets) || ['Dallas', 'Tarrant', 'Collin', 'Denton']
  return (
    <EvoApp world="operations">
      <div className="od-pagehead">
        <div>
          {view === 'overview' ? <p className="od-title">Owner Deal Room</p>
            : <h1 className="od-title"><button type="button" className="od-crumb" onClick={() => go('overview')}>Owner Deal Room</button> <span aria-hidden="true">›</span> <span className="od-title__view">{(VIEWS.find(([k]) => k === view) || [])[1]}</span></h1>}
          <p className="od-sub">Manage Investors · Publish Deals · Track Activity{ov && ov.name ? ` · ${ov.name}` : ''}</p>
        </div>
        <div className="od-pagehead__side">
          <label className="od-market"><span className="evo-sr">Market</span>
            <select value={market} onChange={(e) => setMarket(e.target.value)}>
              <option value="">Dallas - Fort Worth</option>
              {markets.map((m) => <option key={m} value={m}>{m} County</option>)}
            </select></label>
          <label className="drm-switch drm-switch--dark"><input type="checkbox" checked={practice} onChange={(e) => setPractice(e.target.checked)} />Practice records</label>
          <span className={`drm-pill drm-pill--${ov ? ov.status : 'unknown'}`}>{ov ? (active ? 'Active' : ov.status === 'paused' ? 'Paused' : 'Setup') : '…'}</span>
        </div>
      </div>
      {view !== 'overview' ? (
        <nav className="drm-tabs" aria-label="Deal Room sections">
          {VIEWS.map(([k, l]) => {
            const c = ov && ov.counts
            const n = !c ? null : { investors: c.investors, listings: c.listings_live, holds: c.holds_pending,
              messages: c.messages_unread, documents: c.files_to_review }[k]
            return (
              <button key={k} type="button" className={view === k ? 'is-on' : ''} aria-current={view === k ? 'page' : undefined}
                      onClick={() => go(k)}>{k === 'overview' ? 'Dashboard' : l}{n ? <span className="drm-tabs__n">{n}</span> : null}</button>
            )
          })}
        </nav>
      ) : null}
      <Alert>{err}</Alert>
      <Alert kind="ok">{ok}</Alert>
      {view === 'overview' ? <OwnerDashboard {...p} market={market} onInvite={() => setInviting(true)} onAdd={() => setAdding(true)} onPreview={preview} /> : null}
      {view === 'investors' ? <Investors {...p} openBuyer={params.get('buyer')} onInvite={() => setInviting(true)} /> : null}
      {view === 'listings' ? <Listings {...p} onAdd={() => setAdding(true)} /> : null}
      {view === 'holds' ? <Holds {...p} /> : null}
      {view === 'messages' ? <Messages {...p} thread={params.get('thread')} /> : null}
      {view === 'documents' ? <Documents {...p} /> : null}
      {view === 'activity' ? <Activity {...p} /> : null}
      {view === 'settings' ? <SetupWizard onSaved={(m) => done(m)} onFail={fail} status={ov && ov.status} /> : null}
      <InviteDrawer open={inviting} onClose={() => setInviting(false)} roomActive={active} onDone={done} />
      {adding ? <AddDeal {...p} onClose={() => setAdding(false)} /> : null}
      {pv ? <PreviewDrawer pv={pv} onClose={() => setPv(null)} done={done} fail={fail} /> : null}
    </EvoApp>
  )
}

function Feed({ events }) {
  return (
    <ol className="drm-feed">
      {events.map((e, i) => (
        <li key={i}><span className="drm-feed__dot" aria-hidden="true" />
          <span><b>{e.summary}</b><small>{e.by} · {when(e.at)}</small></span></li>
      ))}
    </ol>
  )
}

// ── investors ──────────────────────────────────────────────────────────────
function Investors({ practice, bump, done, fail, go, onInvite, openBuyer }) {
  const [d, setD] = useState(null)
  const [inv, setInv] = useState(null)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const [link, setLink] = useState(null)
  useEffect(() => {
    const t = setTimeout(() => {
      api.get(`${DRS}/investors?include_test=${practice}${q ? '&q=' + encodeURIComponent(q) : ''}`).then((x) => setD(x.investors)).catch(fail)
    }, 200)
    return () => clearTimeout(t)
  }, [practice, bump, q, fail])
  useEffect(() => { api.get(`${DRS}/invites?include_test=${practice}`).then((x) => setInv(x.invites)).catch(fail) }, [practice, bump, fail])
  useEffect(() => { if (openBuyer && d && !open) { const x = d.find((y) => y.buyer_id === openBuyer); if (x) setOpen(x) } }, [openBuyer, d]) // eslint-disable-line react-hooks/exhaustive-deps
  const resend = async (i) => { try { setLink(await api.post(`${DRS}/invites/${i.id}/resend`, {})); done('New link issued; the old one no longer works.') } catch (x) { fail(x) } }
  const revoke = async (i) => { if (!window.confirm(`Revoke ${i.name}'s invitation?`)) return; try { await api.post(`${DRS}/invites/${i.id}/revoke`, {}); done('Invitation revoked.') } catch (x) { fail(x) } }
  const pending = (inv || []).filter((i) => i.status !== 'accepted')
  return (
    <>
      <section className="drm-card">
        <div className="drm-card__head">
          <h2 className="drm-h3">Registered investors {d ? <span className="drm-count">{d.length}</span> : null}</h2>
          <div className="drm-row">
            <label className="evo-sr" htmlFor="drm-q">Search investors</label>
            <input id="drm-q" className="evo-input drm-search" placeholder="Search name, company or email" value={q} onChange={(e) => setQ(e.target.value)} />
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={onInvite}>Invite investor</button>
          </div>
        </div>
        {!d ? <Skeleton rows={4} /> : d.length ? (
          <div className="drm-people">
            {d.map((x) => (
              <button type="button" key={x.account_id} className="drm-person" onClick={() => setOpen(x)}>
                <span className="drm-avatar" aria-hidden="true">{(x.name || '?').slice(0, 1)}</span>
                <span className="drm-person__main">
                  <b>{x.name}</b>
                  <small>{x.company || (x.kind === 'individual' ? 'Buys as an individual' : 'Company not given')}{x.is_test ? ' · practice' : ''}</small>
                  <span className="drm-chips">
                    {x.identity_complete ? <Tag kind="info">Identity complete</Tag> : <Tag>Identity incomplete</Tag>}
                    {x.intel ? <Tag kind={x.intel.funding_documented ? 'live' : undefined}>{x.intel.readiness}</Tag> : null}
                    {x.status !== 'active' ? <Tag kind="danger">{cap(x.status)}</Tag> : null}
                    {x.unread ? <Tag kind="danger">{x.unread} unread</Tag> : null}
                  </span>
                </span>
                <span className="drm-person__side">{x.intel ? <><b>{x.intel.enrichment}%</b><small>enriched</small></> : null}</span>
              </button>
            ))}
          </div>
        ) : <Empty title="No investors yet">Invite an investor - they appear here when they accept.</Empty>}
      </section>
      <section className="drm-card">
        <div className="drm-card__head"><h2 className="drm-h3">Invitations <span className="drm-count">{pending.length}</span></h2></div>
        {link ? (
          <div className="drm-linkbox" role="status">
            <p className="drm-linkbox__t"><b>{link.name}</b> · new secure link (shown once)</p>
            <input readOnly value={link.link} onFocus={(e) => e.target.select()} aria-label="Secure invitation link" />
            <div className="drm-row"><button type="button" className="evo-btn evo-btn--sm" onClick={() => navigator.clipboard && navigator.clipboard.writeText(link.link)}>Copy link</button>
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setLink(null)}>Done</button></div>
          </div>
        ) : null}
        {!inv ? <Skeleton rows={2} /> : pending.length ? (
          <div className="evo-table-wrap"><table className="evo-table">
            <thead><tr><th scope="col">Investor</th><th scope="col">Status</th><th scope="col">Sent</th><th scope="col">Delivery</th><th scope="col"><span className="evo-sr">Actions</span></th></tr></thead>
            <tbody>{pending.map((i) => (
              <tr key={i.id}>
                <td><b>{i.name}</b>{i.is_test ? <> <Tag>Practice</Tag></> : null}<div className="evo-small evo-muted">{i.email}</div></td>
                <td><Tag kind={i.status === 'pending' ? 'info' : undefined}>{i.status === 'pending' ? 'Not registered yet' : cap(i.status)}</Tag></td>
                <td className="evo-small">{i.send_count}× · last {when(i.last_sent_at)}</td>
                <td className="evo-small">{i.delivery && i.delivery.startsWith('not_sent') ? 'Link only (not emailed)' : cap(i.delivery)}</td>
                <td><div className="drm-row">{i.status === 'pending' || i.status === 'expired' ? <>
                  <button type="button" className="evo-btn evo-btn--sm" onClick={() => resend(i)}>New link</button>
                  {i.status === 'pending' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => revoke(i)}>Revoke</button> : null}</> : null}</div></td>
              </tr>))}</tbody>
          </table></div>
        ) : <p className="evo-small evo-muted">No open invitations.</p>}
      </section>
      {open ? <InvestorDrawer x={open} onClose={() => setOpen(null)} done={done} fail={fail} go={go} /> : null}
    </>
  )
}

function InvestorDrawer({ x, onClose, done, fail, go }) {
  const [room, setRoom] = useState(null)
  const nav = useNavigate()
  useEffect(() => { api.get(`${DRS}/buyers/${x.buyer_id}`).then(setRoom).catch(fail) }, [x.buyer_id, fail])
  const setStatus = async (status) => {
    try { await api.post(`${DRS}/accounts/${x.account_id}/status`, { status }); done(status === 'active' ? 'Access restored.' : 'Access suspended.'); onClose() } catch (e) { fail(e) }
  }
  return (
    <Drawer open wide title={x.name} sub={[x.company, x.email].filter(Boolean).join(' · ')} onClose={onClose}
            footer={<div className="drm-row drm-row--gap">
              <button type="button" className="evo-btn evo-btn--primary" onClick={() => nav(`/wholesale/buyers?buyer=${x.buyer_id}`)}>Open full buyer profile</button>
              <button type="button" className="evo-btn evo-btn--secondary" onClick={() => { onClose(); go('messages') }}>Messages</button>
              {x.status === 'active' ? <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setStatus('suspended')}>Suspend access</button>
                : <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setStatus('active')}>Restore access</button>}
            </div>}>
      <div className="drm-dl">
        <div><dt>Buys as</dt><dd>{x.kind === 'business' ? `${x.company || 'Company'}${x.entity_type ? ' (' + x.entity_type.toUpperCase() + ')' : ''}` : 'Individual'}</dd></div>
        <div><dt>Legal name</dt><dd>{x.legal_name || '—'}</dd></div>
        <div><dt>Position</dt><dd>{x.position || '—'}</dd></div>
        <div><dt>Phone</dt><dd>{x.phone || '—'}</dd></div>
        <div><dt>Email</dt><dd>{x.email}{x.email_verified ? ' (verified)' : ''}</dd></div>
        <div><dt>Joined</dt><dd>{day(x.joined_at)}</dd></div>
      </div>
      {x.intel ? (
        <><h3 className="drm-h4">Buyer Intelligence</h3>
          <div className="drm-dl">
            <div><dt>Verification</dt><dd>{x.intel.headline}</dd></div>
            <div><dt>Readiness</dt><dd>{x.intel.readiness}</dd></div>
            <div><dt>Buy box</dt><dd>{x.intel.buy_box}</dd></div>
            <div><dt>Contact</dt><dd>{x.intel.contact}</dd></div>
            <div><dt>Research completeness</dt><dd>{x.intel.enrichment}%</dd></div>
            <div><dt>Proof of funds</dt><dd>{x.intel.funding_documented ? 'Accepted by a person' : 'Not documented'}</dd></div>
          </div>
          <p className="evo-small evo-muted">Edit criteria, purchase evidence and contact facts in the full buyer profile.</p></>
      ) : null}
      <h3 className="drm-h4">Entities and representatives</h3>
      {x.entities.length ? <ul className="drm-list">{x.entities.map((e, i) => (
        <li key={i}><span><b>{e.name}</b><small>{[e.position, e.authority && cap(e.authority)].filter(Boolean).join(' · ')}</small></span>
          <Tag kind={e.status === 'confirmed' ? 'live' : e.status === 'needs_review' ? 'danger' : undefined}>{e.status === 'needs_review' ? 'Needs review' : cap(e.status)}</Tag></li>))}</ul>
        : <p className="evo-small evo-muted">No entities named.</p>}
      {x.representatives.length ? <ul className="drm-list">{x.representatives.map((r, i) => (
        <li key={i}><span><b>{r.name}</b><small>{r.email} · representative</small></span><Tag>{cap(r.status)}</Tag></li>))}</ul> : null}
      {room ? (
        <><h3 className="drm-h4">Conversations</h3>
          {room.threads.length ? <ul className="drm-list">{room.threads.map((t) => (
            <li key={t.id}><span><b>{cap(t.kind)}</b><small>{t.ai_enabled ? 'Concierge answering' : 'Team answering'} · {when(t.last_message_at)}</small></span>
              <button type="button" className="evo-btn evo-btn--sm" onClick={() => { onClose(); go('messages', { thread: t.id }) }}>Open</button></li>))}</ul>
            : <p className="evo-small evo-muted">No conversations yet.</p>}
          <h3 className="drm-h4">Responses to deals</h3>
          {room.interest.length ? <ul className="drm-list">{room.interest.map((o, i) => (
            <li key={i}><span><b>{cap(o.status)}</b><small>{when(o.at)}</small></span><span>{o.offer ? usd(o.offer) : ''}</span></li>))}</ul>
            : <p className="evo-small evo-muted">No responses yet.</p>}
          <h3 className="drm-h4">Documents and signatures</h3>
          <FileRows files={room.files} done={done} fail={fail} />
          {room.signatures.length ? <ul className="drm-list">{room.signatures.map((s) => (
            <li key={s.envelope_id}><span><b>{s.title}</b><small>{s.signer} · {cap(s.signer_status)}</small></span><Tag kind={s.status === 'completed' ? 'live' : 'info'}>{cap(s.status)}</Tag></li>))}</ul> : null}
        </>
      ) : <Skeleton rows={3} />}
    </Drawer>
  )
}

function FileRows({ files, done, fail, showBuyer }) {
  const open = async (f) => {
    try { const u = await fetchObjectUrl(`${DRS}/files/${f.id}`); window.open(u, '_blank', 'noopener'); setTimeout(() => URL.revokeObjectURL(u), 60000) } catch (e) { fail(e) }
  }
  const review = async (f, status) => {
    const body = { status }
    if (status === 'accepted' && f.category === 'proof_of_funds') {
      const exp = window.prompt('Proof of funds valid until (YYYY-MM-DD)', '')
      if (exp) body.pof_expires = exp
    }
    try { await api.post(`${DRS}/files/${f.id}/review`, body); done(status === 'accepted' ? 'Document accepted.' : 'Document rejected.') } catch (e) { fail(e) }
  }
  if (!files || !files.length) return <p className="evo-small evo-muted">No files.</p>
  return (
    <ul className="drm-list">
      {files.map((f) => (
        <li key={f.id}>
          <span><button type="button" className="evo-link" onClick={() => open(f)}><b>{f.name}</b></button>
            <small>{showBuyer && f.buyer_name ? f.buyer_name + ' · ' : ''}{cap(f.category)} · {Math.max(1, Math.round(f.size / 1024))} KB · {f.from_you ? 'uploaded by investor' : 'shared by team'} · {when(f.uploaded_at)}
              {f.scan_detail ? ' · ' + f.scan_detail : ''}</small></span>
          <span className="drm-row">
            <Tag kind={f.review_status === 'accepted' ? 'live' : f.review_status === 'rejected' || f.scan_status === 'blocked' ? 'danger' : 'info'}>
              {f.from_you ? cap(f.review_status || 'pending') : 'Shared'}</Tag>
            {f.from_you && f.review_status === 'pending' ? <>
              <button type="button" className="evo-btn evo-btn--sm" onClick={() => review(f, 'accepted')}>Accept</button>
              <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => review(f, 'rejected')}>Reject</button></> : null}
          </span>
        </li>
      ))}
    </ul>
  )
}

// ── listings ───────────────────────────────────────────────────────────────
function Listings({ practice, bump, done, fail, onAdd }) {
  const [d, setD] = useState(null)
  const [pv, setPv] = useState(null)
  const [aud, setAud] = useState(null)
  useEffect(() => { api.get(`${DRS}/listings?include_test=${practice}`).then(setD).catch(fail) }, [practice, bump, fail])
  const act = async (l, a) => {
    if (a === 'publish' && !window.confirm('Publish this listing to the chosen investors?')) return
    if (a === 'withdraw' && !window.confirm('Withdraw this listing? Investors stop seeing it; their responses are kept.')) return
    try { await api.post(`${DRS}/listings/${l.id}/${a}`, {}); done(`${ACTION_LABEL[a]}: done.`) } catch (e) { fail(e) }
  }
  const patch = async (l, body, msg) => { try { await api.patch(`${DRS}/listings/${l.id}`, body); done(msg) } catch (e) { fail(e) } }
  const preview = async (l) => { try { setPv({ l, d: await api.get(`${DRS}/listings/${l.id}/preview`) }) } catch (e) { fail(e) } }
  const audience = async (l) => { try { setAud({ l, rows: (await api.get(`${DRS}/listings/${l.id}/audience`)).investors }) } catch (e) { fail(e) } }
  if (!d) return <Skeleton rows={4} />
  return (
    <>
      <section className="drm-card">
        <div className="drm-card__head"><h2 className="drm-h3">Listings <span className="drm-count">{d.listings.length}</span></h2>
          <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={onAdd}>Add deal to room</button></div>
        {d.listings.length ? (
          <div className="drm-listings">
            {d.listings.map((l) => {
              const hold = (l.holds || []).find((h) => h.status === 'active')
              return (
                <article key={l.id} className="drm-listing">
                  <div className="drm-listing__head">
                    <div><h3>{l.address}</h3>
                      <p className="evo-small evo-muted">{l.published_at ? `Published ${when(l.published_at)}` : 'Not published'}{l.expires_at ? ` · closes ${day(l.expires_at)}` : ' · no closing date'}{l.is_test ? ' · practice' : ''}</p></div>
                    <Tag kind={STATE_TONE[l.state]}>{cap(l.state)}</Tag>
                  </div>
                  {hold ? <p className="drm-holdline">Exclusive review: <b>{hold.buyer_name}</b> · <TimeLeft endsAt={hold.ends_at} short /></p> : null}
                  <div className="drm-listing__grid">
                    <label className="drm-field"><span>Who sees it</span>
                      <select value={l.audience} onChange={(e) => patch(l, { audience: e.target.value }, 'Audience saved.')}>
                        <option value="matched">Matched investors (score ≥ {l.min_match_score})</option>
                        <option value="all">Every Deal Room investor</option>
                        <option value="selected">Selected investors only</option>
                      </select></label>
                    <label className="drm-field"><span>Closes to new interest</span>
                      <input type="date" value={l.expires_at ? l.expires_at.slice(0, 10) : ''}
                             onChange={(e) => patch(l, { expires_at: e.target.value ? e.target.value + 'T23:59:00' : null }, 'Closing date saved.')} /></label>
                  </div>
                  <div className="drm-listing__resp">
                    <b>Responses</b>
                    {(l.submissions || []).length ? l.submissions.map((s, i) => (
                      <span key={i} className="drm-resp">{s.buyer_name || 'Investor'}: {cap(s.status)}{s.offer ? ' ' + usd(s.offer) : ''}
                        {s.status === 'offer_submitted' || s.status === 'interested' ? <ESignStart deal={l.deal_id} buyer={s} done={done} fail={fail} test={l.is_test} /> : null}</span>
                    )) : <span className="evo-small evo-muted">None yet</span>}
                  </div>
                  <div className="drm-row drm-row--gap">
                    <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => preview(l)}>Preview as investor</button>
                    <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => audience(l)}>Who sees it</button>
                    {(NEXT[l.state] || []).map((a) => (
                      <button key={a} type="button" className={`evo-btn evo-btn--sm ${a === 'publish' || a === 'approve' ? 'evo-btn--primary' : 'evo-btn--ghost'}`} onClick={() => act(l, a)}>{ACTION_LABEL[a]}</button>))}
                    <Link className="evo-btn evo-btn--ghost evo-btn--sm" to={`/wholesale/deals/${l.deal_id}`}>Open deal</Link>
                  </div>
                </article>
              )
            })}
          </div>
        ) : <Empty title="No deals in the room yet">Add a deal whose investor package is approved. <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={onAdd}>Add deal</button></Empty>}
      </section>
      {pv ? <PreviewDrawer pv={pv} onClose={() => setPv(null)} done={done} fail={fail} /> : null}
      {aud ? (
        <Drawer open title="Who sees this listing" sub={aud.l.address} onClose={() => setAud(null)}>
          <ul className="drm-list">{aud.rows.map((r) => (
            <li key={r.buyer_id}><span><b>{r.name}</b><small>{r.match !== null && r.match !== undefined ? `Match ${r.match}%` : 'No buy box'}</small></span>
              {r.eligible ? <Tag kind="live">Sees it</Tag> : <Tag>Hidden</Tag>}</li>))}</ul>
          {!aud.rows.length ? <p className="evo-small evo-muted">No investors have joined yet.</p> : null}
        </Drawer>
      ) : null}
    </>
  )
}

function ESignStart({ deal, buyer, done, fail, test }) {
  const [busy, setBusy] = useState(false)
  const start = async () => {
    if (!window.confirm(`${test ? 'PRACTICE: ' : ''}Assign this deal to ${buyer.buyer_name || 'this investor'} and send the approved assignee disclosure for e-signature?${test ? '' : ' This is a real document.'}`)) return
    setBusy(true)
    try {
      await api.post(`/wholesale/deals/${deal}/assign`, { buyer_id: buyer.buyer_id, buyer_price: buyer.offer || undefined })
      const kind = 'assignee_disclosure'   // the approved assignee disclosure template
      const r = await api.post(`/wholesale/deals/${deal}/contracts/${kind}/send`, {})
      done(r && r.sent ? 'Assignee disclosure sent for signature - the investor signs it in Documents.' : 'Not sent: ' + ((r && (r.reason || r.detail)) || 'see the deal'))
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  return <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={start}>{busy ? 'Sending…' : 'Send disclosure to sign'}</button>
}

function StaffPhoto({ listingId, fileId, className = '' }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    let u = null; let alive = true
    if (listingId && fileId) fetchObjectUrl(`${DRS}/listings/${listingId}/photos/${fileId}`).then((x) => { u = x; if (alive) setSrc(x) }).catch(() => {})
    return () => { alive = false; if (u) URL.revokeObjectURL(u) }
  }, [listingId, fileId])
  return <div className={`drm-photo ${className}`}>{src ? <img src={src} alt="" /> : <span aria-hidden="true">◇</span>}</div>
}

function PreviewDrawer({ pv, onClose, done, fail }) {
  const room = pv.d.room
  const [docs, setDocs] = useState(() => pv.d.all_documents.filter((x) => x.visible).map((x) => x.id))
  const saveDocs = async () => {
    try { await api.put(`${DRS}/listings/${pv.l.id}/documents`, { visible_document_ids: docs }); done('Visible documents saved.') } catch (e) { fail(e) }
  }
  return (
    <Drawer open wide title="Investor preview" sub={pv.d.notice} onClose={onClose}>
      <InvestorCard listingId={pv.l.id} room={room} facts={pv.d.facts} category={pv.d.category} />
      {!pv.d.serving ? <Alert>The investor package is not live (not approved, or changed after approval) - investors cannot see this until it is approved and published again.</Alert> : null}
      <h3 className="drm-h4">About this property</h3>
      <p>{room.summary || 'No summary published.'}</p>
      {pv.d.all_documents.length ? (
        <><h3 className="drm-h4">Documents investors can open</h3>
          {pv.d.all_documents.map((x) => (
            <label key={x.id} className="drm-check"><input type="checkbox" checked={docs.includes(x.id)}
                   onChange={(e) => setDocs(e.target.checked ? [...docs, x.id] : docs.filter((i) => i !== x.id))} />{x.title}</label>
          ))}
          <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={saveDocs}>Save visible documents</button></>
      ) : <p className="evo-small evo-muted">The approved package publishes no documents.</p>}
      {room.disclosure ? <p className="evo-small evo-muted drm-disc"><b>{room.disclosure.title}.</b> {room.disclosure.text}</p> : null}
    </Drawer>
  )
}

export function InvestorCard({ listingId, room, facts, category }) {
  const a = room.property || {}
  const photo = (room.photos || [])[0]
  return (
    <article className="drm-icard" aria-label="Investor card preview">
      <StaffPhoto listingId={listingId} fileId={photo && photo.id} className="drm-icard__photo" />
      <div className="drm-icard__body">
        <div className="drm-icard__price">{usd(room.asking_price)}</div>
        <div className="drm-icard__addr">{a.line1}</div>
        <div className="evo-small evo-muted">{[a.city, a.state, a.zip_code].filter(Boolean).join(', ')}</div>
        <div className="drm-icard__facts">{Object.entries(facts || {}).filter(([, v]) => v).map(([k, v]) => <span key={k}>{v} {words(k).replace('square feet', 'sq ft')}</span>)}</div>
        <div className="drm-chips"><Tag>{cap(category)}</Tag>{'arv' in room ? <Tag kind="info">ARV {usd(room.arv)}</Tag> : null}
          {'estimated_repairs' in room ? <Tag>Repairs {usd(room.estimated_repairs)}</Tag> : null}</div>
      </div>
    </article>
  )
}

// ── add deal ───────────────────────────────────────────────────────────────
function AddDeal({ practice, done, fail, onClose, active }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState(null)
  const [pick, setPick] = useState(null)
  const [busy, setBusy] = useState(false)
  const [f, setF] = useState({ audience: 'matched', days: '14', publishNow: true })
  const [listing, setListing] = useState(null)
  const [pv, setPv] = useState(null)
  const [docs, setDocs] = useState([])
  const [inv, setInv] = useState([])
  const [sel, setSel] = useState([])
  const [n, setN] = useState(0)
  useEffect(() => {
    const t = setTimeout(() => api.get(`${DRS}/candidates?include_test=${practice}${q ? '&q=' + encodeURIComponent(q) : ''}`)
      .then((x) => setRows(x.deals)).catch(fail), 250)
    return () => clearTimeout(t)
  }, [q, practice, n, fail])
  useEffect(() => { api.get(`${DRS}/settings`).then((s) => setF((x) => ({ ...x, audience: s.settings.default_audience || 'matched',
    days: s.settings.default_publish_days ? String(s.settings.default_publish_days) : '' }))).catch(() => {}) }, [])
  useEffect(() => { api.get(`${DRS}/investors?include_test=${practice}`).then((x) => setInv(x.investors)).catch(() => {}) }, [practice])
  const create = async () => {
    setBusy(true)
    try {
      const body = { audience: f.audience }
      if (f.audience === 'selected') body.buyer_ids = sel
      if (f.days) { const d = new Date(Date.now() + Number(f.days) * 86400000); body.expires_at = d.toISOString().slice(0, 19) }
      const l = await api.post(`${DRS}/deals/${pick.deal_id}/listing`, body)
      setListing(l)
      const p = await api.get(`${DRS}/listings/${l.id}/preview`)
      setPv(p); setDocs(p.all_documents.filter((x) => x.visible).map((x) => x.id))
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  const publish = async (now) => {
    setBusy(true)
    try {
      if (pv && pv.all_documents.length) await api.put(`${DRS}/listings/${listing.id}/documents`, { visible_document_ids: docs })
      if (listing.state === 'draft') await api.post(`${DRS}/listings/${listing.id}/approve`, {})
      if (now) await api.post(`${DRS}/listings/${listing.id}/publish`, {})
      done(now ? 'Published to the Deal Room.' : 'Saved and approved - publish it from Listings when ready.')
      onClose()
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  const approvePackage = async (row) => {
    try {
      const st = await api.get(`/wholesale/deals/${row.deal_id}/package`)
      if (!st.approval || st.approval.status !== 'pending') { fail({ message: 'There is no package waiting for approval.' }); return }
      await api.post(`/wholesale/approvals/${st.approval.id}/decide`, { approve: true })
      done('Investor package approved.'); setN((x) => x + 1)
    } catch (e) { fail(e) }
  }
  return (
    <Drawer open wide title="Add deal to the Deal Room" onClose={onClose}
            sub="Pick a Wholesale deal. Only deals with an approved, published investor package can be listed - the rest show the exact step missing.">
      {!pick ? (
        <>
          <label className="evo-sr" htmlFor="drm-cand">Search deals</label>
          <input id="drm-cand" className="evo-input" placeholder="Search address, city or ZIP" value={q} onChange={(e) => setQ(e.target.value)} />
          {!rows ? <Skeleton rows={4} /> : rows.length ? (
            <ul className="drm-cands">
              {rows.map((r) => (
                <li key={r.deal_id} className={r.ready ? 'is-ready' : ''}>
                  <div className="drm-cands__main">
                    <b>{r.address || 'Deal'}</b>
                    <small>{[cap(r.property_type), r.county && r.county + ' County', r.units ? r.units + ' units' : null, r.asking_price ? 'asking ' + usd(r.asking_price) : null, r.is_test ? 'practice' : null].filter(Boolean).join(' · ')}</small>
                    <span className="drm-chips"><Tag kind={r.ready ? 'live' : r.already_listed ? 'info' : 'estimate'}>{r.label}</Tag>
                      <Tag>Package: {r.package_label || cap(r.package)}</Tag></span>
                    {r.steps.length ? <ol className="drm-steps">{r.steps.map((s, i) => <li key={i}>{s}</li>)}</ol> : null}
                  </div>
                  <div className="drm-cands__act">
                    {r.ready ? <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={() => setPick(r)}>Choose</button> : null}
                    {r.already_listed ? <span className="evo-small evo-muted">In the room</span> : null}
                    {!r.ready && !r.already_listed && r.package === 'review' ? <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => approvePackage(r)}>Approve package</button> : null}
                    {!r.ready && !r.already_listed ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setPick({ ...r, fixing: true })}>Fix package</button> : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : <Empty title="No open Wholesale deals found">{q ? 'Nothing matches that search.' : 'Promote a property to a deal first (Acquisition), then build its investor package.'}</Empty>}
        </>
      ) : pick.fixing ? (
        <>
          <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => { setPick(null); setN((x) => x + 1) }}>← Back to deals</button>
          <h3 className="drm-h4">{pick.address}</h3>
          <p className="evo-small">The investor package decides exactly what investors see. Edit its content (summary, asking price, photos) on the deal, send it for approval, then publish it.</p>
          <DealPackagePanel dealId={pick.deal_id} onChanged={() => setN((x) => x + 1)} />
          <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={`/wholesale/deals/${pick.deal_id}`}>Open the deal to edit investor content</Link>
        </>
      ) : !listing ? (
        <>
          <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setPick(null)}>← Back to deals</button>
          <h3 className="drm-h4">{pick.address}</h3>
          <fieldset className="drm-field"><legend>Who can view it</legend>
            {[['matched', 'Investors whose buy box matches'], ['all', 'Every Deal Room investor'], ['selected', 'Only the investors I choose']].map(([k, l]) => (
              <label key={k} className="drm-check"><input type="radio" name="aud" checked={f.audience === k} onChange={() => setF({ ...f, audience: k })} />{l}</label>
            ))}
          </fieldset>
          {f.audience === 'selected' ? (
            <div className="drm-pickers">{inv.length ? inv.map((x) => (
              <label key={x.buyer_id} className="drm-check"><input type="checkbox" checked={sel.includes(x.buyer_id)}
                     onChange={(e) => setSel(e.target.checked ? [...sel, x.buyer_id] : sel.filter((i) => i !== x.buyer_id))} />{x.name}{x.company ? ' · ' + x.company : ''}</label>
            )) : <p className="evo-small evo-muted">No registered investors yet.</p>}</div>
          ) : null}
          <label className="drm-field"><span>Availability window (days open to new interest)</span>
            <input inputMode="numeric" value={f.days} onChange={(e) => setF({ ...f, days: e.target.value.replace(/\D/g, '') })} placeholder="Leave blank for no closing date" /></label>
          {!active && !pick.is_test ? <Alert>Your Deal Room is not active - a real deal can be added and approved now, and published after activation.</Alert> : null}
          <button type="button" className="evo-btn evo-btn--primary" disabled={busy || (f.audience === 'selected' && !sel.length)} onClick={create}>{busy ? 'Preparing…' : 'Continue to preview'}</button>
        </>
      ) : (
        <>
          <h3 className="drm-h4">What investors will see</h3>
          {pv ? <InvestorCard listingId={listing.id} room={pv.room} facts={pv.facts} category={pv.category} /> : <Skeleton rows={3} />}
          {pv && pv.all_documents.length ? (
            <><h3 className="drm-h4">Visible documents</h3>
              {pv.all_documents.map((x) => (
                <label key={x.id} className="drm-check"><input type="checkbox" checked={docs.includes(x.id)}
                       onChange={(e) => setDocs(e.target.checked ? [...docs, x.id] : docs.filter((i) => i !== x.id))} />{x.title}</label>))}</>
          ) : <p className="evo-small evo-muted">The approved package publishes no documents.</p>}
          <p className="evo-small evo-muted">Seller details, the contract price and internal notes are never included.</p>
          <div className="drm-row drm-row--gap">
            <button type="button" className="evo-btn evo-btn--primary" disabled={busy} onClick={() => publish(true)}>Publish now</button>
            <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={() => publish(false)}>Approve, publish later</button>
          </div>
        </>
      )}
    </Drawer>
  )
}

// ── exclusive reviews ──────────────────────────────────────────────────────
function Holds({ practice, bump, done, fail }) {
  const [d, setD] = useState(null)
  const [pick, setPick] = useState({})
  const [rules, setRules] = useState(null)
  useEffect(() => { api.get(`${DRS}/holds`).then(setD).catch(fail) }, [bump, fail])
  useEffect(() => { api.get(`${DRS}/settings`).then((s) => setRules(s.settings)).catch(() => {}) }, [bump])
  const presets = useMemo(() => {
    const base = rules && rules.hold_presets && rules.hold_presets.length ? rules.hold_presets : ['24h', '48h', '72h', '7d']
    return rules && rules.hold_allow_custom === false ? base : [...base, 'custom']
  }, [rules])
  const decide = async (h, decision) => {
    const pr = pick[h.id] || presets[0]
    const body = { decision }
    if (decision === 'approve' || decision === 'extend') {
      if (pr === 'custom') {
        const hrs = Number(window.prompt('How many hours? (up to 720)', '36'))
        if (!hrs) return
        body.minutes = Math.round(hrs * 60)
      } else body.preset = pr
    }
    try { await api.post(`${DRS}/holds/${h.id}`, body); done({ approve: 'Exclusive review approved.', decline: 'Request declined.', extend: 'Exclusive review extended.', release: 'Exclusive review ended - the deal is visible again.' }[decision]) } catch (e) { fail(e) }
  }
  if (!d) return <Skeleton rows={4} />
  const all = d.holds.filter((h) => practice || !h.is_test)
  const soon = Date.now() + 24 * 3600 * 1000
  const groups = [
    ['Pending requests', all.filter((h) => h.status === 'requested'), 'No requests waiting.'],
    ['Active holds', all.filter((h) => h.status === 'active' && new Date(h.ends_at).getTime() > soon), 'No other active holds.'],
    ['Expiring soon (within 24 hours)', all.filter((h) => h.status === 'active' && new Date(h.ends_at).getTime() <= soon), 'Nothing ending in the next 24 hours.'],
    ['Completed', all.filter((h) => h.status === 'ended' || h.status === 'declined'), 'No completed reviews yet.'],
  ]
  const sel = (h) => (
    <select className="evo-input drm-sel" aria-label="Length" value={pick[h.id] || presets[0]} onChange={(e) => setPick({ ...pick, [h.id]: e.target.value })}>
      {presets.map((x) => <option key={x} value={x}>{x === 'custom' ? 'Custom…' : x.replace('h', ' hours').replace('7d', '7 days')}</option>)}
    </select>
  )
  return (
    <>
      <p className="drm-note">{d.notice} Only an owner or manager can approve.</p>
      <div className="drm-holds">
        {groups.map(([title, rows, empty], gi) => (
          <section key={title} className={`drm-card drm-holdcol${gi === 2 && rows.length ? ' is-warn' : ''}`} aria-label={title}>
            <div className="drm-card__head"><h2 className="drm-h3">{title} <span className="drm-count">{rows.length}</span></h2></div>
            {rows.length ? <ul className="drm-list">{rows.map((h) => (
              <li key={h.id} className="drm-hold">
                <span><b>{h.buyer_name}</b><small>{h.address}{h.is_test ? ' · practice' : ''}</small>
                  {h.request_note ? <small>“{h.request_note}”</small> : null}
                  <small>{h.status === 'requested' ? `Asked ${when(h.requested_at)}` : h.status === 'active' ? `Ends ${when(h.ends_at)}${h.extended_count ? ` · extended ${h.extended_count}×` : ''}`
                    : `${cap(h.status)}${h.end_reason ? ' (' + words(h.end_reason) + ')' : ''} · ${when(h.decided_at || h.requested_at)}`}</small></span>
                <span className="drm-hold__act">
                  {h.status === 'active' ? <TimeLeft endsAt={h.ends_at} /> : null}
                  {h.status === 'requested' ? <>{sel(h)}<button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={() => decide(h, 'approve')}>Approve</button>
                    <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => decide(h, 'decline')}>Decline</button></> : null}
                  {h.status === 'active' ? <>{sel(h)}<button type="button" className="evo-btn evo-btn--sm" onClick={() => decide(h, 'extend')}>Extend</button>
                    <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => decide(h, 'release')}>Release now</button></> : null}
                </span>
              </li>))}</ul> : <p className="evo-small evo-muted">{empty}</p>}
          </section>
        ))}
      </div>
    </>
  )
}

// ── messages ───────────────────────────────────────────────────────────────
function Messages({ practice, bump, done, fail, go, thread }) {
  const [d, setD] = useState(null)
  const [team, setTeam] = useState([])
  useEffect(() => { api.get(`${DRS}/inbox?include_test=${practice}`).then((x) => setD(x.threads)).catch(fail) }, [practice, bump, fail])
  useEffect(() => { api.get(`${DRS}/team`).then((x) => setTeam(x.people)).catch(() => {}) }, [])
  useEffect(() => {
    if (!thread && d && d.length && window.matchMedia && window.matchMedia('(min-width: 1000px)').matches) go('messages', { thread: d[0].id })
  }, [thread, d, go])
  return (
    <div className={`drm-inbox${thread ? ' has-thread' : ''}`}>
      <section className="drm-card drm-inbox__list" aria-label="Conversations">
        <div className="drm-card__head"><h2 className="drm-h3">Investor conversations</h2></div>
        {!d ? <Skeleton rows={4} /> : d.length ? (
          <ul className="drm-threads">{d.map((t) => (
            <li key={t.id}><button type="button" className={t.id === thread ? 'is-on' : ''} onClick={() => go('messages', { thread: t.id })}>
              <span className="drm-threads__top"><b>{t.buyer_name}</b>{t.unread ? <span className="drm-badge">{t.unread}</span> : null}</span>
              <span className="drm-threads__k">{cap(t.kind)} · {t.ai_enabled ? 'Concierge' : 'Team'}{t.status === 'handed_off' ? ' · asked for a person' : ''}{t.is_test ? ' · practice' : ''}</span>
              <span className="drm-threads__l">{t.last}</span><small>{when(t.last_message_at)}</small>
            </button></li>))}</ul>
        ) : <Empty title="No conversations yet">Each investor gets a concierge conversation when they join.</Empty>}
      </section>
      {thread ? <Thread key={thread + bump} id={thread} team={team} done={done} fail={fail} onBack={() => go('messages')} /> : null}
    </div>
  )
}

function Thread({ id, team, done, fail, onBack }) {
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
  if (!t) return <section className="drm-card drm-thread"><Skeleton rows={5} /></section>
  const ROLE = { ai: 'Concierge', buyer: 'Investor', staff: 'Team', system: 'Deal Room', internal: 'Internal note' }
  return (
    <section className="drm-card drm-thread" aria-label="Conversation">
      <div className="drm-thread__head">
        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm drm-thread__back" onClick={onBack}>← All</button>
        <div><b>{cap(t.kind)} conversation</b><small>{t.ai_enabled ? 'The concierge is answering' : 'A person is answering'}{t.handoff_reason ? ' · ' + t.handoff_reason : ''}</small></div>
        <label className="drm-field drm-field--inline"><span>Assigned to</span>
          <select value={t.assigned_user_id || ''} onChange={(e) => act('assign', { user_id: e.target.value || null })}>
            <option value="">Nobody</option>{team.map((p) => <option key={p.user_id} value={p.user_id}>{p.name}</option>)}
          </select></label>
      </div>
      <ol className="drm-msgs">
        {(t.transcript || []).map((m, i) => (
          <li key={i} className={`drm-msg drm-msg--${m.role}`}>
            <span className="drm-msg__who">{ROLE[m.role] || cap(m.role)}{m.by_name ? ' · ' + m.by_name : ''}{m.role === 'ai' && m.engine ? (m.engine === 'ai' ? ' · AI' : ' · automatic reply') : ''} · {when(m.at)}</span>
            <p>{m.text}</p>
          </li>
        ))}
      </ol>
      <div className="drm-compose">
        <label className="evo-sr" htmlFor="drm-text">Message</label>
        <textarea id="drm-text" className="evo-input" rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Reply to the investor, or write an internal note" />
        <div className="drm-row drm-row--gap">
          <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={!text.trim()} onClick={() => act('reply', { text })}>Send to investor</button>
          <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={!text.trim()} onClick={() => act('note', { text })}>Internal note</button>
          {t.ai_enabled ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => act('takeover')}>Take over</button>
            : <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => act('return-to-ai')}>Return to concierge</button>}
        </div>
        <p className="evo-small evo-muted">Sending a reply takes over from the concierge. Internal notes are never shown to the investor.</p>
      </div>
      {room ? <div className="drm-thread__files"><h3 className="drm-h4">This investor's documents</h3><FileRows files={room.files} done={(m) => { done(m); setN((x) => x + 1) }} fail={fail} /></div> : null}
    </section>
  )
}

// ── documents & signatures ─────────────────────────────────────────────────
function Documents({ practice, bump, done, fail }) {
  const [d, setD] = useState(null)
  useEffect(() => { api.get(`${DRS}/documents?include_test=${practice}`).then(setD).catch(fail) }, [practice, bump, fail])
  if (!d) return <Skeleton rows={4} />
  const pending = d.files.filter((f) => f.from_you && f.review_status === 'pending')
  return (
    <div className="drm-cols drm-cols--2">
      <section className="drm-card">
        <div className="drm-card__head"><h2 className="drm-h3">Investor documents <span className="drm-count">{d.files.length}</span></h2>
          <span className="evo-small evo-muted">{pending.length} waiting for review · up to 25 MB each</span></div>
        <FileRows files={d.files} done={done} fail={fail} showBuyer />
      </section>
      <section className="drm-card">
        <div className="drm-card__head"><h2 className="drm-h3">E-signatures <span className="drm-count">{d.signatures.length}</span></h2></div>
        {d.signatures.length ? <ul className="drm-list">{d.signatures.map((s) => (
          <li key={s.envelope_id}><span><b>{s.title}</b><small>{s.investor} · sent {when(s.sent_at)}</small>
            <small>{s.parties.map((p) => `${p.role}: ${cap(p.status)}`).join(' · ')}</small></span>
            <span className="drm-row"><Tag kind={s.completed ? 'live' : 'info'}>{s.completed ? 'Completed' : cap(s.status)}</Tag>
              {!s.completed && s.status === 'sent' ? <SignMine env={s.envelope_id} fail={fail} /> : null}
              {s.deal_id ? <Link className="evo-btn evo-btn--ghost evo-btn--sm" to={`/wholesale/deals/${s.deal_id}`}>Deal</Link> : null}</span></li>))}</ul>
          : <p className="evo-small evo-muted">No envelopes yet. Start one from a listing's responses (Send disclosure to sign).</p>}
      </section>
    </div>
  )
}

function SignMine({ env, fail }) {
  const nav = useNavigate()
  const go = async () => {
    try { const r = await api.post(`${DRS}/envelopes/${env}/staff-session`, {}); nav(r.url || `/sign/${r.token}`) } catch (e) { fail(e) }
  }
  return <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={go}>Sign my part</button>
}

function Activity({ bump, fail }) {
  const [d, setD] = useState(null)
  useEffect(() => { api.get(`${DRS}/activity?limit=150`).then((x) => setD(x.events)).catch(fail) }, [bump, fail])
  return (
    <section className="drm-card">
      <div className="drm-card__head"><h2 className="drm-h3">Activity</h2></div>
      {!d ? <Skeleton rows={6} /> : d.length ? <Feed events={d} /> : <Empty title="No activity yet" />}
    </section>
  )
}
