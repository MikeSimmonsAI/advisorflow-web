/* Settings, grouped by purpose: General, Communications, Email, Users & Access,
   Integrations, Safety & Launch. One save (PATCH /program/settings) with inline
   validation. Secrets never reach the browser: integrations show status only.
   Kerry Allan stays a sender profile until a real user account exists. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, Field, PageHead, errText, num, when } from './ui'

const CATS = [
  ['general', 'General'], ['communications', 'Communications'], ['email', 'Email'],
  ['access', 'Users & Access'], ['integrations', 'Integrations'], ['safety', 'Safety & Launch'],
]
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const LABELS = {
  email_sender: 'Email sender', location_email_aliases: 'Location email addresses', sms_number: 'Texting number',
  sms_reply_routing: 'Text reply routing', email_reply_mailbox: 'Reply mailbox', primary_contact_user: 'Primary contact account',
  alert_recipients: 'Alert recipients', held_records: 'Held records', email_campaign_runner: 'Campaign email runner',
  postal_addresses: 'Postal addresses (email footer)', link_domain: 'Link domain', inbox_placement: 'Inbox placement',
  outbound_brake: 'Outbound emergency stop',
}

function formFrom(program) {
  return {
    primary_contact_name: program.primary_contact_name || '', primary_contact_title: program.primary_contact_title || '',
    alert_email: program.alert_email || '', alert_phone: program.alert_phone || '',
    hot_sla_minutes: String(program.hot_sla_minutes ?? 15), reply_instructions_sms: program.reply_instructions_sms || '',
    reply_instructions_email: program.reply_instructions_email || '',
    managers: (program.management_recipients || []).map(m => [m.name, m.email, m.phone].map(x => x || '').join(' | ')).join('\n'),
    staff_alerts_enabled: !!program.staff_alerts_enabled, alias_mode: program.alias_mode || 'from',
    mailbox_folder_path: program.mailbox_folder_path || '',
  }
}

function validate(f) {
  const e = {}
  const sla = Number(f.hot_sla_minutes)
  if (!Number.isInteger(sla) || sla < 1 || sla > 1440) e.hot_sla_minutes = 'Whole minutes from 1 to 1440.'
  if (f.alert_email && !EMAIL_RE.test(f.alert_email.trim())) e.alert_email = 'Enter a valid email address or leave blank.'
  if (f.alert_phone && f.alert_phone.replace(/\D/g, '').length < 10) e.alert_phone = 'Enter a 10-digit phone number or leave blank.'
  if (!f.primary_contact_name.trim()) e.primary_contact_name = 'The name families hear from is required.'
  const bad = f.managers.split('\n').map(l => l.trim()).filter(Boolean).find(l => {
    const [name, email, phone] = l.split('|').map(s => (s || '').trim())
    return !name || (!email && !phone) || (email && !EMAIL_RE.test(email))
  })
  if (bad) e.managers = `Check this line: “${bad}”. Use name | email | phone, with an email or a phone.`
  return e
}

export default function Settings({ isManager, program, readiness, locations, onChange }) {
  const [cat, setCat] = useState('general')
  const [form, setForm] = useState(() => formFrom(program))
  const [saved, setSaved] = useState(() => formFrom(program))
  const [state, setState] = useState({ kind: 'idle', text: '' })   // idle | saving | saved | error
  const [sms, setSms] = useState(null)
  const errors = useMemo(() => validate(form), [form])
  const dirty = JSON.stringify(form) !== JSON.stringify(saved)
  const [held, setHeld] = useState([])
  useEffect(() => {
    api.get('/program/health').then(h => setSms(h.sms || null)).catch(() => {})
    api.get('/program/locations').then(d => setHeld((d || []).filter(p => p.verification_held).map(p => p.official_name))).catch(() => {})
  }, [])
  const set = (k, v) => { setForm(f => ({ ...f, [k]: v })); if (state.kind !== 'saving') setState({ kind: 'idle', text: '' }) }

  const save = async () => {
    if (Object.keys(errors).length) { setState({ kind: 'error', text: 'Fix the highlighted fields first.' }); return }
    setState({ kind: 'saving', text: '' })
    const managers = form.managers.split('\n').map(l => l.split('|').map(s => s.trim())).filter(p => p[0] || p[1] || p[2])
      .map(([name, email, phone]) => ({ name, email, phone, role: 'management' }))
    try {
      await api.patch('/program/settings', {
        primary_contact_name: form.primary_contact_name.trim(), primary_contact_title: form.primary_contact_title.trim(),
        alert_email: form.alert_email.trim() || null, alert_phone: form.alert_phone.trim() || null,
        hot_sla_minutes: Number(form.hot_sla_minutes), reply_instructions_sms: form.reply_instructions_sms,
        reply_instructions_email: form.reply_instructions_email, management_recipients: managers,
        staff_alerts_enabled: form.staff_alerts_enabled, alias_mode: form.alias_mode,
        mailbox_folder_path: form.mailbox_folder_path || null,
      })
      setSaved(form); setState({ kind: 'saved', text: 'Saved.' }); onChange()
    } catch (e) { setState({ kind: 'error', text: errText(e) }) }
  }

  const items = Object.fromEntries((readiness?.items || []).map(i => [i.key, i]))
  const ro = !isManager
  const savable = ['general', 'communications', 'email'].includes(cat)

  return (
    <>
      <PageHead title="Program settings" sub="Configuration grouped by purpose, not buried in one long form." />
      <div className="sci-settings">
        <nav className="sci-panel sci-settings-nav" aria-label="Settings categories">
          {CATS.map(([k, label]) => (
            <button key={k} type="button" aria-current={cat === k} onClick={() => setCat(k)}>{label}</button>
          ))}
        </nav>

        <section className="sci-panel sci-pad" aria-labelledby="sci-set-title">
          <div className="sci-panel-head">
            <h2 id="sci-set-title">{CATS.find(c => c[0] === cat)[1]}</h2>
            {savable && dirty && <Chip tone="warn">Unsaved changes</Chip>}
          </div>

          {cat === 'general' && (
            <div className="sci-grid-even">
              <div className="sci-card">
                <h3 className="sci-h3">Organization</h3>
                <div className="sci-form" style={{ gridTemplateColumns: '1fr' }}>
                  <Field label="Program name" hint="Set when the program was created"><input value={program.name || ''} disabled /></Field>
                  <Field label="Workspace subtitle"><input value={program.hero_subtitle || 'Family Service Lead & Communication Center'} disabled /></Field>
                  <Field label="Primary contact (families hear from)" error={errors.primary_contact_name}
                    hint={program.primary_contact_user_id ? 'User account linked' : 'Sender profile only — no user account yet'}>
                    <input value={form.primary_contact_name} disabled={ro} onChange={e => set('primary_contact_name', e.target.value)} />
                  </Field>
                  <Field label="Title"><input value={form.primary_contact_title} disabled={ro} onChange={e => set('primary_contact_title', e.target.value)} /></Field>
                </div>
              </div>
              <div className="sci-card">
                <h3 className="sci-h3">Program identity</h3>
                <div className="sci-form" style={{ gridTemplateColumns: '1fr' }}>
                  <Field label="Texting number"><input value={sms?.number || 'Checking…'} disabled /></Field>
                  <Field label="Approved messaging scope"><input value={sms ? (sms.approved_scope || (sms.approved ? 'Approved' : 'Not approved')) : 'Checking…'} disabled /></Field>
                  <Field label="Mapped locations"><input value={`${num(locations.length)}${held.length ? ` · held: ${held.join(', ')}` : ''}`} disabled /></Field>
                </div>
                <p className="sci-micro sci-muted" style={{ marginBottom: 0 }}>Credentials are never shown in the browser.</p>
              </div>
            </div>
          )}

          {cat === 'communications' && (
            <div className="sci-form">
              <Field label="Reply instructions in texts" full><input value={form.reply_instructions_sms} disabled={ro} onChange={e => set('reply_instructions_sms', e.target.value)} /></Field>
              <Field label="Reply instructions in emails" full><input value={form.reply_instructions_email} disabled={ro} onChange={e => set('reply_instructions_email', e.target.value)} /></Field>
              <Field label="HOT response SLA (minutes)" error={errors.hot_sla_minutes}>
                <input type="number" min="1" max="1440" inputMode="numeric" value={form.hot_sla_minutes} disabled={ro} onChange={e => set('hot_sla_minutes', e.target.value)} />
              </Field>
              <Field label="Staff text / email alerts" hint="HOT replies alert immediately">
                <select value={form.staff_alerts_enabled ? 'on' : 'off'} disabled={ro} onChange={e => set('staff_alerts_enabled', e.target.value === 'on')}>
                  <option value="on">On — in-app, text and email</option><option value="off">Off — in-app only</option>
                </select>
              </Field>
              <Field label="Alert email" hint="Blank until supplied" error={errors.alert_email}><input type="email" value={form.alert_email} disabled={ro} onChange={e => set('alert_email', e.target.value)} /></Field>
              <Field label="Alert phone" hint="Blank until supplied" error={errors.alert_phone}><input type="tel" value={form.alert_phone} disabled={ro} onChange={e => set('alert_phone', e.target.value)} /></Field>
              <Field label="Management alert recipients" hint="One per line: name | email | phone" error={errors.managers} full>
                <textarea value={form.managers} disabled={ro} onChange={e => set('managers', e.target.value)} />
              </Field>
            </div>
          )}

          {cat === 'email' && <AliasSettings isManager={isManager} form={form} set={set} onChange={onChange} />}

          {cat === 'access' && (
            <div className="sci-stack">
              <StatusRow item={items.primary_contact_user} label={LABELS.primary_contact_user} />
              <p className="sci-small" style={{ margin: 0 }}>
                {program.primary_contact_name || 'The primary contact'} is shown to families as the sender.
                {program.primary_contact_user_id ? ' A user account is linked.' : ' That is a sender profile only — no user account exists yet, so nobody signs in as them.'}
              </p>
              <p className="sci-small sci-muted" style={{ margin: 0 }}>You are signed in as a {isManager ? 'workspace manager (can change settings, campaigns and locations)' : 'team member (read-only settings)'}.</p>
              {isManager && <div><a className="sci-btn" href="/users">Manage users & access</a></div>}
            </div>
          )}

          {cat === 'integrations' && (
            <div className="sci-stack">
              <div className="sci-rows">
                {['email_sender', 'email_reply_mailbox', 'sms_number', 'sms_reply_routing', 'link_domain', 'location_email_aliases']
                  .map(k => <StatusRow key={k} item={items[k]} label={LABELS[k]} />)}
              </div>
              <p className="sci-micro sci-muted" style={{ margin: 0 }}>Keys and tokens live on the server only; this page shows whether each connection works, never the secret itself.</p>
            </div>
          )}

          {cat === 'safety' && (
            <>
              <h3 className="sci-h3">Readiness for production outreach</h3>
              <div className="sci-rows" style={{ marginBottom: 18 }}>
                {(readiness?.items || []).map(i => <StatusRow key={i.key} item={i} label={LABELS[i.key] || i.key.replace(/_/g, ' ')} />)}
              </div>
              {isManager && <ImportAndAlerts onChange={onChange} />}
            </>
          )}

          {savable && isManager && (
            <div className="sci-savebar">
              {state.kind === 'error' && <span className="sci-micro" role="alert" style={{ color: 'var(--bad)' }}>{state.text}</span>}
              {state.kind === 'saved' && !dirty && <span className="sci-micro" role="status" style={{ color: 'var(--ok)' }}>Saved.</span>}
              {dirty && <button type="button" className="sci-btn" onClick={() => { setForm(saved); setState({ kind: 'idle', text: '' }) }}>Discard</button>}
              <button type="button" className="sci-btn primary" disabled={!dirty || state.kind === 'saving'} onClick={save}>
                {state.kind === 'saving' ? 'Saving…' : 'Save settings'}
              </button>
            </div>
          )}
        </section>
      </div>
    </>
  )
}

function StatusRow({ item, label }) {
  if (!item) return <div className="sci-kv"><span>{label}</span><span><Chip plain>Unknown</Chip></span></div>
  return (
    <div className="sci-kv">
      <span>{label}<span className="sci-micro sci-muted" style={{ display: 'block' }}>{item.detail}</span></span>
      <span><Chip tone={item.ok ? 'ok' : 'warn'} plain>{item.ok ? 'Ready' : 'Not ready'}</Chip></span>
    </div>
  )
}

function AliasSettings({ isManager, form, set, onChange }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => { api.get('/program/aliases').then(setD).catch(e => setErr(errText(e))) }, [])
  useEffect(() => { load() }, [load])
  const assign = async () => { try { await api.post('/program/aliases/assign', {}); load(); onChange() } catch (e) { setErr(errText(e)) } }
  const confirmAll = async () => {
    const c = window.prompt('Only after the addresses were added to the central mailbox in Microsoft 365. Type ALIASES RECEIVE MAIL to confirm.')
    if (c == null) return
    try { await api.post('/program/aliases/confirm-receiving', { confirm: c }); load(); onChange() } catch (e) { setErr(errText(e)) }
  }
  if (!d) return err ? <ErrorLine text={err} /> : <p className="sci-loading">Loading…</p>
  const st = d.status
  const eff = st.effective || {}
  return (
    <div className="sci-stack">
      <ErrorLine text={err} />
      <p className="sci-small sci-muted" style={{ margin: 0 }}>
        One address per location on <b>{st.domain || '—'}</b>, all delivered to the central mailbox ({st.verified_from || 'no verified sender'}).
        Authentication: <b>{st.auth_ok ? 'aligned with the verified sender' : 'not aligned — used as Reply-To only'}</b>.
        {' '}{st.seen_receiving} of {st.assigned} seen receiving{st.confirmed_all ? '; all confirmed' : ''}. In use: {eff.from || 0} as From, {eff.reply_to || 0} as Reply-To, {eff.none || 0} not yet.
      </p>
      <div className="sci-form">
        <Field label="Use the location address as">
          <select disabled={!isManager} value={form.alias_mode} onChange={e => set('alias_mode', e.target.value)}>
            <option value="from">From and Reply-To (when aligned)</option><option value="reply_to">Reply-To only</option><option value="off">Not used</option>
          </select>
        </Field>
        <Field label="Outlook folder for processed replies" hint="Each location's folder sits inside it; blank = don't file">
          <input disabled={!isManager} value={form.mailbox_folder_path} placeholder="Inbox/Customers Folder/SCI" onChange={e => set('mailbox_folder_path', e.target.value)} />
        </Field>
      </div>
      {isManager && (
        <div className="sci-row-gap">
          <button type="button" className="sci-btn" onClick={assign}>Assign missing addresses</button>
          {!st.confirmed_all && <button type="button" className="sci-btn" onClick={confirmAll}>Confirm addresses receive mail…</button>}
        </div>
      )}
      <div className="sci-tablewrap">
        <table className="sci-table">
          <thead><tr><th>Location</th><th>Address</th><th>Status</th></tr></thead>
          <tbody>
            {d.items.map(r => (
              <tr key={r.location_id}>
                <td>{r.location}</td><td className="sci-micro">{r.alias || '—'}</td>
                <td className="sci-micro">{r.mode === 'from' ? 'From + Reply-To' : r.mode === 'reply_to' ? 'Reply-To' : (r.alias ? 'Waiting to receive mail' : '—')}{r.seen_receiving_at ? ` · seen ${when(r.seen_receiving_at)}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ImportAndAlerts({ onChange }) {
  const [alerts, setAlerts] = useState([])
  const [file, setFile] = useState(null)
  const [res, setRes] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.get('/program/alerts?limit=20').then(setAlerts).catch(() => {}) }, [])
  const run = async dry => {
    if (!file) { setErr('Choose the source CSV first.'); return }
    setErr(''); setBusy(true)
    const fd = new FormData(); fd.append('file', file); fd.append('dry_run', dry ? 'true' : 'false')
    try { setRes(await api.upload('/program/import', fd)); onChange() } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="sci-grid-even">
      <div className="sci-card">
        <h3 className="sci-h3">Source import</h3>
        <p className="sci-micro sci-muted">A dry run changes nothing. Staging stores the rows with their decisions — it creates no leads, enrolls no one and sends nothing.</p>
        <label className="sci-sr" htmlFor="sci-import-file">Source CSV</label>
        <input id="sci-import-file" className="sci-input" type="file" accept=".csv" onChange={e => setFile(e.target.files?.[0] || null)} />
        <div className="sci-row-gap" style={{ marginTop: 10 }}>
          <button type="button" className="sci-btn" disabled={busy} onClick={() => run(true)}>Dry run</button>
          <button type="button" className="sci-btn primary" disabled={busy} onClick={() => run(false)}>Stage rows</button>
        </div>
        <ErrorLine text={err} />
        {res && <pre className="sci-preview-email" style={{ marginTop: 10, fontSize: 12 }}>{JSON.stringify(res.summary, null, 2)}</pre>}
      </div>
      <div className="sci-card">
        <h3 className="sci-h3">Recent alerts</h3>
        {alerts.length === 0 ? <Empty>No alerts yet.</Empty> : (
          <div className="sci-rows">
            {alerts.map(a => (
              <div className="sci-kv" key={a.id}><span>{a.kind} · {a.audience} · {a.channel}</span><span>{a.delivered ? 'delivered' : a.reason} · {when(a.created_at)}</span></div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
