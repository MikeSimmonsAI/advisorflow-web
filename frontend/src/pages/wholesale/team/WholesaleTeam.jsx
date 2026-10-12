/* Wholesale > Team. Who is on the Wholesale team, what kind of login each
 * person has, and who they report to (owner -> managers -> team leads ->
 * field specialists and canvassers). Server: /wholesale/team. New logins are
 * made with /admin/users: the person gets a one-time link and picks their own
 * password - nobody else ever knows it. */
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../../api/client'
import { EvoApp, PageHead } from '../ds/ds'
import '../ds/evo-pages.css'
import './team.css'

function TreeNode({ m, kids, depth }) {
  const mine = kids[m.user_id] || []
  return (
    <li className="wt-node">
      <div className="wt-node__row" style={{ paddingLeft: depth * 20 }}>
        {depth ? <span className="wt-node__elbow" aria-hidden="true">└</span> : null}
        <span className="wt-node__name">{m.name}</span>
        <span className={`evo-tag ${m.wholesale_only ? 'evo-tag--info' : ''}`}>{m.role_label}</span>
        {mine.length ? <span className="evo-muted evo-small">{mine.length} report{mine.length === 1 ? '' : 's'}</span> : null}
      </div>
      {mine.length ? <ul className="wt-tree">{mine.map((k) => <TreeNode key={k.user_id} m={k} kids={kids} depth={depth + 1} />)}</ul> : null}
    </li>
  )
}

function Tree({ members }) {
  const { roots, kids } = useMemo(() => {
    const ids = new Set(members.map((m) => m.user_id))
    const k = {}
    const r = []
    members.forEach((m) => {
      if (m.reports_to_user_id && ids.has(m.reports_to_user_id)) (k[m.reports_to_user_id] ||= []).push(m)
      else r.push(m)
    })
    return { roots: r, kids: k }
  }, [members])
  if (!members.length) return <p className="evo-muted">Nobody is on the team yet.</p>
  return <ul className="wt-tree">{roots.map((m) => <TreeNode key={m.user_id} m={m} kids={kids} depth={0} />)}</ul>
}

function RoleSelect({ id, roles, value, onChange, disabled }) {
  return (
    <select id={id} className="wt-select" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
      {roles.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
    </select>
  )
}

function BossSelect({ id, members, self, value, onChange, disabled }) {
  return (
    <select id={id} className="wt-select" value={value || ''} disabled={disabled} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">Nobody (top of the tree)</option>
      {members.filter((m) => m.user_id !== self).map((m) => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
    </select>
  )
}

function AddPerson({ data, onSaved }) {
  const [mode, setMode] = useState('new')
  const [pick, setPick] = useState('')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState('field_specialist')
  const [boss, setBoss] = useState(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [link, setLink] = useState(null)
  const firstEver = !data.lock_on
  const roleHelp = data.roles.find((r) => r.key === role)?.help
  const canSave = !busy && (!firstEver || confirm) && (mode === 'existing' ? !!pick : !!(name.trim() && email.trim()))

  async function save(e) {
    e.preventDefault()
    setBusy(true); setErr(null); setLink(null)
    try {
      let userId = pick
      let setup = null
      if (mode === 'new') {
        const u = await api.post('/admin/users', { email: email.trim(), full_name: name.trim(), role: 'advisor' })
        userId = u.id
        setup = u.setup_url || null
      }
      const t = await api.post('/wholesale/team/members', { user_id: userId, role, reports_to_user_id: boss, confirm_lock: confirm })
      onSaved(t)
      setLink(setup ? { url: setup, who: name.trim() } : null)
      setPick(''); setName(''); setEmail(''); setConfirm(false)
    } catch (ex) {
      setErr(ex.message || 'That did not save.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="evo-panel wt-add" onSubmit={save}>
      <h2 className="wt-h2">Add someone to the team</h2>
      <div className="wt-tabs" role="radiogroup" aria-label="New or existing login">
        <label><input type="radio" name="wt-mode" checked={mode === 'new'} onChange={() => setMode('new')} /> New login</label>
        <label><input type="radio" name="wt-mode" checked={mode === 'existing'} onChange={() => setMode('existing')}
                      disabled={!data.not_on_team.length} /> Someone who already has a login</label>
      </div>
      {mode === 'new' ? (
        <div className="wt-grid">
          <label className="wt-lab" htmlFor="wt-name">Full name
            <input id="wt-name" className="wt-input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></label>
          <label className="wt-lab" htmlFor="wt-email">Email
            <input id="wt-email" type="email" className="wt-input" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" /></label>
        </div>
      ) : (
        <label className="wt-lab" htmlFor="wt-pick">Person
          <select id="wt-pick" className="wt-select" value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Choose…</option>
            {data.not_on_team.map((p) => <option key={p.user_id} value={p.user_id}>{p.name} ({p.email})</option>)}
          </select></label>
      )}
      <div className="wt-grid">
        <label className="wt-lab" htmlFor="wt-role">Kind of login
          <RoleSelect id="wt-role" roles={data.roles} value={role} onChange={setRole} /></label>
        <label className="wt-lab" htmlFor="wt-boss">Reports to
          <BossSelect id="wt-boss" members={data.members} value={boss} onChange={setBoss} /></label>
      </div>
      {roleHelp ? <p className="evo-muted evo-small">{roleHelp}</p> : null}
      {firstEver ? (
        <div className="wt-warn">
          <strong>This is the first person on the team.</strong> From now on only the people on this team can open
          Wholesale. You are added automatically with full access.
          {data.not_on_team.length ? <> These people will lose Wholesale until you add them: {data.not_on_team.map((p) => p.name).join(', ')}.</> : null}
          <label className="wt-check"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> I understand</label>
        </div>
      ) : null}
      {err ? <p className="wt-err" role="alert">{err}</p> : null}
      <button className="evo-btn evo-btn--primary" type="submit" disabled={!canSave}>{busy ? 'Saving…' : 'Add to team'}</button>
      {link ? (
        <div className="wt-link">
          <p><strong>Send this link to {link.who}.</strong> It works once and expires; they pick their own password with it.</p>
          <div className="wt-link__row">
            <input className="wt-input" readOnly value={link.url} aria-label="One-time setup link" onFocus={(e) => e.target.select()} />
            <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm"
                    onClick={() => navigator.clipboard?.writeText(link.url)}>Copy</button>
          </div>
        </div>
      ) : null}
    </form>
  )
}

function MemberRow({ m, data, onSaved }) {
  const [role, setRole] = useState(m.role)
  const [boss, setBoss] = useState(m.reports_to_user_id || null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [sure, setSure] = useState(false)
  useEffect(() => { setRole(m.role); setBoss(m.reports_to_user_id || null) }, [m.role, m.reports_to_user_id])
  const changed = role !== m.role || (boss || null) !== (m.reports_to_user_id || null)

  async function run(fn) {
    setBusy(true); setErr(null)
    try { onSaved(await fn()) } catch (ex) { setErr(ex.message || 'That did not save.') } finally { setBusy(false); setSure(false) }
  }
  return (
    <tr>
      <td data-label="Person"><div className="wt-who">{m.name}</div><div className="evo-muted evo-small">{m.email}</div></td>
      <td data-label="Kind of login"><RoleSelect id={`r-${m.user_id}`} roles={data.roles} value={role} onChange={setRole} disabled={busy} /></td>
      <td data-label="Reports to"><BossSelect id={`b-${m.user_id}`} members={data.members} self={m.user_id} value={boss} onChange={setBoss} disabled={busy} /></td>
      <td data-label="" className="wt-actions">
        {changed ? <button className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy}
          onClick={() => run(() => api.post('/wholesale/team/members', { user_id: m.user_id, role, reports_to_user_id: boss }))}>Save</button> : null}
        {sure
          ? <button className="evo-btn evo-btn--danger evo-btn--sm" disabled={busy}
              onClick={() => run(() => api.delete(`/wholesale/team/members/${m.user_id}`))}>Yes, take off</button>
          : <button className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => setSure(true)}>Take off team</button>}
        {err ? <div className="wt-err" role="alert">{err}</div> : null}
      </td>
    </tr>
  )
}

function FieldRules() {
  const [pct, setPct] = useState(null)
  const [raise, setRaise] = useState(10)
  const [comm, setComm] = useState('5')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.get('/wholesale/team/rules').then((r) => { setPct(r.offer_limit_pct); setRaise(r.raise_limit_pct ?? 100); setComm(String(r.commission_pct ?? 0)) }).catch(() => setPct(null))
  }, [])
  if (pct === null) return null
  async function save() {
    setBusy(true); setErr(null); setMsg(null)
    try {
      const r = await api.put('/wholesale/team/rules', { offer_limit_pct: Number(pct), raise_limit_pct: Number(raise), commission_pct: Number(comm) })
      setPct(r.offer_limit_pct); setRaise(r.raise_limit_pct); setComm(String(r.commission_pct)); setMsg(r.explained)
    }
    catch (e) { setErr(e.message || 'That did not save.') } finally { setBusy(false) }
  }
  return (
    <section className="evo-panel">
      <h2 className="wt-h2">Offer limit and commission</h2>
      <p className="evo-muted evo-small" style={{ marginTop: 0 }}>Reps never see your investor % or fee - only the most they may offer.
        Anything above this comes to you to approve. Their first offer on a visit is their opening offer.</p>
      <div className="wt-grid">
        <label className="wt-lab" htmlFor="wt-limit">Most a rep can offer without asking
          <select id="wt-limit" className="wt-select" value={pct} onChange={(e) => setPct(e.target.value)}>
            {[80, 85, 90, 95, 100, 105].map((p) => <option key={p} value={p}>{p}% of the max offer{p === 100 ? ' (the formula, exactly)' : ''}</option>)}
          </select></label>
        <label className="wt-lab" htmlFor="wt-raise">How far a rep can raise their opening offer
          <select id="wt-raise" className="wt-select" value={raise} onChange={(e) => setRaise(e.target.value)}>
            <option value={0}>Not at all - every raise comes to me</option>
            {[5, 10, 15, 20].map((p) => <option key={p} value={p}>Up to {p}% above their opening offer</option>)}
            <option value={100}>No raise rule (only the limit above)</option>
          </select></label>
        <label className="wt-lab" htmlFor="wt-comm">Commission (% of the assignment fee, everyone)
          <input id="wt-comm" className="wt-select" inputMode="decimal" value={comm} onChange={(e) => setComm(e.target.value)} /></label>
        <div style={{ alignSelf: 'end' }}><button type="button" className="evo-btn evo-btn--primary" disabled={busy} onClick={save}>Save</button></div>
      </div>
      {msg ? <p className="evo-small" role="status">{msg}</p> : null}
      {err ? <p className="wt-err" role="alert">{err}</p> : null}
    </section>
  )
}

export default function WholesaleTeam() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    api.get('/wholesale/team').then(setData).catch((ex) => setErr(ex.status === 403
      ? 'Only an admin can manage the Wholesale team.' : (ex.message || 'The team could not be loaded.')))
  }, [])
  return (
    <EvoApp world="operations">
      <PageHead world="operations" eyebrow="Wholesale" title="Team"
                sub="Who can open Wholesale, what kind of login they have, and who they report to." />
      {err ? <p className="wt-err">{err}</p> : null}
      {!data && !err ? <p className="evo-muted">Loading the team…</p> : null}
      {data ? (
        <div className="wt-layout">
          <div className="wt-main">
            <section className="evo-panel">
              <h2 className="wt-h2">The team ({data.members.length})</h2>
              {data.members.length ? (
                <table className="wt-table">
                  <thead><tr><th>Person</th><th>Kind of login</th><th>Reports to</th><th><span className="evo-sr">Actions</span></th></tr></thead>
                  <tbody>{data.members.map((m) => <MemberRow key={m.user_id} m={m} data={data} onSaved={setData} />)}</tbody>
                </table>
              ) : <p className="evo-muted">Nobody is on the team yet. Until the first person is added, everyone in the workspace can open Wholesale, the same as today.</p>}
              <p className="evo-muted evo-small">Taking someone off the team keeps their login and their work; they just can no longer open Wholesale. Anyone who reported to them moves up a level.</p>
            </section>
            <AddPerson data={data} onSaved={setData} />
            <FieldRules />
          </div>
          <aside className="evo-panel wt-side">
            <h2 className="wt-h2">Who reports to whom</h2>
            <Tree members={data.members} />
            <h3 className="wt-h3">Kinds of login</h3>
            <dl className="wt-roles">{data.roles.map((r) => <div key={r.key}><dt>{r.label}</dt><dd>{r.help}</dd></div>)}</dl>
          </aside>
        </div>
      ) : null}
    </EvoApp>
  )
}
