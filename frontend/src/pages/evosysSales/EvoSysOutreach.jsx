/**
 * EvoSys Sales - Outreach: pick a campaign (one per industry + market group),
 * edit its emails, offer, link and schedule, let AI draft copy, preview it for
 * a real lead, approve THIS version, turn it on. The team's sender, From,
 * Reply-To and mailing address are shared by every campaign.
 */
import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { API, Box, Icon, PageHead, errText, useLoad, when } from './EvoSysSales'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const TZS = [['America/Chicago', 'Central'], ['America/New_York', 'Eastern'], ['America/Denver', 'Mountain'], ['America/Los_Angeles', 'Pacific']]

export function Outreach() {
  const loc = useLocation()
  const navigate = useNavigate()
  const list = useLoad(`${API}/campaigns`)
  const team = useLoad(`${API}/outreach`)
  const meta = useLoad(`${API}/groups`)
  const qp = new URLSearchParams(loc.search).get('campaign')
  const camps = list.data?.campaigns || []
  const sel = qp || camps[0]?.id
  const choose = id => navigate(`?campaign=${id}`, { replace: true })
  const [adding, setAdding] = useState(false)
  const reloadAll = () => { list.reload(); team.reload() }
  return (
    <Box error={list.error || team.error} data={list.data && team.data}>
      <PageHead title="Outreach" sub="Automatic follow-up emails, one campaign per industry and market. Nothing sends until you approve a campaign and turn it on." />
      <ReplyTracking rt={team.data?.reply_tracking} />

      <section className="evs-card">
        <div className="evs-card-h"><h3>Campaigns</h3>
          {list.data?.can_manage && <button className="evs-btn evs-btn-sm" onClick={() => setAdding(a => !a)}><Icon name="plus" size={14} />New campaign</button>}</div>
        {adding && meta.data && <NewCampaign meta={meta.data} onMade={c => { setAdding(false); list.reload(); choose(c.id) }} />}
        <div className="evs-camplist">
          {camps.map(c => (
            <button key={c.id} className={'evs-camp' + (c.id === sel ? ' on' : '')} onClick={() => choose(c.id)}>
              <span className="evs-camp-n">{c.name}</span>
              <span className="evs-camp-g">{c.group}</span>
              <span className="evs-camp-s">
                <span className={'evs-pill ' + (c.can_send ? 'evs-good' : c.sending_on ? 'evs-warn' : 'evs-muted')}>{c.can_send ? 'On' : c.sending_on ? 'On - blocked' : 'Off'}</span>
                <span className={'evs-pill ' + (c.approved ? 'evs-blue' : 'evs-warn')}>{c.approved ? `Approved v${c.approved_version}` : 'Needs approval'}</span>
                {c.audience.waiting ? <span className="evs-pill evs-gold">{c.audience.waiting} waiting</span> : null}
              </span>
              <span className="evs-camp-a">{c.audience.matching_leads} leads · {c.audience.in_followup} in follow-up</span>
            </button>
          ))}
        </div>
      </section>

      {sel && <CampaignEditor key={sel} id={sel} meta={meta.data} onChange={reloadAll} />}

      <TeamSettings team={team.data} onSaved={reloadAll} />
    </Box>
  )
}

function ReplyTracking({ rt }) {
  if (!rt) return null
  return (
    <div className={'evs-banner ' + (rt.automatic ? 'evs-banner-ok' : '')}>
      <b>{rt.automatic ? 'Replies are read automatically.' : 'Replies are not being read.'}</b> {rt.text}
      {rt.last_auto_reply_at && <> Last reply picked up {when(rt.last_auto_reply_at)}.</>}
    </div>
  )
}

function NewCampaign({ meta, onMade }) {
  const [ind, setInd] = useState('')
  const [mk, setMk] = useState('')
  const [msg, setMsg] = useState(null)
  const make = async () => {
    setMsg(null)
    try { onMade(await api.post(`${API}/campaigns`, { industry_key: ind || null, market_key: mk || null })) }
    catch (e) { setMsg(errText(e)) }
  }
  return (
    <div className="evs-newcamp">
      <label className="evs-field"><span>Industry</span><select className="evs-input" value={ind} onChange={e => setInd(e.target.value)}><option value="">Any industry</option>{meta.industries.map(i => <option key={i.key} value={i.key}>{i.label}</option>)}</select></label>
      <label className="evs-field"><span>Market</span><select className="evs-input" value={mk} onChange={e => setMk(e.target.value)}><option value="">Any market</option>{meta.markets.map(m => <option key={m.key} value={m.key}>{m.label}{m.state ? ` (${m.state})` : ''}</option>)}</select></label>
      <button className="evs-btn" disabled={!ind && !mk} onClick={make}>Create (starts off)</button>
      {msg && <div className="evs-error">{msg}</div>}
    </div>
  )
}

function CampaignEditor({ id, meta, onChange }) {
  const { data, error, reload } = useLoad(`${API}/campaigns/${id}`)
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [preview, setPreview] = useState(null)
  const [testTo, setTestTo] = useState('')
  const [ai, setAi] = useState(null)
  useEffect(() => { if (data) { setForm(JSON.parse(JSON.stringify(data))); setAi(data.ai_draft) } }, [data])
  const pick = f => ({ name: f.name, steps: f.steps, offer: f.offer, booking_url: f.booking_url, daily_limit: f.daily_limit,
                       send_days: f.send_days, send_start_hour: f.send_start_hour, send_end_hour: f.send_end_hour, timezone: f.timezone })
  const dirty = form && data && JSON.stringify(pick(form)) !== JSON.stringify(pick(data))
  const run = async (fn, ok) => {
    setBusy(true); setMsg(null)
    try { await fn(); if (ok) setMsg({ ok: true, text: ok }); await reload(); onChange() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const setStep = (i, k, v) => setForm(f => ({ ...f, steps: f.steps.map((s, j) => j === i ? { ...s, [k]: k === 'day' ? Number(v) : v } : s) }))
  const can = meta?.can_manage
  return (
    <Box error={error} data={form}>
      <section className={'evs-card evs-switchcard ' + (form?.can_send ? 'on' : '')}>
        <div>
          <b>{form?.name} is {form?.can_send ? 'ON' : form?.sending_on ? 'ON but blocked' : 'OFF'}</b>
          <small>{form?.group} · {form?.hours} · up to {form?.daily_limit} a day</small>
          <small>{form?.approved ? `Version ${form.approved_version} approved ${when(form.approved_at)}` : (form?.approval_note || `Version ${form?.version} not approved`)}{form?.draft_origin && form.draft_origin !== 'manual' ? ` · copy ${form.draft_origin === 'ai' ? 'drafted by AI' : 'drafted by AI, edited by a person'}` : ''}</small>
        </div>
        {can && (form?.sending_on
          ? <button className="evs-btn evs-danger" disabled={busy} onClick={() => run(() => api.post(`${API}/campaigns/${id}/switch`, { on: false }), 'Turned off. Waiting emails stay queued; replies still come in.')}>Turn off</button>
          : <button className="evs-btn" disabled={busy || dirty || !form?.approved}
                    onClick={() => run(() => api.post(`${API}/campaigns/${id}/switch`, { on: true }), 'Turned on.')}>Turn on</button>)}
      </section>
      {form?.audience?.waiting > 0 && !form.can_send && <div className="evs-banner"><b>Paused with emails waiting:</b> {form.audience.waiting} follow-up{form.audience.waiting === 1 ? '' : 's'} will go out when this campaign is approved and on. Nothing is lost or sent twice.</div>}
      {form?.problems?.length > 0 && <div className="evs-banner"><b>Before you can approve:</b><ul>{form.problems.map(p => <li key={p}>{p}</li>)}</ul></div>}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}

      <section className="evs-card">
        <h3>Offer, link and schedule</h3>
        <div className="evs-grid">
          <Field label="Offer (goes where {offer} is) - yours to write; AI never changes it" wide>
            <textarea className="evs-input" rows={2} disabled={!can} value={form?.offer || ''} onChange={e => setForm({ ...form, offer: e.target.value })} />
          </Field>
          <Field label="Booking link ({booking_link})" wide>
            <input className="evs-input" disabled={!can} value={form?.booking_url || ''} onChange={e => setForm({ ...form, booking_url: e.target.value })} />
          </Field>
          <Field label="Sending days">
            <div className="evs-days">{DAYS.map((d, i) => (
              <button key={d} disabled={!can} className={'evs-day-b' + ((form?.send_days || []).includes(i) ? ' on' : '')}
                      onClick={() => setForm({ ...form, send_days: (form.send_days || []).includes(i) ? form.send_days.filter(x => x !== i) : [...(form.send_days || []), i].sort() })}>{d}</button>
            ))}</div>
          </Field>
          <Field label="From / to (hour)">
            <div className="evs-hours">
              <input className="evs-input" type="number" min="0" max="23" disabled={!can} value={form?.send_start_hour ?? 8} onChange={e => setForm({ ...form, send_start_hour: Number(e.target.value) })} />
              <span>to</span>
              <input className="evs-input" type="number" min="1" max="24" disabled={!can} value={form?.send_end_hour ?? 18} onChange={e => setForm({ ...form, send_end_hour: Number(e.target.value) })} />
              <select className="evs-input" disabled={!can} value={form?.timezone} onChange={e => setForm({ ...form, timezone: e.target.value })}>{TZS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
            </div>
          </Field>
          <Field label="Most emails a day">
            <input className="evs-input" type="number" min="1" max="300" disabled={!can} value={form?.daily_limit || 60} onChange={e => setForm({ ...form, daily_limit: Number(e.target.value) })} />
          </Field>
        </div>
      </section>

      <section className="evs-card">
        <div className="evs-card-h"><h3>The emails</h3>
          {can && <button className="evs-btn evs-ghost evs-btn-sm" disabled={busy} onClick={() => run(async () => setAi(await api.post(`${API}/campaigns/${id}/ai-draft${ai ? '?again=true' : ''}`, {})))}><Icon name="spark" size={14} />{ai ? 'AI: draft again' : 'AI: draft the emails'}</button>}
        </div>
        <p className="evs-hint">These fill in by themselves: {'{first_name}'}, {'{company}'}, {'{offer}'}, {'{booking_link}'}, {'{sender_name}'}. Each lead gets them in order and stops when they reply, book, or opt out. An opt-out link is added to every email.</p>
        {ai && <AiDraft ai={ai} can={can} busy={busy} onUse={() => run(() => api.post(`${API}/campaigns/${id}/ai-draft/${ai.id}/apply`, {}), 'AI draft copied in. Read it, edit it, then approve this version.')} />}
        {(form?.steps || []).map((s, i) => (
          <div key={i} className="evs-step">
            <div className="evs-step-head">
              <b>Step {i + 1}</b>
              <label>Day <input className="evs-input evs-day" type="number" min="0" max="90" disabled={!can} value={s.day} onChange={e => setStep(i, 'day', e.target.value)} /></label>
              <select className="evs-input evs-chan" disabled={!can} value={s.channel} onChange={e => setStep(i, 'channel', e.target.value)}>
                <option value="email">Email</option>
                <option value="text">Text (waits for a texting number)</option>
              </select>
              {can && form.steps.length > 1 && <button className="evs-link" onClick={() => setForm({ ...form, steps: form.steps.filter((_, j) => j !== i) })}>Remove</button>}
            </div>
            {s.channel === 'email' && <input className="evs-input" disabled={!can} placeholder="Subject" value={s.subject} onChange={e => setStep(i, 'subject', e.target.value)} />}
            <textarea className="evs-input" rows={6} disabled={!can} value={s.body} onChange={e => setStep(i, 'body', e.target.value)} />
          </div>
        ))}
        {can && (form?.steps?.length || 0) < 10 && (
          <button className="evs-btn evs-ghost" onClick={() => setForm({ ...form, steps: [...form.steps, { day: (form.steps.at(-1)?.day || 0) + 7, channel: 'email', subject: '', body: '' }] })}>+ Add a step</button>
        )}
      </section>

      {can && (
        <div className="evs-card evs-actions">
          <button className="evs-btn" disabled={busy || !dirty} onClick={() => run(() => api.put(`${API}/campaigns/${id}`, pick(form)), 'Saved as a new version. Approve it to send.')}>Save changes</button>
          <button className="evs-btn evs-btn-gold" disabled={busy || dirty || form?.approved || form?.problems?.length > 0}
                  onClick={() => run(() => api.post(`${API}/campaigns/${id}/approve`, {}), 'This version is approved. Turn it on when ready.')}>Approve version {form?.version}</button>
          <button className="evs-btn evs-ghost" disabled={busy} onClick={() => run(async () => setPreview(await api.get(`${API}/outreach/preview?campaign_id=${id}`)))}>Preview with a real lead</button>
        </div>
      )}

      {preview && (
        <section className="evs-card">
          <h3>Preview for {preview.lead?.company || '—'} {preview.lead?.source_channel ? `(came from ${preview.lead.source_channel.replace(/_/g, ' ')})` : ''}</h3>
          {!preview.lead && <p className="evs-empty">No lead with an email in this campaign's group yet.</p>}
          {(preview.steps || []).map((s, i) => (
            <div key={i} className="evs-msg out">
              <div className="evs-msg-head"><b>Step {i + 1} · day {s.day} · {s.channel}</b></div>
              {(s.warnings || []).map((w, k) => <div key={k} className="evs-banner evs-warnline"><Icon name="warn" size={16} /> {w}</div>)}
              <div className="evs-subject">{s.subject}</div>
              <div className="evs-html" dangerouslySetInnerHTML={{ __html: s.html }} />
            </div>
          ))}
        </section>
      )}

      {can && (
        <section className="evs-card">
          <h3>Send yourself a test</h3>
          <div className="evs-actions">
            <input className="evs-input" placeholder="Your email" value={testTo} onChange={e => setTestTo(e.target.value)} />
            <button className="evs-btn evs-ghost" disabled={busy || dirty || !testTo}
                    onClick={() => run(() => api.post(`${API}/outreach/test`, { to: testTo, step: 1, campaign_id: id }), 'Test sent to ' + testTo + '.')}>Send step 1 to me</button>
          </div>
          <p className="evs-hint">Goes only to the address you type, never to a lead.</p>
        </section>
      )}
    </Box>
  )
}

function AiDraft({ ai, can, busy, onUse }) {
  if (ai.status === 'failed') return <div className="evs-error">{ai.error}</div>
  const o = ai.output || {}
  return (
    <div className="evs-aibox evs-aidraft">
      <Icon name="spark" size={20} />
      <div>
        <b>AI draft · {when(ai.at)}{ai.outcome ? ` · ${ai.outcome}` : ''}</b>
        <small>Facts it was given: {ai.facts?.industry}, {ai.facts?.market}, what EvoSys does, {ai.facts?.source_seen ? 'these leads came from ads' : 'these leads did not come from ads'}. No prices - the offer goes where {'{offer}'} is.</small>
        {(o.problems || []).map((p, k) => <small key={k} className="evs-err-small">{p}</small>)}
        {(o.steps || []).map((s, k) => <details key={k}><summary>Step {k + 1} · day {s.day} · {s.subject}</summary><pre className="evs-body">{s.body}</pre></details>)}
        {can && !ai.outcome && (o.steps || []).length > 0 && <button className="evs-btn evs-btn-sm" disabled={busy} onClick={onUse}>Use this draft</button>}
      </div>
    </div>
  )
}

function TeamSettings({ team, onSaved }) {
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (team) setF({ sender_name: team.sender_name, from_email: team.from_email, reply_to_email: team.reply_to_email, mailing_address: team.mailing_address }) }, [team])
  if (!team || !f) return null
  const dirty = ['sender_name', 'from_email', 'reply_to_email', 'mailing_address'].some(k => (f[k] || '') !== (team[k] || ''))
  const save = async () => {
    setBusy(true); setMsg(null)
    try { await api.put(`${API}/outreach`, f); setMsg({ ok: true, text: 'Saved. Every campaign was turned off and needs approving again.' }); onSaved() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const can = team.can_manage
  return (
    <section className="evs-card">
      <h3>Sender for every campaign</h3>
      <div className="evs-grid">
        <Field label="Your name ({sender_name})"><input className="evs-input" disabled={!can} value={f.sender_name || ''} onChange={e => setF({ ...f, sender_name: e.target.value })} /></Field>
        <Field label="Sends from"><input className="evs-input" disabled={!can} value={f.from_email || ''} onChange={e => setF({ ...f, from_email: e.target.value })} /></Field>
        <Field label="Replies go to"><input className="evs-input" disabled={!can} value={f.reply_to_email || ''} onChange={e => setF({ ...f, reply_to_email: e.target.value })} /></Field>
        <Field label="Mailing address (required at the bottom of every sales email)" wide>
          <input className="evs-input" disabled={!can} placeholder="Street, City, State ZIP" value={f.mailing_address || ''} onChange={e => setF({ ...f, mailing_address: e.target.value })} />
        </Field>
      </div>
      {can && <div className="evs-actions"><button className="evs-btn" disabled={busy || !dirty} onClick={save}>Save sender</button><span className="evs-hint">Changing these stops every campaign until each is approved again.</span></div>}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}
    </section>
  )
}

function Field({ label, wide, children }) {
  return <label className={'evs-field' + (wide ? ' wide' : '')}><span>{label}</span>{children}</label>
}
