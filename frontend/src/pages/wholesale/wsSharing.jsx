/* THE PUBLICATION DESK.
 *
 * Where an operator decides what leaves this workspace, and to whom. Two
 * audiences, two rooms, one rule: NOTHING IS SHARED UNTIL SOMEBODY SHARES IT.
 * Every switch on this screen is off on a new deal and every one of them is
 * read by the server when it builds the outside page — this is a control
 * surface over the boundary, not a decoration in front of it.
 *
 * WHY EDITING AND PUBLISHING ARE TWO ACTS. You can write the investor summary,
 * set the asking price and tick the photos for an hour without anything being
 * reachable. Publishing is a separate, audited button. Unpublishing stops
 * every existing link on its next request without revoking any of them, so a
 * room can be closed for an afternoon and reopened to the same people.
 *
 * WHY A LINK IS NOT AN EMAIL. This screen mints a link and shows it. It does
 * not send anything, because sending is what the gated disposition path is
 * for, and a second outbound surface here would be a second place to audit.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { Empty, ErrorBox, errText, fmtMoney, fmtWhen, Note, Why } from './wsShared'

const ORIGIN = typeof window !== 'undefined' ? window.location.origin : ''

export function SharingWorkspace({ deal, buyers, act, busy }) {
  const [state, setState] = useState(null)
  const [activity, setActivity] = useState([])
  const [error, setError] = useState(null)
  const [form, setForm] = useState({})
  const [newLink, setNewLink] = useState({
    audience: 'buyer', buyer_id: '', recipient_name: '', recipient_email: '',
    expires_in_days: 30,
  })
  const [copied, setCopied] = useState(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [pub, act2] = await Promise.all([
        api.get(`/wholesale/deals/${deal.id}/publication`),
        api.get(`/wholesale/deals/${deal.id}/share-activity`),
      ])
      setState(pub)
      setActivity(act2.activity || [])
      setForm({})
    } catch (e) { setError(errText(e)) }
  }, [deal.id])

  useEffect(() => { load() }, [load])

  if (error && !state) return <ErrorBox error={error} />
  if (!state) return <Empty page>Loading…</Empty>

  const b = state.buyer
  const s = state.seller
  const dirty = Object.keys(form).length > 0
  const val = (key, fallback) => (key in form ? form[key] : fallback)
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }))

  async function save() {
    const okSaved = await act(
      () => api.patch(`/wholesale/deals/${deal.id}/publication`, form),
      'Saved. Nothing is shared until you publish.')
    if (okSaved) await load()
  }

  async function setPublished(audience, published) {
    const okSet = await act(
      () => api.post(`/wholesale/deals/${deal.id}/publication/state`,
                     { audience, published }),
      published ? 'Room published.' : 'Room closed. Existing links stop working.')
    if (okSet) await load()
  }

  async function createLink() {
    const okMade = await act(
      () => api.post(`/wholesale/deals/${deal.id}/share-links`, {
        ...newLink,
        buyer_id: newLink.audience === 'buyer' ? newLink.buyer_id || null : null,
        expires_in_days: newLink.expires_in_days === ''
          ? null : Number(newLink.expires_in_days),
      }), 'Link created.')
    if (okMade) {
      setNewLink((n) => ({ ...n, recipient_name: '', recipient_email: '' }))
      await load()
    }
  }

  async function revoke(link) {
    const okRev = await act(
      () => api.post(`/wholesale/share-links/${link.id}/revoke`, {}),
      'Link revoked.')
    if (okRev) await load()
  }

  function copy(link) {
    const url = ORIGIN + link.url_path
    if (navigator.clipboard) navigator.clipboard.writeText(url).catch(() => {})
    setCopied(link.id)
    setTimeout(() => setCopied(null), 2000)
  }

  return (
    <>
      <ErrorBox error={error} />

      <div className="ws-two-col">
        {/* ── The investor room ────────────────────────────────────────── */}
        <div className="panel ws-panel">
          <div className="panel-title ws-panel-title">
            <span>Investor room</span>
            <span className={`ws-pill ${b.published ? 'is-ok' : 'is-muted'}`}>
              {b.published ? 'Published' : 'Not published'}
            </span>
          </div>
          <Note>
            What a cash buyer sees. Everything below is off until you turn it
            on, and the room is unreachable until you publish it.
          </Note>

          <div className="ws-grid">
            <div className="ws-field">
              <label htmlFor="pub-ask">Asking price shown to investors</label>
              <input id="pub-ask" inputMode="decimal"
                     value={val('buyer_room_asking_price', b.asking_price ?? '')}
                     onChange={(e) => set('buyer_room_asking_price',
                                          e.target.value === '' ? null
                                            : Number(e.target.value))} />
            </div>
          </div>

          <div className="ws-field" style={{ marginTop: 12 }}>
            <label htmlFor="pub-summary">Property summary</label>
            <textarea id="pub-summary" rows={3}
                      placeholder="Written for investors. Not your internal notes."
                      value={val('buyer_room_summary', b.summary || '')}
                      onChange={(e) => set('buyer_room_summary', e.target.value)} />
          </div>
          <div className="ws-field" style={{ marginTop: 10 }}>
            <label htmlFor="pub-cond">Condition and repairs</label>
            <textarea id="pub-cond" rows={3}
                      value={val('buyer_room_condition', b.condition || '')}
                      onChange={(e) => set('buyer_room_condition', e.target.value)} />
          </div>

          <div className="ws-subhead">Also show them</div>
          <div className="ws-checks">
          {[['buyer_room_show_arv', 'show_arv', 'Our ARV'],
            ['buyer_room_show_repairs', 'show_repairs', 'Our repair estimate'],
            ['buyer_room_show_comps', 'show_comps', 'The comparable sales']]
            .map(([field, key, label]) => (
              <label className="ws-checkbox" key={field}>
                <input type="checkbox" disabled={busy}
                       checked={!!val(field, b[key])}
                       onChange={(e) => set(field, e.target.checked)} />
                {label}
              </label>
            ))}
          </div>
          <Why label="Why these are off by default">
            <p className="ws-comp__sub">
              The ARV, the repair estimate and the comp set are this
              workspace's working, not a listing. Some operators publish them
              to win trust; publishing them is a decision, not a default.
            </p>
          </Why>

          <div className="ws-kv" style={{ marginTop: 14 }}>
            <div>
              <div className="ws-k">Photos shared</div>
              <div className="ws-v">{b.photo_count} of {state.photo_total}</div>
            </div>
            <div>
              <div className="ws-k">Documents shared</div>
              <div className="ws-v">{b.document_count} of {state.document_total}</div>
            </div>
            <div>
              <div className="ws-k">Published</div>
              <div className="ws-v">
                {b.published_at ? fmtWhen(b.published_at)
                  : <span className="ws-muted">—</span>}
              </div>
            </div>
          </div>

          <div className="ws-actions" style={{ marginTop: 14 }}>
            <button className="btn btn--primary" disabled={busy || !dirty}
                    onClick={save}>Save</button>
            {b.published ? (
              <button className="btn btn--secondary" disabled={busy}
                      onClick={() => setPublished('buyer', false)}>
                Close the room
              </button>
            ) : (
              <button className="btn btn--secondary" disabled={busy}
                      onClick={() => setPublished('buyer', true)}>
                Publish the investor room
              </button>
            )}
          </div>
        </div>

        {/* ── The seller's page ────────────────────────────────────────── */}
        <div className="panel ws-panel">
          <div className="panel-title ws-panel-title">
            <span>Seller page</span>
            <span className={`ws-pill ${s.published ? 'is-ok' : 'is-muted'}`}>
              {s.published ? 'Published' : 'Not published'}
            </span>
          </div>
          <Note>
            What the owner sees about their own sale. No buyers, no offers, no
            fee — the server does not put them in the payload at all.
          </Note>

          <div className="ws-field">
            <label htmlFor="pub-msg">An update for the owner</label>
            <textarea id="pub-msg" rows={3}
                      value={val('seller_room_message', s.message || '')}
                      onChange={(e) => set('seller_room_message', e.target.value)} />
          </div>

          <div className="ws-subhead">Who they should contact</div>
          <div className="ws-grid">
            {[['seller_room_contact_name', 'contact_name', 'Name'],
              ['seller_room_contact_phone', 'contact_phone', 'Phone'],
              ['seller_room_contact_email', 'contact_email', 'Email']]
              .map(([field, key, label]) => (
                <div className="ws-field" key={field}>
                  <label htmlFor={`pub-${key}`}>{label}</label>
                  <input id={`pub-${key}`} value={val(field, s[key] || '')}
                         onChange={(e) => set(field, e.target.value)} />
                </div>
              ))}
          </div>

          <div className="ws-kv" style={{ marginTop: 14 }}>
            <div>
              <div className="ws-k">Documents shared</div>
              <div className="ws-v">{s.document_count} of {state.document_total}</div>
            </div>
            <div>
              <div className="ws-k">Published</div>
              <div className="ws-v">
                {s.published_at ? fmtWhen(s.published_at)
                  : <span className="ws-muted">—</span>}
              </div>
            </div>
          </div>

          <div className="ws-actions" style={{ marginTop: 14 }}>
            <button className="btn btn--primary" disabled={busy || !dirty}
                    onClick={save}>Save</button>
            {s.published ? (
              <button className="btn btn--secondary" disabled={busy}
                      onClick={() => setPublished('seller', false)}>
                Close the page
              </button>
            ) : (
              <button className="btn btn--secondary" disabled={busy}
                      onClick={() => setPublished('seller', true)}>
                Publish the seller page
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Links ──────────────────────────────────────────────────────── */}
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">
          <span>Links ({state.links.length})</span>
          <span className="ws-hint">
            A link is created here and copied. Nothing is emailed from this
            screen.
          </span>
        </div>

        <div className="ws-grid">
          <div className="ws-field">
            <label htmlFor="lk-aud">Who is it for</label>
            <select id="lk-aud" value={newLink.audience}
                    onChange={(e) => setNewLink((n) => ({ ...n, audience: e.target.value }))}>
              <option value="buyer">An investor</option>
              <option value="seller">The property owner</option>
            </select>
          </div>
          {newLink.audience === 'buyer' ? (
            <div className="ws-field">
              <label htmlFor="lk-buyer">Which buyer</label>
              <select id="lk-buyer" value={newLink.buyer_id}
                      onChange={(e) => setNewLink((n) => ({ ...n, buyer_id: e.target.value }))}>
                <option value="">Choose…</option>
                {(buyers || []).map((x) => (
                  <option key={x.buyer_id || x.id} value={x.buyer_id || x.id}>
                    {x.buyer_name || x.company_name || x.display_name}
                  </option>
                ))}
              </select>
            </div>
          ) : null}
          <div className="ws-field">
            <label htmlFor="lk-name">Their name (optional)</label>
            <input id="lk-name" value={newLink.recipient_name}
                   onChange={(e) => setNewLink((n) => ({ ...n, recipient_name: e.target.value }))} />
          </div>
          <div className="ws-field">
            <label htmlFor="lk-days">Expires in (days)</label>
            <input id="lk-days" type="number" value={newLink.expires_in_days}
                   placeholder="Blank = no expiry"
                   onChange={(e) => setNewLink((n) => ({ ...n, expires_in_days: e.target.value }))} />
          </div>
        </div>
        <div className="ws-actions" style={{ marginTop: 12 }}>
          <button className="btn btn--primary" disabled={
            busy || (newLink.audience === 'buyer' && !newLink.buyer_id)}
                  onClick={createLink}>Create a link</button>
        </div>

        {!state.links.length ? (
          <Empty>No links yet.</Empty>
        ) : (
          <div className="ws-scroll" style={{ marginTop: 14 }}>
            <table className="ws-table">
              <thead>
                <tr>
                  <th>For</th><th>Who</th><th>Opened</th><th>Expires</th>
                  <th>Status</th><th>Link</th><th />
                </tr>
              </thead>
              <tbody>
                {state.links.map((link) => (
                  <tr key={link.id} className={link.active ? '' : 'is-excluded'}>
                    <td>{link.audience === 'buyer' ? 'Investor' : 'Owner'}</td>
                    <td>{link.recipient_name || <span className="ws-muted">—</span>}</td>
                    <td className="ws-num">
                      {link.view_count
                        ? `${link.view_count}×`
                        : <span className="ws-muted">never</span>}
                      {link.last_viewed_at ? (
                        <div className="ws-comp__sub">
                          last {fmtWhen(link.last_viewed_at)}
                        </div>
                      ) : null}
                    </td>
                    <td>{link.expires_at ? fmtWhen(link.expires_at)
                      : <span className="ws-muted">never</span>}</td>
                    <td>
                      <span className={`ws-pill ${link.active ? 'is-ok' : 'is-dnc'}`}>
                        {link.revoked_at ? 'Revoked'
                          : link.active ? 'Active' : 'Expired'}
                      </span>
                    </td>
                    <td>
                      <code className="ws-linkpath">{link.url_path}</code>
                    </td>
                    <td>
                      <span className="ws-actions ws-actions--wrap">
                        <button className="btn btn--secondary btn--sm"
                                onClick={() => copy(link)}>
                          {copied === link.id ? 'Copied' : 'Copy'}
                        </button>
                        {link.active ? (
                          <button className="btn btn--secondary btn--sm ws-btn-delete"
                                  disabled={busy}
                                  onClick={() => revoke(link)}>Revoke</button>
                        ) : null}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Who actually looked ────────────────────────────────────────── */}
      <div className="panel ws-panel">
        <div className="panel-title ws-panel-title">
          <span>Outside activity</span>
          <span className="ws-hint">
            Opens and replies from people with a link
          </span>
        </div>
        {!activity.length ? (
          <Empty>Nobody has opened a link yet.</Empty>
        ) : (
          <ul className="ws-feed">
            {activity.map((a) => (
              <li key={a.id}>
                <span className="ws-actor actor-api">{a.who}</span>
                <span className="ws-feed__text">
                  {a.action === 'view' ? 'opened the page'
                    : `${a.action}${a.amount ? ` — ${fmtMoney(a.amount)}` : ''}`}
                  {a.detail ? ` · ${a.detail}` : ''}
                </span>
                <span className="ws-feed__when">{fmtWhen(a.at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  )
}
