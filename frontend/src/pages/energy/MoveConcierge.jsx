/**
 * MOVE CONCIERGE — customers moving who want services started, transferred or
 * stopped.
 *
 *   GET   /energy-ops/moves/meta            statuses, services, vendor status
 *   GET   /energy-ops/moves?status&search   list + summary counts
 *   POST  /energy-ops/moves                 new request (explicit form submit)
 *   GET   /energy-ops/moves/{id}            detail + tasks + activity
 *   PATCH /energy-ops/moves/{id}            status / date / services / notes / owner
 *   POST  /energy-ops/moves/{id}/checklist  tick an item (a person did it)
 *   POST  /energy-ops/moves/{id}/tasks      follow-up task on the linked lead
 *
 * Outside vendors are NOT CONFIGURED: nothing here orders or schedules a
 * service. A move date is shown only when the customer gave one.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import './energy.css'

const STATUS_LABEL = {
  requested: 'Requested', in_progress: 'In Progress', waiting_on_customer: 'Waiting on Customer',
  completed: 'Completed', cancelled: 'Cancelled',
}
const KPIS = [
  { key: 'open', label: 'Open' },
  { key: 'requested', label: 'Requested' },
  { key: 'in_progress', label: 'In Progress' },
  { key: 'waiting_on_customer', label: 'Waiting on Customer' },
  { key: 'unassigned', label: 'Unassigned' },
  { key: 'completed', label: 'Completed' },
]

function statusPill(s) {
  const tone = s === 'completed' ? 'green' : s === 'cancelled' ? '' : s === 'waiting_on_customer' ? 'amber' : ''
  return <span className={`eo-pill${tone ? ` eo-pill--${tone}` : ''}`}>{STATUS_LABEL[s] || s}</span>
}

export default function MoveConcierge() {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || 'open'
  const [meta, setMeta] = useState(null)
  const [data, setData] = useState(null)
  const [search, setSearch] = useState('')
  const [openId, setOpenId] = useState(params.get('id') || null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => { api.get('/energy-ops/moves/meta').then(setMeta).catch(() => {}) }, [])
  const load = useCallback(() => {
    const p = new URLSearchParams({ status, per_page: '50' })
    if (search.trim()) p.set('search', search.trim())
    api.get(`/energy-ops/moves?${p}`).then(setData).catch(e => setError(e.message || 'Could not load move requests'))
  }, [status, search])
  useEffect(() => { load() }, [load])

  const s = data?.summary
  return (
    <div className="eo" data-testid="move-concierge">
      <div className="eo-head">
        <div>
          <h1>Move Concierge</h1>
          <p className="eo-sub">Customers who are moving: the date, the services they asked for, who owns it and what is done.</p>
        </div>
        <button type="button" className="eo-btn eo-btn--primary" onClick={() => setCreating(true)}>New Move Request</button>
      </div>
      {error && <div className="eo-banner eo-banner--error">{error}</div>}
      <div className="eo-banner" data-testid="vendor-status">
        <strong>Vendor integrations: Not configured.</strong>{' '}
        {meta?.vendor_integrations?.detail || 'Checklist items are completed by your team; nothing is ordered automatically.'}
      </div>

      <div className="eo-kpis">
        {KPIS.map(k => (
          <button key={k.key} type="button" className="eo-kpi" aria-pressed={status === k.key}
            onClick={() => setParams({ status: k.key })}>
            <span className="eo-kpi-n">{s ? (s[k.key] ?? 0).toLocaleString() : '…'}</span>
            <span className="eo-kpi-l">{k.label}</span>
          </button>
        ))}
      </div>
      {s && <p className="eo-note">{s.moving_in_14_days} open move(s) dated in the next 14 days · {s.no_move_date} open move(s) with no move date given.</p>}

      <div className="eo-toolbar">
        <input type="search" placeholder="Search name or address" value={search}
          onChange={e => setSearch(e.target.value)} aria-label="Search moves" />
      </div>

      <div className="eo-panel">
        {!data ? <div className="eo-empty">Loading…</div> : data.items.length === 0 ? (
          <div className="eo-empty">No move requests here. Use “New Move Request” when a customer tells you they are moving.</div>
        ) : (
          <table className="eo-table">
            <thead><tr><th>Customer</th><th>Move date</th><th>New address</th><th className="eo-hide-sm">Services</th><th className="eo-hide-sm">Checklist</th><th className="eo-hide-sm">Owner</th><th>Status</th></tr></thead>
            <tbody>
              {data.items.map(m => (
                <tr key={m.id} className="eo-click" onClick={() => setOpenId(m.id)}>
                  <td>{m.contact_name}</td>
                  <td>{m.move_date || <span className="eo-pill">Not provided</span>}</td>
                  <td>{m.to_address || '—'}</td>
                  <td className="eo-hide-sm">{m.services.map(x => x.label).join(', ') || '—'}</td>
                  <td className="eo-hide-sm">{m.checklist_done}/{m.checklist_total}</td>
                  <td className="eo-hide-sm">{m.owner || 'Unassigned'}</td>
                  <td>{statusPill(m.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {openId && <MoveDrawer id={openId} meta={meta} onClose={() => setOpenId(null)} onChanged={load} />}
      {creating && <NewMove meta={meta} onClose={() => setCreating(false)}
        onCreated={m => { setCreating(false); load(); setOpenId(m.id) }} />}
    </div>
  )
}

function ServicesPicker({ meta, value, onChange }) {
  const toggle = k => onChange(value.includes(k) ? value.filter(x => x !== k) : [...value, k])
  return (
    <div className="eo-services">
      {(meta?.services || []).map(s => (
        <label key={s.key}><input type="checkbox" checked={value.includes(s.key)} onChange={() => toggle(s.key)} />{s.label}</label>
      ))}
    </div>
  )
}

function NewMove({ meta, onClose, onCreated }) {
  const [params] = useSearchParams()
  const [form, setForm] = useState({ contact_name: '', lead_id: params.get('lead') || '', move_date: '', from_address: '', to_address: '', notes: '' })
  const [services, setServices] = useState(['electricity_start'])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = k => e => setForm(f => ({ ...f, [k]: e.target.value }))
  const submit = async e => {
    e.preventDefault()
    setBusy(true); setErr('')
    const body = { services }
    for (const [k, v] of Object.entries(form)) if (v.trim()) body[k] = v.trim()
    try { onCreated(await api.post('/energy-ops/moves', body)) } catch (x) { setErr(x.message || 'Could not create') } finally { setBusy(false) }
  }
  return (
    <div className="eo-layer" onClick={onClose}>
      <form className="eo-drawer" onClick={e => e.stopPropagation()} onSubmit={submit}>
        <h2>New Move Request</h2>
        <p className="eo-sub">Records what the customer asked for. Nothing is sent or ordered.</p>
        {err && <div className="eo-banner eo-banner--error">{err}</div>}
        <div className="eo-form" style={{ marginTop: 12 }}>
          <label>Customer name<input value={form.contact_name} onChange={set('contact_name')} placeholder="Required unless a lead is linked" /></label>
          <LeadPicker value={form.lead_id} onPick={(l) => setForm(f => ({ ...f, lead_id: l ? l.id : '',
            contact_name: l && !f.contact_name.trim() ? [l.first_name, l.last_name].filter(Boolean).join(' ') : f.contact_name }))} />
          <label>Move date (if given)<input type="date" value={form.move_date} onChange={set('move_date')} /></label>
          <span />
          <label className="eo-span">Current address<input value={form.from_address} onChange={set('from_address')} /></label>
          <label className="eo-span">New address<input value={form.to_address} onChange={set('to_address')} /></label>
          <div className="eo-span"><div className="eo-sub" style={{ marginBottom: 6 }}>Services requested</div><ServicesPicker meta={meta} value={services} onChange={setServices} /></div>
          <label className="eo-span">Notes<textarea rows={3} value={form.notes} onChange={set('notes')} /></label>
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <button type="submit" className="eo-btn eo-btn--primary" disabled={busy}>{busy ? 'Saving…' : 'Create request'}</button>
          <button type="button" className="eo-btn" onClick={onClose}>Cancel</button>
        </div>
      </form>
    </div>
  )
}

// Link the move to an existing customer by SEARCHING (name, phone, email) -
// staff never know an internal record id. Uses the same /leads/ search the
// composer uses, so it is scoped to what this person may see.
function LeadPicker({ value, onPick }) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [picked, setPicked] = useState(null)
  useEffect(() => {
    const term = q.trim()
    if (term.length < 2 || picked) { setResults([]); return }
    let alive = true
    const t = setTimeout(() => {
      api.get('/leads/', { params: { search: term, page_size: 6, page: 1 } })
        .then(d => { if (alive) setResults(d.items || d || []) })
        .catch(() => { if (alive) setResults([]) })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [q, picked])
  const name = l => [l.first_name, l.last_name].filter(Boolean).join(' ') || l.email || l.phone || 'Unnamed'
  if (picked || (value && !q)) {
    return (
      <div className="eo-picker-field">
        <span className="eo-picker-label">Linked customer</span>
        <span className="eo-picked" data-testid="lead-picked">
          {picked ? name(picked) : 'Linked record'}
          <button type="button" className="eo-btn eo-btn--sm" onClick={() => { setPicked(null); setQ(''); onPick(null) }}>Change</button>
        </span>
      </div>
    )
  }
  return (
    <div className="eo-picker eo-picker-field">
      {/* Not a <label>: a click on a result would be forwarded to the control. */}
      <span className="eo-picker-label">Link to customer (optional)</span>
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, phone or email" aria-label="Search customer" />
      {results.length > 0 && (
        <ul className="eo-picker-list" role="listbox">
          {results.map(l => (
            <li key={l.id}><button type="button" role="option" onClick={() => { setPicked(l); onPick(l) }}>
              <strong>{name(l)}</strong> <span className="eo-sub">{[l.phone, l.email].filter(Boolean).join(' · ')}</span>
            </button></li>))}
        </ul>)}
    </div>
  )
}

function MoveDrawer({ id, meta, onClose, onChanged }) {
  const [m, setM] = useState(null)
  const [err, setErr] = useState('')
  const [notes, setNotes] = useState('')
  const [taskTitle, setTaskTitle] = useState('')
  const [taskDue, setTaskDue] = useState('')
  const reload = useCallback(() => {
    api.get(`/energy-ops/moves/${id}`).then(d => { setM(d); setNotes(d.notes || '') }).catch(e => setErr(e.message || 'Not found'))
  }, [id])
  useEffect(() => { reload() }, [reload])
  const act = async fn => { setErr(''); try { await fn(); reload(); onChanged() } catch (x) { setErr(x.message || 'Failed') } }
  const patch = body => act(() => api.patch(`/energy-ops/moves/${id}`, body))

  return (
    <div className="eo-layer" onClick={onClose}>
      <aside className="eo-drawer" onClick={e => e.stopPropagation()} aria-label="Move request">
        {!m ? <div className="eo-empty">{err || 'Loading…'}</div> : (<>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <div><h2>{m.contact_name}</h2>{statusPill(m.status)}</div>
            <button type="button" className="eo-btn" onClick={onClose}>Close</button>
          </div>
          {err && <div className="eo-banner eo-banner--error" style={{ marginTop: 10 }}>{err}</div>}
          <div className="eo-sec">
            <h3>Details</h3>
            <dl className="eo-dl">
              <dt>Move date</dt><dd><input type="date" defaultValue={m.move_date || ''} onBlur={e => (e.target.value || null) !== m.move_date && patch({ move_date: e.target.value || null })} aria-label="Move date" /> {!m.move_date && <span className="eo-pill">Not provided</span>}</dd>
              <dt>From</dt><dd>{m.from_address || '—'}</dd>
              <dt>To</dt><dd>{m.to_address || '—'}</dd>
              <dt>Owner</dt><dd>{m.owner || 'Unassigned'}</dd>
              <dt>Linked record</dt><dd>{m.lead_link ? <Link to={m.lead_link}>Open lead</Link> : m.contact_link ? <Link to={m.contact_link}>Open contact</Link> : 'None'}</dd>
              <dt>Status</dt><dd>
                <select value={m.status} onChange={e => patch({ status: e.target.value })} aria-label="Status">
                  {(meta?.statuses || Object.keys(STATUS_LABEL)).map(s => <option key={s} value={s}>{STATUS_LABEL[s] || s}</option>)}
                </select>
              </dd>
            </dl>
          </div>
          <div className="eo-sec">
            <h3>Services requested</h3>
            <ServicesPicker meta={meta} value={m.services.map(s => s.key)} onChange={v => patch({ services: v })} />
          </div>
          <div className="eo-sec">
            <h3>Checklist ({m.checklist_done}/{m.checklist_total})</h3>
            {m.checklist.map(i => (
              <label key={i.key} className="eo-check">
                <input type="checkbox" checked={!!i.done}
                  onChange={e => act(() => api.post(`/energy-ops/moves/${id}/checklist`, { key: i.key, done: e.target.checked }))} />
                <span>{i.label}{i.done_at && <span className="eo-sub"> — done {new Date(i.done_at).toLocaleDateString()}</span>}</span>
              </label>
            ))}
          </div>
          <div className="eo-sec">
            <h3>Tasks</h3>
            {!m.tasks_available ? <p className="eo-sub">Link this move to a lead to add follow-up tasks.</p> : (<>
              {m.tasks.length === 0 ? <p className="eo-sub">No tasks yet.</p> : (
                <ul className="eo-feed">{m.tasks.map(t => <li key={t.id}>{t.title} · {t.status}{t.due_at ? ` · due ${new Date(t.due_at).toLocaleDateString()}` : ''}</li>)}</ul>
              )}
              <form className="eo-toolbar" style={{ marginTop: 8 }} onSubmit={e => {
                e.preventDefault()
                if (!taskTitle.trim()) return
                const body = { title: taskTitle.trim() }
                if (taskDue) body.due_at = new Date(`${taskDue}T09:00`).toISOString()  // 9am where the user is
                act(async () => { await api.post(`/energy-ops/moves/${id}/tasks`, body); setTaskTitle(''); setTaskDue('') })
              }}>
                <input value={taskTitle} onChange={e => setTaskTitle(e.target.value)} placeholder="New task" aria-label="Task title" />
                <input type="date" value={taskDue} onChange={e => setTaskDue(e.target.value)} aria-label="Task due" />
                <button type="submit" className="eo-btn">Add task</button>
              </form>
            </>)}
          </div>
          <div className="eo-sec">
            <h3>Notes</h3>
            <textarea rows={4} style={{ width: '100%', boxSizing: 'border-box' }} value={notes} onChange={e => setNotes(e.target.value)} aria-label="Notes" />
            <button type="button" className="eo-btn" disabled={notes === (m.notes || '')} onClick={() => patch({ notes })}>Save notes</button>
          </div>
          <div className="eo-sec">
            <h3>Activity</h3>
            <ul className="eo-feed">
              {m.activity.map((a, i) => <li key={i}>{new Date(a.at).toLocaleString()} · {a.actor || 'Someone'} · {a.action.replace('move_request.', '').replace('_', ' ')}</li>)}
            </ul>
          </div>
        </>)}
      </aside>
    </div>
  )
}
