/* PHONE NUMBERS & VOICEMAIL - inside Organization Control Center > Operations.
   Stream XC. Reads GET /god/telephony/orgs/{id}; every write is its own
   god-only endpoint and the section reloads from the server afterwards.

   Nothing here buys, provisions or tests a number against Twilio. "Assign" is
   a record that says which number this organization calls from and answers on;
   the number itself must already exist in the organization's Twilio account
   with its Voice webhook pointed at the URL shown. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { errText } from '../GodOpsShared'
import { Pill } from './ccShared'

const CAPS = [['voice_outbound', 'Outbound calls'], ['voice_inbound', 'Inbound calls'],
  ['voicemail', 'Voicemail'], ['sms', 'SMS']]
const MODES = [['ring_then_voicemail', 'Ring people, then voicemail'],
  ['voicemail_only', 'Straight to voicemail'], ['ai_agent', 'AI agent (known callers only)']]

function fmt(v) {
  if (!v) return '—'
  const d = String(v).replace(/\D/g, '')
  const t = d.length === 11 && d.startsWith('1') ? d.slice(1) : d
  return t.length === 10 ? `(${t.slice(0, 3)}) ${t.slice(3, 6)}-${t.slice(6)}` : v
}

function blankForm(n) {
  const r = n?.inbound_route || {}
  return {
    e164: n?.e164 || '', label: n?.label || '', workspace_id: n?.workspace_id || '',
    cap_voice_outbound: n ? n.capabilities.voice_outbound : true,
    cap_voice_inbound: n ? n.capabilities.voice_inbound : true,
    cap_voicemail: n ? n.capabilities.voicemail : true,
    cap_sms: n ? n.capabilities.sms : false,
    is_active: n ? n.is_active : true,
    mode: r.mode || 'ring_then_voicemail', ring_user_ids: r.ring_user_ids || [],
    timeout_seconds: r.timeout_seconds || 20, voicemail: r.voicemail !== false,
    greeting_text: r.greeting_text || '', greeting_recording_url: r.greeting_recording_url || '',
  }
}

function NumberForm({ orgId, data, number, onDone, onCancel }) {
  const [f, setF] = useState(blankForm(number))
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k, v) => setF(p => ({ ...p, [k]: v }))
  const toggleRing = id => set('ring_user_ids', f.ring_user_ids.includes(id)
    ? f.ring_user_ids.filter(x => x !== id) : [...f.ring_user_ids, id])

  async function save(e) {
    e.preventDefault()
    setBusy(true); setErr('')
    const body = {
      label: f.label, workspace_id: f.workspace_id || '',
      cap_voice_outbound: f.cap_voice_outbound, cap_voice_inbound: f.cap_voice_inbound,
      cap_voicemail: f.cap_voicemail, cap_sms: f.cap_sms, is_active: f.is_active,
      inbound_route: {
        mode: f.mode, ring_user_ids: f.ring_user_ids, timeout_seconds: Number(f.timeout_seconds) || 20,
        voicemail: f.voicemail, greeting_text: f.greeting_text || null,
        greeting_recording_url: f.greeting_recording_url || null,
      },
    }
    try {
      if (number) await api.patch(`/god/telephony/numbers/${number.id}`, body)
      else await api.post('/god/telephony/numbers', { ...body, e164: f.e164, organization_id: orgId })
      onDone()
    } catch (e2) { setErr(errText(e2)) } finally { setBusy(false) }
  }

  return (
    <form className="occ-card" onSubmit={save} style={{ marginTop: 10 }}>
      <h3>{number ? `Edit ${fmt(number.e164)}` : 'Assign a number to this organization'}</h3>
      <p className="occ-sub">The number must already exist in the organization's Twilio account. Nothing is purchased here.</p>
      {err && <div className="go-err">{err}</div>}
      <div className="go-fields">
        {!number && (
          <div><label className="go-label">Number</label>
            <input className="go-input" value={f.e164} placeholder="(214) 555-0100" required
                   onChange={e => set('e164', e.target.value)} /></div>
        )}
        <div><label className="go-label">Label</label>
          <input className="go-input" value={f.label} placeholder="Main line" onChange={e => set('label', e.target.value)} /></div>
        {(data.workspaces || []).length > 0 && (
          <div><label className="go-label">Location (optional)</label>
            <select className="go-input" value={f.workspace_id} onChange={e => set('workspace_id', e.target.value)}>
              <option value="">Whole organization</option>
              {data.workspaces.map(w => <option key={w.id} value={w.id}>{w.name || w.id}</option>)}
            </select></div>
        )}
      </div>
      <div className="go-actions" style={{ margin: '10px 0', justifyContent: 'flex-start' }}>
        {CAPS.map(([k, l]) => (
          <label key={k} className="occ-muted" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
            <input type="checkbox" checked={f['cap_' + k]} onChange={e => set('cap_' + k, e.target.checked)} /> {l}
          </label>
        ))}
        <label className="occ-muted" style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={f.is_active} onChange={e => set('is_active', e.target.checked)} /> Active
        </label>
      </div>
      <h3 style={{ fontSize: 14 }}>Inbound calls</h3>
      <div className="go-fields">
        <div><label className="go-label">When someone calls</label>
          <select className="go-input" value={f.mode} onChange={e => set('mode', e.target.value)}>
            {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select></div>
        <div><label className="go-label">Ring for (seconds)</label>
          <input className="go-input" type="number" min={5} max={60} value={f.timeout_seconds}
                 onChange={e => set('timeout_seconds', e.target.value)} /></div>
        <div><label className="go-label">Voicemail greeting (spoken)</label>
          <input className="go-input" value={f.greeting_text} maxLength={500}
                 placeholder="Default: You have reached <organization>…" onChange={e => set('greeting_text', e.target.value)} /></div>
        <div><label className="go-label">Greeting recording URL (https, optional)</label>
          <input className="go-input" value={f.greeting_recording_url} placeholder="https://…"
                 onChange={e => set('greeting_recording_url', e.target.value)} /></div>
      </div>
      <label className="occ-muted" style={{ display: 'inline-flex', gap: 6, alignItems: 'center', margin: '8px 0' }}>
        <input type="checkbox" checked={f.voicemail} onChange={e => set('voicemail', e.target.checked)} /> Take a voicemail when nobody answers
      </label>
      {f.mode === 'ring_then_voicemail' && (
        <div>
          <label className="go-label">Ring these people (their verified callback phone)</label>
          <ul className="occ-list">
            {(data.members || []).map(m => (
              <li key={m.user_id}>
                <label style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                  <input type="checkbox" checked={f.ring_user_ids.includes(m.user_id)} onChange={() => toggleRing(m.user_id)} />
                  {m.name}
                </label>
                <span className="occ-muted">{m.callback_phone ? fmt(m.callback_phone) : 'No verified callback phone - will not ring'}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="go-actions" style={{ marginTop: 10 }}>
        <button className="go-btn" disabled={busy}>{busy ? 'Saving…' : number ? 'Save changes' : 'Assign number'}</button>
        <button type="button" className="go-btn ghost" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  )
}

function DropCard({ orgId, data, reload }) {
  const approved = data.voicemail_drop?.approved
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  async function save(approve) {
    setBusy(true); setErr('')
    try {
      await api.post(`/god/telephony/orgs/${orgId}/voicemail-drop`, { message_text: text || null, recording_url: url || null, approve })
      setText(''); setUrl(''); reload()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  async function revoke() {
    setBusy(true); setErr('')
    try { await api.post(`/god/telephony/voicemail-drops/${approved.id}/revoke`, {}); reload() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <section className="occ-card">
      <h3>Answering-machine message</h3>
      <p className="occ-sub">
        When an automated call reaches voicemail, only an APPROVED message is ever played - and never to a
        Do Not Contact, suppressed or paused contact. With no approved message the call hangs up without speaking.
      </p>
      {approved ? (
        <ul className="occ-list"><li>
          <span style={{ minWidth: 0, flex: '1 1 260px' }}>
            <strong>Approved</strong>
            <div className="occ-muted">{approved.message_text || approved.recording_url}</div>
          </span>
          <button className="go-btn ghost sm" disabled={busy} onClick={revoke}>Revoke</button>
        </li></ul>
      ) : <p className="occ-muted" style={{ margin: '0 0 8px' }}>No approved message - machines are hung up on.</p>}
      {err && <div className="go-err">{err}</div>}
      <div className="go-fields" style={{ marginTop: 8 }}>
        <div><label className="go-label">Message (spoken)</label>
          <input className="go-input" value={text} maxLength={1000} onChange={e => setText(e.target.value)}
                 placeholder="Hi, this is … please call us back at …" /></div>
        <div><label className="go-label">Or recording URL (https)</label>
          <input className="go-input" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://…" /></div>
      </div>
      <div className="go-actions" style={{ marginTop: 10 }}>
        <button className="go-btn" disabled={busy || (!text.trim() && !url.trim())} onClick={() => save(true)}>Approve message</button>
        <button className="go-btn ghost" disabled={busy || (!text.trim() && !url.trim())} onClick={() => save(false)}>Save as draft</button>
      </div>
    </section>
  )
}

export default function TelephonySection({ orgId }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState(null)       // null | 'new' | number
  const load = useCallback(() => {
    if (!orgId) return
    setErr('')
    api.get(`/god/telephony/orgs/${orgId}`).then(setData).catch(e => setErr(errText(e)))
  }, [orgId])
  useEffect(() => { load() }, [load])

  if (err) return <section className="occ-card"><h3>Phone numbers</h3><div className="go-err">{err}</div></section>
  if (!data) return <section className="occ-card"><h3>Phone numbers</h3><p className="occ-muted">Loading…</p></section>
  const out = data.resolved.outbound
  const inn = data.resolved.inbound

  return (
    <>
      <section className="occ-card">
        <div className="occ-card-head">
          <div>
            <h3>Phone numbers</h3>
            <p className="occ-sub">Which number this organization calls from and answers on. Most specific wins:
              location → organization → brand → platform.</p>
          </div>
          <div className="occ-head-actions">
            <button className="go-btn" onClick={() => setEditing('new')}>Assign number</button>
          </div>
        </div>
        <div className="occ-facts" style={{ borderTop: 0, marginTop: 0, paddingTop: 0 }}>
          <div><div className="occ-fact-k">Outbound calls use</div>
            <div className="occ-fact-v">{out.ok ? `${fmt(out.e164)} (${out.level})` : 'None - calls are refused'}</div></div>
          <div><div className="occ-fact-k">Inbound answered on</div>
            <div className="occ-fact-v">{inn.ok ? `${fmt(inn.e164)} (${inn.level})` : 'None'}</div></div>
          <div><div className="occ-fact-k">Twilio account</div>
            <div className="occ-fact-v">{data.legacy.org_account_stored ? 'Stored' : 'Missing'}</div></div>
          <div><div className="occ-fact-k">Transcription</div><div className="occ-fact-v">Not enabled</div></div>
          <div><div className="occ-fact-k">Browser calling</div><div className="occ-fact-v">Not available (bridge)</div></div>
        </div>
        {!out.ok && <div className="go-err" style={{ marginTop: 10 }}>Provider/config required: {out.reason}</div>}
        {out.ok && !data.legacy.org_account_stored && out.level !== 'brand' && out.level !== 'platform' && (
          <div className="go-err" style={{ marginTop: 10 }}>Provider/config required: no Twilio account is stored for this
            organization, so calls cannot be placed from {fmt(out.e164)} and recordings cannot be played.</div>
        )}
        <ul className="occ-list" style={{ marginTop: 10 }}>
          {data.numbers.map(n => (
            <li key={n.id}>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>{fmt(n.e164)}</strong>{n.label ? ` · ${n.label}` : ''}
                <div className="occ-muted">
                  {n.scope}{n.workspace_id ? ' (location)' : ''} · {CAPS.filter(([k]) => n.capabilities[k]).map(([, l]) => l).join(', ') || 'no capabilities'}
                  {' · '}inbound: {MODES.find(([k]) => k === n.inbound_route.mode)?.[1]}
                  {n.inbound_route.ring_user_ids.length ? `, rings ${n.inbound_route.ring_user_ids.length}` : ''}
                </div>
              </span>
              <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                <Pill tone={n.is_active ? 'good' : 'neutral'} label={n.is_active ? 'Active' : 'Inactive'} />
                <button className="go-btn ghost sm" onClick={() => setEditing(n)}>Edit</button>
              </span>
            </li>
          ))}
          {data.legacy.org_number && !data.legacy.org_number_governed_by_record && (
            <li>
              <span style={{ minWidth: 0, flex: '1 1 260px' }}>
                <strong>{fmt(data.legacy.org_number)}</strong> · stored on the organization record
                <div className="occ-muted">Used as the organization's number until a record above governs it.
                  Inbound: straight to voicemail.</div>
              </span>
              <Pill tone="neutral" label="Legacy" />
            </li>
          )}
          {data.numbers.length === 0 && !data.legacy.org_number && (
            <li><span className="occ-muted">No number is assigned to this organization.</span></li>
          )}
        </ul>
        {(data.pool_numbers || []).length > 0 && (
          <p className="occ-muted" style={{ marginTop: 8 }}>
            Shared pool numbers (brand/platform): {data.pool_numbers.map(p => fmt(p.e164)).join(', ')}
          </p>
        )}
        <p className="occ-muted" style={{ marginTop: 8 }}>
          Twilio Voice webhook for each inbound number: <code>{data.webhooks.voice_url || 'API_BASE_URL not configured'}</code> ({data.webhooks.method})
        </p>
        {editing && (
          <NumberForm orgId={orgId} data={data} number={editing === 'new' ? null : editing}
                      onCancel={() => setEditing(null)} onDone={() => { setEditing(null); load() }} />
        )}
      </section>
      <DropCard orgId={orgId} data={data} reload={load} />
    </>
  )
}
