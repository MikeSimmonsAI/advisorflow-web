/**
 * SERVICE OPERATIONS pages inside the service company's existing client
 * portal (#452/#506): Operations overview, Customers & locations, Jobs,
 * Recurring schedule, Team, Service history, Invoices & estimates.
 *
 * Shown only when EvoSys switched Service Operations on for the company.
 * The server decides what each role may see and do; the buttons here only
 * appear when the server says the move is allowed.
 */
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'
import { BASE, useAcq } from './AcquisitionShell'
import { AvailabilityPanel, NewScheduleForm, QuickActions, qsNew } from './ClientTools'
import { Confirm, Dot, Empty, Loading, PageHeader, Stat, Toolbar, errText, money, useLoad, useToast, when } from './ui'

export const OPS = `${BASE}/ops`
const TONE = { draft: 'gray', scheduled: 'blue', assigned: 'blue', confirmed: 'violet', in_progress: 'amber',
               completed: 'green', customer_review: 'amber', approved: 'green', cancelled: 'gray', needs_attention: 'red',
               issued: 'blue', partially_paid: 'amber', paid: 'green', overdue: 'red', void: 'gray' }
const can = (me, p) => !!me?.perms?.includes(p)
const field = (id, label, input) => <div className="aq-field"><label htmlFor={id}>{label}</label>{input}</div>
const SETUP_LABEL = { pending: 'Invited — link not used yet', expired: 'Link expired — send a new one', revoked: 'Link replaced', accepted: 'Set up — not signed in yet' }
const usd = v => (v === null || v === undefined ? '—' : '$' + Number(v).toFixed(2))

export function useOps() {
  const { ops } = useAcq() || {}
  return ops
}

function StatusDot({ s, label }) { return <Dot tone={TONE[s] || 'gray'}>{label || s}</Dot> }

function JobRows({ items, empty = 'No jobs here yet.' }) {
  const nav = useNavigate()
  if (!items?.length) return <Empty icon="calendar" title={empty} />
  return (
    <div className="hb-table-wrap"><table className="aq-table"><thead><tr><th>When</th><th>Service</th><th>Customer / location</th>
      <th>Team</th><th>Status</th></tr></thead><tbody>{items.map(j => (
        <tr key={j.id} className="hb-click" onClick={() => nav(`${OPS}/jobs/${j.id}`)}>
          <td>{j.scheduled_start ? when(j.scheduled_start) : 'Not scheduled'}</td>
          <td><b>{j.service_type}</b></td>
          <td>{j.customer?.name}<div className="hb-muted">{j.location?.name}</div></td>
          <td>{j.assigned.map(a => a.name).join(', ') || <span className="hb-muted">Unassigned</span>}</td>
          <td><StatusDot s={j.status} label={j.status_label} /></td></tr>))}</tbody></table></div>
  )
}

// ── Operations overview ───────────────────────────────────────────────────
export function OpsOverview() {
  const me = useOps()
  const [r] = useLoad(can(me, 'reports') ? '/service-ops/report' : null)
  const [s] = useLoad('/service-ops/suggestions')
  const [req, , reloadReq] = useLoad(can(me, 'requests') ? '/service-ops/requests?status=open' : null)
  const [toast, say] = useToast()
  const answer = async (id, status) => {
    try { await api.post(`/service-ops/requests/${id}`, { status }); reloadReq(); say('Updated') } catch (e) { say(errText(e)) }
  }
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Service Operations" title="Operations" sub="The work you deliver after a contract is won." />
      <QuickActions me={me} />
      {r && <div className="hb-stats">
        <Stat icon="check" tone="green" label="Completed (30 days)" value={r.completed_in_period} sub={r.on_time_rate !== null ? `${Math.round(r.on_time_rate * 100)}% started on time` : null} />
        <Stat icon="calendar" tone="blue" label="Recurring agreements" value={r.recurring_agreements} sub={`${money(r.recurring_monthly_value)}/mo expected`} />
        <Stat icon="dollar" tone="violet" label="Paid (confirmed)" value={money(r.confirmed_paid)} sub={`${money(r.outstanding)} outstanding`} />
        <Stat icon="building" tone="amber" label="Customers / locations" value={`${r.active_customers} / ${r.active_locations}`} sub={`${r.open_requests} open requests`} />
      </div>}
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Needs your attention</h2>
          <p className="hb-muted">Suggestions only — nothing here acts on its own.</p>
          {!s ? <Loading /> : s.items.length ? <ul className="hb-feed">{s.items.slice(0, 12).map((x, i) => (
            <li key={i}><span className={`hb-feed__icon hb-tone--${x.severity === 'high' ? 'red' : x.severity === 'medium' ? 'amber' : 'violet'}`}>!</span>
              <div><b>{x.text}</b>{x.job_id && <Link to={`${OPS}/jobs/${x.job_id}`}>Open job</Link>}
                {x.invoice_id && <Link to={`${OPS}/invoices?open=${x.invoice_id}`}>Open invoice</Link>}</div></li>))}</ul>
            : <Empty icon="check" title="All clear" text="No unassigned, late or uninvoiced work." />}
        </section>
        {req && <section className="aq-card"><h2>Customer requests</h2>
          {req.items.length ? req.items.map(x => (
            <div className="aq-row" key={x.id}><div className="aq-row__main"><b>{x.customer_name} · {x.kind_label}</b><span>{x.body}</span></div>
              <button className="aq-btn aq-btn--sm" onClick={() => answer(x.id, 'acknowledged')}>Acknowledge</button>
              <button className="aq-btn aq-btn--sm" onClick={() => answer(x.id, 'closed')}>Close</button></div>))
            : <Empty icon="chat" title="No open requests" />}
        </section>}
      </div>
    </div>
  )
}

// ── Customers & locations ─────────────────────────────────────────────────
const blankCust = { name: '', contact_name: '', contact_email: '', contact_phone: '', billing_address: '',
                    loc_name: '', address: '', city: '', state: '', service_category: 'cleaning', access_instructions: '' }

export function OpsCustomers() {
  const me = useOps()
  const [d, err, reload] = useLoad('/service-ops/customers')
  const [team] = useLoad('/service-ops/team')
  const [buyers, , reloadBuyers] = useLoad(can(me, 'buyers') && me?.buyer_portal_enabled ? '/service-ops/buyers' : null)
  const [form, setForm] = useState(() => (qsNew() ? { ...blankCust } : null))
  const [locFor, setLocFor] = useState(null)
  const [edit, setEdit] = useState(null)
  const [invite, setInvite] = useState(null)
  const [link, setLink] = useState(null)
  const [toast, say] = useToast()
  const set = (s, f) => k => e => f({ ...s, [k]: e.target.value })
  const cats = d?.categories || []
  const save = async () => {
    try {
      const body = { name: form.name, contact_name: form.contact_name, contact_email: form.contact_email,
                     contact_phone: form.contact_phone, billing_address: form.billing_address }
      if (form.loc_name) body.location = { name: form.loc_name, address: form.address, city: form.city, state: form.state,
                                           service_category: form.service_category, access_instructions: form.access_instructions }
      await api.post('/service-ops/customers', body); setForm(null); reload(); say('Customer added')
    } catch (e) { say(errText(e)) }
  }
  const addLoc = async () => {
    try { await api.post(`/service-ops/customers/${locFor.cid}/locations`, locFor.f); setLocFor(null); reload(); say('Location added') } catch (e) { say(errText(e)) }
  }
  const saveLoc = async () => {
    try { await api.put(`/service-ops/locations/${edit.id}`, edit.f); setEdit(null); reload(); say('Saved') } catch (e) { say(errText(e)) }
  }
  const sendInvite = async () => {
    try { const r = await api.post('/service-ops/buyers', invite); setLink(r.setup_url); setInvite(null); reloadBuyers() } catch (e) { say(errText(e)) }
  }
  const revoke = async id => { try { await api.post(`/service-ops/buyers/${id}/revoke`, {}); reloadBuyers(); say('Access removed') } catch (e) { say(errText(e)) } }
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  const members = team?.items?.filter(m => m.active) || []
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Service Operations" title="Customers & Locations" sub="Your customers and the places you service."
        actions={can(me, 'customers') && <button className="aq-btn aq-btn--primary" onClick={() => setForm({ ...blankCust })}>Add customer</button>} />
      {link && <div className="aq-note">Portal setup link (copy and send it yourself — EvoSys didn’t email anyone): <code style={{ wordBreak: 'break-all' }}>{link}</code>
        <button className="aq-btn aq-btn--sm" onClick={() => { navigator.clipboard?.writeText(link); say('Copied') }}>Copy</button></div>}
      {form && <section className="aq-card"><h2>New customer</h2>
        <div className="aq-grid aq-two">{field('c-n', 'Customer name', <input id="c-n" value={form.name} onChange={set(form, setForm)('name')} />)}
          {field('c-c', 'Contact name', <input id="c-c" value={form.contact_name} onChange={set(form, setForm)('contact_name')} />)}
          {field('c-e', 'Contact email', <input id="c-e" type="email" value={form.contact_email} onChange={set(form, setForm)('contact_email')} />)}
          {field('c-p', 'Contact phone', <input id="c-p" value={form.contact_phone} onChange={set(form, setForm)('contact_phone')} />)}
          {field('c-b', 'Billing address', <input id="c-b" value={form.billing_address} onChange={set(form, setForm)('billing_address')} />)}
          {field('c-l', 'First location name', <input id="c-l" value={form.loc_name} onChange={set(form, setForm)('loc_name')} />)}
          {field('c-a', 'Location address', <input id="c-a" value={form.address} onChange={set(form, setForm)('address')} />)}
          {field('c-ci', 'City', <input id="c-ci" value={form.city} onChange={set(form, setForm)('city')} />)}
          {field('c-cat', 'Service', <select id="c-cat" value={form.service_category} onChange={set(form, setForm)('service_category')}>{cats.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}</select>)}
          {field('c-ai', 'Access instructions (private)', <input id="c-ai" placeholder="Gate code, key box…" value={form.access_instructions} onChange={set(form, setForm)('access_instructions')} />)}</div>
        <div className="aq-actions"><button className="aq-btn" onClick={() => setForm(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={save} disabled={!form.name}>Save customer</button></div>
      </section>}
      {!d ? <Loading /> : !d.items.length ? <Empty icon="building" title="No customers yet" text="Convert a won contract on the Jobs page, or add a customer." />
        : d.items.map(c => (
          <section className="aq-card" key={c.id}>
            <div className="aq-row"><div className="aq-row__main"><b>{c.name}</b><span>{[c.contact_name, c.contact_email, c.contact_phone].filter(Boolean).join(' · ') || 'No contact on file'}</span></div>
              {can(me, 'customers') && <button className="aq-btn aq-btn--sm" onClick={() => setLocFor({ cid: c.id, f: { name: '', service_category: 'cleaning' } })}>Add location</button>}
              {buyers && <button className="aq-btn aq-btn--sm" onClick={() => setInvite({ customer_id: c.id, email: '', full_name: '', can_approve_estimates: false })}>Invite to portal</button>}</div>
            {locFor?.cid === c.id && <div className="aq-grid aq-two">
              {field('l-n', 'Location name', <input id="l-n" value={locFor.f.name} onChange={e => setLocFor({ ...locFor, f: { ...locFor.f, name: e.target.value } })} />)}
              {field('l-a', 'Address', <input id="l-a" value={locFor.f.address || ''} onChange={e => setLocFor({ ...locFor, f: { ...locFor.f, address: e.target.value } })} />)}
              {field('l-c', 'Service', <select id="l-c" value={locFor.f.service_category} onChange={e => setLocFor({ ...locFor, f: { ...locFor.f, service_category: e.target.value } })}>{cats.map(x => <option key={x.key} value={x.key}>{x.label}</option>)}</select>)}
              <div className="aq-actions"><button className="aq-btn" onClick={() => setLocFor(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={addLoc} disabled={!locFor.f.name}>Add</button></div></div>}
            {invite?.customer_id === c.id && <div className="aq-grid aq-two">
              {field('i-n', 'Their name', <input id="i-n" value={invite.full_name} onChange={e => setInvite({ ...invite, full_name: e.target.value })} />)}
              {field('i-e', 'Their email', <input id="i-e" type="email" value={invite.email} onChange={e => setInvite({ ...invite, email: e.target.value })} />)}
              <label className="hb-check"><input type="checkbox" checked={invite.can_approve_estimates} onChange={e => setInvite({ ...invite, can_approve_estimates: e.target.checked })} /> May approve estimates</label>
              <div className="aq-actions"><button className="aq-btn" onClick={() => setInvite(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={sendInvite}>Create portal login</button></div></div>}
            {c.locations.map(l => (
              <div className="aq-row" key={l.id}><div className="aq-row__main"><b>{l.name} <span className="hb-muted">· {l.category_label}{l.active ? '' : ' · inactive'}</span></b>
                <span>{[l.address, l.city, l.state].filter(Boolean).join(', ') || 'No address'}{l.has_access_instructions && ' · access instructions on file'}</span>
                {edit?.id === l.id && <div className="aq-grid aq-two">
                  {field('e-ai', 'Access instructions (private)', <input id="e-ai" value={edit.f.access_instructions ?? ''} onChange={e => setEdit({ ...edit, f: { ...edit.f, access_instructions: e.target.value } })} />)}
                  {field('e-sr', 'Special requirements', <input id="e-sr" value={edit.f.special_requirements ?? ''} onChange={e => setEdit({ ...edit, f: { ...edit.f, special_requirements: e.target.value } })} />)}
                  {field('e-d', 'Standard duration (min)', <input id="e-d" inputMode="numeric" value={edit.f.standard_duration_min ?? ''} onChange={e => setEdit({ ...edit, f: { ...edit.f, standard_duration_min: e.target.value } })} />)}
                  {field('e-m', 'Assigned team', <select id="e-m" multiple value={edit.f.assigned_member_ids} onChange={e => setEdit({ ...edit, f: { ...edit.f, assigned_member_ids: [...e.target.selectedOptions].map(o => o.value) } })}>{members.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select>)}
                  <div className="aq-actions"><button className="aq-btn" onClick={() => setEdit(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={saveLoc}>Save</button></div></div>}
              </div>
                {can(me, 'customers') && edit?.id !== l.id && <button className="aq-btn aq-btn--sm" onClick={() => setEdit({ id: l.id, f: { access_instructions: l.access_instructions || '', special_requirements: l.special_requirements || '', standard_duration_min: l.standard_duration_min || '', assigned_member_ids: l.assigned_member_ids } })}>Edit</button>}</div>))}
            {buyers?.items?.filter(b => b.customer_id === c.id).map(b => (
              <div className="aq-row" key={b.id}><div className="aq-row__main"><span>Portal: {b.name} · {b.email} · {b.status === 'revoked' ? 'access removed' : SETUP_LABEL[b.setup?.state] || b.status}</span></div>
                {b.status !== 'revoked' && <><button className="aq-btn aq-btn--sm" onClick={async () => { try { const r = await api.post(`/service-ops/buyers/${b.id}/setup-link`, {}); setLink(r.setup_url); reloadBuyers() } catch (e) { say(errText(e)) } }}>New link</button>
                  <button className="aq-btn aq-btn--sm" onClick={() => revoke(b.id)}>Remove access</button></>}</div>))}
          </section>))}
    </div>
  )
}

// ── Jobs ──────────────────────────────────────────────────────────────────
const TABS = [['upcoming', 'Upcoming', 'draft,scheduled,assigned,confirmed,in_progress'], ['attention', 'Needs attention', 'needs_attention,customer_review'],
              ['done', 'Completed', 'completed,approved'], ['all', 'All', '']]

export function OpsJobs() {
  const me = useOps()
  const [tab, setTab] = useState('upcoming')
  const q = TABS.find(t => t[0] === tab)[2]
  const [d, err, reload] = useLoad(`/service-ops/jobs${q ? `?status=${q}` : ''}${!can(me, 'view_all') ? `${q ? '&' : '?'}mine=${!can(me, 'view_team')}` : ''}`)
  const [won, , reloadWon] = useLoad(can(me, 'schedule') ? '/service-ops/won' : null)
  const [cust] = useLoad(can(me, 'schedule') ? '/service-ops/customers' : null)
  const [conv, setConv] = useState(null)
  const [nj, setNj] = useState(null)
  const [toast, say] = useToast()
  const nav = useNavigate()
  const locs = useMemo(() => (cust?.items || []).flatMap(c => c.locations.map(l => ({ ...l, cname: c.name }))), [cust])
  const convert = async () => {
    try {
      const r = await api.post(`/service-ops/won/${conv.id}/convert`, {
        location: { name: conv.loc, service_category: conv.cat, address: conv.address },
        job: { local_start: conv.start || undefined, service_type: conv.service || undefined } })
      setConv(null); reloadWon(); reload(); nav(`${OPS}/jobs/${r.job.id}`)
    } catch (e) { say(errText(e)) }
  }
  const create = async () => {
    try { const r = await api.post('/service-ops/jobs', { ...nj, idempotency_key: nj.key }); nav(`${OPS}/jobs/${r.id}`) } catch (e) { say(errText(e)) }
  }
  useEffect(() => {
    if (qsNew() && cust && !nj) setNj({ location_id: locs[0]?.id || '', local_start: '', duration_min: 60, price: '', service_type: '', key: crypto.randomUUID?.() || String(Date.now()) })
  }, [cust]) // eslint-disable-line react-hooks/exhaustive-deps
  const pending = (won?.items || []).filter(x => !x.job_id)
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Service Operations" title="Jobs" sub="Delivery work — separate from the sales appointment it came from."
        actions={can(me, 'schedule') && <button className="aq-btn aq-btn--primary" onClick={() => setNj({ location_id: locs[0]?.id || '', local_start: '', duration_min: 60, price: '', service_type: '', key: crypto.randomUUID?.() || String(Date.now()) })}>New job</button>} />
      {pending.length > 0 && <section className="aq-card"><h2>Won contracts to set up</h2>
        {pending.map(x => (
          <div className="aq-row" key={x.id}><div className="aq-row__main"><b>{x.business_name}</b><span>{x.monthly_value ? `${money(x.monthly_value)}/mo` : 'Won'} · {x.address || x.city || ''}</span>
            {conv?.id === x.id && <div className="aq-grid aq-two">
              {field('v-l', 'Service location name', <input id="v-l" value={conv.loc} onChange={e => setConv({ ...conv, loc: e.target.value })} />)}
              {field('v-a', 'Address', <input id="v-a" value={conv.address} onChange={e => setConv({ ...conv, address: e.target.value })} />)}
              {field('v-c', 'Service', <select id="v-c" value={conv.cat} onChange={e => setConv({ ...conv, cat: e.target.value })}>{(cust?.categories || []).map(c => <option key={c.key} value={c.key}>{c.label}</option>)}</select>)}
              {field('v-s', 'First visit (local time)', <input id="v-s" type="datetime-local" value={conv.start} onChange={e => setConv({ ...conv, start: e.target.value })} />)}
              {field('v-t', 'Service type', <input id="v-t" placeholder="e.g. Exterior windows" value={conv.service} onChange={e => setConv({ ...conv, service: e.target.value })} />)}
              <div className="aq-actions"><button className="aq-btn" onClick={() => setConv(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={convert}>Convert to Job</button></div></div>}</div>
            {conv?.id !== x.id && <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={() => setConv({ id: x.id, loc: x.business_name, address: x.address || '', cat: 'cleaning', start: '', service: '' })}>Convert to Job</button>}
            <Link className="aq-btn aq-btn--sm" to={`${BASE}/appointments/${x.id}`}>View Related Contract</Link></div>))}
      </section>}
      {nj && <section className="aq-card"><h2>New job</h2><div className="aq-grid aq-two">
        {field('n-l', 'Location', <select id="n-l" value={nj.location_id} onChange={e => setNj({ ...nj, location_id: e.target.value })}>{locs.map(l => <option key={l.id} value={l.id}>{l.cname} — {l.name}</option>)}</select>)}
        {field('n-t', 'Service type', <input id="n-t" value={nj.service_type} onChange={e => setNj({ ...nj, service_type: e.target.value })} />)}
        {field('n-s', 'Start (local time)', <input id="n-s" type="datetime-local" value={nj.local_start} onChange={e => setNj({ ...nj, local_start: e.target.value })} />)}
        {field('n-d', 'Duration (min)', <input id="n-d" inputMode="numeric" value={nj.duration_min} onChange={e => setNj({ ...nj, duration_min: e.target.value })} />)}
        {field('n-p', 'Price ($)', <input id="n-p" inputMode="decimal" value={nj.price} onChange={e => setNj({ ...nj, price: e.target.value })} />)}
        <div className="aq-actions"><button className="aq-btn" onClick={() => setNj(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={create} disabled={!nj.location_id}>Create job</button></div></div>
        {!locs.length && <p className="hb-muted">Add a customer and location first.</p>}</section>}
      <Toolbar><div className="hb-tabs">{TABS.map(t => <button key={t[0]} className={tab === t[0] ? 'on' : ''} onClick={() => setTab(t[0])}>{t[1]}</button>)}</div></Toolbar>
      {err ? <div className="aq-err">{errText(err)}</div> : !d ? <Loading /> : <JobRows items={d.items} />}
    </div>
  )
}

export function OpsHistory() {
  const [d, err] = useLoad('/service-ops/jobs?status=completed,customer_review,approved')
  return (
    <div className="aq-page"><PageHeader eyebrow="Service Operations" title="Service History" sub="Completed visits with who did the work, the checklist, notes and photos." />
      {err ? <div className="aq-err">{errText(err)}</div> : !d ? <Loading /> : <JobRows items={[...d.items].reverse()} empty="No completed visits yet." />}</div>
  )
}

function Photo({ jid, a }) {
  const [src, setSrc] = useState(null)
  useEffect(() => { let u; fetchObjectUrl(`/service-ops/jobs/${jid}/photos/${a.id}`).then(x => { u = x; setSrc(x) }).catch(() => {}); return () => u && URL.revokeObjectURL(u) }, [jid, a.id])
  return src ? <img src={src} alt={a.caption || 'Job photo'} style={{ maxWidth: 160, maxHeight: 120, borderRadius: 8, marginRight: 8 }} /> : <span className="hb-muted">Loading photo…</span>
}

export function OpsJob() {
  const { id } = useParams()
  const me = useOps()
  const [j, err, reload] = useLoad(`/service-ops/jobs/${id}`)
  const [team] = useLoad(can(me, 'schedule') ? '/service-ops/team' : null)
  const [pick, setPick] = useState('')
  const [why, setWhy] = useState(null)
  const [resched, setResched] = useState(null)
  const [sched, setSched] = useState(null)
  const [ai, setAi] = useState(null)
  const [toast, say] = useToast()
  const nav = useNavigate()
  const act = async (fn, ok) => { try { await fn(); reload(); if (ok) say(ok) } catch (e) { say(errText(e)) } }
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!j) return <Loading />
  const finished = ['completed', 'customer_review', 'approved'].includes(j.status)
  const mayStatus = s => j.next.includes(s)
  const invoice = () => act(async () => { const r = await api.post('/service-ops/invoices', { job_id: j.id }); nav(`${OPS}/invoices?open=${r.id}`) })
  const makeSchedule = () => act(async () => {
    await api.post('/service-ops/schedules', { ...sched, location_id: j.location.id, opportunity_id: j.opportunity_id || undefined,
                                                 service_type: j.service_type, assigned_member_ids: j.assigned.map(a => a.member_id) })
    setSched(null); nav(`${OPS}/schedule`)
  })
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow={<Link to={`${OPS}/jobs`}>‹ Jobs</Link>} title={j.service_type}
        sub={`${j.customer?.name} · ${j.location?.name} · ${j.scheduled_start ? when(j.scheduled_start) : 'Not scheduled'} (${j.timezone})`}
        actions={<StatusDot s={j.status} label={j.status_label} />} />
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Details</h2>
          <div className="hb-kv"><span>Location</span><b>{j.location_detail?.address || j.location?.address || '—'}</b>
            <span>Duration</span><b>{j.duration_min} min</b><span>Price</span><b>{usd(j.price)}</b>
            <span>Team</span><b>{j.assigned.map(a => a.name).join(', ') || 'Unassigned'}</b>
            {j.location_detail?.access_instructions && <><span>Access (private)</span><b>{j.location_detail.access_instructions}</b></>}
            {j.instructions && <><span>Instructions</span><b>{j.instructions}</b></>}
            {j.attention_reason && <><span>Attention</span><b>{j.attention_reason}</b></>}
            {j.completion_notes && <><span>Completion notes</span><b>{j.completion_notes}</b></>}</div>
          <div className="aq-actions" style={{ flexWrap: 'wrap' }}>
            {j.opportunity_id && <Link className="aq-btn aq-btn--sm" to={`${BASE}/appointments/${j.opportunity_id}`}>View Related Contract</Link>}
            {can(me, 'view_all') && <Link className="aq-btn aq-btn--sm" to={`${OPS}/customers`}>View Customer</Link>}
            {can(me, 'schedule') && !j.schedule_id && !finished && j.status !== 'cancelled' && <button className="aq-btn aq-btn--sm" onClick={() => setSched({ frequency: 'weekly', start_date: (j.local_start || '').slice(0, 10), start_time: (j.local_start || 'T09:00').slice(11, 16), price_per_visit: j.price || '' })}>Create Recurring Schedule</button>}
            {can(me, 'schedule') && mayStatus('confirmed') && <button className="aq-btn aq-btn--sm" onClick={() => act(() => api.post(`/service-ops/jobs/${j.id}/status`, { status: 'confirmed' }), 'Confirmed')}>Confirm</button>}
            {can(me, 'schedule') && !finished && j.status !== 'cancelled' && <button className="aq-btn aq-btn--sm" onClick={() => setResched({ local_start: j.local_start || '', duration_min: j.duration_min })}>Reschedule</button>}
            {can(me, 'approve') && mayStatus('approved') && <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={() => act(() => api.post(`/service-ops/jobs/${j.id}/status`, { status: 'approved' }), 'Approved')}>Approve work</button>}
            {can(me, 'billing') && finished && <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={invoice}>Create invoice</button>}
            {me?.ai_assist_enabled && finished && <button className="aq-btn aq-btn--sm" onClick={async () => { try { setAi(await api.get(`/service-ops/jobs/${j.id}/ai-summary`)) } catch (e) { say(errText(e)) } }}>AI summary (suggestion)</button>}
            {can(me, 'schedule') && j.schedule_id && mayStatus('cancelled') && <button className="aq-btn aq-btn--sm" onClick={() => setWhy({ to: 'skip', text: '' })}>Skip this visit</button>}
            {can(me, 'schedule') && mayStatus('cancelled') && <button className="aq-btn aq-btn--sm aq-btn--danger" onClick={() => setWhy({ to: 'cancelled', text: '' })}>Cancel job</button>}
            {mayStatus('needs_attention') && <button className="aq-btn aq-btn--sm" onClick={() => setWhy({ to: 'needs_attention', text: '' })}>Flag</button>}
          </div>
          {why && <Confirm text={why.to === 'skip' ? 'Skip only this visit?' : why.to === 'cancelled' ? 'Cancel this job?' : 'What needs attention?'} danger={why.to === 'cancelled'}
            onCancel={() => setWhy(null)} onConfirm={() => act(async () => {
              if (why.to === 'skip') await api.post(`/service-ops/jobs/${j.id}/skip`, { reason: why.text })
              else await api.post(`/service-ops/jobs/${j.id}/status`, { status: why.to, reason: why.text }); setWhy(null) })}>
            <input aria-label="Reason" placeholder="Reason" value={why.text} onChange={e => setWhy({ ...why, text: e.target.value })} /></Confirm>}
          {resched && <div className="aq-grid aq-two">{field('r-s', 'New start (local)', <input id="r-s" type="datetime-local" value={resched.local_start} onChange={e => setResched({ ...resched, local_start: e.target.value })} />)}
            {field('r-d', 'Duration (min)', <input id="r-d" inputMode="numeric" value={resched.duration_min} onChange={e => setResched({ ...resched, duration_min: e.target.value })} />)}
            <div className="aq-actions"><button className="aq-btn" onClick={() => setResched(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={() => act(async () => { await api.put(`/service-ops/jobs/${j.id}`, resched); setResched(null) }, 'Rescheduled')}>Save</button></div></div>}
          {sched && <div className="aq-grid aq-two">
            {field('s-f', 'How often', <select id="s-f" value={sched.frequency} onChange={e => setSched({ ...sched, frequency: e.target.value })}>{[['daily', 'Daily'], ['weekly', 'Weekly'], ['biweekly', 'Every 2 weeks'], ['monthly', 'Monthly'], ['custom', 'Every N days']].map(f => <option key={f[0]} value={f[0]}>{f[1]}</option>)}</select>)}
            {sched.frequency === 'custom' && field('s-i', 'Every how many days', <input id="s-i" inputMode="numeric" value={sched.interval_days || ''} onChange={e => setSched({ ...sched, interval_days: e.target.value })} />)}
            {field('s-s', 'Start date', <input id="s-s" type="date" value={sched.start_date} onChange={e => setSched({ ...sched, start_date: e.target.value })} />)}
            {field('s-e', 'End date (optional)', <input id="s-e" type="date" value={sched.end_date || ''} onChange={e => setSched({ ...sched, end_date: e.target.value })} />)}
            {field('s-t', 'Time (local)', <input id="s-t" type="time" value={sched.start_time} onChange={e => setSched({ ...sched, start_time: e.target.value })} />)}
            {field('s-p', 'Price per visit ($)', <input id="s-p" inputMode="decimal" value={sched.price_per_visit} onChange={e => setSched({ ...sched, price_per_visit: e.target.value })} />)}
            <div className="aq-actions"><button className="aq-btn" onClick={() => setSched(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={makeSchedule}>Create schedule</button></div></div>}
          {ai && <div className="aq-note"><b>AI suggestion</b> — {ai.ok ? ai.summary : ai.reason}{ai.follow_ups?.length > 0 && <ul>{ai.follow_ups.map(f => <li key={f}>{f}</li>)}</ul>}</div>}
        </section>
        <section className="aq-card"><h2>Team</h2>
          {j.assigned.map(a => <div className="aq-row" key={a.member_id}><div className="aq-row__main"><b>{a.name}</b></div>
            {can(me, 'schedule') && !finished && <button className="aq-btn aq-btn--sm" onClick={() => act(() => api.post(`/service-ops/jobs/${j.id}/assign`, { remove: [a.member_id] }), 'Removed')}>Remove</button>}</div>)}
          {team && !finished && j.status !== 'cancelled' && <div className="aq-actions"><select aria-label="Team member" value={pick} onChange={e => setPick(e.target.value)}>
            <option value="">Assign someone…</option>{team.items.filter(m => m.active && !j.assigned.some(a => a.member_id === m.id)).map(m => <option key={m.id} value={m.id}>{m.name} · {m.role_label}</option>)}</select>
            <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!pick} onClick={() => act(async () => { await api.post(`/service-ops/jobs/${j.id}/assign`, { add: [pick] }); setPick('') }, 'Assigned')}>Assign</button></div>}
          <h2 style={{ marginTop: 16 }}>Checklist</h2>
          {j.checklist.length ? j.checklist.map(i => <div key={i.key} className="hb-muted">{i.done ? '☑' : '☐'} {i.label}{i.required ? '' : ' (optional)'}</div>) : <span className="hb-muted">No checklist</span>}
          {j.attachments.length > 0 && <><h2 style={{ marginTop: 16 }}>Photos</h2><div>{j.attachments.map(a => <Photo key={a.id} jid={j.id} a={a} />)}</div></>}
        </section>
      </div>
      <section className="aq-card"><h2>History</h2>
        <p className="hb-muted">{j.history_check.intact ? `Record intact — ${j.history_check.events} entries, none altered.` : `Warning: history entry ${j.history_check.broken_at} was altered.`}</p>
        <ul className="hb-feed">{j.history.map(h => <li key={h.seq}><div><b>{h.kind === 'status' ? `${h.from || '—'} → ${h.to}` : h.kind}</b>
          {h.data?.reason && <span>{h.data.reason}</span>}{h.data?.body && <span>{h.data.body}</span>}</div><time>{when(h.at)}</time></li>)}</ul>
      </section>
    </div>
  )
}

// ── Recurring schedule ────────────────────────────────────────────────────
export function OpsSchedule() {
  const me = useOps()
  const [d, err, reload] = useLoad('/service-ops/schedules')
  const [cust] = useLoad('/service-ops/customers')
  const [toast, say] = useToast()
  const names = useMemo(() => Object.fromEntries((cust?.items || []).flatMap(c => c.locations.map(l => [l.id, `${c.name} — ${l.name}`]))), [cust])
  const [team] = useLoad('/service-ops/team')
  const [adding, setAdding] = useState(() => !!qsNew())
  const locs = useMemo(() => (cust?.items || []).flatMap(c => c.locations.map(l => ({ ...l, cname: c.name }))), [cust])
  const set = async (id, status) => { try { await api.post(`/service-ops/schedules/${id}/status`, { status, reason: status === 'cancelled' ? 'Cancelled by office' : undefined }); reload(); say('Updated') } catch (e) { say(errText(e)) } }
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Service Operations" title="Schedule" sub="Recurring service for your customers, and the hours EvoSys can book new appointments for you."
        actions={can(me, 'schedule') && !adding && <button className="aq-btn aq-btn--primary" onClick={() => setAdding(true)}>New recurring service</button>} />
      {adding && cust && <NewScheduleForm locs={locs} team={(team?.items || []).filter(m => m.active)} say={say} onCancel={() => setAdding(false)}
        onDone={() => { setAdding(false); reload(); say('Recurring service created — visits are on the Jobs page') }} />}
      <AvailabilityPanel say={say} compact />
      {err ? <div className="aq-err">{errText(err)}</div> : !d ? <Loading /> : !d.items.length ? <Empty icon="calendar" title="No recurring service yet" text="Choose New recurring service above, or open a job and choose Create Recurring Schedule." />
        : <div className="hb-table-wrap"><table className="aq-table"><thead><tr><th>Location</th><th>Service</th><th>How often</th><th>Next visit</th><th>Value</th><th>Status</th><th /></tr></thead>
          <tbody>{d.items.map(s => <tr key={s.id}><td>{names[s.location_id] || '—'}</td><td>{s.service_type}</td>
            <td>{s.frequency_label} · {s.start_time} {s.timezone.split('/')[1]?.replace('_', ' ')}</td><td>{s.next_visit ? when(s.next_visit) : '—'}</td>
            <td>{s.expected_monthly_value ? `${money(s.expected_monthly_value)}/mo` : '—'}</td><td><StatusDot s={s.status === 'active' ? 'scheduled' : 'cancelled'} label={s.status} /></td>
            <td>{can(me, 'schedule') && s.status !== 'cancelled' && <>{s.status === 'active' ? <button className="aq-btn aq-btn--sm" onClick={() => set(s.id, 'paused')}>Pause</button>
              : <button className="aq-btn aq-btn--sm" onClick={() => set(s.id, 'active')}>Resume</button>}
              <button className="aq-btn aq-btn--sm aq-btn--danger" onClick={() => set(s.id, 'cancelled')}>Cancel</button></>}</td></tr>)}</tbody></table></div>}
    </div>
  )
}

// ── Team ──────────────────────────────────────────────────────────────────
export function OpsTeam() {
  const me = useOps()
  const [d, err, reload] = useLoad('/service-ops/team')
  const [w] = useLoad('/service-ops/team/workload')
  const [inv, setInv] = useState(() => (qsNew() ? { full_name: '', email: '', role: 'field_worker', team_name: '' } : null))
  const [link, setLink] = useState(null)
  const [toast, say] = useToast()
  const load = Object.fromEntries((w?.items || []).map(m => [m.id, m.upcoming]))
  const invite = async () => { try { const r = await api.post('/service-ops/team', inv); setLink(r.setup_url); setInv(null); reload() } catch (e) { say(errText(e)) } }
  const upd = async (id, body) => { try { await api.put(`/service-ops/team/${id}`, body); reload(); say(body.active === false ? 'Turned off — they can’t sign in' : 'Saved') } catch (e) { say(errText(e)) } }
  const relink = async id => { try { const r = await api.post(`/service-ops/team/${id}/setup-link`, {}); setLink(r.setup_url); reload() } catch (e) { say(errText(e)) } }
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Service Operations" title="Team & Assignments" sub="Everyone uses their own EvoSys login. Field workers see only the jobs they’re assigned."
        actions={can(me, 'team') && <button className="aq-btn aq-btn--primary" onClick={() => setInv({ full_name: '', email: '', role: 'field_worker', team_name: '' })}>Invite team member</button>} />
      {link && <div className="aq-note">Setup link (copy and send it yourself — nothing was emailed): <code style={{ wordBreak: 'break-all' }}>{link}</code>
        <button className="aq-btn aq-btn--sm" onClick={() => { navigator.clipboard?.writeText(link); say('Copied') }}>Copy</button></div>}
      {inv && <section className="aq-card"><div className="aq-grid aq-two">
        {field('t-n', 'Name', <input id="t-n" value={inv.full_name} onChange={e => setInv({ ...inv, full_name: e.target.value })} />)}
        {field('t-e', 'Email', <input id="t-e" type="email" value={inv.email} onChange={e => setInv({ ...inv, email: e.target.value })} />)}
        {field('t-r', 'Role', <select id="t-r" value={inv.role} onChange={e => setInv({ ...inv, role: e.target.value })}>{(d?.roles || []).filter(r => r.key !== 'company_admin' || me?.role === 'company_admin').map(r => <option key={r.key} value={r.key}>{r.label}</option>)}</select>)}
        {field('t-t', 'Team (optional)', <input id="t-t" placeholder="e.g. North crew" value={inv.team_name} onChange={e => setInv({ ...inv, team_name: e.target.value })} />)}
        <div className="aq-actions"><button className="aq-btn" onClick={() => setInv(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={invite}>Create login</button></div></div></section>}
      {err ? <div className="aq-err">{errText(err)}</div> : !d ? <Loading /> : !d.items.length ? <Empty icon="users" title="No team yet" />
        : <div className="hb-table-wrap"><table className="aq-table"><thead><tr><th>Name</th><th>Role</th><th>Team</th><th>Next 14 days</th><th>Login</th><th /></tr></thead>
          <tbody>{d.items.map(m => <tr key={m.id}><td><b>{m.name}</b><div className="hb-muted">{m.email}</div></td>
            <td>{can(me, 'team') && m.role !== 'company_admin' ? <select aria-label="Role" value={m.role} onChange={e => upd(m.id, { role: e.target.value })}>{d.roles.filter(r => r.key !== 'company_admin').map(r => <option key={r.key} value={r.key}>{r.label}</option>)}</select> : m.role_label}</td>
            <td>{m.team_name || '—'}</td><td>{load[m.id] ? `${load[m.id].jobs} jobs · ${Math.round(load[m.id].minutes / 60)} h` : '—'}</td>
            <td>{!m.active ? 'Off — can’t sign in' : m.has_signed_in ? 'Active' : SETUP_LABEL[m.setup?.state] || 'Invited'}</td>
            <td>{can(me, 'team') && <>{m.active && m.role !== 'company_admin' && <button className="aq-btn aq-btn--sm" onClick={() => relink(m.id)}>{m.has_signed_in ? 'Password reset link' : 'New setup link'}</button>}
              <button className="aq-btn aq-btn--sm" onClick={() => upd(m.id, { active: !m.active })}>{m.active ? 'Turn off' : 'Turn on'}</button></>}</td></tr>)}</tbody></table></div>}
    </div>
  )
}

// ── Invoices & estimates ──────────────────────────────────────────────────
async function openPdf(path, say) {
  try { const u = await fetchObjectUrl(path); window.open(u, '_blank', 'noopener'); setTimeout(() => URL.revokeObjectURL(u), 60000) } catch (e) { say(errText(e)) }
}

export function OpsInvoices() {
  const me = useOps()
  const [d, err, reload] = useLoad('/service-ops/invoices')
  const [est, , reloadEst] = useLoad('/service-ops/estimates')
  const [cust] = useLoad('/service-ops/customers')
  const [setg, , reloadSet] = useLoad('/service-ops/settings')
  const [open, setOpen] = useState(() => new URLSearchParams(window.location.search).get('open'))
  const [pay, setPay] = useState(null)
  const [ne, setNe] = useState(() => (qsNew() === 'estimate' ? { customer_id: '', lines: [{ description: '', quantity: 1, rate: '' }] } : null))
  const [ni, setNi] = useState(() => (qsNew() === 'invoice' ? { customer_id: '', lines: [{ description: '', quantity: 1, rate: '' }] } : null))
  const [st, setSt] = useState(null)
  useEffect(() => {
    const first = cust?.items?.[0]?.id
    if (!first) return
    if (ne && !ne.customer_id) setNe({ ...ne, customer_id: first })
    if (ni && !ni.customer_id) setNi({ ...ni, customer_id: first })
  }, [cust, ne, ni])
  const [toast, say] = useToast()
  const act = async (fn, ok) => { try { await fn(); reload(); if (ok) say(ok) } catch (e) { say(errText(e)) } }
  const inv = d?.items.find(i => i.id === open)
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Service Operations" title="Invoices & Estimates" sub="Payments are recorded here, not collected. A reported payment counts as paid only once you confirm it arrived."
        actions={<><button className="aq-btn aq-btn--primary" onClick={() => setNi({ customer_id: cust?.items?.[0]?.id || '', lines: [{ description: '', quantity: 1, rate: '' }] })}>New invoice</button>
          <button className="aq-btn" onClick={() => setNe({ customer_id: cust?.items?.[0]?.id || '', lines: [{ description: '', quantity: 1, rate: '' }] })}>New estimate</button>
          {me?.role === 'company_admin' && <button className="aq-btn" onClick={() => setSt({ ...setg })}>Invoice settings</button>}</>} />
      {st && <section className="aq-card"><h2>Invoice settings</h2><div className="aq-grid aq-two">
        {['business_name', 'billing_address', 'billing_email', 'billing_phone', 'payment_instructions', 'default_tax_rate', 'payment_terms_days', 'invoice_prefix'].map(k =>
          field(`st-${k}`, k.replace(/_/g, ' '), <input id={`st-${k}`} value={st[k] ?? ''} onChange={e => setSt({ ...st, [k]: e.target.value })} />))}
        <div className="aq-actions"><button className="aq-btn" onClick={() => setSt(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={async () => {
          try { const b = Object.fromEntries(Object.entries(st).filter(([k]) => !k.endsWith('_enabled') && k !== 'timezone' && k !== 'estimate_prefix')); await api.put('/service-ops/settings', b); setSt(null); reloadSet(); say('Saved') } catch (e) { say(errText(e)) } }}>Save</button></div></div></section>}
      {ni && <section className="aq-card"><h2>New invoice</h2>
        {field('ni-c', 'Customer', <select id="ni-c" value={ni.customer_id} onChange={e => setNi({ ...ni, customer_id: e.target.value })}>{(cust?.items || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select>)}
        {ni.lines.map((l, i) => <div className="aq-grid aq-two" key={i}>
          {field(`ni-d${i}`, 'Description', <input id={`ni-d${i}`} value={l.description} onChange={e => { const ls = [...ni.lines]; ls[i] = { ...l, description: e.target.value }; setNi({ ...ni, lines: ls }) }} />)}
          {field(`ni-r${i}`, 'Rate ($) × qty', <span style={{ display: 'flex', gap: 6, minWidth: 0 }}><input id={`ni-r${i}`} inputMode="decimal" style={{ minWidth: 0 }} value={l.rate} onChange={e => { const ls = [...ni.lines]; ls[i] = { ...l, rate: e.target.value }; setNi({ ...ni, lines: ls }) }} />
            <input aria-label="Quantity" inputMode="decimal" style={{ width: 70 }} value={l.quantity} onChange={e => { const ls = [...ni.lines]; ls[i] = { ...l, quantity: e.target.value }; setNi({ ...ni, lines: ls }) }} /></span>)}</div>)}
        {!cust?.items?.length && <p className="hb-muted">Add a customer first.</p>}
        <div className="aq-actions"><button className="aq-btn aq-btn--sm" onClick={() => setNi({ ...ni, lines: [...ni.lines, { description: '', quantity: 1, rate: '' }] })}>Add line</button>
          <button className="aq-btn" onClick={() => setNi(null)}>Cancel</button>
          <button className="aq-btn aq-btn--primary" disabled={!ni.customer_id} onClick={async () => { try { const r = await api.post('/service-ops/invoices', ni); setNi(null); reload(); setOpen(r.id || r.invoice?.id || null); say('Draft invoice saved — mark it issued when you send it') } catch (e) { say(errText(e)) } }}>Save invoice</button></div></section>}
      {ne && <section className="aq-card"><h2>New estimate</h2>
        {field('ne-c', 'Customer', <select id="ne-c" value={ne.customer_id} onChange={e => setNe({ ...ne, customer_id: e.target.value })}>{(cust?.items || []).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select>)}
        {ne.lines.map((l, i) => <div className="aq-grid aq-two" key={i}>
          {field(`ne-d${i}`, 'Description', <input id={`ne-d${i}`} value={l.description} onChange={e => { const ls = [...ne.lines]; ls[i] = { ...l, description: e.target.value }; setNe({ ...ne, lines: ls }) }} />)}
          {field(`ne-r${i}`, 'Rate ($) × qty', <span style={{ display: 'flex', gap: 6 }}><input id={`ne-r${i}`} inputMode="decimal" value={l.rate} onChange={e => { const ls = [...ne.lines]; ls[i] = { ...l, rate: e.target.value }; setNe({ ...ne, lines: ls }) }} />
            <input aria-label="Quantity" inputMode="decimal" style={{ width: 70 }} value={l.quantity} onChange={e => { const ls = [...ne.lines]; ls[i] = { ...l, quantity: e.target.value }; setNe({ ...ne, lines: ls }) }} /></span>)}</div>)}
        <div className="aq-actions"><button className="aq-btn aq-btn--sm" onClick={() => setNe({ ...ne, lines: [...ne.lines, { description: '', quantity: 1, rate: '' }] })}>Add line</button>
          <button className="aq-btn" onClick={() => setNe(null)}>Cancel</button>
          <button className="aq-btn aq-btn--primary" onClick={async () => { try { await api.post('/service-ops/estimates', ne); setNe(null); reloadEst(); say('Estimate saved') } catch (e) { say(errText(e)) } }}>Save estimate</button></div></section>}
      {inv && <section className="aq-card"><h2>Invoice {inv.number} <StatusDot s={inv.status} label={inv.status_label} /></h2>
        <p className="hb-muted">{inv.customer_name} · total {usd(inv.total)} · confirmed {usd(inv.amount_confirmed)} · reported, not confirmed {usd(inv.amount_reported)} · balance {usd(inv.balance)}</p>
        <div className="hb-table-wrap"><table className="aq-table"><thead><tr><th>Description</th><th>Qty</th><th>Rate</th><th>Amount</th></tr></thead>
          <tbody>{inv.lines.map((l, i) => <tr key={i}><td>{l.description}</td><td>{l.quantity}</td><td>{usd(l.rate)}</td><td>{usd(l.amount)}</td></tr>)}</tbody></table></div>
        <div className="aq-actions" style={{ flexWrap: 'wrap' }}>
          {me?.advanced_enabled && <button className="aq-btn aq-btn--sm" onClick={() => openPdf(`/service-ops/invoices/${inv.id}/pdf`, say)}>View PDF</button>}
          {inv.status === 'draft' && <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={() => act(() => api.post(`/service-ops/invoices/${inv.id}/issue`, {}), 'Issued — not emailed')}>Mark issued</button>}
          {['issued', 'partially_paid', 'overdue'].includes(inv.status) && <button className="aq-btn aq-btn--sm" onClick={() => setPay({ amount: inv.balance, method: 'check', reference: '' })}>Record payment</button>}
          {inv.status !== 'void' && inv.amount_confirmed === 0 && <button className="aq-btn aq-btn--sm aq-btn--danger" onClick={() => act(() => api.post(`/service-ops/invoices/${inv.id}/void`, { reason: 'Voided by office' }), 'Voided')}>Void</button>}
          <button className="aq-btn aq-btn--sm" onClick={() => setOpen(null)}>Close</button></div>
        {pay && <div className="aq-grid aq-two">{field('p-a', 'Amount ($)', <input id="p-a" inputMode="decimal" value={pay.amount} onChange={e => setPay({ ...pay, amount: e.target.value })} />)}
          {field('p-m', 'Method', <select id="p-m" value={pay.method} onChange={e => setPay({ ...pay, method: e.target.value })}>{['check', 'ach', 'card', 'cash', 'other'].map(m => <option key={m}>{m}</option>)}</select>)}
          {field('p-r', 'Reference', <input id="p-r" value={pay.reference} onChange={e => setPay({ ...pay, reference: e.target.value })} />)}
          <div className="aq-actions"><button className="aq-btn" onClick={() => setPay(null)}>Cancel</button><button className="aq-btn aq-btn--primary" onClick={() => act(async () => { await api.post(`/service-ops/invoices/${inv.id}/payments`, pay); setPay(null) }, 'Recorded as reported')}>Record (reported)</button></div></div>}
        {inv.payments.map(p => <div className="aq-row" key={p.id}><div className="aq-row__main"><span>{usd(p.amount)} · {p.method} {p.reference || ''} · <b>{p.status}</b></span></div>
          {p.status === 'reported' && <><button className="aq-btn aq-btn--sm aq-btn--primary" onClick={() => act(() => api.post(`/service-ops/invoices/${inv.id}/payments/${p.id}/confirm`, { confirm: true }), 'Confirmed')}>Confirm received</button>
            <button className="aq-btn aq-btn--sm" onClick={() => act(() => api.post(`/service-ops/invoices/${inv.id}/payments/${p.id}/confirm`, { confirm: false }), 'Rejected')}>Not received</button></>}</div>)}
      </section>}
      <section className="aq-card"><h2>Invoices</h2>
        {err ? <div className="aq-err">{errText(err)}</div> : !d ? <Loading /> : !d.items.length ? <Empty icon="doc" title="No invoices yet" text="Open a completed job and choose Create invoice." />
          : <div className="hb-table-wrap"><table className="aq-table"><thead><tr><th>Number</th><th>Customer</th><th>Total</th><th>Balance</th><th>Due</th><th>Status</th></tr></thead>
            <tbody>{d.items.map(i => <tr key={i.id} className="hb-click" onClick={() => setOpen(i.id)}><td><b>{i.number}</b></td><td>{i.customer_name}</td><td>{usd(i.total)}</td>
              <td>{usd(i.balance)}</td><td>{i.due_date || '—'}</td><td><StatusDot s={i.status} label={i.status_label} /></td></tr>)}</tbody></table></div>}
      </section>
      <section className="aq-card"><h2>Estimates</h2>
        {!est ? <Loading /> : !est.items.length ? <Empty icon="doc" title="No estimates yet" />
          : est.items.map(e => <div className="aq-row" key={e.id}><div className="aq-row__main"><b>{e.number} · {e.customer_name}</b><span>{usd(e.total)} · {e.status}{e.expires_on ? ` · expires ${e.expires_on}` : ''}</span></div>
            {e.status === 'draft' && <button className="aq-btn aq-btn--sm" onClick={async () => { try { await api.post(`/service-ops/estimates/${e.id}/status`, { status: 'sent' }); reloadEst() } catch (x) { say(errText(x)) } }}>Mark sent</button>}
            {['draft', 'sent'].includes(e.status) && <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={async () => { try { await api.post(`/service-ops/estimates/${e.id}/status`, { status: 'accepted' }); reloadEst() } catch (x) { say(errText(x)) } }}>Customer accepted</button>}</div>)}
      </section>
    </div>
  )
}
