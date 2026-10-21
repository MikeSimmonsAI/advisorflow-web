/**
 * Settings & Playbooks - the roofing company's configuration, the automated
 * texting switch, the follow-up playbook and the team.
 * PUT /roofing-ops/profile (owners), GET/POST /roofing-ops/text-switch,
 * GET /roofing-ops/playbooks, POST /roofing-ops/setup.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { useRoofing } from './RoofingShell'
import { Avatar, Empty, ErrorNote, Icon, Loading, Panel, Pill, errText, n, useApi, useToast } from './ui'
import './roofing-ops.css'

export default function Settings() {
  const { status, reloadStatus } = useRoofing()
  const [toast, say] = useToast()
  const [busy, setBusy] = useState(false)

  const runSetup = async () => {
    setBusy(true)
    try { await api.post('/roofing-ops/setup', {}); say('Roofing setup finished'); await reloadStatus() } catch (e) { say(errText(e), 'err') } finally { setBusy(false) }
  }

  return (
    <div className="rf-page">
      <div className="rf-page__head">
        <div>
          <h1 className="rf-h1">Settings &amp; playbooks</h1>
          <p className="rf-lede">How your company appears to homeowners, how inspections are scheduled, and the follow-up your AI team runs.</p>
        </div>
      </div>
      <div className="rf-stack">
        {!status.configured && (
          <div className="rf-note rf-note--warn">
            <Icon name="alert" size={18} />
            <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', flex: 1 }}>
              <span>Roofing setup hasn’t run for this company yet. It adds the roofing fields to imports and creates the follow-up playbook. Nothing is sent.</span>
              {status.can_manage && <button className="rf-btn rf-btn--primary rf-btn--sm" onClick={runSetup} disabled={busy}>{busy ? 'Setting up…' : 'Run setup'}</button>}
            </div>
          </div>
        )}
        <Profile status={status} say={say} reloadStatus={reloadStatus} />
        <div className="rf-grid-2">
          <TextSwitch say={say} />
          <Team team={status.team} canManage={status.can_manage} />
        </div>
        <Playbooks />
      </div>
      {toast}
    </div>
  )
}

function TagInput({ value, onChange, disabled, placeholder }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const v = draft.trim()
    if (v && !value.includes(v)) onChange([...value, v])
    setDraft('')
  }
  return (
    <div className="rf-stack" style={{ gap: 8 }}>
      <div className="rf-chips">
        {value.length === 0 && <span className="rf-muted" style={{ fontSize: 13 }}>None yet</span>}
        {value.map((v) => (
          <span className="rf-chip" key={v}>{v}
            {!disabled && <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(value.filter((x) => x !== v))}><Icon name="close" size={13} /></button>}
          </span>
        ))}
      </div>
      {!disabled && (
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="rf-input" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={placeholder} maxLength={80}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} />
          <button type="button" className="rf-btn rf-btn--ghost" onClick={add} disabled={!draft.trim()}>Add</button>
        </div>
      )}
    </div>
  )
}

function Profile({ status, say, reloadStatus }) {
  const p = status.profile || {}
  const ro = !status.can_manage
  const init = () => ({
    company_name: p.company_name_override || '', logo_url: p.logo_url || '', color_primary: p.color_primary || '#0b1f3a',
    color_accent: p.color_accent || '#f97316', service_areas: p.service_areas || [], inspection_types: p.inspection_types || [],
    inspection_duration_minutes: p.inspection_duration_minutes || 60, default_salesperson_id: p.default_salesperson_id || '',
    notify_user_ids: p.notify_user_ids || [], business_phone: p.business_phone || '', business_email: p.business_email || '',
  })
  const [f, setF] = useState(init)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { setF(init()) }, [status]) // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setF({ ...f, [k]: e && e.target ? e.target.value : e })

  const save = async (e) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try {
      await api.put('/roofing-ops/profile', {
        ...f,
        company_name: f.company_name.trim() || null, logo_url: f.logo_url.trim() || null,
        inspection_duration_minutes: Number(f.inspection_duration_minutes) || 60,
        default_salesperson_id: f.default_salesperson_id || null,
        business_phone: f.business_phone.trim() || null, business_email: f.business_email.trim() || null,
      })
      say('Company settings saved')
      await reloadStatus()
    } catch (e2) { setErr(errText(e2)) } finally { setBusy(false) }
  }
  const team = status.team || []

  return (
    <Panel title="Your roofing company" sub={ro ? 'Only an owner or manager can change these.' : 'Used on inspection alerts, the follow-up messages and your workspace.'}>
      <form className="rf-form" onSubmit={save}>
        <label className="rf-label">Company name
          <input className="rf-input" value={f.company_name} onChange={set('company_name')} disabled={ro} placeholder={p.company_name} maxLength={120} />
        </label>
        <label className="rf-label">Logo URL
          <input className="rf-input" value={f.logo_url} onChange={set('logo_url')} disabled={ro} placeholder="https://" maxLength={500} />
        </label>
        <label className="rf-label">Primary color
          <span className="rf-color"><input type="color" value={f.color_primary} onChange={set('color_primary')} disabled={ro} aria-label="Primary color" /><input className="rf-input" value={f.color_primary} onChange={set('color_primary')} disabled={ro} /></span>
        </label>
        <label className="rf-label">Accent color
          <span className="rf-color"><input type="color" value={f.color_accent} onChange={set('color_accent')} disabled={ro} aria-label="Accent color" /><input className="rf-input" value={f.color_accent} onChange={set('color_accent')} disabled={ro} /></span>
        </label>
        <div className="rf-label">Service areas
          <TagInput value={f.service_areas} onChange={set('service_areas')} disabled={ro} placeholder="City or ZIP" />
        </div>
        <div className="rf-label">Inspection types
          <TagInput value={f.inspection_types} onChange={set('inspection_types')} disabled={ro} placeholder="e.g. Storm Damage Assessment" />
        </div>
        <label className="rf-label">Inspection length (minutes)
          <input className="rf-input" type="number" min={15} max={480} step={15} value={f.inspection_duration_minutes} onChange={set('inspection_duration_minutes')} disabled={ro} />
        </label>
        <label className="rf-label">Default salesperson for new inspections
          <select className="rf-select" value={f.default_salesperson_id} onChange={set('default_salesperson_id')} disabled={ro}>
            <option value="">The homeowner’s assigned salesperson</option>
            {team.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
        </label>
        <label className="rf-label">Business phone
          <input className="rf-input" value={f.business_phone} onChange={set('business_phone')} disabled={ro} maxLength={40} />
        </label>
        <label className="rf-label">Business email
          <input className="rf-input" type="email" value={f.business_email} onChange={set('business_email')} disabled={ro} maxLength={200} />
        </label>
        {team.length > 0 && (
          <div className="rf-label rf-full">Also alert these people when an inspection is booked
            <div className="rf-checks">
              {team.map((u) => (
                <label key={u.id}>
                  <input type="checkbox" disabled={ro} checked={f.notify_user_ids.includes(u.id)}
                    onChange={(e) => setF({ ...f, notify_user_ids: e.target.checked ? [...f.notify_user_ids, u.id] : f.notify_user_ids.filter((x) => x !== u.id) })} />
                  {u.name}
                </label>
              ))}
            </div>
            <span className="rf-muted" style={{ fontWeight: 500 }}>The salesperson on the inspection is always alerted. Each person gets one alert per booking.</span>
          </div>
        )}
        {err && <div className="rf-full"><ErrorNote>{err}</ErrorNote></div>}
        {!ro && <div className="rf-full"><button className="rf-btn rf-btn--primary" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save company settings'}</button></div>}
      </form>
    </Panel>
  )
}

function TextSwitch({ say }) {
  const { data, error, reload, setData } = useApi('/roofing-ops/text-switch')
  const [busy, setBusy] = useState(false)
  const flip = async () => {
    setBusy(true)
    try {
      const r = await api.post('/roofing-ops/text-switch', { on: !data.on })
      setData(r); say(r.on ? 'Automated texts switched on' : 'Automated texts switched off')
    } catch (e) { say(errText(e), 'err') } finally { setBusy(false) }
  }
  return (
    <Panel title="Automated texting" sub="Whether follow-up texts actually leave for this company.">
      {error && <ErrorNote onRetry={reload}>{error}</ErrorNote>}
      {!data && !error && <Loading />}
      {data && (
        <div className="rf-stack" style={{ gap: 12 }}>
          <div className="rf-switch">
            <button className="rf-toggle" role="switch" aria-checked={data.on} aria-label="Automated texts" disabled={!data.can_change || busy} onClick={flip} />
            <b>{data.on ? 'On' : 'Off'}</b>
            <Pill tone={data.on ? 'green' : 'slate'}>{data.on ? 'Texts are sending' : 'Nothing sends automatically'}</Pill>
          </div>
          <p className="rf-muted" style={{ fontSize: 13.5 }}>
            {data.can_change
              ? 'This switch is for this company only. Consent, opt-out and quiet-hour checks still apply to every text.'
              : 'Only the platform owner can switch automated texting on or off. Manual replies follow their own send checks.'}
          </p>
        </div>
      )}
    </Panel>
  )
}

function Team({ team, canManage }) {
  return (
    <Panel title="Team" sub="Owners see the whole company. Salespeople see their own homeowners.">
      {!canManage ? <p className="rf-muted" style={{ fontSize: 14 }}>Your owner manages the team.</p>
        : !team?.length ? <Empty icon="users" title="No team members yet" />
          : (
            <div className="rf-stack" style={{ gap: 10 }}>
              {team.map((u) => (
                <div key={u.id} className="rf-cell-person">
                  <Avatar name={u.name} size={32} />
                  <div><b>{u.name}</b><span>{u.email}</span></div>
                  <span style={{ marginLeft: 'auto' }}><Pill tone={u.role === 'advisor' ? 'blue' : 'orange'}>{u.role === 'advisor' ? 'Salesperson' : 'Owner / manager'}</Pill></span>
                </div>
              ))}
            </div>
          )}
    </Panel>
  )
}

const CH = { sms: 'Text', email: 'Email', both: 'Text + email' }

function Playbooks() {
  const { data, error, loading, reload } = useApi('/roofing-ops/playbooks')
  if (error) return <ErrorNote onRetry={reload}>{error}</ErrorNote>
  if (!data) return loading ? <Loading label="Loading playbooks" /> : null
  const a = data.active
  return (
    <div className="rf-grid-2" style={{ alignItems: 'start' }}>
      <Panel title={a.name} sub={a.goal}
        actions={<Pill tone={!a.exists ? 'slate' : a.texts_on ? 'green' : 'amber'}>{!a.exists ? 'Not set up' : a.texts_on ? 'Running' : 'Ready, texting off'}</Pill>}>
        {!a.exists ? (
          <Empty icon="spark" title="The follow-up playbook isn’t set up yet">Run setup above to create it.</Empty>
        ) : (
          <div className="rf-stack" style={{ gap: 16 }}>
            <p className="rf-muted" style={{ fontSize: 13.5 }}>Who it works: {a.audience}.</p>
            <div className="rf-stats">
              <div><b>{n(a.enrolled)}</b><span>Enrolled</span></div>
              <div><b>{n(a.engaged)}</b><span>Replied</span></div>
              <div><b>{n(a.booked)}</b><span>Inspections booked</span></div>
              <div><b>{n(a.opted_out)}</b><span>Opted out</span></div>
            </div>
            <ol className="rf-timeline">
              {a.touches.map((t) => (
                <li key={t.touch}>
                  <span className="rf-timeline__when">Touch {t.touch}<span>Day {t.day}, {CH[t.channel] || t.channel}</span></span>
                  <span className="rf-timeline__text">{t.subject ? <b>{t.subject}{'\n'}</b> : null}{t.text}</span>
                </li>
              ))}
            </ol>
            <div>
              <p className="rf-label" style={{ marginBottom: 6 }}>Stops automatically when</p>
              <ul className="rf-rules">{a.stop_rules.map((r) => <li key={r}>{r}</li>)}</ul>
            </div>
            <p className="rf-muted" style={{ fontSize: 12.5 }}>Names in braces are filled in from the real homeowner, salesperson and company.</p>
          </div>
        )}
      </Panel>
      <Panel title="More playbooks" sub="For leads you already have. EvoSys never sources new leads for you.">
        <div className="rf-avail">
          {data.available.map((p) => (
            <div className="rf-avail__item" key={p.key}>
              <div><b>{p.name}</b><p>{p.goal}</p></div>
              <Pill tone="slate">Available, not running</Pill>
            </div>
          ))}
        </div>
        <p className="rf-muted" style={{ fontSize: 13, marginTop: 14 }}>{data.available_note}</p>
      </Panel>
    </div>
  )
}
