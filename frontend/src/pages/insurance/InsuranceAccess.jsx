/**
 * Insurance Team & Access, role dashboards, My Settings and invitation
 * acceptance (Issue #523). Data: /agency/retention/access/*, /dashboard, /me.
 *
 * The server is the authority on every permission shown here. These screens
 * only offer what /access/catalog says is real, grey out what the signed-in
 * person can't hand out (their delegation ceiling), and show the server's own
 * effective-access preview before anything is saved.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { api, setWorkspaceContext } from '../../api/client'
import { BASE, useIns } from './InsuranceShell'
import { AssistantPanel, AssignDialog, BookDialog, Err, KPI, QueueTable, TeamPage, TodayPage, greeting, useData } from './InsurancePages'
import { Empty, Icon, Modal, errText, fmtDate, fmtDateTime, fmtTime, initials, useToast } from './insuranceUi'
import './insurance.css'

const R = '/agency/retention'
const SCOPE_HELP = {
  assigned: 'Only records assigned to them (plus claimable shared work if they may claim).',
  team: 'Their own work and their team’s work at their locations.',
  locations: 'Everything at the locations they’re assigned to.',
  agency: 'Every location in the agency.',
}
const DASH_LABEL = { admin: 'Agency administrator', manager: 'Location manager', producer: 'Producer / agent',
  retention: 'Retention / customer service', scheduler: 'Appointment setter / VA', analyst: 'Read-only analyst', auto: 'Chosen from access' }
const STATUS_TONE = { active: 'green', suspended: 'amber', removed: 'grey', pending: 'blue', accepted: 'green', revoked: 'grey', expired: 'amber' }

function Pill({ tone = 'grey', children }) { return <span className={`ins-pill ins-pill--${tone}`}>{children}</span> }
function names(ids, list) { const m = new Map((list || []).map(x => [x.id, x.name])); return (ids || []).map(i => m.get(i)).filter(Boolean) }
function toggleIn(arr, v) { return arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v] }
function copy(text, say) {
  try { navigator.clipboard.writeText(text).then(() => say('Link copied.'), () => say('Select the link and copy it.', 'err')) } catch { say('Select the link and copy it.', 'err') }
}

// ═════════════════════════════ TEAM & ACCESS ═════════════════════════════

const TABS = [
  { key: 'people', label: 'People', need: ['team', 'view'] },
  { key: 'teams', label: 'Teams', need: ['team', 'view'] },
  { key: 'profiles', label: 'Access Profiles', need: ['access', 'view'] },
  { key: 'invitations', label: 'Invitations', need: ['access', 'view'] },
  { key: 'history', label: 'History', need: ['access', 'view'] },
  { key: 'locations', label: 'Locations', need: ['locations', 'view'] },
  { key: 'workload', label: 'Workload', need: ['team', 'view'] },
]

export function TeamAccessPage() {
  const c = useIns()
  const [params, setParams] = useSearchParams()
  const tabs = TABS.filter(t => c.can(...t.need))
  const tab = tabs.find(t => t.key === params.get('tab'))?.key || tabs[0]?.key
  const setTab = k => { const n = new URLSearchParams(params); n.set('tab', k); setParams(n, { replace: true }) }
  const [toast, say] = useToast()
  const [ref, setRef] = useState(null)        // catalog + people + profiles + teams + locations
  const [refErr, setRefErr] = useState(null)
  const [dlg, setDlg] = useState(null)
  const [tick, setTick] = useState(0)
  const bump = () => setTick(t => t + 1)

  const loadRef = useCallback(async () => {
    try {
      const want = {
        catalog: c.can('access', 'view') ? api.get(c.q(`${R}/access/catalog`)) : null,
        people: c.can('team', 'view') ? api.get(c.q(`${R}/access/people`)) : null,
        profiles: c.can('access', 'view') ? api.get(c.q(`${R}/access/profiles`)) : null,
        teams: c.can('team', 'view') ? api.get(c.q(`${R}/access/teams`, { include_archived: true })) : null,
        locations: c.can('locations', 'view') ? api.get(c.q(`${R}/access/locations`)) : null,
      }
      const keys = Object.keys(want)
      const vals = await Promise.all(keys.map(k => want[k]))
      const out = {}
      keys.forEach((k, i) => { out[k] = k === 'catalog' ? vals[i] : (vals[i]?.items || []) })
      setRef(out); setRefErr(null)
    } catch (e) { setRefErr(e) }
  }, [c.q, tick]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { loadRef() }, [loadRef])

  const canInvite = c.can('access', 'manage')
  const after = msg => { if (msg) say(msg); bump(); c.refresh() }
  if (!tabs.length) return <Empty>Your access doesn’t include Team & Access.</Empty>
  return (
    <div className="ins-access">
      <div className="ins-pagehead ins-pagehead--split">
        <div><h1>Team & Access</h1>
          <p className="ins-muted">Give each person their own login, an access profile, the locations they serve and what records they can see. Every change is checked on the server and recorded in History.</p></div>
        <div className="ins-row ins-row--wrap">
          {canInvite && <button className="ins-btn ins-btn--primary" onClick={() => setDlg({ kind: 'invite' })}><Icon name="user" size={16} /> Invite member</button>}
          {c.can('team', 'manage') && <button className="ins-btn" onClick={() => setDlg({ kind: 'team', team: null })}>Create team</button>}
          {c.can('access', 'manage') && <button className="ins-btn" onClick={() => setDlg({ kind: 'profile', profile: null })}>Create access profile</button>}
          {c.can('locations', 'manage') && <button className="ins-btn" onClick={() => setTab('locations')}>Manage locations</button>}
        </div>
      </div>
      <div className="ins-tabs" role="tablist" aria-label="Team & Access sections">
        {tabs.map(t => <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? 'on' : ''} onClick={() => setTab(t.key)}>{t.label}</button>)}
      </div>
      <Err error={refErr} onRetry={loadRef} />
      {!ref ? <div className="ins-loading">Loading…</div> : (<>
        {tab === 'people' && <PeopleTab refd={ref} onOpen={p => setDlg({ kind: 'member', userId: p.user_id })} onResend={after} say={say} />}
        {tab === 'teams' && <TeamsTab refd={ref} onEdit={t => setDlg({ kind: 'team', team: t })} />}
        {tab === 'profiles' && <ProfilesTab refd={ref} say={say} after={after}
          onEdit={p => setDlg({ kind: 'profile', profile: p })} onRetire={p => setDlg({ kind: 'retire', profile: p })} />}
        {tab === 'invitations' && <InvitationsTab refd={ref} tick={tick} after={after} say={say} />}
        {tab === 'history' && <HistoryTab tick={tick} />}
        {tab === 'locations' && <LocationsTab refd={ref} onEdit={l => setDlg({ kind: 'location', loc: l })} />}
        {tab === 'workload' && <TeamPage embedded />}
      </>)}
      {dlg?.kind === 'invite' && ref && <InviteDrawer refd={ref} onClose={() => setDlg(null)} onDone={m => after(m)} say={say} />}
      {dlg?.kind === 'member' && ref && <MemberDrawer refd={ref} userId={dlg.userId} onClose={() => setDlg(null)} onDone={m => after(m)} />}
      {dlg?.kind === 'profile' && ref && <ProfileBuilder refd={ref} profile={dlg.profile} onClose={() => setDlg(null)} onDone={m => { setDlg(null); after(m) }} />}
      {dlg?.kind === 'retire' && ref && <RetireDialog refd={ref} profile={dlg.profile} onClose={() => setDlg(null)} onDone={m => { setDlg(null); after(m) }} />}
      {dlg?.kind === 'team' && ref && <TeamDialog refd={ref} team={dlg.team} onClose={() => setDlg(null)} onDone={m => { setDlg(null); after(m) }} />}
      {dlg?.kind === 'location' && ref && <LocationDialog refd={ref} loc={dlg.loc} onClose={() => setDlg(null)} onDone={m => { setDlg(null); after(m) }} />}
      {toast}
    </div>
  )
}

// ───────────────────────────── People ─────────────────────────────

function statusOf(p) {
  if (p.membership !== 'active') return [p.membership, STATUS_TONE[p.membership] || 'grey']
  if (p.invitation === 'pending') return ['Invited', 'blue']
  if (p.invitation === 'expired') return ['Invite expired', 'amber']
  if (p.account === 'disabled') return ['Login disabled', 'grey']
  return ['Active', 'green']
}

function PeopleTab({ refd, onOpen }) {
  const [q, setQ] = useState('')
  const [show, setShow] = useState('real')
  const list = refd.people.filter(p => (show === 'all' || !p.sample) &&
    (!q || `${p.name} ${p.email} ${p.profile}`.toLowerCase().includes(q.toLowerCase())))
  const samples = refd.people.filter(p => p.sample).length
  return (
    <section className="ins-card" aria-label="People">
      <div className="ins-row ins-row--wrap ins-toolbar">
        <label className="ins-search"><Icon name="search" size={16} /><span className="sr-only">Search people</span>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, email or profile" /></label>
        {samples > 0 && <label className="ins-select ins-select--light"><span className="sr-only">Show</span>
          <select value={show} onChange={e => setShow(e.target.value)}>
            <option value="real">Logins only</option><option value="all">Include {samples} sample staff</option></select></label>}
      </div>
      {list.length === 0 ? <Empty>No people match.</Empty> : (
        <div className="ins-table-wrap"><table className="ins-table">
          <thead><tr><th>Person</th><th>Access profile</th><th>Can see</th><th>Locations</th><th>Teams</th><th>Status</th><th>Open work</th></tr></thead>
          <tbody>{list.map(p => {
            const [st, tone] = statusOf(p)
            return (
              <tr key={p.user_id} className="ins-click" tabIndex={0} onClick={() => onOpen(p)} onKeyDown={e => { if (e.key === 'Enter') onOpen(p) }}>
                <td><span className="ins-person"><span className="ins-avatar ins-avatar--sm">{initials(p.name)}</span>
                  <span><b>{p.name}</b><small>{p.email}</small></span></span></td>
                <td>{p.profile}{p.is_owner && <> <Pill tone="navy">Owner</Pill></>}{p.sample && <> <Pill>Sample</Pill></>}</td>
                <td>{p.scope_label}</td>
                <td>{names(p.location_ids, refd.locations).join(', ') || (p.record_scope === 'agency' ? 'All' : '—')}</td>
                <td>{names(p.team_ids, refd.teams).join(', ') || '—'}</td>
                <td><Pill tone={tone}>{st}</Pill></td>
                <td>{p.workload}</td>
              </tr>)
          })}</tbody></table></div>)}
      <p className="ins-fine">Sample staff are demo records only — they have no password and can’t sign in. Real members are invited with their own email.</p>
    </section>
  )
}

// ───────────────────────────── Permission grid ─────────────────────────────

function PermGrid({ catalog, value, onChange, ceiling, mode = 'set', base }) {
  // mode 'set': checkboxes → value {mod:[acts]}
  // mode 'override': value {grants, denies}; base = profile permissions
  const allowedByCeiling = (m, a) => ceiling?.owner || (ceiling?.permissions?.[m] || []).includes(a)
  return (
    <div className="ins-perm" role="group" aria-label="Permissions">
      {catalog.modules.map(m => (
        <fieldset key={m.key} className="ins-perm__row">
          <legend>{m.label}</legend>
          <div className="ins-perm__acts">{m.actions.map(a => {
            const id = `perm-${m.key}-${a.key}`
            const blocked = !allowedByCeiling(m.key, a.key)
            if (mode === 'set') {
              const on = (value[m.key] || []).includes(a.key)
              return (
                <label key={a.key} htmlFor={id} className={`ins-perm__act ${blocked ? 'is-blocked' : ''}`} title={blocked ? 'You can’t grant more access than you have.' : ''}>
                  <input id={id} type="checkbox" checked={on} disabled={blocked && !on}
                    onChange={() => {
                      const cur = value[m.key] || []
                      let next = cur.includes(a.key) ? cur.filter(x => x !== a.key) : [...cur, a.key]
                      if (a.key === 'view' && !next.includes('view')) next = []
                      else if (a.key !== 'view' && next.length && !next.includes('view') && m.actions.some(x => x.key === 'view')) next = ['view', ...next]
                      onChange({ ...value, [m.key]: next })
                    }} />
                  {a.label}{a.sensitive && <span className="ins-tag">Sensitive</span>}
                </label>)
            }
            const inBase = (base?.[m.key] || []).includes(a.key)
            const g = (value.grants[m.key] || []).includes(a.key)
            const d = (value.denies[m.key] || []).includes(a.key)
            const state = g ? 'grant' : d ? 'deny' : 'inherit'
            const set = s => {
              const strip = obj => ({ ...obj, [m.key]: (obj[m.key] || []).filter(x => x !== a.key) })
              let grants = strip(value.grants); let denies = strip(value.denies)
              if (s === 'grant') grants = { ...grants, [m.key]: [...(grants[m.key] || []), a.key] }
              if (s === 'deny') denies = { ...denies, [m.key]: [...(denies[m.key] || []), a.key] }
              onChange({ grants, denies })
            }
            return (
              <label key={a.key} className="ins-perm__act ins-perm__act--ovr">
                <span>{a.label}{a.sensitive && <span className="ins-tag">Sensitive</span>}</span>
                <select value={state} onChange={e => set(e.target.value)} aria-label={`${m.label}: ${a.label}`}>
                  <option value="inherit">{inBase ? 'Profile: yes' : 'Profile: no'}</option>
                  {!inBase && <option value="grant" disabled={blocked}>Allow for this person</option>}
                  {inBase && <option value="deny">Block for this person</option>}
                </select>
              </label>)
          })}</div>
        </fieldset>))}
    </div>
  )
}

function EffectivePreview({ data, title = 'What they’ll be able to do' }) {
  if (!data) return <div className="ins-eff ins-muted">Choose a profile to see their access.</div>
  return (
    <aside className="ins-eff" aria-live="polite">
      <h3>{title}</h3>
      <p><b>Sees:</b> {data.scope_label}{data.locations?.length ? ` — ${data.locations.join(', ')}` : ''}</p>
      {data.dashboard && <p><b>Lands on:</b> {DASH_LABEL[data.dashboard] || data.dashboard} dashboard</p>}
      {data.aggregate_only && <p><b>Totals only</b> — no individual client records.</p>}
      <ul className="ins-eff__lines">{data.lines.map(l => (
        <li key={l.module} className={l.allowed ? 'on' : 'off'}>
          <span aria-hidden="true">{l.allowed ? '✓' : '—'}</span> <b>{l.label}</b>{l.allowed && l.actions.length ? `: ${l.actions.join(', ')}` : l.allowed ? '' : ': no access'}
        </li>))}</ul>
      {data.sensitive?.length > 0 && <p className="ins-fine">Sensitive: {data.sensitive.join('; ')}</p>}
    </aside>
  )
}

function usePreview(c, body, enabled) {
  const [data, setData] = useState(null)
  const key = JSON.stringify(body)
  useEffect(() => {
    if (!enabled) { setData(null); return }
    const t = setTimeout(() => { api.post(c.q(`${R}/access/preview`), body).then(setData).catch(() => setData(null)) }, 250)
    return () => clearTimeout(t)
  }, [key, enabled]) // eslint-disable-line react-hooks/exhaustive-deps
  return data
}

function ScopeRadios({ value, onChange, profileScope, ceiling, allowInherit = true }) {
  const rank = { assigned: 0, team: 1, locations: 2, agency: 3 }
  const max = ceiling?.owner ? 3 : rank[ceiling?.record_scope || 'assigned']
  return (
    <fieldset className="ins-radios"><legend>Which records can they see?</legend>
      {allowInherit && <label><input type="radio" checked={!value} onChange={() => onChange(null)} /> Use the profile’s setting{profileScope ? ` (${profileScope})` : ''}</label>}
      {['assigned', 'team', 'locations', 'agency'].map(k => (
        <label key={k} className={rank[k] > max ? 'is-blocked' : ''}>
          <input type="radio" checked={value === k} disabled={rank[k] > max} onChange={() => onChange(k)} />
          <span><b>{{ assigned: 'Assigned only', team: 'My team', locations: 'Permitted locations', agency: 'Entire agency' }[k]}</b><small>{SCOPE_HELP[k]}</small></span>
        </label>))}
    </fieldset>
  )
}

function Checks({ legend, items, value, onChange, disabledIds = [], empty }) {
  return (
    <fieldset className="ins-checks"><legend>{legend}</legend>
      {items.length === 0 ? <span className="ins-muted">{empty || 'None yet.'}</span> : items.map(it => (
        <label key={it.id} className={disabledIds.includes(it.id) ? 'is-blocked' : ''}>
          <input type="checkbox" checked={value.includes(it.id)} disabled={disabledIds.includes(it.id) && !value.includes(it.id)}
            onChange={() => onChange(toggleIn(value, it.id))} /> {it.name}
        </label>))}
    </fieldset>
  )
}

// ───────────────────────────── Invite ─────────────────────────────

function InviteDrawer({ refd, onClose, onDone, say }) {
  const c = useIns()
  const profiles = refd.profiles.filter(p => !p.archived && !p.frozen)
  const ceil = refd.catalog?.ceiling
  const myLocs = ceil?.owner || ceil?.record_scope === 'agency' ? null : (ceil?.location_ids || [])
  const locs = refd.locations.filter(l => !l.archived)
  const [f, setF] = useState({ full_name: '', email: '', profile_id: profiles.find(p => p.template_key === 'producer')?.id || '',
    location_ids: [], team_ids: [], reports_to_user_id: '', record_scope: null })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [result, setResult] = useState(null)
  const prof = profiles.find(p => p.id === f.profile_id)
  const preview = usePreview(c, { profile_id: f.profile_id || null, record_scope: f.record_scope, location_ids: f.location_ids }, !!f.profile_id)
  const submit = async send => {
    setBusy(true); setErr('')
    try {
      const r = await api.post(c.q(`${R}/access/invitations`), { ...f, record_scope: f.record_scope || undefined,
        reports_to_user_id: f.reports_to_user_id || undefined, send })
      setResult(r); onDone(send ? null : 'Invitation saved.')
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  if (result) {
    const d = result.delivery
    return (
      <Modal title="Invitation ready" onClose={onClose} width={620}>
        <div className="ins-form">
          <p><b>{result.invite.name || result.invite.email}</b> — {result.invite.profile} · {result.invite.kind === 'new_login' ? 'new login' : 'existing EvoSys Pro login'}</p>
          {d ? (d.state === 'sent' ? <div className="ins-alert ins-alert--ok" role="status">Email handed to the mail provider for {result.invite.email}.</div>
            : d.state === 'simulated' ? <div className="ins-alert" role="status">Email delivery isn’t connected here, so nothing was sent. Copy the link below and give it to the person directly.</div>
              : <div className="ins-alert" role="alert">Email wasn’t sent: {d.error || d.state}. Copy the link below instead.</div>)
            : <p className="ins-muted">Saved without sending. Copy the link below, or send it later from Invitations.</p>}
          <label>Invitation link (single use, expires {fmtDate(result.invite.expires_at)})
            <div className="ins-row"><input readOnly value={result.link} onFocus={e => e.target.select()} />
              <button type="button" className="ins-btn" onClick={() => copy(result.link, say)}>Copy</button></div></label>
          <p className="ins-fine">This link lets {result.invite.kind === 'new_login' ? 'them choose their own password' : 'them add this agency to the login they already have'}. Share it only with this person. Sending again replaces it with a new link.</p>
          <div className="ins-row ins-row--end"><button className="ins-btn ins-btn--primary" onClick={onClose}>Done</button></div>
        </div>
      </Modal>
    )
  }
  const ok = f.email.includes('@') && f.profile_id && ((f.record_scope || prof?.record_scope) === 'agency' || f.location_ids.length)
  return (
    <Modal title="Invite member" onClose={onClose} width={900}>
      <div className="ins-split">
        <form className="ins-form" onSubmit={e => { e.preventDefault(); submit(true) }}>
          <div className="ins-row ins-row--wrap">
            <label>Full name<input value={f.full_name} onChange={e => setF({ ...f, full_name: e.target.value })} maxLength={120} autoComplete="off" /></label>
            <label>Work email<input type="email" required value={f.email} onChange={e => setF({ ...f, email: e.target.value })} autoComplete="off" /></label>
          </div>
          <label>Access profile<select value={f.profile_id} onChange={e => setF({ ...f, profile_id: e.target.value })} required>
            <option value="">Choose…</option>
            {profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          {prof?.description && <p className="ins-fine">{prof.description}</p>}
          <Checks legend="Locations" items={locs} value={f.location_ids} onChange={v => setF({ ...f, location_ids: v })}
            disabledIds={myLocs ? locs.filter(l => !myLocs.includes(l.id)).map(l => l.id) : []} />
          <Checks legend="Teams (optional)" items={refd.teams.filter(t => !t.archived)} value={f.team_ids} onChange={v => setF({ ...f, team_ids: v })} empty="No teams yet." />
          <label>Reports to (optional)<select value={f.reports_to_user_id} onChange={e => setF({ ...f, reports_to_user_id: e.target.value })}>
            <option value="">No one</option>
            {refd.people.filter(p => !p.sample && p.membership === 'active').map(p => <option key={p.user_id} value={p.user_id}>{p.name}</option>)}</select></label>
          <ScopeRadios value={f.record_scope} onChange={v => setF({ ...f, record_scope: v })} profileScope={prof?.scope_label} ceiling={ceil} />
          {err && <div className="ins-alert" role="alert">{err}</div>}
          <p className="ins-fine">They get their own login. Nobody sees or sets their password, and your sign-in is never shared.</p>
          <div className="ins-row ins-row--end ins-row--wrap">
            <button type="button" className="ins-btn" onClick={onClose}>Cancel</button>
            <button type="button" className="ins-btn" disabled={busy || !ok} onClick={() => submit(false)}>Save invitation</button>
            <button className="ins-btn ins-btn--primary" disabled={busy || !ok}>{busy ? 'Working…' : 'Send invitation'}</button>
          </div>
        </form>
        <EffectivePreview data={preview} />
      </div>
    </Modal>
  )
}

// ───────────────────────────── Member drawer ─────────────────────────────

function MemberDrawer({ refd, userId, onClose, onDone }) {
  const c = useIns()
  const { data: p, error, reload } = useData(() => c.q(`${R}/access/people/${userId}`), [userId])
  const [edit, setEdit] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [off, setOff] = useState(null)       // { action, work, reassign_to, release }
  const manage = c.can('access', 'manage') && p?.can_manage
  useEffect(() => {
    if (p) setEdit({ profile_id: p.profile_id || '', record_scope: p.record_scope_override, location_ids: p.location_ids, team_ids: p.team_ids,
      reports_to_user_id: p.reports_to_user_id || '', ov: { grants: p.grants || {}, denies: p.denies || {} } })
  }, [p])
  const prof = refd.profiles.find(x => x.id === edit?.profile_id)
  const preview = usePreview(c, edit ? { profile_id: edit.profile_id || null, grants: edit.ov.grants, denies: edit.ov.denies,
    record_scope: edit.record_scope, location_ids: edit.location_ids } : {}, !!(manage && edit?.profile_id))
  const save = async () => {
    setBusy(true); setErr('')
    try {
      await api.patch(c.q(`${R}/access/people/${userId}`), { profile_id: edit.profile_id, grants: edit.ov.grants, denies: edit.ov.denies,
        record_scope: edit.record_scope || '', location_ids: edit.location_ids, team_ids: edit.team_ids, reports_to_user_id: edit.reports_to_user_id || '' })
      reload(); onDone(`Access updated for ${p.name}.`)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const status = async (action, extra = {}) => {
    setBusy(true); setErr('')
    try { await api.post(c.q(`${R}/access/people/${userId}/${action}`), extra); setOff(null); reload(); onDone(`${p.name}: ${action === 'reactivate' ? 'reactivated' : action === 'suspend' ? 'suspended' : 'removed'}.`) }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const startOff = async action => {
    setErr('')
    try { const work = await api.get(c.q(`${R}/access/people/${userId}/owned-work`)); setOff({ action, work, reassign_to: '', release: false }) }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <Modal title={p ? p.name : 'Person'} onClose={onClose} width={960}>
      <Err error={error} onRetry={reload} />
      {!p || !edit ? <div className="ins-loading">Loading…</div> : (
        <div className="ins-split">
          <div className="ins-form">
            <p className="ins-muted">{p.email} · {p.profile}{p.sample ? ' · sample staff (no login)' : ''} · <Pill tone={statusOf(p)[1]}>{statusOf(p)[0]}</Pill></p>
            {p.protected && <div className="ins-alert" role="note">{p.protected}</div>}
            {manage ? (<>
              <label>Access profile<select value={edit.profile_id} onChange={e => setEdit({ ...edit, profile_id: e.target.value })}>
                <option value="">Choose…</option>{refd.profiles.filter(x => !x.archived || x.id === edit.profile_id).map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
              <ScopeRadios value={edit.record_scope} onChange={v => setEdit({ ...edit, record_scope: v })} profileScope={prof?.scope_label} ceiling={refd.catalog?.ceiling} />
              <Checks legend="Locations" items={refd.locations.filter(l => !l.archived)} value={edit.location_ids} onChange={v => setEdit({ ...edit, location_ids: v })} />
              <Checks legend="Teams" items={refd.teams.filter(t => !t.archived)} value={edit.team_ids} onChange={v => setEdit({ ...edit, team_ids: v })} empty="No teams yet." />
              <label>Reports to<select value={edit.reports_to_user_id} onChange={e => setEdit({ ...edit, reports_to_user_id: e.target.value })}>
                <option value="">No one</option>{refd.people.filter(x => x.user_id !== p.user_id && !x.sample).map(x => <option key={x.user_id} value={x.user_id}>{x.name}</option>)}</select></label>
              {refd.catalog && prof && <details className="ins-details"><summary>Individual overrides</summary>
                <p className="ins-fine">Allow or block single actions for this person only. You can only allow what you hold yourself.</p>
                <PermGrid catalog={refd.catalog} mode="override" base={prof.permissions} value={edit.ov} ceiling={refd.catalog.ceiling} onChange={ov => setEdit({ ...edit, ov })} />
              </details>}
              {err && <div className="ins-alert" role="alert">{err}</div>}
              <div className="ins-row ins-row--end ins-row--wrap">
                <button className="ins-btn ins-btn--primary" disabled={busy || !edit.profile_id} onClick={save}>Save access</button>
              </div>
              <section className="ins-subcard"><h3>Account</h3>
                {off ? (
                  <div className="ins-form">
                    <p>{p.name} owns {off.work.renewals} open renewal(s), {off.work.appointments} upcoming appointment(s), {off.work.clients} client(s) and {off.work.conversations} unanswered conversation(s).</p>
                    <label>Hand their work to<select value={off.reassign_to} onChange={e => setOff({ ...off, reassign_to: e.target.value, release: false })}>
                      <option value="">Choose a person…</option>
                      {refd.people.filter(x => x.user_id !== p.user_id && x.membership === 'active').map(x => <option key={x.user_id} value={x.user_id}>{x.name}</option>)}</select></label>
                    <label className="ins-check"><input type="checkbox" checked={off.release} onChange={e => setOff({ ...off, release: e.target.checked, reassign_to: '' })} /> Release it to the shared queue instead</label>
                    <p className="ins-fine">History stays on every record. {off.action === 'suspend' ? 'Suspending keeps their work unless you move it.' : 'Removing ends their access to this agency.'}</p>
                    <div className="ins-row ins-row--end">
                      <button className="ins-btn" onClick={() => setOff(null)}>Cancel</button>
                      <button className="ins-btn ins-btn--danger" disabled={busy || (off.action === 'remove' && Object.values(off.work).some(Boolean) && !off.reassign_to && !off.release)}
                        onClick={() => status(off.action, { reassign_to: off.reassign_to || undefined, release_to_queue: off.release })}>
                        {off.action === 'suspend' ? 'Suspend access' : 'Remove from agency'}</button>
                    </div>
                  </div>
                ) : (
                  <div className="ins-row ins-row--wrap">
                    {p.membership === 'active' ? <>
                      <button className="ins-btn" onClick={() => startOff('suspend')}>Suspend</button>
                      <button className="ins-btn ins-btn--danger" onClick={() => startOff('remove')}>Remove</button></>
                      : <button className="ins-btn" disabled={busy} onClick={() => status('reactivate')}>Reactivate</button>}
                  </div>)}
              </section>
            </>) : (
              <p className="ins-muted">{p.sample ? 'Sample staff are part of the demo data and can’t be changed.' : p.is_owner ? 'The agency owner’s access comes from ownership and isn’t managed here.' : 'You can view this person’s access but not change it.'}</p>
            )}
            {p.history?.length > 0 && <section className="ins-subcard"><h3>History</h3><HistoryList items={p.history} /></section>}
          </div>
          <div>
            <EffectivePreview data={manage && preview ? preview : { ...p.effective, locations: names(p.location_ids, refd.locations) }}
              title={manage && preview ? 'After saving, they’ll be able to' : 'What they can do now'} />
            <div className="ins-eff"><h3>Owned work</h3><p>{p.owned_work.renewals} renewals · {p.owned_work.appointments} appointments · {p.owned_work.clients} clients · {p.owned_work.conversations} conversations</p>
              <p className="ins-fine">Last sign-in: {p.last_sign_in ? fmtDateTime(p.last_sign_in, c.settings.timezone) : 'never'}</p></div>
          </div>
        </div>)}
    </Modal>
  )
}

// ───────────────────────────── Profiles ─────────────────────────────

function ProfilesTab({ refd, onEdit, onRetire, say, after }) {
  const c = useIns()
  const manage = c.can('access', 'manage')
  const dup = async p => { try { await api.post(c.q(`${R}/access/profiles/${p.id}/duplicate`), {}); after(`Copied ${p.name}.`) } catch (e) { say(errText(e), 'err') } }
  return (
    <section className="ins-profiles" aria-label="Access profiles">
      {refd.profiles.map(p => (
        <article key={p.id} className="ins-card ins-profile">
          <div className="ins-card__head"><h2>{p.name}</h2>{p.frozen && <Pill>Frozen copy</Pill>}{p.template_key && p.template_key !== 'custom' && <Pill tone="navy">Starter</Pill>}</div>
          {p.description && <p className="ins-muted">{p.description}</p>}
          <dl className="ins-dl">
            <div><dt>Sees</dt><dd>{p.scope_label}</dd></div>
            <div><dt>Dashboard</dt><dd>{DASH_LABEL[p.dashboard] || p.dashboard}</dd></div>
            <div><dt>People</dt><dd>{p.members}</dd></div>
          </dl>
          <p className="ins-fine">{Object.entries(p.permissions).filter(([, a]) => a.length).map(([m]) => refd.catalog?.modules.find(x => x.key === m)?.label || m).join(' · ') || 'No access'}{p.aggregate_only ? ' · totals only' : ''}</p>
          {manage && <div className="ins-row ins-row--wrap">
            {!p.frozen && <button className="ins-btn ins-btn--sm" onClick={() => onEdit(p)}>Edit</button>}
            <button className="ins-btn ins-btn--sm" onClick={() => dup(p)}>Duplicate</button>
            <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={() => onRetire(p)}>Retire</button>
          </div>}
        </article>))}
      <p className="ins-fine">A profile is a reusable set of permissions — not an account and not a licence. Editing a profile that would give its people more access asks first.</p>
    </section>
  )
}

function ProfileBuilder({ refd, profile, onClose, onDone }) {
  const c = useIns()
  const cat = refd.catalog
  const [f, setF] = useState(() => profile ? { name: profile.name, description: profile.description || '', permissions: profile.permissions,
    record_scope: profile.record_scope, dashboard: profile.dashboard, aggregate_only: profile.aggregate_only }
    : { name: '', description: '', permissions: { today: ['view'] }, record_scope: 'assigned', dashboard: 'auto', aggregate_only: false })
  const [err, setErr] = useState('')
  const [widen, setWiden] = useState(null)
  const [busy, setBusy] = useState(false)
  const save = async apply => {
    setBusy(true); setErr('')
    try {
      if (profile) await api.patch(c.q(`${R}/access/profiles/${profile.id}`), { ...f, apply })
      else await api.post(c.q(`${R}/access/profiles`), f)
      onDone(profile ? `Profile ${f.name} saved.` : `Profile ${f.name} created.`)
    } catch (e) {
      if (e.status === 409 && e.detail?.affected) setWiden(e.detail)
      else setErr(errText(e))
    } finally { setBusy(false) }
  }
  const sens = cat.modules.flatMap(m => m.actions.filter(a => a.sensitive && (f.permissions[m.key] || []).includes(a.key)).map(a => `${m.label}: ${a.label}`))
  return (
    <Modal title={profile ? `Edit ${profile.name}` : 'Create access profile'} onClose={onClose} width={900}>
      {widen ? (
        <div className="ins-form">
          <div className="ins-alert" role="alert">{widen.message}</div>
          <p>People on this profile: {widen.affected.map(a => a.name).join(', ')}</p>
          <p className="ins-fine">Adds: {(Array.isArray(widen.added) ? widen.added : []).join('; ') || 'wider record visibility'}</p>
          <div className="ins-row ins-row--end ins-row--wrap">
            <button className="ins-btn" onClick={() => setWiden(null)}>Back</button>
            <button className="ins-btn" disabled={busy} onClick={() => save('freeze')}>Keep them on today’s version</button>
            <button className="ins-btn ins-btn--primary" disabled={busy} onClick={() => save('update')}>Give them the new access</button>
          </div>
        </div>
      ) : (
        <form className="ins-form" onSubmit={e => { e.preventDefault(); save() }}>
          <div className="ins-row ins-row--wrap">
            <label>Name<input required maxLength={80} value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></label>
            <label>Landing dashboard<select value={f.dashboard} onChange={e => setF({ ...f, dashboard: e.target.value })}>
              {cat.dashboards.map(d => <option key={d} value={d}>{DASH_LABEL[d] || d}</option>)}</select></label>
          </div>
          <label>Description<input maxLength={600} value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></label>
          <ScopeRadios value={f.record_scope} onChange={v => setF({ ...f, record_scope: v })} ceiling={cat.ceiling} allowInherit={false} />
          <label className="ins-check"><input type="checkbox" checked={f.aggregate_only} onChange={e => setF({ ...f, aggregate_only: e.target.checked })} /> Totals only (no individual client records)</label>
          <h3>Permissions</h3>
          <PermGrid catalog={cat} value={f.permissions} ceiling={cat.ceiling} onChange={permissions => setF({ ...f, permissions })} />
          {sens.length > 0 && <p className="ins-fine">Sensitive actions included: {sens.join('; ')}</p>}
          {err && <div className="ins-alert" role="alert">{err}</div>}
          <div className="ins-row ins-row--end"><button type="button" className="ins-btn" onClick={onClose}>Cancel</button>
            <button className="ins-btn ins-btn--primary" disabled={busy || !f.name.trim()}>{profile ? 'Save profile' : 'Create profile'}</button></div>
        </form>)}
    </Modal>
  )
}

function RetireDialog({ refd, profile, onClose, onDone }) {
  const c = useIns()
  const [to, setTo] = useState('')
  const [err, setErr] = useState('')
  const go = async () => {
    try { await api.post(c.q(`${R}/access/profiles/${profile.id}/retire`), { move_to: to || undefined }); onDone(`${profile.name} retired.`) }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <Modal title={`Retire ${profile.name}`} onClose={onClose}>
      <div className="ins-form">
        {profile.members > 0 ? <label>{profile.members} {profile.members === 1 ? 'person uses' : 'people use'} this profile. Move them to
          <select value={to} onChange={e => setTo(e.target.value)}><option value="">Choose…</option>
            {refd.profiles.filter(p => p.id !== profile.id && !p.archived).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          : <p>No one uses this profile. Retiring hides it from new invitations; history keeps it.</p>}
        {err && <div className="ins-alert" role="alert">{err}</div>}
        <div className="ins-row ins-row--end"><button className="ins-btn" onClick={onClose}>Cancel</button>
          <button className="ins-btn ins-btn--danger" disabled={profile.members > 0 && !to} onClick={go}>Retire profile</button></div>
      </div>
    </Modal>
  )
}

// ───────────────────────────── Invitations ─────────────────────────────

function InvitationsTab({ tick, after, say }) {
  const c = useIns()
  const { data, error, reload } = useData(() => c.q(`${R}/access/invitations`), [tick])
  const [link, setLink] = useState(null)
  const manage = c.can('access', 'manage')
  const resend = async (inv, send) => {
    try {
      const r = await api.post(c.q(`${R}/access/invitations/${inv.id}/resend`), { send })
      setLink({ email: inv.email, link: r.link, delivery: r.delivery }); reload()
      after(send ? (r.delivery?.state === 'sent' ? `Sent again to ${inv.email}. The old link no longer works.` : `New link made for ${inv.email}; email isn’t connected here, so copy it.`) : `New link made for ${inv.email}. The old link no longer works.`)
    } catch (e) { say(errText(e), 'err') }
  }
  const revoke = async inv => {
    if (!window.confirm(`Revoke the invitation for ${inv.email}?`)) return
    try { await api.post(c.q(`${R}/access/invitations/${inv.id}/revoke`), {}); reload(); after(`Invitation for ${inv.email} revoked.`) } catch (e) { say(errText(e), 'err') }
  }
  return (
    <section className="ins-card" aria-label="Invitations">
      <Err error={error} onRetry={reload} />
      {link && <div className="ins-alert ins-alert--ok" role="status">
        <p>New link for {link.email} (single use):</p>
        <div className="ins-row"><input readOnly value={link.link} onFocus={e => e.target.select()} /><button className="ins-btn" onClick={() => copy(link.link, say)}>Copy</button>
          <button className="ins-btn ins-btn--ghost" onClick={() => setLink(null)}>Hide</button></div></div>}
      {!data ? <div className="ins-loading">Loading…</div> : data.items.length === 0 ? <Empty>No invitations yet. Use “Invite member”.</Empty> : (
        <div className="ins-table-wrap"><table className="ins-table">
          <thead><tr><th>Invited</th><th>Profile</th><th>Login</th><th>Status</th><th>Sent</th><th>Expires</th><th aria-label="Actions" /></tr></thead>
          <tbody>{data.items.map(i => (
            <tr key={i.id}>
              <td><b>{i.name || i.email}</b><small>{i.email} · by {i.invited_by || '—'}</small></td>
              <td>{i.profile}</td>
              <td>{i.kind === 'new_login' ? 'New login' : 'Existing login'}</td>
              <td><Pill tone={STATUS_TONE[i.status]}>{i.status}</Pill></td>
              <td>{i.send_count ? `${i.send_count}× · ${fmtDate(i.last_sent_at)}` : 'Not sent'}</td>
              <td>{i.status === 'accepted' ? `Accepted ${fmtDate(i.accepted_at)}` : fmtDate(i.expires_at)}</td>
              <td>{manage && ['pending', 'expired'].includes(i.status) && <span className="ins-row ins-row--wrap">
                <button className="ins-btn ins-btn--sm" onClick={() => resend(i, true)}>Resend</button>
                <button className="ins-btn ins-btn--sm" onClick={() => resend(i, false)}>New link</button>
                <button className="ins-btn ins-btn--sm ins-btn--ghost" onClick={() => revoke(i)}>Revoke</button></span>}</td>
            </tr>))}</tbody></table></div>)}
      <p className="ins-fine">Links are single use and expire. Resending always makes a new link and cancels the old one. Links are never stored — only a fingerprint.</p>
    </section>
  )
}

// ───────────────────────────── History ─────────────────────────────

function HistoryList({ items }) {
  const c = useIns()
  return (
    <ul className="ins-history">{items.map(h => (
      <li key={h.id}><time>{fmtDateTime(h.at, c.settings.timezone)}</time>
        <span><b>{h.actor || 'System'}</b> {h.label.toLowerCase()}{h.target ? <> — <b>{h.target}</b></> : null}</span></li>))}</ul>
  )
}
function HistoryTab({ tick }) {
  const c = useIns()
  const { data, error, reload } = useData(() => c.q(`${R}/access/history`), [tick])
  return (
    <section className="ins-card" aria-label="Access history"><Err error={error} onRetry={reload} />
      {!data ? <div className="ins-loading">Loading…</div> : data.items.length === 0 ? <Empty>No access changes yet.</Empty> : <HistoryList items={data.items} />}
    </section>
  )
}

// ───────────────────────────── Teams ─────────────────────────────

function TeamsTab({ refd, onEdit }) {
  const c = useIns()
  const manage = c.can('team', 'manage')
  const [arch, setArch] = useState(false)
  const list = refd.teams.filter(t => arch || !t.archived)
  return (
    <section aria-label="Teams">
      <label className="ins-check"><input type="checkbox" checked={arch} onChange={e => setArch(e.target.checked)} /> Show archived teams</label>
      {list.length === 0 ? <Empty>No teams yet.</Empty> : <div className="ins-profiles">{list.map(t => (
        <article key={t.id} className="ins-card ins-profile">
          <div className="ins-card__head"><h2>{t.name}</h2>{t.archived && <Pill>Archived</Pill>}</div>
          <dl className="ins-dl"><div><dt>Lead</dt><dd>{t.lead_name || '—'}</dd></div>
            <div><dt>Locations</dt><dd>{names(t.location_ids, refd.locations).join(', ') || '—'}</dd></div>
            <div><dt>Members</dt><dd>{t.members.length}</dd></div></dl>
          <p className="ins-fine">{t.members.slice(0, 6).map(m => m.name).join(', ')}{t.members.length > 6 ? ` +${t.members.length - 6}` : ''}</p>
          <p className="ins-fine">{t.shared_queue ? 'Shared queue on' : 'No shared queue'} · {t.routing_eligible ? 'In auto-routing' : 'Not routed'}</p>
          {manage && <button className="ins-btn ins-btn--sm" onClick={() => onEdit(t)}>Edit</button>}
        </article>))}</div>}
    </section>
  )
}

function TeamDialog({ refd, team, onClose, onDone }) {
  const c = useIns()
  const [f, setF] = useState(team ? { name: team.name, description: team.description || '', lead_user_id: team.lead_user_id || '', location_ids: team.location_ids,
    member_ids: team.members.map(m => m.user_id), shared_queue: team.shared_queue, routing_eligible: team.routing_eligible }
    : { name: '', description: '', lead_user_id: '', location_ids: [], member_ids: [], shared_queue: true, routing_eligible: true })
  const [err, setErr] = useState('')
  const people = refd.people.filter(p => p.membership === 'active')
  const save = async (extra = {}) => {
    try {
      if (team) await api.patch(c.q(`${R}/access/teams/${team.id}`), { ...f, lead_user_id: f.lead_user_id || '', ...extra })
      else await api.post(c.q(`${R}/access/teams`), { ...f, lead_user_id: f.lead_user_id || undefined })
      onDone(extra.archive ? `${f.name} archived.` : extra.restore ? `${f.name} restored.` : `Team ${f.name} saved.`)
    } catch (e) { setErr(errText(e)) }
  }
  return (
    <Modal title={team ? `Edit ${team.name}` : 'Create team'} onClose={onClose} width={720}>
      <form className="ins-form" onSubmit={e => { e.preventDefault(); save() }}>
        <label>Name<input required maxLength={80} value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></label>
        <label>Description<input maxLength={400} value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></label>
        <label>Team lead<select value={f.lead_user_id} onChange={e => setF({ ...f, lead_user_id: e.target.value })}><option value="">None</option>
          {people.map(p => <option key={p.user_id} value={p.user_id}>{p.name}</option>)}</select></label>
        <Checks legend="Locations" items={refd.locations.filter(l => !l.archived)} value={f.location_ids} onChange={v => setF({ ...f, location_ids: v })} />
        <Checks legend="Members" items={people.map(p => ({ id: p.user_id, name: p.name + (p.sample ? ' (sample)' : '') }))} value={f.member_ids} onChange={v => setF({ ...f, member_ids: v })} />
        <label className="ins-check"><input type="checkbox" checked={f.shared_queue} onChange={e => setF({ ...f, shared_queue: e.target.checked })} /> Members may claim the team’s shared queue</label>
        <label className="ins-check"><input type="checkbox" checked={f.routing_eligible} onChange={e => setF({ ...f, routing_eligible: e.target.checked })} /> Include in automatic routing</label>
        {err && <div className="ins-alert" role="alert">{err}</div>}
        <div className="ins-row ins-row--end ins-row--wrap">
          {team && (team.archived ? <button type="button" className="ins-btn" onClick={() => save({ restore: true })}>Restore</button>
            : <button type="button" className="ins-btn ins-btn--ghost" onClick={() => save({ archive: true })}>Archive team</button>)}
          <button type="button" className="ins-btn" onClick={onClose}>Cancel</button>
          <button className="ins-btn ins-btn--primary" disabled={!f.name.trim()}>Save team</button>
        </div>
      </form>
    </Modal>
  )
}

// ───────────────────────────── Locations ─────────────────────────────

function LocationsTab({ refd, onEdit }) {
  const c = useIns()
  const manage = c.can('locations', 'manage')
  return (
    <section className="ins-card" aria-label="Locations">
      <div className="ins-card__head"><h2>Locations</h2>{manage && <button className="ins-btn ins-btn--primary ins-btn--sm" onClick={() => onEdit(null)}>Add location</button>}</div>
      <div className="ins-table-wrap"><table className="ins-table">
        <thead><tr><th>Office</th><th>Address</th><th>Staff</th><th>Open renewals</th><th>Status</th><th aria-label="Actions" /></tr></thead>
        <tbody>{refd.locations.map(l => (
          <tr key={l.id}>
            <td><b>{l.name}</b>{l.is_primary && <> <Pill tone="navy">Primary</Pill></>}</td>
            <td>{l.verified && l.address ? `${l.address}, ${l.city}, ${l.state}` : [l.city, l.state].filter(Boolean).join(', ') || '—'}{!l.verified && c.isDemo && <small>Sample office</small>}</td>
            <td>{l.staff}</td><td>{l.open_renewals}</td>
            <td><Pill tone={l.archived ? 'grey' : 'green'}>{l.archived ? 'Archived' : 'Active'}</Pill></td>
            <td>{manage && <button className="ins-btn ins-btn--sm" onClick={() => onEdit(l)}>Edit</button>}</td>
          </tr>))}</tbody></table></div>
    </section>
  )
}

function LocationDialog({ refd, loc, onClose, onDone }) {
  const c = useIns()
  const staffNow = refd.people.filter(p => loc && p.location_ids.includes(loc.id)).map(p => p.user_id)
  const [f, setF] = useState(loc ? { name: loc.name, city: loc.city || '', state: loc.state || '', address: loc.address || '', phone: loc.phone || '', timezone: loc.timezone || '', staff_user_ids: staffNow }
    : { name: '', city: '', state: '', address: '', phone: '', timezone: c.settings.timezone, staff_user_ids: [] })
  const [moveTo, setMoveTo] = useState('')
  const [err, setErr] = useState('')
  const save = async (extra = {}) => {
    try {
      if (loc) await api.patch(c.q(`${R}/access/locations/${loc.id}`), { ...f, ...extra })
      else await api.post(c.q(`${R}/access/locations`), f)
      onDone(extra.archive ? `${f.name} archived.` : `${f.name} saved.`)
    } catch (e) { setErr(errText(e)) }
  }
  const t = k => ({ value: f[k], onChange: e => setF({ ...f, [k]: e.target.value }) })
  return (
    <Modal title={loc ? `Edit ${loc.name}` : 'Add location'} onClose={onClose} width={720}>
      <form className="ins-form" onSubmit={e => { e.preventDefault(); save() }}>
        <label>Office name<input required maxLength={80} {...t('name')} /></label>
        <div className="ins-row ins-row--wrap"><label>City<input {...t('city')} /></label><label>State<input maxLength={2} {...t('state')} /></label><label>Phone<input {...t('phone')} /></label></div>
        <label>Street address<input {...t('address')} /></label>
        <Checks legend="Staff at this office" items={refd.people.filter(p => p.membership === 'active').map(p => ({ id: p.user_id, name: p.name + (p.sample ? ' (sample)' : '') }))}
          value={f.staff_user_ids} onChange={v => setF({ ...f, staff_user_ids: v })} />
        {err && <div className="ins-alert" role="alert">{err}</div>}
        <div className="ins-row ins-row--end ins-row--wrap">
          {loc && !loc.archived && !loc.is_primary && <>
            {loc.open_renewals > 0 && <label className="ins-inline">Move open work to<select value={moveTo} onChange={e => setMoveTo(e.target.value)}><option value="">Choose…</option>
              {refd.locations.filter(x => x.id !== loc.id && !x.archived).map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
            <button type="button" className="ins-btn ins-btn--ghost" disabled={loc.open_renewals > 0 && !moveTo} onClick={() => save({ archive: true, move_work_to: moveTo || undefined })}>Archive location</button></>}
          {loc?.archived && <button type="button" className="ins-btn" onClick={() => save({ restore: true })}>Restore</button>}
          <button type="button" className="ins-btn" onClick={onClose}>Cancel</button>
          <button className="ins-btn ins-btn--primary" disabled={!f.name.trim()}>Save location</button>
        </div>
      </form>
    </Modal>
  )
}

// ═════════════════════════════ ROLE DASHBOARDS ═════════════════════════════

const TILE = { reviews_today: ['calendar', 'blue'], followups: ['clock', 'purple'], unassigned: ['users', 'amber'], overdue: ['alert', 'red'],
  my_renewals: ['calendar', 'blue'], replies: ['chat', 'red'], premium: ['alert', 'amber'], booked: ['calcheck', 'green'],
  to_book: ['calendar', 'blue'], callbacks: ['clock', 'purple'], booked_today: ['calcheck', 'green'], confirmations: ['check', 'navy'],
  cohort: ['calendar', 'blue'], completed: ['calcheck', 'green'], confirmed: ['check', 'navy'], unresolved: ['alert', 'amber'] }

function Tiles({ metrics }) {
  return (
    <section className="ins-kpis" aria-label="Key numbers">{metrics.map(m => {
      const [icon, tone] = TILE[m.key] || KPI[m.key] && [KPI[m.key].icon, KPI[m.key].tone] || ['spark', 'blue']
      return (
        <div key={m.key} className={`ins-kpi ins-kpi--${tone}`}>
          <span className="ins-kpi__icon"><Icon name={icon} size={22} /></span>
          <span className="ins-kpi__body"><span className="ins-kpi__label">{m.label}</span><span className="ins-kpi__value">{m.value}</span></span>
        </div>)
    })}</section>
  )
}

function Hero({ sub }) {
  const c = useIns()
  const first = (c.viewer.actor_name || '').split(' ')[0]
  return (
    <div className="ins-hero"><div>
      <h1>{greeting(c.settings.timezone)}{first ? `, ${first}` : ''}.</h1>
      <p className="ins-hero__tag">{sub}</p>
      <p className="ins-muted">{c.settings.display_name} · {c.viewer.profile || DASH_LABEL[c.viewer.dashboard]} · {c.viewer.scope_label}</p>
    </div></div>
  )
}

function ApptList({ items, empty = 'No appointments today.', withDay }) {
  const c = useIns()
  if (!items?.length) return <Empty>{empty}</Empty>
  return (
    <ul className="ins-appts">{items.map(a => (
      <li key={a.id}><span className="ins-appts__time">{withDay ? `${fmtDate(a.starts_at)} ` : ''}{fmtTime(a.starts_at, c.settings.timezone)}</span>
        <button className="ins-appts__body" onClick={() => c.openClient(a.lead_id)}><b>{a.notes || a.title}</b>
          <span>{a.household}{a.product_label ? ` · ${a.product_label}` : ''}{a.location ? ` · ${a.location}` : ''}{a.agent_name ? ` · ${a.agent_name}` : ''}</span></button></li>))}</ul>
  )
}

export function DashboardPage() {
  const c = useIns()
  const { data, error, reload } = useData(() => c.q(`${R}/dashboard`, { location_id: c.locationId }), [c.locationId, c.tick, c.q])
  if (error) return <Err error={error} onRetry={reload} />
  if (!data) return <div className="ins-loading">Loading your dashboard…</div>
  const props = { d: data, reload }
  switch (data.dashboard) {
    case 'admin': return <AdminDash {...props} />
    case 'manager': return <ManagerDash {...props} />
    case 'producer': return <ProducerDash {...props} />
    case 'retention': return <RetentionDash {...props} />
    case 'scheduler': return <SchedulerDash {...props} />
    case 'analyst': return <AnalystDash {...props} />
    default: return <Empty>Your access profile doesn’t include a dashboard yet. Ask the agency owner.</Empty>
  }
}

function AdminDash({ d }) {
  const c = useIns()
  return (<>
    <TodayPage />
    <div className="ins-grid ins-grid--even">
      <section className="ins-card" aria-labelledby="lp-h"><div className="ins-card__head"><h2 id="lp-h"><Icon name="pin" /> Location performance</h2></div>
        <div className="ins-table-wrap"><table className="ins-table ins-table--compact">
          <thead><tr><th>Location</th><th>Due 30 d</th><th>Attention</th><th>Booked</th><th>Renewal rate (90 d)</th></tr></thead>
          <tbody>{d.locations.map(l => <tr key={l.location_id}><td><b>{l.location}</b></td><td>{l.due}</td><td>{l.attention}</td><td>{l.booked}</td>
            <td>{l.renewal_rate == null ? '—' : `${l.renewal_rate}% (${l.confirmed}/${l.decided})`}</td></tr>)}</tbody></table></div></section>
      <section className="ins-card" aria-labelledby="cap-h"><div className="ins-card__head"><h2 id="cap-h"><Icon name="users" /> Team capacity & invitations</h2>
        {c.can('team', 'view') && <Link className="ins-link" to={`${BASE}/team`}>Team & Access <Icon name="arrow" size={14} /></Link>}</div>
        <p><b>{d.capacity.available}</b> of {d.capacity.members} people available · <b>{d.capacity.open_items}</b> open items assigned</p>
        {d.invitations ? (d.invitations.length === 0 ? <p className="ins-muted">No pending invitations.</p> : (
          <ul className="ins-list">{d.invitations.map(i => <li key={i.id}>{i.name || i.email} · {i.profile} · expires {fmtDate(i.expires_at)}</li>)}</ul>)) : null}
      </section>
    </div>
  </>)
}

function ManagerDash({ d, reload }) {
  const c = useIns()
  const [assign, setAssign] = useState(null)
  const [book, setBook] = useState(null)
  const [toast, say] = useToast()
  const after = m => { say(m); reload(); c.refresh() }
  return (
    <div className="ins-today">
      <Hero sub="Your locations’ renewals, reviews and team load." />
      <Tiles metrics={d.metrics} />
      <div className="ins-grid">
        <section className="ins-card ins-card--queue"><div className="ins-card__head"><h2>Needs attention</h2>
          <Link to={`${BASE}/renewals?view=attention`} className="ins-link">All <Icon name="arrow" size={14} /></Link></div>
          <QueueTable rows={d.queue} onAssign={setAssign} onBook={setBook} /></section>
        <section className="ins-card"><div className="ins-card__head"><h2><Icon name="calendar" /> Today’s reviews</h2></div><ApptList items={d.appointments} /></section>
      </div>
      <section className="ins-card"><div className="ins-card__head"><h2><Icon name="users" /> Team workload</h2></div>
        {d.workload.length === 0 ? <Empty>No team members in your locations.</Empty> : (
          <table className="ins-table ins-table--compact"><thead><tr><th>Person</th><th>Open items</th><th>Available</th></tr></thead>
            <tbody>{d.workload.map(p => <tr key={p.user_id}><td>{p.name}<small>{p.title}</small></td><td>{p.items}</td><td>{p.available ? 'Yes' : 'No'}</td></tr>)}</tbody></table>)}
      </section>
      {assign && <AssignDialog row={assign} onClose={() => setAssign(null)} onDone={m => { setAssign(null); after(m) }} />}
      {book && <BookDialog row={book} onClose={() => setBook(null)} onDone={m => { setBook(null); after(m) }} />}
      {toast}
    </div>
  )
}

function ProducerDash({ d, reload }) {
  const c = useIns()
  const [assign, setAssign] = useState(null)
  const [book, setBook] = useState(null)
  const [toast, say] = useToast()
  const after = m => { say(m); reload(); c.refresh() }
  return (
    <div className="ins-today">
      <Hero sub="Your renewals, reviews and replies for today." />
      <Tiles metrics={d.metrics} />
      <div className="ins-grid">
        <section className="ins-card ins-card--queue"><div className="ins-card__head"><h2>My renewals</h2>
          <Link to={`${BASE}/renewals`} className="ins-link">All <Icon name="arrow" size={14} /></Link></div>
          {d.queue.length === 0 ? <Empty>Nothing assigned to you right now.</Empty> : <QueueTable rows={d.queue} onAssign={setAssign} onBook={setBook} />}</section>
        <section className="ins-card"><div className="ins-card__head"><h2><Icon name="calendar" /> Next 7 days</h2></div><ApptList items={d.appointments} withDay empty="No reviews booked this week." /></section>
      </div>
      {d.claimable?.length > 0 && <section className="ins-card"><div className="ins-card__head"><h2>Shared queue you can claim</h2></div>
        <QueueTable rows={d.claimable} onAssign={setAssign} onBook={setBook} /></section>}
      {assign && <AssignDialog row={assign} onClose={() => setAssign(null)} onDone={m => { setAssign(null); after(m) }} />}
      {book && <BookDialog row={book} onClose={() => setBook(null)} onDone={m => { setBook(null); after(m) }} />}
      {toast}
    </div>
  )
}

function RetentionDash({ d, reload }) {
  const c = useIns()
  const [toast, say] = useToast()
  const [esc, setEsc] = useState(null)
  const conv = d.conversation
  const [draft, setDraft] = useState(conv?.draft || '')
  useEffect(() => { setDraft(conv?.draft || '') }, [conv?.renewal_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const after = m => { say(m); reload(); c.refresh() }
  const record = async () => {
    try { await api.post(c.q(`${R}/renewals/${conv.renewal_id}/note`), { body: `Reply prepared: ${draft}`, resolve: true }); after('Reply recorded on the client timeline. Delivery is simulated — nothing was sent.') }
    catch (e) { say(errText(e), 'err') }
  }
  return (
    <div className="ins-today">
      <Hero sub="Client follow-ups, replies and review requests." />
      <Tiles metrics={d.metrics} />
      <div className="ins-grid">
        <section className="ins-card ins-card--queue"><div className="ins-card__head"><h2>Follow-up queue</h2></div>
          {d.queue.length === 0 ? <Empty>No follow-ups need you right now.</Empty> : (
            <div className="ins-table-wrap"><table className="ins-table">
              <thead><tr><th>Client</th><th>Reason</th><th>Producer</th><th>Renews</th><th aria-label="Actions" /></tr></thead>
              <tbody>{d.queue.map(r => <tr key={r.id}>
                <td><button className="ins-linkbtn" onClick={() => c.openClient(r.lead_id)}><b>{r.household}</b></button><small>{r.product_label}</small></td>
                <td>{r.attention?.label || r.attention?.code}</td><td>{r.assigned_name || 'Unassigned'}</td><td>{fmtDate(r.renewal_date)}</td>
                <td>{c.can('renewals', 'edit') && !c.readOnly && <button className="ins-btn ins-btn--sm" onClick={() => setEsc(r)}>Escalate</button>}</td></tr>)}</tbody></table></div>)}
        </section>
        <section className="ins-card" aria-labelledby="conv-h"><div className="ins-card__head"><h2 id="conv-h"><Icon name="chat" /> Conversation</h2></div>
          {!conv ? <Empty>No replies waiting.</Empty> : (<div className="ins-form">
            <p><b>{conv.household}</b> · {fmtDateTime(conv.at, c.settings.timezone)}</p>
            <blockquote className="ins-quote">{conv.message}</blockquote>
            {c.can('conversations', 'reply') && !c.readOnly ? (<>
              <label>Suggested reply (edit before using)<textarea rows={4} value={draft} onChange={e => setDraft(e.target.value)} maxLength={1000} /></label>
              <div className="ins-row ins-row--wrap"><button className="ins-btn ins-btn--primary" disabled={!draft.trim()} onClick={record}>Record reply</button>
                <button className="ins-btn" onClick={() => setEsc(d.queue.find(r => r.id === conv.renewal_id) || { id: conv.renewal_id, household: conv.household })}>Escalate to producer</button></div>
              <p className="ins-fine">Message delivery is simulated in this workspace. Recording the reply notes it on the timeline; nothing reaches the client.</p>
            </>) : <p className="ins-fine">You can read conversations but not reply.</p>}
          </div>)}
        </section>
      </div>
      <section className="ins-card"><div className="ins-card__head"><h2><Icon name="calendar" /> Today’s reviews</h2></div><ApptList items={d.appointments} /></section>
      {esc && <EscalateDialog row={esc} producers={d.producers || []} onClose={() => setEsc(null)} onDone={m => { setEsc(null); after(m) }} />}
      {toast}
    </div>
  )
}

function EscalateDialog({ row, producers, onClose, onDone }) {
  const c = useIns()
  const [to, setTo] = useState(row.assigned_user_id || '')
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const go = async e => {
    e.preventDefault()
    try { await api.post(c.q(`${R}/renewals/${row.id}/escalate`), { to_user_id: to || undefined, note }); onDone(`Escalated ${row.household}.`) }
    catch (er) { setErr(errText(er)) }
  }
  return (
    <Modal title={`Escalate ${row.household}`} onClose={onClose}>
      <form className="ins-form" onSubmit={go}>
        <label>To<select value={to} onChange={e => setTo(e.target.value)}><option value="">Current producer</option>
          {producers.map(p => <option key={p.user_id} value={p.user_id}>{p.name}</option>)}</select></label>
        <label>What do they need to know?<textarea required rows={3} maxLength={1000} value={note} onChange={e => setNote(e.target.value)} /></label>
        <p className="ins-fine">Policy and coverage questions go to a producer. Escalating flags the renewal and sets a follow-up.</p>
        {err && <div className="ins-alert" role="alert">{err}</div>}
        <div className="ins-row ins-row--end"><button type="button" className="ins-btn" onClick={onClose}>Cancel</button><button className="ins-btn ins-btn--primary" disabled={!note.trim()}>Escalate</button></div>
      </form>
    </Modal>
  )
}

function SchedulerDash({ d, reload }) {
  const c = useIns()
  const [toast, say] = useToast()
  const [slotFor, setSlotFor] = useState(null)
  const after = m => { say(m); reload(); c.refresh() }
  const confirm = async a => {
    try { await api.post(c.q(`${R}/appointments/${a.id}/status`), { status: 'confirmed' }); after(`Confirmed ${a.household}.`) } catch (e) { say(errText(e), 'err') }
  }
  const canBook = c.can('appointments', 'schedule') && !c.readOnly
  return (
    <div className="ins-today">
      <Hero sub="Reviews to book, callbacks and confirmations." />
      <p className="ins-muted ins-note" role="note"><Icon name="shield" size={15} /> {d.notice}</p>
      <Tiles metrics={d.metrics} />
      <div className="ins-grid">
        <section className="ins-card ins-card--queue"><div className="ins-card__head"><h2>Scheduling queue</h2></div>
          {d.queue.length === 0 ? <Empty>Nothing to book right now.</Empty> : (
            <div className="ins-table-wrap"><table className="ins-table">
              <thead><tr><th>Client</th><th>Task</th><th>Producer</th><th>Renews</th><th aria-label="Actions" /></tr></thead>
              <tbody>{d.queue.map(r => <tr key={r.id}>
                <td><b>{r.household}</b><small>{r.product_label}{r.location_name ? ` · ${r.location_name}` : ''}</small></td>
                <td>{r.task}{r.when ? <small>{fmtDateTime(r.when, c.settings.timezone)}</small> : null}</td>
                <td>{r.producer || 'Unassigned'}</td><td>{fmtDate(r.renewal_date)}</td>
                <td>{canBook && <button className="ins-btn ins-btn--sm ins-btn--primary" onClick={() => setSlotFor(r)}>Book</button>}</td></tr>)}</tbody></table></div>)}
        </section>
        <section className="ins-card"><div className="ins-card__head"><h2><Icon name="check" /> Confirmations due</h2></div>
          {d.confirmations.length === 0 ? <Empty>No confirmations due in the next two days.</Empty> : (
            <ul className="ins-appts">{d.confirmations.map(a => <li key={a.id}><span className="ins-appts__time">{fmtDate(a.starts_at)} {fmtTime(a.starts_at, c.settings.timezone)}</span>
              <span className="ins-appts__body"><b>{a.household}</b><span>{a.agent_name}{a.location ? ` · ${a.location}` : ''}</span></span>
              {c.can('appointments', 'edit') && !c.readOnly && <button className="ins-btn ins-btn--sm" onClick={() => confirm(a)}>Confirm</button>}</li>)}</ul>)}
        </section>
      </div>
      <section className="ins-card"><div className="ins-card__head"><h2><Icon name="calendar" /> Today</h2></div><ApptList items={d.appointments} /></section>
      {slotFor && <SlotDialog row={slotFor} onClose={() => setSlotFor(null)} onDone={m => { setSlotFor(null); after(m) }} />}
      {toast}
    </div>
  )
}

function SlotDialog({ row, onClose, onDone }) {
  const c = useIns()
  const [day, setDay] = useState(() => new Date().toLocaleDateString('en-CA', { timeZone: c.settings.timezone }))
  const [medium, setMedium] = useState('phone')
  const [err, setErr] = useState('')
  const { data, error } = useData(() => c.q(`${R}/renewals/${row.id}/availability`, { day }), [row.id, day])
  const book = async s => {
    try { await api.post(c.q(`${R}/renewals/${row.id}/book-review`), { starts_at: s.starts_at, medium }); onDone(`Review booked for ${row.household} at ${s.label}.`) }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <Modal title={`Book a review — ${row.household}`} onClose={onClose} width={560}>
      <div className="ins-form">
        <div className="ins-row ins-row--wrap"><label>Day<input type="date" value={day} onChange={e => setDay(e.target.value)} /></label>
          <label>How<select value={medium} onChange={e => setMedium(e.target.value)}><option value="phone">Phone</option><option value="in_person">In person</option><option value="video">Video</option></select></label></div>
        <Err error={error} />
        {!data ? <div className="ins-loading">Checking availability…</div> : !data.agent_id ? <p className="ins-muted">{data.note}</p> : (<>
          <p>Open times with <b>{data.agent_name}</b>:</p>
          {data.slots.length === 0 ? <Empty>No open times that day.</Empty> : <div className="ins-slots">{data.slots.map(s => <button key={s.starts_at} className="ins-btn ins-btn--sm" onClick={() => book(s)}>{s.label}</button>)}</div>}
        </>)}
        {err && <div className="ins-alert" role="alert">{err}</div>}
      </div>
    </Modal>
  )
}

function AnalystDash({ d }) {
  const funnel = d.report?.funnel || []
  const max = Math.max(1, ...funnel.map(f => f.count))
  const maxL = Math.max(1, ...(d.reviews_completed_by_location || []).map(x => x.count))
  return (
    <div className="ins-today">
      <Hero sub="Renewal results across the locations you can see." />
      <Tiles metrics={d.metrics} />
      {d.aggregate_only && <p className="ins-fine">Totals only. Individual client records aren’t part of this profile.</p>}
      <div className="ins-grid ins-grid--even">
        <section className="ins-card"><div className="ins-card__head"><h2><Icon name="chart" /> Renewal funnel</h2></div>
          <ul className="ins-bars">{funnel.map(f => <li key={f.key}><span>{f.label}</span><span className="ins-bar"><span style={{ width: `${(100 * f.count) / max}%` }} /></span><b>{f.count}</b></li>)}</ul></section>
        <section className="ins-card"><div className="ins-card__head"><h2><Icon name="pin" /> Reviews completed by location</h2></div>
          <ul className="ins-bars">{(d.reviews_completed_by_location || []).map(x => <li key={x.location}><span>{x.location}</span><span className="ins-bar"><span style={{ width: `${(100 * x.count) / maxL}%` }} /></span><b>{x.count}</b></li>)}</ul></section>
      </div>
    </div>
  )
}

// ═════════════════════════════ MY SETTINGS ═════════════════════════════

export function MySettingsPage() {
  const c = useIns()
  const me = c.me
  const [prefs, setPrefs] = useState(me.prefs || {})
  const [toast, say] = useToast()
  const save = async () => {
    try { const r = await api.patch(c.q(`${R}/me/prefs`), { landing: prefs.landing || '', compact: !!prefs.compact }); setPrefs(r.prefs); say('Saved.'); c.reloadWorkspace() }
    catch (e) { say(errText(e), 'err') }
  }
  const allowed = Object.entries(me.permissions || {}).filter(([, a]) => a.length)
  return (
    <div>
      <div className="ins-pagehead"><h1>My settings</h1></div>
      <div className="ins-grid ins-grid--even">
        <section className="ins-card"><h2>My access</h2>
          <dl className="ins-dl ins-dl--stack">
            <div><dt>Name</dt><dd>{me.actor_name || me.name}</dd></div>
            <div><dt>Email</dt><dd>{me.email}</dd></div>
            <div><dt>Access profile</dt><dd>{me.is_owner ? 'Agency owner' : me.profile || '—'}</dd></div>
            <div><dt>Can see</dt><dd>{me.scope_label}{me.location_names?.length ? ` — ${me.location_names.join(', ')}` : ''}</dd></div>
            <div><dt>Status</dt><dd>{me.status || 'active'}</dd></div>
          </dl>
          <h3>What I can do</h3>
          <ul className="ins-list">{allowed.map(([m, a]) => <li key={m}><b>{m}</b>: {a.join(', ')}</li>)}</ul>
          <p className="ins-fine">Only the agency owner or an administrator can change your access.</p>
        </section>
        <section className="ins-card"><h2>Preferences</h2>
          {me.configured ? (<div className="ins-form">
            <label>Open this page when I sign in<select value={prefs.landing || ''} onChange={e => setPrefs({ ...prefs, landing: e.target.value })}>
              <option value="">My dashboard</option>{(me.nav || []).filter(n => n.key !== 'me' && n.key !== 'today').map(n => <option key={n.key} value={n.key}>{n.label}</option>)}</select></label>
            <label className="ins-check"><input type="checkbox" checked={!!prefs.compact} onChange={e => setPrefs({ ...prefs, compact: e.target.checked })} /> Compact tables</label>
            <div className="ins-row"><button className="ins-btn ins-btn--primary" onClick={save}>Save preferences</button></div>
          </div>) : <p className="ins-muted">Owners manage their account from the main account settings.</p>}
          <h3>Sign-in</h3>
          <p><a className="ins-link" href="/change-password">Change my password</a></p>
          <p className="ins-fine">Your password is yours alone. EvoSys Pro staff and agency admins can’t see it.</p>
        </section>
      </div>
      {toast}
    </div>
  )
}

// ═════════════════════════════ ACCEPT INVITATION ═════════════════════════════

export function AcceptInvitePage() {
  const [params] = useSearchParams()
  const nav = useNavigate()
  const token = params.get('token') || ''
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (!token) { setErr('This invitation link is incomplete.'); return }
    if (!(localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token'))) return
    api.post('/agency-invites/preview', { token }).then(setInfo).catch(e => setErr(errText(e)))
  }, [token])
  const accept = async () => {
    setBusy(true); setErr('')
    try { const r = await api.post('/agency-invites/accept', { token }); setWorkspaceContext(r.organization_id); nav(r.path || '/program/insurance', { replace: true }) }
    catch (e) { setErr(errText(e)); setBusy(false) }
  }
  const usable = info && info.status === 'pending'
  const signedIn = !!(localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token'))
  if (!signedIn) return <Navigate to={`/login?next=${encodeURIComponent('/agency-invite?token=' + token)}`} replace />
  return (
    <div className="ins ins-gate"><div className="ins-card ins-gate__card">
      {!info && !err && <h1>Checking your invitation…</h1>}
      {err && !info && <><h1>This invitation can’t be used</h1><p className="ins-muted">{err}</p><p className="ins-fine">Ask the agency for a new invitation.</p></>}
      {info && <>
        <h1>Join {info.agency}</h1>
        <p>This invitation is for <b>{info.email}</b>.{info.expires_at ? ` It expires ${fmtDate(info.expires_at)}.` : ''}</p>
        {!usable ? <p className="ins-alert" role="alert">This invitation is {info.status}. Ask the agency for a new one.</p> : <>
          <p className="ins-muted">You’ll keep your existing login. The agency decides what you can see there.</p>
          {err && <div className="ins-alert" role="alert">{err}</div>}
          <div className="ins-row"><button className="ins-btn ins-btn--primary" disabled={busy} onClick={accept}>Accept and open the agency</button>
            <a className="ins-btn" href="/workspaces">Not now</a></div></>}
      </>}
    </div></div>
  )
}
