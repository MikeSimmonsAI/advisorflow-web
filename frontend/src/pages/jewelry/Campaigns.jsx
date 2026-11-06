import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { useJewelry } from './JewelryShell'
import { CustomerPicker, Empty, ErrorNote, Field, Loading, Modal, Pill, errText, localInput, money, n, useApi, useToast, when } from './jwui'

const STATUS_TONE = { draft: '', scheduled: 'warn', paused: '', cancelled: 'err', completed: 'ok' }
const EXCL = { opted_out: 'Opted out', suppressed: 'On do-not-text list', no_sms_permission: 'No text permission',
               no_email_permission: 'No email permission', no_phone: 'No mobile number', no_email: 'No email address',
               test_record: 'Test record' }

function Editor({ id, status, onChanged, toast }) {
  const [previewLead, setPreviewLead] = useState(null)
  const { data: c, error, reload, setData } = useApi(`/jewelry-ops/campaigns/${id}` + (previewLead ? `?preview_lead_id=${previewLead}` : ''), [id, previewLead])
  const [f, setF] = useState(null)
  const [tab, setTab] = useState('sms')
  const [when_, setWhen] = useState(localInput(new Date(Date.now() + 2 * 86400000)).slice(0, 11) + '10:00')
  const [err, setErr] = useState(null)
  const [sendResult, setSendResult] = useState(null)
  useEffect(() => { if (c) { setF({ name: c.name, segments: c.segments, channel: c.channel, sms_body: c.sms_body || '', email_subject: c.email_subject || '', email_body: c.email_body || '', marketing_cost: c.marketing_cost ?? '' }); setTab(c.channel === 'email' ? 'email' : 'sms') } }, [c?.id, c?.updated_at])
  if (error) return <ErrorNote onRetry={reload}>{error}</ErrorNote>
  if (!c || !f) return <Loading />
  const locked = c.status === 'cancelled' || c.status === 'completed'
  const dirty = JSON.stringify(f) !== JSON.stringify({ name: c.name, segments: c.segments, channel: c.channel, sms_body: c.sms_body || '', email_subject: c.email_subject || '', email_body: c.email_body || '', marketing_cost: c.marketing_cost ?? '' })
  const run = async (fn, ok) => { setErr(null); try { await fn(); await reload(); onChanged(); if (ok) toast(ok); return true } catch (e) { setErr(errText(e)); return false } }
  const save = () => run(() => api.patch(`/jewelry-ops/campaigns/${id}`, { ...f, marketing_cost: f.marketing_cost === '' ? null : Number(f.marketing_cost) }), 'Draft saved.')
  const segs = status.segments || []
  const rec = c.recipients
  return (
    <div className="jw-card">
      <div className="jw-card__head"><div><div className="jw-eyebrow">Campaign</div><h3 className="jw-h3">{c.name}</h3></div>
        <div className="jw-row"><Pill tone={STATUS_TONE[c.status]}>{c.status}{c.status === 'scheduled' ? ' · on hold' : ''}</Pill>{c.approved && <Pill tone="ok">Approved</Pill>}</div></div>
      <div className="jw-form2">
        <Field label="Name"><input className="jw-input" disabled={locked} value={f.name} onChange={e => setF({ ...f, name: e.target.value })} maxLength={160} /></Field>
        <Field label="Channel"><select className="jw-input" disabled={locked} value={f.channel} onChange={e => setF({ ...f, channel: e.target.value })}>
          <option value="sms">Text message</option><option value="email">Email</option><option value="both">Text + email</option></select></Field>
      </div>
      <Field label="Customer segments">
        <div className="jw-chips">{segs.map(s => (
          <button key={s.key} type="button" disabled={locked} className={`jw-chip ${f.segments.includes(s.key) ? 'on' : ''}`}
                  onClick={() => setF({ ...f, segments: f.segments.includes(s.key) ? f.segments.filter(x => x !== s.key) : [...f.segments, s.key] })}>{s.label}</button>
        ))}</div>
      </Field>
      <div className="jw-grid jw-grid--2">
        <div>
          {f.channel !== 'email' && <Field label={`Text message · ${f.sms_body.length} characters`} hint="{first_name} and {business_name} are filled in for each customer. “Reply STOP to opt out.” is added if missing.">
            <textarea className="jw-input" rows={4} disabled={locked} value={f.sms_body} onChange={e => setF({ ...f, sms_body: e.target.value })} maxLength={640} /></Field>}
          {f.channel !== 'sms' && <>
            <Field label="Email subject"><input className="jw-input" disabled={locked} value={f.email_subject} onChange={e => setF({ ...f, email_subject: e.target.value })} maxLength={200} /></Field>
            <Field label="Email message"><textarea className="jw-input" rows={6} disabled={locked} value={f.email_body} onChange={e => setF({ ...f, email_body: e.target.value })} maxLength={8000} /></Field>
          </>}
          <Field label="Marketing cost (optional)" hint="What this promotion cost - used in reports. Never estimated."><input className="jw-input" type="number" min="0" step="0.01" disabled={locked} value={f.marketing_cost} onChange={e => setF({ ...f, marketing_cost: e.target.value })} /></Field>
          {!locked && <div className="jw-row"><button className="jw-btn jw-btn--navy" disabled={!dirty} onClick={save}>Save draft</button>{dirty && <small className="jw-sub">Unsaved changes</small>}</div>}
        </div>
        <div>
          <div className="jw-row jw-row--between"><b>Preview</b>
            <div className="jw-seg">{c.channel !== 'email' && <button className={tab === 'sms' ? 'on' : ''} onClick={() => setTab('sms')}>Text</button>}{c.channel !== 'sms' && <button className={tab === 'email' ? 'on' : ''} onClick={() => setTab('email')}>Email</button>}</div></div>
          <Field label="Preview as customer (optional)"><CustomerPicker value={previewLead} onChange={setPreviewLead} placeholder="Pick a customer to fill in {first_name}" /></Field>
          {tab === 'sms' ? (
            <div className="jw-phone"><div className="jw-phone__from">{c.preview.from_name}</div><div className="jw-bubble">{c.preview.sms || 'No text written yet.'}</div><small>Preview only - not sent</small></div>
          ) : (
            <div className="jw-email"><div className="jw-email__head">{c.preview.from_name}</div><div className="jw-email__meta"><b>Subject:</b> {c.preview.email_subject}</div><div className="jw-email__body">{c.preview.email_body}</div><div className="jw-email__foot">Unsubscribe link added on send · Preview only - not sent</div></div>
          )}
          {dirty && <p className="jw-note">Save the draft to refresh the preview and recipients.</p>}
        </div>
      </div>
      <div className="jw-grid jw-grid--2">
        <div className="jw-subcard">
          <b>Recipients</b>
          <div className="jw-kpis jw-kpis--3">
            <div className="jw-mini"><span>In segments</span><b>{n(rec.matched)}</b></div>
            <div className="jw-mini jw-mini--navy"><span>Eligible</span><b>{n(rec.eligible)}</b></div>
            <div className="jw-mini"><span>Text / email</span><b>{n(rec.eligible_sms)} / {n(rec.eligible_email)}</b></div>
          </div>
          <ul className="jw-list">{Object.entries(rec.excluded).filter(([, v]) => v).map(([k, v]) => <li key={k}><span>Excluded: {EXCL[k]}</span><b>{n(v)}</b></li>)}</ul>
          {rec.sample.length > 0 && <p className="jw-small">e.g. {rec.sample.slice(0, 6).map(s => s.name).join(', ')}{rec.eligible > 6 ? '…' : ''}</p>}
          {rec.matched > 0 && rec.eligible === 0 && <p className="jw-note">Nobody is eligible yet: customers need recorded marketing permission for this channel.</p>}
        </div>
        <div className="jw-subcard">
          <b>Before anything can send</b>
          <ul className="jw-checks">{c.readiness.checks.map(x => <li key={x.key} className={x.ok ? 'ok' : ''}><span>{x.ok ? '✓' : '✕'}</span><div><b>{x.label}</b><small>{x.detail}</small></div></li>)}</ul>
        </div>
      </div>
      <div className="jw-subcard">
        <b>Results</b> <small className="jw-sub">counted from real records</small>
        <div className="jw-kpis jw-kpis--4">
          <div className="jw-mini"><span>Delivered</span><b>{c.results.delivery_connected ? n(c.results.delivered) : 'Not connected'}</b></div>
          <div className="jw-mini"><span>Appointments</span><b>{n(c.results.appointments)}</b></div>
          <div className="jw-mini"><span>Transactions</span><b>{n(c.results.transactions)}</b></div>
          <div className="jw-mini"><span>Recorded revenue</span><b>{money(c.results.recorded_revenue)}</b></div>
        </div>
      </div>
      <ErrorNote>{err}</ErrorNote>
      {!locked && (
        <div className="jw-row jw-row--wrap jw-actions">
          <input className="jw-input jw-input--sm" type="datetime-local" value={when_} onChange={e => setWhen(e.target.value)} aria-label="Schedule for" />
          <button className="jw-btn" disabled={dirty} onClick={() => run(() => api.post(`/jewelry-ops/campaigns/${id}/schedule`, { scheduled_for: when_ }), 'Scheduled - on hold until sending is connected. Nothing will be sent.')}>Schedule</button>
          {c.status === 'scheduled' && <button className="jw-btn" onClick={() => run(() => api.post(`/jewelry-ops/campaigns/${id}/pause`, {}), 'Paused.')}>Pause</button>}
          {c.status === 'paused' && <button className="jw-btn" onClick={() => run(() => api.post(`/jewelry-ops/campaigns/${id}/resume`, {}), 'Resumed - still on hold until sending is connected.')}>Resume</button>}
          {status.is_owner && !c.approved && <button className="jw-btn" disabled={dirty} onClick={() => run(() => api.post(`/jewelry-ops/campaigns/${id}/approve`, {}), 'Approved.')}>Approve</button>}
          <button className="jw-btn jw-btn--ghost" onClick={() => run(() => api.post(`/jewelry-ops/campaigns/${id}/cancel`, {}), 'Campaign cancelled.')}>Cancel campaign</button>
          <button className="jw-btn jw-btn--locked" onClick={async () => { try { await api.post(`/jewelry-ops/campaigns/${id}/send`, {}) } catch (e) { setSendResult(e.detail || { message: errText(e) }) } }}>🔒 Send now</button>
        </div>
      )}
      {c.status === 'scheduled' && <p className="jw-note">Scheduled for {when(c.scheduled_for)} and on hold: no job sends this campaign until sending is connected and every check above passes.</p>}
      {sendResult && (
        <Modal title="Nothing was sent" onClose={() => setSendResult(null)}>
          <p>{sendResult.message}</p>
          <ul className="jw-checks">{(sendResult.readiness?.checks || []).map(x => <li key={x.key} className={x.ok ? 'ok' : ''}><span>{x.ok ? '✓' : '✕'}</span><div><b>{x.label}</b><small>{x.detail}</small></div></li>)}</ul>
          <div className="jw-row jw-row--end"><button className="jw-btn jw-btn--gold" onClick={() => setSendResult(null)}>OK</button></div>
        </Modal>
      )}
    </div>
  )
}

export default function Campaigns() {
  const { status } = useJewelry()
  const [params, setParams] = useSearchParams()
  const { data, error, loading, reload } = useApi('/jewelry-ops/campaigns')
  const [creating, setCreating] = useState(false)
  const [toast, toastNode] = useToast()
  const sel = params.get('c') || data?.campaigns?.[0]?.id
  const create = async (kind) => {
    try { const c = await api.post('/jewelry-ops/campaigns', { kind }); setCreating(false); await reload(); setParams({ c: c.id }) }
    catch (e) { toast(errText(e), 'err') }
  }
  return (
    <div>
      <div className="jw-head">
        <div><div className="jw-eyebrow">Campaign Studio</div><h1>Promotions</h1><p>Build promotions and see exactly who could receive them. <b>Sending is locked</b> until messaging is connected and the owner authorizes outreach.</p></div>
        <button className="jw-btn jw-btn--gold" onClick={() => setCreating(true)}>+ New campaign</button>
      </div>
      <ErrorNote onRetry={reload}>{error}</ErrorNote>
      {loading && !data && <Loading />}
      {data && (
        <div className="jw-grid jw-grid--side">
          <div className="jw-card">
            <div className="jw-card__head"><h3>Campaigns</h3><small>{data.campaigns.length}</small></div>
            {data.campaigns.length === 0 ? <Empty title="No campaigns yet" action={<button className="jw-btn jw-btn--gold" onClick={() => setCreating(true)}>Create one</button>}>Start from a Gold Buying, Reactivation, Jewelry, Coin, Seasonal or VIP template.</Empty> : (
              <div className="jw-camps">{data.campaigns.map(c => (
                <button key={c.id} className={`jw-camp ${sel === c.id ? 'on' : ''}`} onClick={() => setParams({ c: c.id })}>
                  <b>{c.name}</b><span><Pill tone={STATUS_TONE[c.status]}>{c.status}</Pill> {c.channel}</span>
                  <small>{c.results.appointments} appointments · {money(c.results.recorded_revenue)} recorded</small>
                </button>
              ))}</div>
            )}
          </div>
          {sel ? <Editor key={sel} id={sel} status={status} onChanged={reload} toast={toast} /> : <div />}
        </div>
      )}
      {creating && (
        <Modal title="New campaign" onClose={() => setCreating(false)}>
          <p>Choose a starting template. It’s saved as a draft you can edit.</p>
          <div className="jw-templates">{(status.campaign_kinds || []).map(k => <button key={k.key} className="jw-template" onClick={() => create(k.key)}>{k.label}</button>)}</div>
        </Modal>
      )}
      {toastNode}
    </div>
  )
}
