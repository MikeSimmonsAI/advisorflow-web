/**
 * EVOSYS PRO — ACQUISITION HUB pages (#474). Program Access → Acquisition.
 * Owner-only pages call /god/acquisition/* which the server refuses for anyone
 * but the platform owner; the navigation only hides what they can't open.
 * Every number comes from the server. Nothing on these pages sends a message.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { OPS, useOps } from './opsContext'
import { Confirm, Dot, Drawer, Empty, Feed, Funnel, Icon, Loading, PageHeader, Stat, Stepper, Toolbar,
         errText, money, useLoad, useToast, when } from './ui'

const QUEUE_TONE = { needs_research: 'rose', needs_email: 'amber', verification_pending: 'blue',
                     ready_for_review: 'green', verified_contact: 'green', not_eligible: 'gray' }
const EMAIL_LABEL = { none: 'No email', invalid: 'Invalid email', unverified: 'Unverified', verified: 'Verified' }
const SEG = { office: 'Office', medical_dental: 'Medical / dental', apartments: 'Apartments',
              property_management: 'Property mgmt', short_term_rental: 'Short-term rental' }

function useGodOnly() {
  const { ov } = useOps()
  return ov.is_god
}

function OwnerOnly() {
  return <div className="aq-page"><Empty icon="shield" title="Owner only" text="This page belongs to the EvoSys platform owner. Your access covers your assigned client workspaces." /></div>
}

// ── 1. MASTER OVERVIEW ──────────────────────────────────────────────────
export function MasterOverview() {
  const [h, err, reload] = useLoad('/god/acquisition/hub')
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!h) return <div className="aq-page"><Loading label="Loading the Acquisition Control Center…" /></div>
  const p = h.pool, r = h.results
  const setup = h.clients.filter(c => !c.launch_ready).length
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Program Access · Acquisition" title="EvoSys Pro — Acquisition Control Center"
        sub="Commercial Cleaning Prospecting, Outreach, Appointment Acquisition & Client Delivery"
        actions={<>
          <a className="aq-btn aq-btn--primary" href={`${OPS}/clients?add=1`}><Icon name="store" size={16} />Add Cleaning Company</a>
          <a className="aq-btn" href={`${OPS}/import`}><Icon name="upload" size={16} />Import Businesses</a>
          <button className="aq-btn" onClick={reload}>Refresh</button></>} />
      <div className="hb-stats">
        <Stat icon="database" label="Master prospects" value={p.total} sub={`${p.qualified} qualified`} to={`${OPS}/master`} />
        <Stat icon="check" tone="green" label="Available" value={p.available} sub="Unassigned, ready to review" to={`${OPS}/assignments`} />
        <Stat icon="share" tone="blue" label="Assigned" value={p.assigned} sub="To cleaning clients" to={`${OPS}/master?status=assigned`} />
        <Stat icon="mail" tone={p.email_verified ? 'green' : 'rose'} label="Verified emails" value={p.email_verified} sub={`${p.needs_email} need contact research`} to={`${OPS}/research`} />
        <Stat icon="store" tone="violet" label="Cleaning clients" value={h.clients.length} sub={`${setup} still setting up`} to={`${OPS}/clients`} />
        <Stat icon="calendar" tone="amber" label="Appointments" value={r.booked} sub={`${r.interested} interested · ${r.won} won`} to={`${OPS}/reporting`} />
        <Stat icon="dollar" tone="green" label="Monthly value won" value={money(r.monthly_value_won)} sub="From contracts marked won" to={`${OPS}/reporting`} />
        <Stat icon="chart" tone="blue" label="Research spend" value={'$' + Number(h.spend.usd || 0).toFixed(2)} sub={`${h.spend.provider_calls} metered provider calls`} to={`${OPS}/reporting`} />
      </div>
      <section className="aq-card" style={{ marginBottom: 14 }}>
        <div className="aq-card__head"><h2>Operating funnel (all clients, real records only)</h2><a href={`${OPS}/reporting`}>Reporting →</a></div>
        <Funnel steps={h.funnel} />
        {p.email_ready === 0 && p.total > 0 && <div className="aq-note aq-note--warn" style={{ marginTop: 12, marginBottom: 0 }}>
          <b>No business is email-ready yet.</b> {p.total} researched businesses have no verified published email. They can be assigned, but nobody can be emailed until a real contact is recorded. <a href={`${OPS}/research`}>Open Research →</a></div>}
      </section>
      <div className="aq-grid aq-split">
        <section className="aq-card">
          <div className="aq-card__head"><h2>Recent activity</h2><a href={`${OPS}/activity`}>View all →</a></div>
          <Feed items={[
            ...h.recent_imports.map(b => ({ icon: 'upload', tone: 'green', title: `Import ${b.status === 'committed' ? 'committed' : b.status.replace(/_/g, ' ')}: ${b.committed} added`, sub: `${b.name} · ${b.rows} rows · ${b.duplicates} duplicates`, at: b.at })),
            ...h.recent_assignments.map(a => ({ icon: 'share', tone: 'violet', title: `${a.name} → ${a.client}`, sub: 'Assigned from the master pool (nothing sent)', at: a.at })),
          ].sort((a, b) => String(b.at).localeCompare(String(a.at))).slice(0, 8)} empty="No imports or assignments yet." />
        </section>
        <section className="aq-card">
          <div className="aq-card__head"><h2>Cleaning clients</h2><a href={`${OPS}/clients`}>Directory →</a></div>
          {h.clients.length ? h.clients.map(c => {
            const max = Math.max(1, ...h.clients.map(x => x.assigned))
            return <div className="aq-row" key={c.organization_id}><div className="aq-row__main"><b>{c.name}</b>{c.is_test && <> <Dot tone="amber">TEST</Dot></>}
              <span>{c.assigned} assigned · {c.contacts_verified} verified contacts · {c.booked} appointments</span>
              <div className="hb-bar" style={{ marginTop: 6 }}><i style={{ width: `${(c.assigned / max) * 100}%` }} /></div></div>
              <Dot tone={c.paused ? 'amber' : 'green'}>{c.paused ? 'Paused' : 'Running'}</Dot></div>
          }) : <Empty icon="store" title="No cleaning clients yet" action={<a className="aq-btn aq-btn--primary" href={`${OPS}/clients?add=1`}>Add Cleaning Company</a>} />}
        </section>
      </div>
      <section className="aq-card" style={{ marginTop: 14 }}>
        <h2>Quick actions</h2>
        <div className="aq-actions" style={{ marginTop: 0 }}>
          <a className="aq-btn" href={`${OPS}/import`}><Icon name="upload" size={16} />Import Businesses</a>
          <a className="aq-btn" href={`${OPS}/research`}><Icon name="flask" size={16} />Research Contacts</a>
          <a className="aq-btn" href={`${OPS}/assignments`}><Icon name="share" size={16} />Assign Prospects</a>
          <a className="aq-btn" href={`${OPS}/clients?add=1`}><Icon name="store" size={16} />Add Cleaning Company</a>
          <a className="aq-btn" href={`${OPS}/clients?convert=1`}><Icon name="users" size={16} />Convert Sales Prospects</a>
          <a className="aq-btn" href={`${OPS}/client-dashboard`}><Icon name="dashboard" size={16} />Open Client Workspace</a>
        </div>
        <p className="hb-muted" style={{ marginBottom: 0 }}>Importing, assigning or adding a company never sends an email and never starts a pilot. Researched prospects are email-only.</p>
      </section>
    </div>
  )
}

// ── shared prospect detail drawer (pool + research) ──────────────────────
function ProspectDrawer({ x, onClose, onChanged }) {
  const [f, setF] = useState({ email: '', source_url: '', contact_name: '', contact_title: '', verified: false, note: '' })
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [msg, setMsg] = useState(null)
  const [rel, setRel] = useState(false)
  const [reason, setReason] = useState('')
  const save = async () => {
    setBusy(true); setFail(null); setMsg(null)
    try { const r = await api.post(`/god/acquisition/master/${x.id}/contact`, f); setMsg(`Saved. Now: ${r.readiness.queue_label}${r.pushed_to_client ? ' · copied to the client' : ''}. Nothing was sent.`); onChanged() }
    catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const release = async () => {
    setBusy(true); setFail(null)
    try { await api.post(`/god/acquisition/master/${x.id}/release`, { reason }); setRel(false); setMsg('Released back to the master pool.'); onChanged() }
    catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const ev = x.evidence || {}
  return (
    <Drawer title={x.name} onClose={onClose}>
      <Dot tone={QUEUE_TONE[x.queue]}>{x.queue_label}</Dot>
      {!x.email_ready && <ul className="hb-muted" style={{ margin: '8px 0 0 18px', padding: 0 }}>{(x.not_ready_reasons || []).map(r => <li key={r}>{r}</li>)}</ul>}
      <div className="hb-section-title">Company</div>
      <dl className="aq-dl">
        <dt>Type</dt><dd>{SEG[x.segment] || x.industry || '—'}</dd>
        <dt>Location</dt><dd>{[x.address, x.city, x.state, x.zip_code].filter(Boolean).join(', ') || '—'} <span className="hb-muted">({x.geo})</span></dd>
        <dt>Website</dt><dd>{x.website ? <a href={x.website} target="_blank" rel="noreferrer">{x.website}</a> : '—'}</dd>
        <dt>Business phone</dt><dd>{x.phone || '—'} <span className="hb-muted">(not an email channel)</span></dd>
      </dl>
      <div className="hb-section-title">Source & evidence</div>
      <dl className="aq-dl">
        <dt>Source</dt><dd>{x.source}{x.source_url ? <> · <a href={x.source_url} target="_blank" rel="noreferrer">source page</a></> : ' · no link'}</dd>
        <dt>Researched</dt><dd>{x.researched_at ? when(x.researched_at) : '—'}</dd>
        <dt>Evidence</dt><dd>{ev.evidence || '—'}</dd>
        <dt>Confidence</dt><dd>{ev.confidence || '—'}</dd>
      </dl>
      <div className="hb-section-title">Contact</div>
      <dl className="aq-dl">
        <dt>Email</dt><dd>{x.email ? <>{x.email} <Dot tone={x.email_status === 'verified' ? 'green' : 'amber'}>{EMAIL_LABEL[x.email_status]}</Dot></> : <span className="hb-muted">None published yet</span>}</dd>
        <dt>Contact</dt><dd>{x.contact_name ? `${x.contact_name}${x.contact_title ? ', ' + x.contact_title : ''}` : '—'}</dd>
      </dl>
      {(x.research_log || []).length > 0 && <><div className="hb-section-title">Research log</div>
        <Feed items={x.research_log.map(l => ({ icon: 'flask', tone: l.verified ? 'green' : 'blue', title: `${l.email || l.contact_name} ${l.verified ? '(verified)' : '(unverified)'}`, sub: `${l.by} · ${l.source_url}`, at: l.at }))} /></>}
      <div className="hb-section-title">Record a published contact</div>
      <p className="hb-muted">Only what the business actually publishes, with the page you found it on. Never a guessed address.</p>
      <div className="aq-field"><input placeholder="Published email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} /></div>
      <div className="aq-field"><input placeholder="Page where it's published (https://…)" value={f.source_url} onChange={e => setF({ ...f, source_url: e.target.value })} /></div>
      <div className="aq-grid aq-two" style={{ gap: 8 }}>
        <input placeholder="Contact name (if published)" value={f.contact_name} onChange={e => setF({ ...f, contact_name: e.target.value })} />
        <input placeholder="Title (if published)" value={f.contact_title} onChange={e => setF({ ...f, contact_title: e.target.value })} /></div>
      <label className="aq-checks" style={{ marginTop: 10 }}><label><input type="checkbox" checked={f.verified} onChange={e => setF({ ...f, verified: e.target.checked })} />I checked this email on that page</label></label>
      <div className="aq-actions"><button className="aq-btn aq-btn--primary" disabled={busy || !f.source_url || !(f.email || f.contact_name)} onClick={save}>Save contact research</button></div>
      <div className="hb-section-title">Assignment</div>
      {x.assigned_to ? <>
        <dl className="aq-dl"><dt>Client</dt><dd><b>{x.assigned_to}</b></dd><dt>Assigned</dt><dd>{x.assigned_by || '—'} · {when(x.assigned_at)}</dd>
          <dt>Client stage</dt><dd>{x.client_outcome ? `${x.client_outcome.stage_label}${x.client_outcome.outreach_started ? ' · contacted' : ' · not contacted'}${x.client_outcome.appointment ? ' · appointment' : ''}${x.client_outcome.won ? ' · won' : ''}` : '—'}</dd></dl>
        {x.client_outcome && !x.client_outcome.outreach_started && (rel
          ? <Confirm text="Release this business back to the master pool?" confirmLabel="Release" danger busy={busy} onConfirm={release} onCancel={() => setRel(false)}>
              <input placeholder="Why (required)" value={reason} onChange={e => setReason(e.target.value)} style={{ width: '100%', marginTop: 8 }} /></Confirm>
          : <button className="aq-btn aq-btn--sm" onClick={() => setRel(true)}>Release to pool</button>)}
      </> : <p className="hb-muted">Available. <a href={`${OPS}/assignments`}>Assign it →</a></p>}
      {fail && <div className="aq-err">{fail}</div>}
      {msg && <div className="aq-ok">{msg}</div>}
    </Drawer>
  )
}

function ProspectCards({ items, onOpen }) {
  return (
    <div className="hb-mcards">{items.map(x => (
      <article className="hb-mcard" key={x.id}>
        <div className="hb-mcard__top"><b>{x.name}</b>
          {x.assigned_to ? <span className="aq-pill aq-pill--appointment_booked">Assigned</span> : <span className="aq-pill aq-pill--ok">Unassigned</span>}</div>
        <div className="hb-muted">{SEG[x.segment] || x.industry || 'Category not set'} · {[x.city, x.state].filter(Boolean).join(', ') || 'Location not set'}</div>
        {x.assigned_to && <div className="hb-muted">Client: <b>{x.assigned_to}</b></div>}
        <div className="hb-mcard__dots"><Dot tone={QUEUE_TONE[x.queue]}>{x.queue_label}</Dot>
          <Dot tone={x.email_ready ? 'green' : 'rose'}>{x.email_ready ? 'Email ready' : 'Not email ready'}</Dot></div>
        <div className="hb-mcard__foot">
          {x.source_url ? <a href={x.source_url} target="_blank" rel="noreferrer">Source page ↗</a> : <span className="hb-muted">No source link</span>}
          <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={() => onOpen(x)}>Details</button></div>
      </article>))}</div>
  )
}

function ProspectTable({ items, onOpen, select, chosen, toggle }) {
  return (<>
    <ProspectCards items={items} onOpen={onOpen} />
    <div className="hb-table-wrap hb-desk"><table className="aq-table"><thead><tr>
      {select && <th />}<th>Business</th><th>Category</th><th>City / State</th><th>Source</th><th>Researched</th><th>Email</th><th>Readiness</th><th>Assignment</th></tr></thead>
      <tbody>{items.map(x => (
        <tr key={x.id} className="hb-click" onClick={() => onOpen(x)}>
          {select && <td onClick={e => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${x.name}`} checked={chosen.includes(x.id)} onChange={() => toggle(x.id)} /></td>}
          <td><b>{x.name}</b>{x.website && <div className="hb-muted">{x.website.replace(/^https?:\/\/(www\.)?/, '').slice(0, 34)}</div>}</td>
          <td>{SEG[x.segment] || x.industry || '—'}</td>
          <td>{[x.city, x.state].filter(Boolean).join(', ') || '—'}</td>
          <td><span className="hb-muted">{x.source}</span>{x.source_url && <> · <a href={x.source_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}>link</a></>}</td>
          <td className="hb-muted">{x.researched_at ? new Date(x.researched_at).toLocaleDateString() : '—'}</td>
          <td><Dot tone={x.email_status === 'verified' ? 'green' : x.email_status === 'unverified' ? 'blue' : 'rose'}>{EMAIL_LABEL[x.email_status]}</Dot></td>
          <td><Dot tone={QUEUE_TONE[x.queue]}>{x.email_ready ? 'Email ready' : 'Not email ready'}</Dot></td>
          <td>{x.assigned_to ? <span className="aq-pill aq-pill--appointment_booked">{x.assigned_to}</span> : <span className="aq-pill aq-pill--ok">Available</span>}</td>
        </tr>))}</tbody></table></div>
  </>)
}

// ── 3. MASTER PROSPECT POOL ──────────────────────────────────────────────
export function MasterPoolPage() {
  const god = useGodOnly()
  const init = new URLSearchParams(window.location.search).get('status') || 'all'
  const [status, setStatus] = useState(['available', 'assigned', 'all'].includes(init) ? init : 'all')
  const [queue, setQueue] = useState('')
  const [q, setQ] = useState('')
  const [seg, setSeg] = useState('')
  const [open, setOpen] = useState(null)
  const path = god ? `/god/acquisition/master/prospects?status=${status}&limit=500${queue ? `&queue=${queue}` : ''}${q ? `&search=${encodeURIComponent(q)}` : ''}` : null
  const [data, err, reload] = useLoad(path)
  if (!god) return <OwnerOnly />
  const items = (data?.items || []).filter(x => !seg || x.segment === seg)
  const current = open && items.find(x => x.id === open.id)
  return (
    <div className="aq-page">
      <PageHeader eyebrow="EvoSys Master Acquisition" title="Master Prospect Pool"
        sub="EvoSys-owned commercial businesses that might buy cleaning. Each goes to one client only, when you assign it."
        actions={<><a className="aq-btn" href={`${OPS}/import`}><Icon name="upload" size={16} />Import</a>
          <a className="aq-btn aq-btn--primary" href={`${OPS}/assignments`}><Icon name="share" size={16} />Assign</a></>} />
      <Toolbar>
        <div className="aq-tabs" style={{ marginBottom: 0 }}>{[['all', 'All'], ['available', 'Available'], ['assigned', 'Assigned']].map(([k, l]) => (
          <button key={k} className={status === k ? 'on' : ''} onClick={() => setStatus(k)}>{l}</button>))}</div>
        <select aria-label="Research status" value={queue} onChange={e => setQueue(e.target.value)}>
          <option value="">Any research status</option><option value="needs_research">Needs research</option><option value="needs_email">Needs email</option>
          <option value="verification_pending">Email found, unverified</option><option value="ready_for_review">Verified, ready to assign</option>
          <option value="verified_contact">Verified contact (assigned)</option><option value="not_eligible">Excluded / not eligible</option></select>
        <select aria-label="Business type" value={seg} onChange={e => setSeg(e.target.value)}><option value="">All types</option>
          {Object.entries(SEG).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <input type="search" placeholder="Search business, city or website" value={q} onChange={e => setQ(e.target.value)} />
        <span className="aq-pill">{data ? `${items.length} of ${data.total}` : '…'}</span>
      </Toolbar>
      {err && <div className="aq-err">{errText(err)}</div>}
      {!data ? <Loading /> : items.length ? <ProspectTable items={items} onOpen={setOpen} />
        : <Empty icon="database" title="No businesses match" text="Import a researched file to fill the pool." action={<a className="aq-btn aq-btn--primary" href={`${OPS}/import`}>Open Import Center</a>} />}
      {current && <ProspectDrawer x={current} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  )
}

// ── 4. RESEARCH & QUALIFICATION ─────────────────────────────────────────
export function ResearchPage() {
  const god = useGodOnly()
  const [hub] = useLoad(god ? '/god/acquisition/hub' : null)
  const [queue, setQueue] = useState('needs_email')
  const [assignedFirst, setAssignedFirst] = useState(true)
  const [data, err, reload] = useLoad(god ? `/god/acquisition/master/prospects?status=all&limit=500&queue=${queue}` : null)
  const [open, setOpen] = useState(null)
  if (!god) return <OwnerOnly />
  const items = [...(data?.items || [])].sort((a, b) => assignedFirst ? (b.assigned_to ? 1 : 0) - (a.assigned_to ? 1 : 0) : 0)
  const current = open && items.find(x => x.id === open.id)
  return (
    <div className="aq-page">
      <PageHeader eyebrow="EvoSys Master Acquisition" title="Research & Qualification"
        sub="Turn discovered businesses into documented prospects. For this email-only workflow: no verified, usable email means NOT email ready — a phone number doesn't count." />
      <div className="hb-stats">{(hub?.queues || []).map(qq => (
        <button key={qq.key} className={`hb-stat hb-stat--${queue === qq.key ? 'violet' : 'blue'}`} style={{ cursor: 'pointer', textAlign: 'left', font: 'inherit' }} onClick={() => setQueue(qq.key)}>
          <span className={`hb-stat__icon hb-tone--${QUEUE_TONE[qq.key] === 'gray' ? 'violet' : QUEUE_TONE[qq.key]}`}><Icon name="flask" size={18} /></span>
          <span className="hb-stat__txt"><span className="hb-stat__label">{qq.label}</span><b>{qq.count}</b></span></button>))}</div>
      <Toolbar><label className="aq-checks"><label><input type="checkbox" checked={assignedFirst} onChange={e => setAssignedFirst(e.target.checked)} />Already-assigned businesses first</label></label>
        <span className="hb-muted">Research cost so far: ${Number(hub?.spend?.usd || 0).toFixed(2)} (metered provider calls only; manual research is free).</span></Toolbar>
      {err && <div className="aq-err">{errText(err)}</div>}
      {!data ? <Loading /> : items.length ? <ProspectTable items={items} onOpen={setOpen} />
        : <Empty icon="check" title="Nothing in this queue" text="Pick another queue above." />}
      {current && <ProspectDrawer x={current} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  )
}

// ── 5. ASSIGNMENTS & DISTRIBUTION ───────────────────────────────────────
export function AssignmentsPage() {
  const god = useGodOnly()
  const [pool, pErr, reloadPool] = useLoad(god ? '/god/acquisition/master/prospects?status=available&limit=500' : null)
  const [dir] = useLoad(god ? '/god/acquisition/directory' : null)
  const [chosen, setChosen] = useState([])
  const [client, setClient] = useState('')
  const [segment, setSegment] = useState('')
  const [plan, setPlan] = useState(null)
  const [result, setResult] = useState(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const { reload } = useOps()
  if (!god) return <OwnerOnly />
  const items = pool?.items || []
  const names = Object.fromEntries(items.map(x => [x.id, x.name]))
  const step = result ? 4 : plan ? 3 : client && chosen.length ? 2 : chosen.length ? 1 : 0
  const toggle = id => { setChosen(v => v.includes(id) ? v.filter(x => x !== id) : v.length >= 20 ? v : [...v, id]); setPlan(null); setResult(null) }
  const body = { destination_org_id: client, prospect_ids: chosen, segment: segment || null }
  const run = async fn => { setBusy(true); setFail(null); try { await fn() } catch (e) { setFail(errText(e)) } finally { setBusy(false) } }
  const preview = () => run(async () => setPlan(await api.post('/god/acquisition/master/assign/preview', body)))
  const assign = () => run(async () => { const r = await api.post('/god/acquisition/master/assign', body); setResult(r); setPlan(null); setConfirm(false); setChosen([]); reloadPool(); reload() })
  const fits = (plan?.decisions || []).filter(d => d.eligible), held = (plan?.decisions || []).filter(d => !d.eligible)
  const cname = (dir?.items || []).find(c => c.organization_id === client)?.name
  return (
    <div className="aq-page">
      <PageHeader eyebrow="EvoSys Master Acquisition" title="Assignments & Distribution"
        sub="Choose businesses, choose ONE cleaning client, check the fit, then confirm. Assigning sends nothing and starts no pilot." />
      <Stepper steps={['Select prospects', 'Select client', 'Review fit', 'Confirm', 'Results']} current={step} />
      {fail && <div className="aq-err">{fail}</div>}
      {result && <section className="aq-card" style={{ marginBottom: 14 }}><h2>Results</h2>
        <div className="hb-stats"><Stat icon="check" tone="green" label="Assigned" value={result.assigned} sub={`To ${cname || 'client'}`} />
          <Stat icon="pause" tone="amber" label="Held back" value={result.refused} /><Stat icon="mail" tone="violet" label="Emails sent" value={result.sent} /></div>
        {(result.results || []).filter(r => r.status === 'refused').map(r => <div className="aq-row" key={r.prospect_id}><div className="aq-row__main"><b>{names[r.prospect_id] || r.prospect_id}</b><span>{(r.reasons || []).join(', ').replace(/_/g, ' ')}</span></div></div>)}
        <div className="aq-actions"><button className="aq-btn" onClick={() => setResult(null)}>Assign more</button><a className="aq-btn aq-btn--primary" href={`${OPS}/master?status=assigned`}>See assigned</a></div></section>}
      <div className="aq-grid aq-split">
        <section className="aq-card">
          <div className="aq-card__head"><h2>1 · Available businesses ({items.length})</h2><span className="aq-pill">{chosen.length} selected (max 20)</span></div>
          {pErr && <div className="aq-err">{errText(pErr)}</div>}
          {!pool ? <Loading /> : items.length ? <><div className="hb-mcards hb-mcards--pick">{items.map(x => (
            <label className="hb-mcard" key={x.id}><div className="hb-mcard__top"><b>{x.name}</b>
              <input type="checkbox" aria-label={`Select ${x.name}`} checked={chosen.includes(x.id)} onChange={() => toggle(x.id)} /></div>
              <div className="hb-muted">{SEG[x.segment] || '—'} · {[x.city, x.state].filter(Boolean).join(', ')}</div>
              <div className="hb-mcard__dots"><Dot tone={x.email_ready ? 'green' : 'rose'}>{x.email_ready ? 'Email ready' : 'Not email ready'}</Dot></div></label>))}</div>
            <div className="hb-table-wrap hb-desk" style={{ maxHeight: 430 }}><table className="aq-table"><thead><tr><th /><th>Business</th><th>Type</th><th>City</th><th>Email</th></tr></thead>
            <tbody>{items.map(x => <tr key={x.id} className="hb-click" onClick={() => toggle(x.id)}>
              <td><input type="checkbox" aria-label={`Select ${x.name}`} checked={chosen.includes(x.id)} readOnly /></td><td><b>{x.name}</b></td>
              <td>{SEG[x.segment] || '—'}</td><td>{[x.city, x.state].filter(Boolean).join(', ')}</td>
              <td><Dot tone={x.email_ready ? 'green' : 'rose'}>{x.email_ready ? 'Ready' : 'Not ready'}</Dot></td></tr>)}</tbody></table></div></>
            : <Empty icon="database" title="No available businesses" action={<a className="aq-btn" href={`${OPS}/import`}>Import</a>} />}
        </section>
        <section className="aq-card">
          <h2>2 · Cleaning client</h2>
          <div className="aq-field"><select aria-label="Cleaning client" value={client} onChange={e => { setClient(e.target.value); setPlan(null) }}>
            <option value="">Choose a company…</option>{(dir?.items || []).map(c => <option key={c.organization_id} value={c.organization_id}>{c.name}{c.territory_confirmed ? '' : ' (territory not set)'}</option>)}</select></div>
          <div className="aq-field"><label htmlFor="hb-seg">Cleaning segment</label><select id="hb-seg" value={segment} onChange={e => { setSegment(e.target.value); setPlan(null) }}>
            <option value="">Use each business's own type</option>{Object.entries(SEG).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></div>
          <button className="aq-btn aq-btn--primary" disabled={!client || !chosen.length || busy} onClick={preview}>3 · Check fit ({chosen.length})</button>
          {plan && <>
            <div className="hb-section-title">Fits ({fits.length})</div>
            {fits.length ? fits.map(d => <div className="aq-row" key={d.prospect_id}><div className="aq-row__main"><b>{names[d.prospect_id]}</b>
              <span>{(d.warnings || []).length ? 'Note: ' + d.warnings.join(', ').replace(/_/g, ' ') : 'No issues'}</span></div><Dot tone="green">Fits</Dot></div>) : <p className="hb-muted">None.</p>}
            <div className="hb-section-title">Held back ({held.length})</div>
            {held.length ? held.map(d => <div className="aq-row" key={d.prospect_id}><div className="aq-row__main"><b>{names[d.prospect_id]}</b>
              <span>{(d.refused_because || []).join(', ').replace(/_/g, ' ')}</span></div><Dot tone="amber">Held</Dot></div>) : <p className="hb-muted">None.</p>}
            {fits.length > 0 && (confirm
              ? <Confirm text={`Assign ${fits.length} business${fits.length === 1 ? '' : 'es'} to ${cname}? Nothing is sent.`} confirmLabel="4 · Assign" busy={busy} onConfirm={assign} onCancel={() => setConfirm(false)} />
              : <div className="aq-actions"><button className="aq-btn aq-btn--primary" onClick={() => setConfirm(true)}>4 · Confirm assignment</button></div>)}
          </>}
        </section>
      </div>
    </div>
  )
}

// ── 6. CLEANING CLIENT DIRECTORY + ONBOARDING ───────────────────────────
const STATUS_LABEL = { workspace_created: 'Workspace created', onboarding_incomplete: 'Workspace created · onboarding incomplete',
                       ready_for_assignment: 'Ready for prospect assignment', outreach_ready: 'Outreach ready', pilot_active: 'Pilot running' }
const STATUS_TONE = { onboarding_incomplete: 'amber', ready_for_assignment: 'violet', outreach_ready: 'green', pilot_active: 'green', workspace_created: 'gray' }
const INVITE_TONE = { not_sent: 'gray', sent: 'blue', accepted: 'violet', active: 'green', expired: 'amber' }
const split = s => String(s || '').split(/[,\n]/).map(x => x.trim()).filter(Boolean)

function ResultRows({ results }) {
  return (results || []).map((r, i) => (
    <div className="aq-row" key={r.organization_id || i}><div className="aq-row__main"><b>{r.name}</b>
      <span>{r.status === 'created' ? `Workspace created · paused · no pilot clock${r.invitation ? '' : ' · no client login yet'}` : r.status === 'existing' ? 'Already existed — nothing duplicated' : r.status === 'duplicate' ? r.message : `Failed: ${r.error}`}
        {r.invitation?.status === 'invitation_pending' && <> · invitation link issued (not accepted yet)</>}{r.invitation?.status === 'failed' && <> · invitation failed: {r.invitation.error}</>}</span>
      {r.invitation?.setup_url && <div className="hb-muted" style={{ marginTop: 4 }}>One-time setup link (send it to {r.invitation.email}; shown once): <input readOnly value={new URL(r.invitation.setup_url, window.location.origin).href} onFocus={e => e.target.select()} style={{ width: '100%', marginTop: 4 }} /></div>}</div>
      <Dot tone={r.status === 'failed' ? 'rose' : r.status === 'duplicate' ? 'amber' : 'green'}>{r.status}</Dot></div>))
}

function AddCompanyWizard({ opts, prefill, onDone, onCancel }) {
  const [step, setStep] = useState(0)
  const [f, setF] = useState({ name: '', contact_name: '', contact_email: '', contact_phone: '', website: '', cities: '', states: '', zips: '', exclusions: '',
    services: [], custom_services: '', offer_type: 'free_pilot', offer_terms: '', invite: false, operator_email: '', ...(prefill || {}) })
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [out, setOut] = useState(null)
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const toggle = k => setF({ ...f, services: f.services.includes(k) ? f.services.filter(x => x !== k) : [...f.services, k] })
  const create = async () => {
    setBusy(true); setFail(null)
    try {
      const r = await api.post('/god/acquisition/clients', { ...f, cities: split(f.cities), states: split(f.states), zips: split(f.zips), exclusions: split(f.exclusions),
        custom_services: split(f.custom_services), invite_email: f.invite ? f.contact_email : null, operator_email: f.operator_email || null })
      setOut(r); onDone && onDone(r)
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const STEPS = ['Business', 'Services', 'Territory', 'Offer', 'Access', 'Create']
  if (out) return <section className="aq-card"><h2>{out.status === 'duplicate' ? 'Already exists' : 'Done'}</h2><ResultRows results={[out]} />
    <div className="aq-actions"><button className="aq-btn" onClick={onCancel}>Close</button></div></section>
  return (
    <section className="aq-card">
      <div className="aq-card__head"><h2>Add Cleaning Company{f.sales_opportunity_id ? ' (from EvoSys Sales)' : ''}</h2><button className="aq-btn aq-btn--sm" onClick={onCancel}>Cancel</button></div>
      <Stepper steps={STEPS} current={step} />
      {step === 0 && <div className="aq-grid aq-two">
        <div className="aq-field"><label htmlFor="w-name">Cleaning company name *</label><input id="w-name" value={f.name} onChange={set('name')} autoFocus /></div>
        <div className="aq-field"><label htmlFor="w-contact">Primary contact</label><input id="w-contact" value={f.contact_name} onChange={set('contact_name')} /></div>
        <div className="aq-field"><label htmlFor="w-email">Contact email</label><input id="w-email" value={f.contact_email} onChange={set('contact_email')} /></div>
        <div className="aq-field"><label htmlFor="w-phone">Contact phone</label><input id="w-phone" value={f.contact_phone} onChange={set('contact_phone')} /></div>
        <div className="aq-field"><label htmlFor="w-web">Website (optional)</label><input id="w-web" value={f.website} onChange={set('website')} /></div>
      </div>}
      {step === 1 && <><div className="aq-checks">{(opts?.services || []).map(s => <label key={s.key}><input type="checkbox" checked={f.services.includes(s.key)} onChange={() => toggle(s.key)} />{s.label}</label>)}</div>
        <div className="aq-field" style={{ marginTop: 12 }}><label htmlFor="w-custom">Other services (comma separated)</label><input id="w-custom" value={f.custom_services} onChange={set('custom_services')} /></div>
        <p className="hb-muted">Skip it if you don't know yet — you can finish later.</p></>}
      {step === 2 && <><div className="aq-grid aq-two">
        <div className="aq-field"><label htmlFor="w-cities">Service cities</label><input id="w-cities" placeholder="e.g. Irving, Plano" value={f.cities} onChange={set('cities')} /></div>
        <div className="aq-field"><label htmlFor="w-states">State(s)</label><input id="w-states" placeholder="e.g. TX" value={f.states} onChange={set('states')} /></div>
        <div className="aq-field"><label htmlFor="w-zips">ZIP codes</label><input id="w-zips" value={f.zips} onChange={set('zips')} /></div>
        <div className="aq-field"><label htmlFor="w-excl">Never contact (existing customers, domains)</label><input id="w-excl" value={f.exclusions} onChange={set('exclusions')} /></div></div>
        <p className="hb-muted">Left blank, the territory stays <b>not confirmed</b>: the company is created, but no prospects can be assigned to it until you set it.</p></>}
      {step === 3 && <><div className="aq-field"><label htmlFor="w-offer">Offer</label><select id="w-offer" value={f.offer_type} onChange={set('offer_type')}>
        {(opts?.offers || []).map(o => <option key={o.key} value={o.key}>{o.label}</option>)}</select></div>
        <div className="aq-field"><label htmlFor="w-terms">Terms agreed (for the record)</label><textarea id="w-terms" value={f.offer_terms} onChange={set('offer_terms')} /></div>
        <p className="hb-muted">Recorded only. No billing or subscription is created.</p></>}
      {step === 4 && <><label className="aq-checks"><label><input type="checkbox" checked={f.invite} disabled={!f.contact_email} onChange={e => setF({ ...f, invite: e.target.checked })} />Create the client's admin login for {f.contact_email || '(add an email in step 1)'}</label></label>
        <p className="hb-muted">You get a one-time setup link to send them. No password is created or shown, and nothing is emailed automatically.</p>
        <div className="aq-field"><label htmlFor="w-op">EvoSys operator email (optional)</label><input id="w-op" placeholder="an existing EvoSys login" value={f.operator_email} onChange={set('operator_email')} /></div></>}
      {step === 5 && <dl className="aq-dl">
        <dt>Company</dt><dd><b>{f.name || '—'}</b></dd><dt>Contact</dt><dd>{[f.contact_name, f.contact_email, f.contact_phone].filter(Boolean).join(' · ') || 'Not yet'}</dd>
        <dt>Services</dt><dd>{[...f.services.map(k => (opts?.services || []).find(s => s.key === k)?.label), ...split(f.custom_services)].filter(Boolean).join(', ') || 'Not yet'}</dd>
        <dt>Territory</dt><dd>{[f.cities, f.states, f.zips].filter(Boolean).join(' · ') || 'Not confirmed'}</dd>
        <dt>Offer</dt><dd>{(opts?.offers || []).find(o => o.key === f.offer_type)?.label}</dd>
        <dt>Access</dt><dd>{f.invite ? `Admin login for ${f.contact_email}` : 'No client login yet'}{f.operator_email ? ` · operator ${f.operator_email}` : ''}</dd>
        <dt>Starts</dt><dd>Paused · no pilot clock · nothing sent</dd></dl>}
      {fail && <div className="aq-err">{fail}</div>}
      <div className="aq-actions">
        {step > 0 && <button className="aq-btn" onClick={() => setStep(step - 1)}>Back</button>}
        {step < 5 && <button className="aq-btn" disabled={f.name.trim().length < 2} onClick={() => setStep(5)}>Skip to review</button>}
        {step < 5 && <button className="aq-btn aq-btn--primary" disabled={step === 0 && f.name.trim().length < 2} onClick={() => setStep(step + 1)}>Next</button>}
        {step === 5 && <button className="aq-btn aq-btn--primary" disabled={busy || f.name.trim().length < 2} onClick={create}>{busy ? 'Creating…' : 'Create Cleaning Company Workspace'}</button>}
      </div>
    </section>
  )
}

function BulkConvert({ opts, onDone, onCancel }) {
  const [cands, err] = useLoad('/god/acquisition/sales-candidates')
  const [picked, setPicked] = useState([])
  const [edits, setEdits] = useState({})
  const [def, setDef] = useState({ services: ['office'], offer_type: 'free_pilot', invite: false })
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [out, setOut] = useState(null)
  const [t0] = useState(Date.now())
  const items = (cands?.items || []).filter(c => c.status !== 'lost')
  const togglePick = id => setPicked(v => v.includes(id) ? v.filter(x => x !== id) : [...v, id])
  const run = async () => {
    setBusy(true); setFail(null)
    try {
      const r = await api.post('/god/acquisition/clients/batch', {
        defaults: { services: def.services, offer_type: def.offer_type },
        items: picked.map(id => { const c = items.find(x => x.id === id); const e = edits[id] || {}
          return { sales_opportunity_id: id, name: e.name || c.company, contact_name: c.contact, contact_email: c.email, contact_phone: c.phone,
                   website: c.website, cities: split(e.cities), states: split(e.states), invite_email: def.invite ? c.email : null } }) })
      setOut({ ...r, seconds: Math.round((Date.now() - t0) / 1000) }); onDone && onDone(r)
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  if (out) return <section className="aq-card"><h2>Created {out.created} · already existed {out.existing} · duplicates {out.duplicates} · failed {out.failed}</h2>
    <p className="hb-muted">Elapsed on this screen: {Math.floor(out.seconds / 60)}m {out.seconds % 60}s. Emails sent: {out.sent}. Retrying failed rows is safe.</p>
    <ResultRows results={out.results} /><div className="aq-actions"><button className="aq-btn" onClick={onCancel}>Close</button></div></section>
  return (
    <section className="aq-card">
      <div className="aq-card__head"><h2>Convert Sales prospects → client workspaces</h2><button className="aq-btn aq-btn--sm" onClick={onCancel}>Cancel</button></div>
      <p className="hb-muted">Cleaning companies from EvoSys Sales. Choose the ones that agreed; each gets its own private, paused workspace. Their Sales record is kept and linked.</p>
      {err && <div className="aq-err">{errText(err)}</div>}
      {!cands ? <Loading /> : <div className="hb-table-wrap" style={{ maxHeight: 380 }}><table className="aq-table"><thead><tr><th /><th>Company</th><th>Contact</th><th>Source</th><th>Status</th><th>City (optional)</th></tr></thead>
        <tbody>{items.map(c => <tr key={c.id}>
          <td><input type="checkbox" aria-label={`Select ${c.company}`} disabled={c.status === 'workspace_created'} checked={picked.includes(c.id)} onChange={() => togglePick(c.id)} /></td>
          <td><b>{c.company}</b></td><td className="hb-muted">{[c.contact, c.email].filter(Boolean).join(' · ') || '—'}</td>
          <td className="hb-muted">{c.source || '—'}</td>
          <td><Dot tone={c.status === 'workspace_created' ? 'green' : c.status === 'interested' ? 'blue' : 'gray'}>{c.status === 'workspace_created' ? `Workspace: ${c.workspace}` : c.status === 'interested' ? 'Interested' : 'Prospect'}</Dot></td>
          <td>{picked.includes(c.id) && <input aria-label={`City for ${c.company}`} placeholder="Not confirmed" value={edits[c.id]?.cities || ''} onChange={e => setEdits({ ...edits, [c.id]: { ...(edits[c.id] || {}), cities: e.target.value } })} style={{ width: 140 }} />}</td>
        </tr>)}</tbody></table></div>}
      <div className="hb-section-title">Defaults for all selected</div>
      <div className="aq-checks">{(opts?.services || []).map(s => <label key={s.key}><input type="checkbox" checked={def.services.includes(s.key)} onChange={() => setDef({ ...def, services: def.services.includes(s.key) ? def.services.filter(x => x !== s.key) : [...def.services, s.key] })} />{s.label}</label>)}</div>
      <div className="aq-actions"><select aria-label="Offer" value={def.offer_type} onChange={e => setDef({ ...def, offer_type: e.target.value })}>{(opts?.offers || []).map(o => <option key={o.key} value={o.key}>{o.label}</option>)}</select>
        <label className="aq-checks"><label><input type="checkbox" checked={def.invite} onChange={e => setDef({ ...def, invite: e.target.checked })} />Create admin logins (setup links, nothing emailed)</label></label></div>
      {fail && <div className="aq-err">{fail}</div>}
      <div className="aq-actions"><button className="aq-btn aq-btn--primary" disabled={!picked.length || busy} onClick={run}>{busy ? 'Creating…' : `Create ${picked.length} Client Workspace${picked.length === 1 ? '' : 's'}`}</button></div>
    </section>
  )
}

export function ClientDirectory() {
  const god = useGodOnly()
  const { onSelectOrg, reloadClients } = useOps()
  const nav = useNavigate()
  const qs = new URLSearchParams(window.location.search)
  const [dir, err, reload] = useLoad(god ? '/god/acquisition/directory' : null)
  const [opts] = useLoad(god ? '/god/acquisition/onboarding-options' : null)
  const [mode, setMode] = useState(qs.get('add') ? 'add' : qs.get('convert') ? 'convert' : '')
  const [inv, setInv] = useState(null)
  const [invOut, setInvOut] = useState(null)
  const [toast, say] = useToast()
  if (!god) return <OwnerOnly />
  const done = () => { reload(); reloadClients && reloadClients() }
  const openOps = c => { onSelectOrg(c.organization_id); nav(`${OPS}/client-dashboard`) }
  const openPortal = async c => {
    try { const { enterCustomer } = await import('../god/enterCustomer'); await enterCustomer(c.organization_id, c.name); window.location.href = '/program/acquisition' }
    catch (e) { say(errText(e)) }
  }
  const sendInvite = async () => {
    try { const r = await api.post(`/god/acquisition/clients/${inv.organization_id}/invite`, { email: inv.email, name: inv.name }); setInvOut(r); reload() }
    catch (e) { setInvOut({ status: 'failed', error: errText(e) }) }
  }
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="EvoSys Master Acquisition" title="Cleaning Client Directory"
        sub="Every cleaning company EvoSys serves. Create first, finish details later. Every new company starts paused."
        actions={<><button className="aq-btn aq-btn--primary" onClick={() => setMode('add')}><Icon name="store" size={16} />+ Add Cleaning Company</button>
          <button className="aq-btn" onClick={() => setMode('convert')}><Icon name="users" size={16} />Convert Sales Prospects</button></>} />
      {mode === 'add' && <div style={{ marginBottom: 14 }}><AddCompanyWizard opts={opts} onDone={done} onCancel={() => setMode('')} /></div>}
      {mode === 'convert' && <div style={{ marginBottom: 14 }}><BulkConvert opts={opts} onDone={done} onCancel={() => setMode('')} /></div>}
      {inv && <section className="aq-card" style={{ marginBottom: 14 }}><h2>Client login for {inv.org}</h2>
        <div className="aq-grid aq-two"><input placeholder="Email" value={inv.email} onChange={e => setInv({ ...inv, email: e.target.value })} /><input placeholder="Name" value={inv.name} onChange={e => setInv({ ...inv, name: e.target.value })} /></div>
        <div className="aq-actions"><button className="aq-btn aq-btn--primary" disabled={!inv.email} onClick={sendInvite}>Create / resend setup link</button><button className="aq-btn" onClick={() => { setInv(null); setInvOut(null) }}>Close</button></div>
        {invOut && (invOut.status === 'failed' ? <div className="aq-err">{invOut.error}</div> : <ResultRows results={[{ name: inv.org, status: 'created', invitation: invOut }]} />)}</section>}
      {err && <div className="aq-err">{errText(err)}</div>}
      {!dir ? <Loading /> : dir.items.length ? <div className="hb-cards">{dir.items.map(c => (
        <div className="hb-ccard" key={c.organization_id}>
          <div className="hb-ccard__top"><span className="hb-strip__mark" style={{ width: 36, height: 36 }}>{c.name.split(/\s+/).map(w => w[0]).slice(0, 2).join('')}</span>
            <div style={{ minWidth: 0 }}><b>{c.name}</b>{c.is_test && <> <Dot tone="amber">TEST — not counted in results</Dot></>}<div className="hb-muted">{c.contact || 'No contact yet'} · added {c.created_at ? new Date(c.created_at).toLocaleDateString() : '—'}</div></div></div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><Dot tone={STATUS_TONE[c.status]}>{STATUS_LABEL[c.status]}</Dot>
            <Dot tone={c.paused ? 'amber' : 'green'}>{c.paused ? 'Paused' : 'Running'}</Dot></div>
          <div className="hb-muted">Client login: <Dot tone={INVITE_TONE[c.invitation.status]}>{c.invitation.label}</Dot>
            {c.invitation.status !== 'active' && <span> — the workspace exists, but the client can't sign in until they accept an invitation.</span>}</div>
          <div><div className="hb-muted" style={{ marginBottom: 4 }}>Onboarding {c.onboarding.done}/{c.onboarding.total}</div><div className="hb-bar"><i style={{ width: `${(c.onboarding.done / c.onboarding.total) * 100}%` }} /></div></div>
          <div className="hb-muted">Territory: {c.territory_confirmed ? c.territory.join(', ') : <b style={{ color: '#fbbf24' }}>not confirmed</b>} · {c.offer_label}{c.pilot_started_at ? ' · pilot active' : ' · pilot not started'}</div>
          <dl><div><dt>Assigned</dt><dd>{c.assigned}</dd></div><div><dt>Verified contacts</dt><dd>{c.contacts_verified}</dd></div><div><dt>Contacted</dt><dd>{c.contacted}</dd></div>
            <div><dt>Appointments</dt><dd>{c.booked}</dd></div><div><dt>Won</dt><dd>{c.won}</dd></div><div><dt>Monthly won</dt><dd>{money(c.monthly_value_won)}</dd></div></dl>
          <div className="aq-actions" style={{ marginTop: 0 }}>
            <button className="aq-btn aq-btn--sm aq-btn--primary" onClick={() => openOps(c)}>Open Client Operations</button>
            <button className="aq-btn aq-btn--sm" onClick={() => { onSelectOrg(c.organization_id); nav(`${OPS}/onboarding`) }}>Edit Business Setup</button>
            <button className="aq-btn aq-btn--sm" onClick={() => setInv({ organization_id: c.organization_id, org: c.name, email: c.contact_email || '', name: c.contact || '' })}>{c.invitation.status === 'not_sent' ? 'Invite client' : c.invitation.status === 'active' ? 'Add client login' : 'Resend invitation'}</button>
            <button className="aq-btn aq-btn--sm" onClick={() => openPortal(c)}>Open Client Portal</button>
            <button className="aq-btn aq-btn--sm" onClick={() => { onSelectOrg(c.organization_id); nav(`${OPS}/settings`) }}>Pause · Operators</button>
          </div>
        </div>))}</div> : <Empty icon="store" title="No cleaning clients yet" action={<button className="aq-btn aq-btn--primary" onClick={() => setMode('add')}>Add Cleaning Company</button>} />}
    </div>
  )
}

// ── 10. ALL-CLIENTS REPORTING ───────────────────────────────────────────
export function Reporting() {
  const god = useGodOnly()
  const [h, err] = useLoad(god ? '/god/acquisition/hub' : null)
  if (!god) return <OwnerOnly />
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!h) return <div className="aq-page"><Loading /></div>
  return (
    <div className="aq-page">
      <PageHeader eyebrow="EvoSys Master Acquisition" title="All-Clients Reporting" sub="Platform totals first, then each client. Practice records never count. Revenue is only what clients marked won." />
      <section className="aq-card" style={{ marginBottom: 14 }}><h2>Platform funnel</h2><Funnel steps={h.funnel} /></section>
      <div className="hb-stats">
        <Stat icon="reply" tone="blue" label="Replies" value={h.results.replied} /><Stat icon="users" tone="violet" label="Interested" value={h.results.interested} />
        <Stat icon="calendar" tone="amber" label="Booked" value={h.results.booked} /><Stat icon="check" tone="green" label="Won" value={h.results.won} />
        <Stat icon="dollar" tone="green" label="Monthly value won" value={money(h.results.monthly_value_won)} /><Stat icon="chart" tone="blue" label="Real spend" value={'$' + Number(h.spend.usd).toFixed(2)} sub={h.spend.source} />
      </div>
      <section className="aq-card"><h2>By client</h2>
        <div className="hb-table-wrap"><table className="aq-table"><thead><tr><th>Client</th><th>Assigned</th><th>Verified contacts</th><th>Contacted</th><th>Replied</th><th>Interested</th><th>Booked</th><th>Won</th><th>Monthly won</th><th>Status</th></tr></thead>
          <tbody>{h.clients.map(c => <tr key={c.organization_id}><td><b>{c.name}</b></td><td>{c.assigned}</td><td>{c.contacts_verified}</td><td>{c.contacted}</td><td>{c.replied}</td><td>{c.interested}</td><td>{c.booked}</td><td>{c.won}</td><td>{money(c.monthly_value_won)}</td>
            <td><Dot tone={c.paused ? 'amber' : 'green'}>{c.paused ? 'Paused' : 'Running'}</Dot></td></tr>)}</tbody></table></div></section>
    </div>
  )
}

// ── Activity history (owner) ────────────────────────────────────────────
export function Activity() {
  const god = useGodOnly()
  const { ov } = useOps()
  const [h] = useLoad(god ? '/god/acquisition/hub' : null)
  if (!god) return <OwnerOnly />
  const items = [
    ...((h?.recent_imports) || []).map(b => ({ icon: 'upload', tone: 'green', title: `Master import: ${b.committed} added, ${b.duplicates} duplicates, ${b.invalid} invalid`, sub: `${b.name} · ${b.source}`, at: b.at })),
    ...((h?.recent_assignments) || []).map(a => ({ icon: 'share', title: `${a.name} assigned to ${a.client}`, sub: 'No message sent', at: a.at })),
    ...(ov.organization_id ? ov.recent_events || [] : []).map(e => ({ icon: 'clock', tone: 'blue', title: `${ov.organization_name}: ${e.kind.replace(/_/g, ' ')}${e.to ? ' → ' + e.to.replace(/_/g, ' ') : ''}`, sub: e.note, at: e.at })),
  ].sort((a, b) => String(b.at).localeCompare(String(a.at)))
  return (
    <div className="aq-page"><PageHeader eyebrow="Administration" title="Audit History" sub="Master imports and assignments, plus the selected client's recorded events." />
      <section className="aq-card">{h ? <Feed items={items} /> : <Loading />}</section></div>
  )
}

// ── Client Outreach / Responses (truthful, client-scoped) ───────────────
export function OutreachPage() {
  const { list, ov } = useOps()
  const items = list?.items || []
  const ready = items.filter(x => !x.is_practice && x.email && ['contact_verified', 'outreach_ready'].includes(x.stage))
  const blocked = items.filter(x => !x.is_practice && !['contacted', 'replied', 'interested', 'appointment_booked', 'appointment_held', 'quote_issued', 'won', 'lost', 'disqualified', 'no_show'].includes(x.stage) && !ready.includes(x))
  const sent = items.filter(x => ['contacted', 'replied', 'interested', 'appointment_booked', 'appointment_held', 'quote_issued', 'won'].includes(x.stage))
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Client Operations" title="Email Outreach" sub={`One email at a time from support@evosyspro.live (Microsoft 365), each checked against onboarding, location, never-contact and the daily limit. Email only — no texts or calls.`} />
      <div className="hb-stats"><Stat icon="check" tone="green" label="Ready to email" value={ready.length} /><Stat icon="pause" tone="amber" label="Not ready yet" value={blocked.length} sub="Need a verified email or Contact verified stage" />
        <Stat icon="mail" tone="blue" label="Contacted so far" value={sent.length} /><Stat icon="clock" tone="violet" label="Sent last 24h" value={`${ov.sending?.sent_last_24h ?? 0} / ${ov.sending?.daily_cap ?? '—'}`} /></div>
      {!ov.profile.readiness?.launch_ready && <div className="aq-note aq-note--warn">Real businesses can't be emailed until onboarding is complete: {(ov.profile.readiness?.missing || []).join(', ')}.</div>}
      <section className="aq-card"><h2>Ready to email</h2>
        {ready.length ? ready.map(x => <div className="aq-row" key={x.id}><div className="aq-row__main"><b>{x.business_name}</b><span>{x.email} · {x.stage_label}</span></div><a className="aq-btn aq-btn--sm aq-btn--primary" href={`${OPS}/prospects`}>Open Email tab</a></div>)
          : <Empty icon="mail" title="Nobody is ready to email" text="A prospect needs a verified published email (record it in Research) and the Contact verified stage." />}</section>
    </div>
  )
}

export function ResponsesPage() {
  const { list, ov } = useOps()
  const items = (list?.items || []).filter(x => ['replied', 'interested', 'appointment_booked'].includes(x.stage))
  const replies = (ov.recent_events || []).filter(e => e.to === 'replied' || e.kind === 'reply')
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Client Operations" title="Responses" sub="Replies to support@ are matched every 5 minutes. STOP goes on the never-contact list; complaints and legal threats are flagged for a person." />
      <section className="aq-card" style={{ marginBottom: 14 }}><h2>Prospects who replied</h2>
        {items.length ? items.map(x => <div className="aq-row" key={x.id}><div className="aq-row__main"><b>{x.business_name}</b><span>{x.stage_label}{x.interest_note ? ' · ' + x.interest_note : ''}</span></div><a className="aq-btn aq-btn--sm" href={`${OPS}/prospects`}>Open</a></div>)
          : <Empty icon="reply" title="No replies yet" text="Replies appear here once outreach has gone out and someone answers." />}</section>
      <section className="aq-card"><h2>Recent reply events</h2><Feed items={replies.map(e => ({ icon: 'reply', tone: 'blue', title: e.note || 'Reply received', at: e.at }))} empty="No reply events recorded." /></section>
    </div>
  )
}
