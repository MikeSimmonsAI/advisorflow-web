import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { CustomerForm, SegTags } from './Customers'
import { BASE, useJewelry } from './JewelryShell'
import { Empty, ErrorNote, Field, Loading, Modal, Pill, day, errText, money, useApi, useToast, when } from './jwui'

function Permission({ c, onSaved, toast }) {
  const [open, setOpen] = useState(false)
  const [src, setSrc] = useState(c.permission_source || '')
  const [sms, setSms] = useState(c.allow_sms)
  const [email, setEmail] = useState(c.allow_email)
  const [err, setErr] = useState(null)
  const save = async () => {
    try {
      onSaved(await api.patch('/jewelry-ops/customers/' + c.id, { allow_sms: sms, allow_email: email, permission_source: src }))
      setOpen(false); toast('Contact permission saved.')
    } catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="jw-card">
      <div className="jw-card__head"><h3>Marketing preferences</h3>
        {!c.opted_out && <button className="jw-btn jw-btn--sm" onClick={() => setOpen(true)}>Edit</button>}</div>
      {c.opted_out ? <p><Pill tone="err">Opted out</Pill> This customer asked not to be contacted. Marketing is off on every channel and their number is on the do-not-text list.</p> : (
        <ul className="jw-list">
          <li><span>Text messages</span><Pill tone={c.allow_sms ? 'ok' : ''}>{c.allow_sms ? 'Permission recorded' : 'No permission'}</Pill></li>
          <li><span>Email</span><Pill tone={c.allow_email ? 'ok' : ''}>{c.allow_email ? 'Permission recorded' : 'No permission'}</Pill></li>
          {c.permission_source && <li><span>Source</span><small>{c.permission_source}</small></li>}
        </ul>
      )}
      {open && (
        <Modal title="Marketing preferences" onClose={() => setOpen(false)}>
          <label className="jw-check"><input type="checkbox" checked={sms} onChange={e => setSms(e.target.checked)} /> Customer gave permission for marketing texts</label>
          <label className="jw-check"><input type="checkbox" checked={email} onChange={e => setEmail(e.target.checked)} /> Customer gave permission for marketing email</label>
          <Field label="Where did the permission come from?" hint="Required to turn a channel on, e.g. “Signed in-store form, Nov 2026”">
            <input className="jw-input" value={src} onChange={e => setSrc(e.target.value)} maxLength={200} />
          </Field>
          <ErrorNote>{err}</ErrorNote>
          <div className="jw-row jw-row--end"><button className="jw-btn" onClick={() => setOpen(false)}>Cancel</button><button className="jw-btn jw-btn--gold" onClick={save}>Save</button></div>
        </Modal>
      )}
    </div>
  )
}

export default function CustomerDetail() {
  const { id } = useParams()
  const { status } = useJewelry()
  const nav = useNavigate()
  const { data: c, error, reload, setData } = useApi('/jewelry-ops/customers/' + id, [id])
  const { data: h, reload: reloadH } = useApi('/jewelry-ops/customers/' + id + '/history', [id])
  const [tab, setTab] = useState('activity')
  const [editing, setEditing] = useState(false)
  const [note, setNote] = useState('')
  const [fu, setFu] = useState({ title: '', due_at: '' })
  const [tx, setTx] = useState({ kind: 'sale', amount: '', occurred_on: new Date().toISOString().slice(0, 10), description: '' })
  const [optOut, setOptOut] = useState(false)
  const [err, setErr] = useState(null)
  const [toast, toastNode] = useToast()
  if (error) return <ErrorNote onRetry={reload}>{error}</ErrorNote>
  if (!c) return <Loading />
  const act = async (fn, okText) => {
    setErr(null)
    try { await fn(); await Promise.all([reload(), reloadH()]); if (okText) toast(okText); return true } catch (e) { setErr(errText(e)); return false }
  }
  return (
    <div>
      <div className="jw-row"><Link to={`${BASE}/customers`} className="jw-link">← All customers</Link></div>
      <section className="jw-profile">
        <div className="jw-avatar" aria-hidden="true">{(c.name || '?').split(' ').map(x => x[0]).join('').slice(0, 2).toUpperCase()}</div>
        <div className="jw-profile__main">
          <h1>{c.name}</h1>
          <div className="jw-sub">{[c.phone, c.email, [c.city, c.state].filter(Boolean).join(', ')].filter(Boolean).join(' · ') || 'No contact details yet'}</div>
          <div className="jw-tags"><SegTags keys={c.segments} status={status} />{c.interests.map(i => <span key={i} className="jw-tag jw-tag--interest">{i}</span>)}</div>
        </div>
        <div className="jw-row">
          <button className="jw-btn" onClick={() => setEditing(true)}>Edit</button>
          <button className="jw-btn jw-btn--gold" onClick={() => nav(`${BASE}/appointments?lead=${c.id}`)}>Book appointment</button>
        </div>
      </section>
      <div className="jw-kpis jw-kpis--4">
        <div className="jw-mini"><span>Last seen</span><b>{c.last_seen ? day(c.last_seen) : 'No visits recorded'}</b></div>
        <div className="jw-mini"><span>Transactions</span><b>{c.transactions}</b></div>
        <div className="jw-mini"><span>Recorded total</span><b>{money(c.recorded_total)}</b></div>
        <div className="jw-mini"><span>Completed visits</span><b>{c.completed_visits}</b></div>
      </div>
      <ErrorNote>{err}</ErrorNote>
      <div className="jw-grid jw-grid--main">
        <div className="jw-card">
          <div className="jw-tabs" role="tablist">
            {[['activity', 'Notes & follow-ups'], ['transactions', 'Transactions'], ['appointments', 'Appointments'], ['comms', 'Communication']].map(([k, l]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          {!h && <Loading />}
          {h && tab === 'activity' && (
            <>
              <form className="jw-row" onSubmit={e => { e.preventDefault(); act(() => api.post(`/jewelry-ops/customers/${c.id}/notes`, { body: note }), 'Note added.').then(ok => ok && setNote('')) }}>
                <input className="jw-input jw-grow" value={note} onChange={e => setNote(e.target.value)} placeholder="Add a note…" maxLength={4000} />
                <button className="jw-btn jw-btn--navy" disabled={!note.trim()}>Add note</button>
              </form>
              <form className="jw-row" onSubmit={e => { e.preventDefault(); act(() => api.post(`/jewelry-ops/customers/${c.id}/follow-ups`, { title: fu.title, due_at: fu.due_at || null }), 'Follow-up set.').then(ok => ok && setFu({ title: '', due_at: '' })) }}>
                <input className="jw-input jw-grow" value={fu.title} onChange={e => setFu({ ...fu, title: e.target.value })} placeholder="Follow-up, e.g. Call about the coin set" maxLength={200} />
                <input className="jw-input" type="date" value={fu.due_at} onChange={e => setFu({ ...fu, due_at: e.target.value })} aria-label="Due date" />
                <button className="jw-btn" disabled={!fu.title.trim()}>Set follow-up</button>
              </form>
              {h.follow_ups.filter(t => t.status === 'open').map(t => (
                <div key={t.id} className="jw-task"><span>☐ <b>{t.title}</b> {t.due_at && <small>due {day(t.due_at)}</small>}</span>
                  <button className="jw-btn jw-btn--sm" onClick={() => act(() => api.post(`/jewelry-ops/follow-ups/${t.id}/complete`, {}), 'Follow-up done.')}>Done</button></div>
              ))}
              {h.notes.length === 0 && h.follow_ups.length === 0 && <Empty title="No notes yet" />}
              <ul className="jw-timeline">{h.notes.map(x => <li key={x.id}><small>{when(x.at)} · {x.author}</small><div>{x.body}</div></li>)}</ul>
            </>
          )}
          {h && tab === 'transactions' && (
            <>
              <form className="jw-row jw-row--wrap" onSubmit={e => { e.preventDefault(); act(() => api.post(`/jewelry-ops/customers/${c.id}/transactions`, { ...tx, amount: Number(tx.amount) }), 'Transaction recorded.').then(ok => ok && setTx({ ...tx, amount: '', description: '' })) }}>
                <select className="jw-input" value={tx.kind} onChange={e => setTx({ ...tx, kind: e.target.value })} aria-label="Type">
                  <option value="sale">Sale to customer</option><option value="purchase">Bought from customer</option><option value="trade">Trade</option>
                </select>
                <input className="jw-input" type="date" value={tx.occurred_on} onChange={e => setTx({ ...tx, occurred_on: e.target.value })} aria-label="Date" />
                <input className="jw-input" type="number" min="0" step="0.01" placeholder="Amount $" value={tx.amount} onChange={e => setTx({ ...tx, amount: e.target.value })} aria-label="Amount" />
                <input className="jw-input jw-grow" placeholder="What was it?" value={tx.description} onChange={e => setTx({ ...tx, description: e.target.value })} maxLength={300} />
                <button className="jw-btn jw-btn--navy" disabled={tx.amount === ''}>Record</button>
              </form>
              <p className="jw-note">Recorded by staff. There is no point-of-sale connection.</p>
              {h.transactions.length === 0 ? <Empty title="No transactions recorded" /> : (
                <div className="jw-table-wrap"><table className="jw-table"><thead><tr><th>Date</th><th>Type</th><th>Description</th><th className="num">Amount</th></tr></thead>
                  <tbody>{h.transactions.map(t => <tr key={t.id}><td>{day(t.occurred_on)}</td><td>{t.kind}</td><td>{t.description}</td><td className="num">{money(t.amount)}</td></tr>)}</tbody></table></div>
              )}
            </>
          )}
          {h && tab === 'appointments' && (h.appointments.length === 0 ? <Empty title="No appointments yet" /> : (
            <ul className="jw-list">{h.appointments.map(a => <li key={a.id}><div><b>{a.type}</b><small>{when(a.starts_at)} · {a.staff_name}</small></div><Pill>{a.status.replace('_', ' ')}</Pill></li>)}</ul>
          ))}
          {h && tab === 'comms' && (h.communications.length === 0 ? <Empty title="No messages">Texts and emails appear here once messaging is connected. Nothing has been sent from this workspace.</Empty> : (
            <ul className="jw-timeline">{h.communications.map((m, i) => <li key={i}><small>{when(m.at)} · {m.channel} · {m.direction}</small><div>{m.body}</div></li>)}</ul>
          ))}
        </div>
        <div>
          <Permission c={c} onSaved={setData} toast={toast} />
          <div className="jw-card">
            <div className="jw-card__head"><h3>Details</h3></div>
            <ul className="jw-list">
              <li><span>Follow-up status</span><b>{(status.follow_up_statuses.find(s => s.key === c.follow_up) || {}).label}</b></li>
              <li><span>Assigned to</span><b>{c.assigned_to_name || 'Not assigned'}</b></li>
              <li><span>Customer since</span><b>{day(c.created_at)}</b></li>
              <li><span>Source</span><b>{c.source || '—'}</b></li>
            </ul>
            {c.notes && <p className="jw-small">{c.notes}</p>}
            {!c.opted_out && <button className="jw-btn jw-btn--danger jw-btn--sm" onClick={() => setOptOut(true)}>Customer asked not to be contacted</button>}
          </div>
        </div>
      </div>
      {editing && (
        <Modal title="Edit customer" onClose={() => setEditing(false)} wide>
          <CustomerForm status={status} initial={{ ...c, segments: c.assigned_segments }} onCancel={() => setEditing(false)}
                        onSaved={(x) => { setData(x); setEditing(false); reloadH(); toast('Customer saved.') }} />
        </Modal>
      )}
      {optOut && (
        <Modal title="Record opt-out" onClose={() => setOptOut(false)}>
          <p>This turns off texts and email for {c.name}, adds their number to the do-not-text list and marks them “Do not contact”. It can’t be reversed from this screen.</p>
          <div className="jw-row jw-row--end"><button className="jw-btn" onClick={() => setOptOut(false)}>Cancel</button>
            <button className="jw-btn jw-btn--danger" onClick={() => act(() => api.post(`/jewelry-ops/customers/${c.id}/opt-out`, {}), 'Opt-out recorded.').then(ok => ok && setOptOut(false))}>Record opt-out</button></div>
        </Modal>
      )}
      {toastNode}
    </div>
  )
}
