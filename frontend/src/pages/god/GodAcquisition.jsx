/**
 * GOD MODE › Acquisition pilot (Issue #452) - the platform owner's operations
 * for the managed client-acquisition pilot: setup, emergency pause, the full
 * pipeline with sources and evidence, the practice cohort, workers, spend,
 * client feedback and the audit trail.
 *
 * Every number is the server's. Workers that are not built say so; nothing
 * on this page can send a message or call a data provider.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import '../acquisition/acquisition.css'

const errText = e => (typeof e?.detail === 'string' ? e.detail : e?.message) || 'Something went wrong.'
const fmt = iso => (iso ? new Date(iso).toLocaleString() : '—')

function useLoad(path) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const load = async () => {
    if (!path) return
    try { setData(await api.get(path)); setError(null) } catch (e) { setError(e) }
  }
  useEffect(() => { setData(null); load() }, [path])
  return [data, error, load]
}

function Enable({ onDone }) {
  const [orgId, setOrgId] = useState('')
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const go = async () => {
    setBusy(true); setFail(null)
    try { await api.post(`/god/acquisition/${orgId.trim()}/enable`, {}); onDone() } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  return (
    <section className="aq-card">
      <h2>Set up the pilot client</h2>
      <p className="aq-lede">Paste the organization ID of the client’s workspace (Organizations › open the org). The pilot is limited to one cleaning company and starts paused. Nothing is sent.</p>
      <div className="aq-field"><label htmlFor="aq-org">Organization ID</label>
        <input id="aq-org" value={orgId} onChange={e => setOrgId(e.target.value)} placeholder="e.g. 3f2c…" /></div>
      {fail && <div className="aq-err">{fail}</div>}
      <button className="aq-btn aq-btn--primary" disabled={busy || !orgId.trim()} onClick={go}>{busy ? 'Setting up…' : 'Enable acquisition pilot'}</button>
    </section>
  )
}

function AddProspect({ orgId, segments, onDone }) {
  const blank = { business_name: '', segment: '', website: '', source_url: '', city: '', phone: '',
                  contact_first_name: '', contact_last_name: '', contact_title: '', email: '', is_practice: true }
  const [f, setF] = useState(blank)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const set = k => e => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  const go = async () => {
    setBusy(true); setFail(null)
    try { await api.post(`/god/acquisition/${orgId}/prospects`, { ...f, segment: f.segment || null }); setF(blank); onDone() }
    catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const input = (k, label) => (
    <div className="aq-field"><label>{label}</label><input value={f[k]} onChange={set(k)} /></div>
  )
  return (
    <section className="aq-card">
      <h2>Add a business by hand</h2>
      <div className="aq-grid aq-two">
        <div>
          {input('business_name', 'Business name *')}
          <div className="aq-field"><label>Kind of business</label>
            <select value={f.segment} onChange={set('segment')}><option value="">—</option>
              {segments.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}</select></div>
          {input('website', 'Website')}
          {input('source_url', 'Where it was found (URL)')}
          {input('city', 'City')}
        </div>
        <div>
          {input('contact_first_name', 'Contact first name')}
          {input('contact_last_name', 'Contact last name')}
          {input('contact_title', 'Contact title')}
          {input('email', 'Contact email')}
          {input('phone', 'Phone')}
        </div>
      </div>
      <label className="aq-checks"><label><input type="checkbox" checked={f.is_practice} onChange={set('is_practice')} />Practice cohort (never shown to the client, never contacted)</label></label>
      {fail && <div className="aq-err">{fail}</div>}
      <div className="aq-actions"><button className="aq-btn aq-btn--primary" disabled={busy || !f.business_name.trim()} onClick={go}>{busy ? 'Adding…' : 'Add business'}</button></div>
    </section>
  )
}

function ProspectDetail({ orgId, x, stages, kinds, onDone }) {
  const [stage, setStage] = useState('')
  const [note, setNote] = useState('')
  const [ev, setEv] = useState({ kind: kinds[0], value: '', source_url: '', confidence: '' })
  const [fail, setFail] = useState(null)
  const move = async () => {
    setFail(null)
    try { await api.post(`/god/acquisition/${orgId}/prospects/${x.id}/stage`, { stage, note, reason: note }); setStage(''); setNote(''); onDone() }
    catch (e) { setFail(errText(e)) }
  }
  const addEv = async () => {
    setFail(null)
    try { await api.post(`/god/acquisition/${orgId}/prospects/${x.id}/evidence`, { ...ev, confidence: ev.confidence === '' ? null : ev.confidence }); setEv({ ...ev, value: '', source_url: '', confidence: '' }); onDone() }
    catch (e) { setFail(errText(e)) }
  }
  return (
    <div style={{ background: '#f8fafc', borderRadius: 12, padding: 14, margin: '6px 0 12px' }}>
      <dl className="aq-dl">
        <dt>Contact</dt><dd>{[x.contact_name, x.contact_title, x.email, x.phone].filter(Boolean).join(' · ') || '—'}</dd>
        <dt>Source</dt><dd>{x.source}{x.source_url ? <> · <a href={x.source_url} target="_blank" rel="noreferrer">link</a></> : ''}</dd>
        <dt>Score</dt><dd>{x.score ?? 'not scored'}{x.score_reasons?.length ? ` — ${x.score_reasons.join('; ')}` : ''}</dd>
        {x.disqualified_reason && <><dt>Not a fit</dt><dd>{x.disqualified_reason}</dd></>}
        {x.interest_note && <><dt>Interest</dt><dd>{x.interest_note}</dd></>}
      </dl>
      <h3 style={{ fontSize: 14, margin: '14px 0 6px' }}>Evidence</h3>
      {x.evidence.length ? x.evidence.map(e => (
        <div key={e.id} className="aq-row"><div className="aq-row__main"><b>{e.kind}: {e.value || '—'}</b>
          <span>{e.source_url ? <a href={e.source_url} target="_blank" rel="noreferrer">{e.source_url}</a> : 'no source'} · confidence {e.confidence ?? '—'} · {e.verified ? 'verified' : 'unverified'}</span></div></div>
      )) : <div className="aq-empty">No evidence recorded.</div>}
      <div className="aq-actions">
        <select value={ev.kind} onChange={e => setEv({ ...ev, kind: e.target.value })}>{kinds.map(k => <option key={k}>{k}</option>)}</select>
        <input placeholder="Fact" value={ev.value} onChange={e => setEv({ ...ev, value: e.target.value })} />
        <input placeholder="Source URL" value={ev.source_url} onChange={e => setEv({ ...ev, source_url: e.target.value })} />
        <input placeholder="Confidence 0–1" style={{ width: 120 }} value={ev.confidence} onChange={e => setEv({ ...ev, confidence: e.target.value })} />
        <button className="aq-btn aq-btn--sm" onClick={addEv}>Add evidence</button>
      </div>
      <div className="aq-actions">
        <select value={stage} onChange={e => setStage(e.target.value)}><option value="">Move to stage…</option>
          {stages.filter(s => !['contacted', 'replied'].includes(s.key)).map(s => <option key={s.key} value={s.key}>{s.label}</option>)}</select>
        <input placeholder="Note / reason (required for Interested and Not a fit)" style={{ flex: 1, minWidth: 220 }} value={note} onChange={e => setNote(e.target.value)} />
        <button className="aq-btn aq-btn--sm aq-btn--primary" disabled={!stage} onClick={move}>Move</button>
      </div>
      {fail && <div className="aq-err">{fail}</div>}
    </div>
  )
}

function Client({ orgId }) {
  const [ov, err, reloadOv] = useLoad(`/god/acquisition/${orgId}/overview`)
  const [filter, setFilter] = useState({ stage: '', practice: '' })
  const q = new URLSearchParams(Object.entries(filter).filter(([, v]) => v !== '')).toString()
  const [list, listErr, reloadList] = useLoad(`/god/acquisition/${orgId}/prospects${q ? '?' + q : ''}`)
  const [fb] = useLoad(`/god/acquisition/${orgId}/feedback`)
  const [open, setOpen] = useState(null)
  const [reason, setReason] = useState('')
  const [excl, setExcl] = useState(null)
  const [fail, setFail] = useState(null)
  useEffect(() => { if (ov && excl === null) setExcl((ov.profile.exclusions || []).join(', ')) }, [ov])
  const reload = () => { reloadOv(); reloadList() }
  if (err) return <div className="aq-err">{errText(err)}</div>
  if (!ov) return <div className="aq-empty">Loading…</div>
  const p = ov.profile
  const f = ov.funnel
  const pause = async () => {
    setFail(null)
    try { await api.post(`/god/acquisition/${orgId}/pause`, { reason }); setReason(''); reload() } catch (e) { setFail(errText(e)) }
  }
  const resume = async () => {
    setFail(null)
    try { await api.post(`/god/acquisition/${orgId}/resume`, {}); reload() } catch (e) { setFail(errText(e)) }
  }
  const saveExcl = async () => {
    setFail(null)
    try { await api.put(`/god/acquisition/${orgId}/profile`, { exclusions: excl }); reload() } catch (e) { setFail(errText(e)) }
  }
  return (
    <div className="aq-grid">
      <section className="aq-card" style={{ borderColor: p.paused ? '#f5c26b' : '#9ee0b9' }}>
        <h2>{ov.organization_name} — {p.paused ? 'PAUSED' : 'Unpaused'}</h2>
        <p className="aq-lede" style={{ marginBottom: 10 }}>
          {p.paused ? `Paused ${fmt(p.paused_at)}: ${p.paused_reason || ''}.` : 'Not paused. No worker exists yet, so nothing runs.'}
          {' '}Pilot clock: {p.pilot_started_at ? `started ${fmt(p.pilot_started_at)}` : 'not started (starts only when live operations are verified).'}
        </p>
        <div className="aq-actions">
          {p.paused ? <button className="aq-btn" onClick={resume}>Unpause</button> : (
            <>
              <input placeholder="Why pause? (required)" value={reason} onChange={e => setReason(e.target.value)} style={{ minWidth: 260 }} />
              <button className="aq-btn aq-btn--primary" style={{ background: '#d23c5a', borderColor: '#d23c5a' }} onClick={pause}>Emergency pause</button>
            </>
          )}
        </div>
        {fail && <div className="aq-err">{fail}</div>}
      </section>

      <div className="aq-grid aq-kpis">
        {[['Real prospects', f.total - f.practice], ['Practice', f.practice], ['Interested', f.interested], ['Booked', f.booked],
          ['Held', f.held], ['Quoted', f.quoted], ['Won', f.won], ['Won $/mo', '$' + f.won_monthly_value.toLocaleString()]].map(([l, v]) => (
          <div className="aq-kpi" key={l}><b>{v}</b><span>{l}</span></div>
        ))}
      </div>

      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Workers</h2>
          {ov.workers.map(w => (
            <div className="aq-row" key={w.key}><div className="aq-row__main"><b>{w.label}</b><span>Planned in {w.phase}</span></div>
              <span className="aq-pill">Not built</span></div>
          ))}
        </section>
        <section className="aq-card"><h2>Sending & spend</h2>
          <dl className="aq-dl">
            <dt>Outbound</dt><dd>{ov.sending.note}</dd>
            <dt>Workspace texts</dt><dd>{ov.sending.workspace_texts_on ? 'On' : 'Off'}</dd>
            <dt>Spend</dt><dd>${ov.spend.usd.toFixed(2)} — {ov.spend.note}</dd>
          </dl>
          <h2 style={{ marginTop: 16 }}>Never contact (client exclusions)</h2>
          <div className="aq-field"><textarea value={excl ?? ''} onChange={e => setExcl(e.target.value)} placeholder="existing-client.com, name@company.com" />
            <small>Domains cover their sub-domains. Existing clients and anyone who asked not to be contacted go here.</small></div>
          <button className="aq-btn aq-btn--sm" onClick={saveExcl}>Save exclusions</button>
        </section>
      </div>

      <section className="aq-card"><h2>Pipeline</h2>
        <div className="aq-actions" style={{ marginTop: 0, marginBottom: 10 }}>
          <select value={filter.stage} onChange={e => setFilter({ ...filter, stage: e.target.value })}>
            <option value="">All stages</option>{ov.stages.map(s => <option key={s.key} value={s.key}>{s.label} ({f.by_stage[s.key] || 0})</option>)}</select>
          <select value={filter.practice} onChange={e => setFilter({ ...filter, practice: e.target.value })}>
            <option value="">Real + practice</option><option value="false">Real only</option><option value="true">Practice only</option></select>
        </div>
        {listErr && <div className="aq-err">{errText(listErr)}</div>}
        {!list ? <div className="aq-empty">Loading…</div> : list.items.length ? list.items.map(x => (
          <div key={x.id}>
            <div className="aq-row">
              <div className="aq-row__main"><b>{x.business_name} {x.is_practice && <span className="aq-pill">practice</span>}</b>
                <span>{[x.segment_label, x.city, x.domain].filter(Boolean).join(' · ')} · {x.evidence.length} evidence</span></div>
              <span className={`aq-pill aq-pill--${x.stage}`}>{x.stage_label}</span>
              <button className="aq-btn aq-btn--sm" onClick={() => setOpen(open === x.id ? null : x.id)}>{open === x.id ? 'Close' : 'Open'}</button>
            </div>
            {open === x.id && <ProspectDetail orgId={orgId} x={x} stages={ov.stages} kinds={ov.evidence_kinds} onDone={reload} />}
          </div>
        )) : <div className="aq-empty">No businesses in the pipeline yet. Discovery (P2) is not built; add a practice cohort by hand below.</div>}
      </section>

      <AddProspect orgId={orgId} segments={ov.segments} onDone={reload} />

      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Client feedback</h2>
          {!fb ? <div className="aq-empty">Loading…</div> : fb.items.length ? fb.items.map(i => (
            <div className="aq-row" key={i.id}><div className="aq-row__main"><b>{i.body}</b><span>{i.by} · {i.rating} · {fmt(i.at)}</span></div></div>
          )) : <div className="aq-empty">No feedback yet.</div>}
        </section>
        <section className="aq-card"><h2>Recent activity</h2>
          {ov.recent_events.length ? ov.recent_events.map((e, i) => (
            <div className="aq-row" key={i}><div className="aq-row__main"><b>{e.kind}{e.to ? ` → ${e.to}` : ''}</b><span>{fmt(e.at)}{e.note ? ` · ${e.note}` : ''}</span></div></div>
          )) : <div className="aq-empty">No activity yet.</div>}
        </section>
      </div>
    </div>
  )
}

export default function GodAcquisition() {
  const [clients, err, reload] = useLoad('/god/acquisition/clients')
  const [sel, setSel] = useState(null)
  useEffect(() => { if (clients?.items?.length && !sel) setSel(clients.items[0].organization_id) }, [clients])
  return (
    <div className="aq aq-embed">
      <div className="aq-page" style={{ padding: '20px 24px 48px', maxWidth: 'none' }}>
        <h1 className="aq-h1">Acquisition pilot</h1>
        <p className="aq-lede">Managed customer acquisition for one client (Issue #452). Development stage: no discovery, outreach or booking worker runs, and nothing can be sent.</p>
        {err && <div className="aq-err">{errText(err)}</div>}
        {!clients ? <div className="aq-empty">Loading…</div>
          : clients.items.length === 0 ? <Enable onDone={reload} />
          : <Client orgId={sel || clients.items[0].organization_id} />}
      </div>
    </div>
  )
}
