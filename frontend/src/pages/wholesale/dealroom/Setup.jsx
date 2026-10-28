/* DEAL ROOM SETUP - five guided steps, saved on the server at every step.
 * Prefilled from what the workspace already knows; after activation the same
 * screen edits settings without starting over. Activation is the server's
 * answer, shown as it came back (never a fake success banner). */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Alert, Skeleton, Tag } from '../ds/ds'
import { errText } from '../wsShared'
import { DRS, InviteDrawer, cap, when } from './shared'

const STEPS = [
  ['details', 'Deal Room details'], ['experience', 'Investor experience'], ['publishing', 'Deal publishing'],
  ['invitations', 'Invitations'], ['review', 'Review & activate'],
]

export default function SetupWizard({ onSaved, onFail, status }) {
  const [v, setV] = useState(null)
  const [s, setS] = useState(null)
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  const load = useCallback(() => api.get(`${DRS}/settings`).then((x) => { setV(x); setS(x.settings) }).catch((e) => setErr(errText(e))), [])
  useEffect(() => { load() }, [load])
  const save = async (next) => {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const x = await api.put(`${DRS}/settings`, s)
      setV(x); setS(x.settings); setMsg('Saved.'); onSaved && onSaved(null)
      if (next !== undefined) setStep(next)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  if (!v || !s) return err ? <Alert>{err}</Alert> : <Skeleton rows={6} />
  const set = (k, val) => setS((x) => ({ ...x, [k]: val }))
  const toggle = (k, val) => setS((x) => ({ ...x, [k]: (x[k] || []).includes(val) ? x[k].filter((y) => y !== val) : [...(x[k] || []), val] }))
  const T = (k, label, props = {}) => (
    <label className="drm-field"><span>{label}{v.prefilled.includes(k) && s[k] ? <em className="drm-pre">filled in for you</em> : null}</span>
      <input value={s[k] || ''} onChange={(e) => set(k, e.target.value)} {...props} /></label>
  )
  return (
    <div className="drm-setup">
      <ol className="drm-stepper" aria-label="Setup steps">
        {STEPS.map(([k, l], i) => (
          <li key={k} className={i === step ? 'is-on' : i < step ? 'is-done' : ''}>
            <button type="button" onClick={() => setStep(i)} aria-current={i === step ? 'step' : undefined}>
              <span className="drm-stepper__n" aria-hidden="true">{i < step ? '✓' : i + 1}</span><span>{l}</span></button>
          </li>
        ))}
      </ol>
      <section className="drm-card drm-setup__body" aria-labelledby="step-h">
        <div className="drm-card__head"><h2 id="step-h" className="drm-h3">Step {step + 1} · {STEPS[step][1]}</h2>
          <span className={`drm-pill drm-pill--${v.status}`}>{v.status === 'active' ? 'Active' : v.status === 'paused' ? 'Paused' : 'Not activated'}</span></div>
        <Alert>{err}</Alert>
        <Alert kind="ok">{msg}</Alert>
        {step === 0 ? (
          <div className="drm-grid2">
            {T('name', 'Deal Room name', { required: true })}
            {T('brand_name', 'Brand shown to investors')}
            {T('tagline', 'Tagline')}
            {T('owner_name', 'Owner / main contact')}
            {T('owner_email', 'Owner email', { type: 'email' })}
            {T('owner_phone', 'Owner phone', { type: 'tel' })}
            {T('support_email', 'Support email for investors', { type: 'email' })}
            {T('support_phone', 'Support phone for investors', { type: 'tel' })}
            <fieldset className="drm-field drm-span2"><legend>Approved team members (answer investors, receive notifications)</legend>
              <div className="drm-pickers">{v.options.team.map((p) => (
                <label key={p.user_id} className="drm-check"><input type="checkbox" checked={(s.team_user_ids || []).includes(p.user_id)}
                       onChange={() => toggle('team_user_ids', p.user_id)} />{p.name} <small className="evo-muted">{cap(p.role)}</small></label>
              ))}</div></fieldset>
            {T('welcome_title', 'Welcome headline')}
            <label className="drm-field drm-span2"><span>Welcome message</span>
              <textarea rows={3} value={s.welcome_message || ''} onChange={(e) => set('welcome_message', e.target.value)} /></label>
          </div>
        ) : null}
        {step === 1 ? (
          <div className="drm-grid2">
            <fieldset className="drm-field drm-span2"><legend>Portal design</legend>
              {v.options.designs.map((d) => (
                <label key={d.key} className={`drm-design${s.design === d.key ? ' is-on' : ''}`}>
                  <input type="radio" name="design" checked={s.design === d.key} onChange={() => set('design', d.key)} />
                  <span className="drm-design__swatch" aria-hidden="true"><i /><i /><i /></span>
                  <span><b>{d.label}</b><small>{d.detail}</small></span></label>
              ))}</fieldset>
            <fieldset className="drm-field drm-span2"><legend>Investment categories investors can expect</legend>
              <div className="drm-pills">{v.options.categories.map((c) => (
                <button type="button" key={c.key} aria-pressed={(s.categories || []).includes(c.key)} className={(s.categories || []).includes(c.key) ? 'is-on' : ''}
                        onClick={() => toggle('categories', c.key)}>{c.label}</button>))}</div></fieldset>
            <fieldset className="drm-field drm-span2"><legend>Markets</legend>
              <div className="drm-pills">{v.options.markets.map((m) => (
                <button type="button" key={m} aria-pressed={(s.markets || []).includes(m)} className={(s.markets || []).includes(m) ? 'is-on' : ''}
                        onClick={() => toggle('markets', m)}>{m} County</button>))}</div></fieldset>
            <label className="drm-check drm-span2"><input type="checkbox" checked={!!s.ai_concierge_enabled} onChange={(e) => set('ai_concierge_enabled', e.target.checked)} />
              AI investment concierge - answers only from what you publish, never guesses prices, returns or legal terms, and hands off to a person on request</label>
            <fieldset className="drm-field drm-span2"><legend>Notify the team when…</legend>
              {v.options.notify.map((n) => (
                <label key={n.key} className="drm-check"><input type="checkbox" checked={(s.notify || {})[n.key] !== false}
                       onChange={(e) => set('notify', { ...(s.notify || {}), [n.key]: e.target.checked })} />{n.label}</label>))}</fieldset>
          </div>
        ) : null}
        {step === 2 ? (
          <div className="drm-grid2">
            <PackagesReady />
            <label className="drm-field"><span>Who sees a new deal by default</span>
              <select value={s.default_audience} onChange={(e) => set('default_audience', e.target.value)}>
                <option value="matched">Investors whose buy box matches</option>
                <option value="all">Every Deal Room investor</option>
                <option value="selected">Only investors I choose per deal</option>
              </select></label>
            <label className="drm-field"><span>Minimum match score for “matched”</span>
              <input inputMode="numeric" value={s.default_min_match ?? ''} onChange={(e) => set('default_min_match', e.target.value.replace(/\D/g, ''))} /></label>
            <label className="drm-field"><span>Default days a deal stays open (blank = no closing date)</span>
              <input inputMode="numeric" value={s.default_publish_days ?? ''} onChange={(e) => set('default_publish_days', e.target.value.replace(/\D/g, ''))} /></label>
            <fieldset className="drm-field drm-span2"><legend>Exclusive-review lengths you allow</legend>
              <div className="drm-pills">{v.options.hold_presets.map((p) => (
                <button type="button" key={p} aria-pressed={(s.hold_presets || []).includes(p)} className={(s.hold_presets || []).includes(p) ? 'is-on' : ''}
                        onClick={() => toggle('hold_presets', p)}>{p.replace('h', ' hours').replace('7d', '7 days')}</button>))}</div>
              <label className="drm-check"><input type="checkbox" checked={!!s.hold_allow_custom} onChange={(e) => set('hold_allow_custom', e.target.checked)} />Allow a custom length (up to 30 days)</label></fieldset>
            <label className="drm-field"><span>Open requests per investor</span>
              <input inputMode="numeric" value={s.hold_max_per_investor ?? 1} onChange={(e) => set('hold_max_per_investor', e.target.value.replace(/\D/g, ''))} /></label>
            <div className="drm-field">
              <label className="drm-check"><input type="checkbox" checked={!!s.hold_require_note} onChange={(e) => set('hold_require_note', e.target.checked)} />Investor must say why</label>
              <label className="drm-check"><input type="checkbox" checked={!!s.hold_require_pof} onChange={(e) => set('hold_require_pof', e.target.checked)} />Require accepted proof of funds first</label>
            </div>
            <p className="evo-small evo-muted drm-span2">Exclusive review is a temporary hold on Deal Room visibility. It is not an option, a contract or a right to purchase - every investor screen says so.</p>
          </div>
        ) : null}
        {step === 3 ? <InvitesStep active={v.status === 'active'} onFail={onFail} /> : null}
        {step === 4 ? <ReviewStep status={v.status} onChanged={() => { load(); onSaved && onSaved(null) }} /> : null}
        <div className="drm-setup__foot">
          {step > 0 ? <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setStep(step - 1)}>Back</button> : <span />}
          {step < 3 ? <button type="button" className="evo-btn evo-btn--primary" disabled={busy} onClick={() => save(step + 1)}>{busy ? 'Saving…' : 'Save and continue'}</button> : null}
          {step === 3 ? <button type="button" className="evo-btn evo-btn--primary" onClick={() => setStep(4)}>Continue to review</button> : null}
        </div>
      </section>
    </div>
  )
}

function PackagesReady() {
  const [rows, setRows] = useState(null)
  useEffect(() => { api.get(`${DRS}/candidates?include_test=true`).then((x) => setRows(x.deals)).catch(() => setRows([])) }, [])
  const ready = (rows || []).filter((r) => r.ready)
  return (
    <div className="drm-field drm-span2"><span>Eligible approved deal packages</span>
      {!rows ? <Skeleton rows={2} /> : ready.length ? (
        <ul className="drm-list">{ready.slice(0, 6).map((r) => <li key={r.deal_id}><span><b>{r.address}</b><small>{cap(r.property_type)}{r.is_test ? ' · practice' : ''}</small></span><Tag kind="live">Ready</Tag></li>)}</ul>
      ) : <p className="evo-small evo-muted">No approved investor packages yet ({rows.length} open deal(s)). Use Add deal on the Deal Room to see the exact step each deal needs.</p>}
    </div>
  )
}

function InvitesStep({ active }) {
  const [inv, setInv] = useState(null)
  const [open, setOpen] = useState(false)
  const [bump, setBump] = useState(0)
  const [link, setLink] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.get(`${DRS}/invites?include_test=true`).then((x) => setInv(x.invites)).catch((e) => setErr(errText(e))) }, [bump])
  const resend = async (i) => { try { setLink(await api.post(`${DRS}/invites/${i.id}/resend`, {})); setBump((x) => x + 1) } catch (e) { setErr(errText(e)) } }
  const revoke = async (i) => { if (!window.confirm(`Revoke ${i.name}'s invitation?`)) return; try { await api.post(`${DRS}/invites/${i.id}/revoke`, {}); setBump((x) => x + 1) } catch (e) { setErr(errText(e)) } }
  return (
    <div>
      <p className="evo-small">Invite investors now (practice invitations work before activation), or after you activate.</p>
      <button type="button" className="evo-btn evo-btn--primary" onClick={() => setOpen(true)}>Invite investor</button>
      <Alert>{err}</Alert>
      {link ? <div className="drm-linkbox" role="status"><p className="drm-linkbox__t"><b>{link.name}</b> · new secure link</p>
        <input readOnly value={link.link} aria-label="Secure invitation link" onFocus={(e) => e.target.select()} />
        <button type="button" className="evo-btn evo-btn--sm" onClick={() => navigator.clipboard && navigator.clipboard.writeText(link.link)}>Copy link</button></div> : null}
      {!inv ? <Skeleton rows={2} /> : inv.length ? (
        <ul className="drm-list">{inv.map((i) => (
          <li key={i.id}><span><b>{i.name}</b><small>{i.email}{i.is_test ? ' · practice' : ''} · sent {when(i.last_sent_at)}</small></span>
            <span className="drm-row"><Tag kind={i.status === 'accepted' ? 'live' : i.status === 'pending' ? 'info' : undefined}>{i.status === 'accepted' ? 'Registered' : cap(i.status)}</Tag>
              {i.status === 'pending' || i.status === 'expired' ? <button type="button" className="evo-btn evo-btn--sm" onClick={() => resend(i)}>New link</button> : null}
              {i.status === 'pending' ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => revoke(i)}>Revoke</button> : null}</span></li>))}</ul>
      ) : <p className="evo-small evo-muted">No invitations yet.</p>}
      <InviteDrawer open={open} onClose={() => setOpen(false)} roomActive={active} onDone={() => setBump((x) => x + 1)} />
    </div>
  )
}

function ReviewStep({ status, onChanged }) {
  const [rd, setRd] = useState(null)
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const load = useCallback(() => api.get(`${DRS}/readiness`).then(setRd).catch((e) => setErr(errText(e))), [])
  useEffect(() => { load() }, [load])
  const activate = async () => {
    setBusy(true); setErr(null)
    try { const r = await api.post(`${DRS}/activate`, {}); setRes(r); setRd(r.readiness); onChanged() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const pause = async () => {
    if (!window.confirm('Pause the Deal Room? Investors keep their logins; no new real invitations or publications go out.')) return
    try { await api.post(`${DRS}/pause`, {}); setRes({ activated: false, message: 'Deal Room paused.' }); load(); onChanged() } catch (e) { setErr(errText(e)) }
  }
  if (!rd) return <Skeleton rows={5} />
  return (
    <div>
      <ul className="drm-checks">
        {rd.items.map((i) => (
          <li key={i.key} className={i.ok ? 'is-ok' : i.blocking ? 'is-block' : 'is-warn'}>
            <span className="drm-checks__dot" aria-hidden="true">{i.ok ? '✓' : i.blocking ? '!' : '·'}</span>
            <span><b>{i.label}{i.blocking && !i.ok ? ' - required' : ''}</b><small>{i.detail}</small></span>
          </li>
        ))}
      </ul>
      <p className="evo-small">Investor portal address: <b>{window.location.origin}/investors</b></p>
      {rd.blockers.length ? <Alert>Activation is blocked until: {rd.blockers.map((b) => b.label).join('; ')}.</Alert> : null}
      {res ? <div className={`drm-result ${res.activated ? 'is-ok' : 'is-no'}`} role="status">{res.message}</div> : null}
      <Alert>{err}</Alert>
      <div className="drm-row drm-row--gap">
        <a className="evo-btn evo-btn--secondary" href="/investors/preview" target="_blank" rel="noopener noreferrer">Preview Deal Room</a>
        <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !rd.can_activate} onClick={activate}>
          {busy ? 'Activating…' : status === 'active' ? 'Re-check and keep active' : 'Activate Deal Room'}</button>
        {status === 'active' ? <button type="button" className="evo-btn evo-btn--ghost" onClick={pause}>Pause</button> : null}
      </div>
    </div>
  )
}
