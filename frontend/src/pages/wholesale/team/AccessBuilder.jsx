/* Wholesale > Team: custom access (#439). Invite people, build reusable access
 * profiles, fine-tune one person's access, preview exactly what they will be
 * able to do, and see every change. Every rule is enforced by the server
 * (/wholesale/team/access/*); this screen only shows and asks. */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { Drawer, Search, Tabs } from '../ds/ds'

const msg = (e) => (e && (typeof e.detail === 'string' ? e.detail : e.message)) || 'That did not save.'
const total = (w) => Object.values(w || {}).reduce((a, b) => a + b, 0)

// ── permission builder ──────────────────────────────────────────────────────
export function PermissionBuilder({ catalog, value, onChange, ceiling, limitTo, readOnly, idPrefix = 'pb', takeAway = false }) {
  const [q, setQ] = useState('')
  const groups = useMemo(() => {
    const out = {}
    for (const m of catalog.modules) {
      if (limitTo && !(limitTo[m.key] || []).length) continue
      const hay = `${m.label} ${m.description} ${m.group}`.toLowerCase()
      if (q && !hay.includes(q.toLowerCase())) continue
      ;(out[m.group] = out[m.group] || []).push(m)
    }
    return out
  }, [catalog, q, limitTo])
  const has = (mod, act) => (value[mod] || []).includes(act)
  const allowed = (mod, act) => (!ceiling || (ceiling[mod] || []).includes(act)) && (!limitTo || (limitTo[mod] || []).includes(act))
  function toggle(mod, act) {
    const cur = new Set(value[mod] || [])
    if (cur.has(act)) {
      cur.delete(act)
      if (act === 'view') cur.clear()                       // no view means nothing in the module
    } else {
      cur.add(act)
      // Granting an action needs seeing the module. Taking one away must not
      // also take away seeing it - that would remove the whole module.
      if (!takeAway && act !== 'view' && (!limitTo || (limitTo[mod] || []).includes('view'))) cur.add('view')
    }
    const next = { ...value }
    if (cur.size) next[mod] = Array.from(cur); else delete next[mod]
    onChange(next)
  }
  function setRow(mod, acts, on) {
    const next = { ...value }
    const pick = acts.filter((a) => allowed(mod, a))
    if (on && pick.length) next[mod] = pick; else delete next[mod]
    onChange(next)
  }
  return (
    <div className="wa-builder">
      <Search value={q} onChange={setQ} placeholder="Search permissions" label="Search permissions" />
      {Object.keys(groups).length === 0 ? <p className="evo-muted">No permissions match.</p> : null}
      {Object.entries(groups).map(([g, mods]) => (
        <fieldset key={g} className="wa-group">
          <legend className="wa-legend">{g}</legend>
          {mods.map((m) => {
            const acts = limitTo ? m.actions.filter((a) => (limitTo[m.key] || []).includes(a)) : m.actions
            const all = acts.length > 0 && acts.every((a) => has(m.key, a) || !allowed(m.key, a))
              && acts.some((a) => has(m.key, a))
            return (
              <div className="wa-mod" key={m.key}>
                <div className="wa-mod__head">
                  <div>
                    <div className="wa-mod__name">{m.label}</div>
                    <div className="evo-muted evo-small">{m.description}</div>
                  </div>
                  {!readOnly ? (
                    <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm"
                            onClick={() => setRow(m.key, acts, !all)}>{all ? 'Clear' : 'All'}</button>
                  ) : null}
                </div>
                <div className="wa-acts" role="group" aria-label={`${m.label} actions`}>
                  {acts.map((a) => {
                    const id = `${idPrefix}-${m.key}-${a}`
                    const can = allowed(m.key, a)
                    return (
                      <label key={a} htmlFor={id} className={`wa-act${!can ? ' is-off' : ''}`}
                             title={!can ? 'You can only give access you have yourself.' : undefined}>
                        <input id={id} type="checkbox" checked={has(m.key, a)} disabled={readOnly || !can}
                               onChange={() => toggle(m.key, a)} /> {m.action_labels[a]}
                      </label>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </fieldset>
      ))}
    </div>
  )
}

export function ScopePicker({ catalog, value, onChange, allowInherit, inheritLabel, id = 'wa-scope', ceilingScope }) {
  const rank = { assigned: 0, team: 1, workspace: 2 }
  return (
    <label className="wt-lab" htmlFor={id}>Which records they see
      <select id={id} className="wt-select" value={value || ''} onChange={(e) => onChange(e.target.value || null)}>
        {allowInherit ? <option value="">{inheritLabel || "Same as the profile"}</option> : null}
        {catalog.scopes.map((s) => (
          <option key={s.key} value={s.key} disabled={ceilingScope && rank[s.key] > rank[ceilingScope]}>{s.label}</option>
        ))}
      </select>
      <span className="evo-muted evo-small">Applies to deals, properties, sellers, appointments, canvass routes and assignments.
        Other areas are all-or-nothing.</span>
    </label>
  )
}

export function EffectivePreview({ request }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const key = JSON.stringify(request)
  useEffect(() => {
    let live = true
    if (!request || (!request.profile_id && !request.permissions && !request.grants)) { setData(null); return undefined }
    const t = setTimeout(() => {
      api.post('/wholesale/team/access/preview', request).then((d) => live && (setData(d), setErr(null)))
        .catch((e) => live && setErr(msg(e)))
    }, 250)
    return () => { live = false; clearTimeout(t) }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <p className="wt-err">{err}</p>
  if (!data) return <p className="evo-muted evo-small">Choose a profile or permissions to see exactly what they will be able to do.</p>
  const on = data.modules.filter((m) => m.actions.length)
  return (
    <div className="wa-preview" aria-live="polite">
      <h3 className="wt-h3">What they will be able to do</h3>
      {on.length ? (
        <ul className="wa-preview__list">{on.map((m) => <li key={m.module}><strong>{m.label}:</strong> {m.summary}</li>)}</ul>
      ) : <p className="evo-muted">Nothing yet - they would see an empty Wholesale workspace.</p>}
      <p className="evo-small"><strong>Records:</strong> {data.record_scope_label}</p>
      <p className="evo-small evo-muted"><strong>Never:</strong> {data.never.join(', ')}.</p>
    </div>
  )
}

// ── invite ──────────────────────────────────────────────────────────────────
export function InviteTab({ catalog, dir, reload }) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [profileId, setProfileId] = useState('')
  const [scope, setScope] = useState(null)
  const [boss, setBoss] = useState('')
  const [send, setSend] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [done, setDone] = useState(null)
  const profiles = dir.profiles
  async function submit(e) {
    e.preventDefault()
    setBusy(true); setErr(null); setDone(null)
    try {
      const out = await api.post('/wholesale/team/invites', {
        email: email.trim(), full_name: name.trim() || null, profile_id: profileId, record_scope: scope,
        reports_to_user_id: boss || null, send, base_url: window.location.origin })
      setDone(out); setEmail(''); setName(''); setSend(false); reload()
    } catch (ex) { setErr(msg(ex)) } finally { setBusy(false) }
  }
  return (
    <div className="wa-two">
      <form className="evo-panel wt-add" onSubmit={submit}>
        <h2 className="wt-h2">Invite someone</h2>
        <p className="evo-muted evo-small" style={{ marginTop: 0 }}>New people get a one-time link to pick their own password.
          Someone who already has a login signs in as usual and accepts. Nobody ever shares a password.</p>
        <div className="wt-grid">
          <label className="wt-lab" htmlFor="wa-email">Email
            <input id="wa-email" type="email" required className="wt-input" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" /></label>
          <label className="wt-lab" htmlFor="wa-name">Name (for a new login)
            <input id="wa-name" className="wt-input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" /></label>
        </div>
        <div className="wt-grid">
          <label className="wt-lab" htmlFor="wa-prof">Access profile
            <select id="wa-prof" required className="wt-select" value={profileId} onChange={(e) => setProfileId(e.target.value)}>
              <option value="">Choose…</option>
              {profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            {!profiles.length ? <span className="evo-small">No profiles yet - create one on the Access profiles tab first.</span> : null}
          </label>
          <label className="wt-lab" htmlFor="wa-boss">Reports to
            <select id="wa-boss" className="wt-select" value={boss} onChange={(e) => setBoss(e.target.value)}>
              <option value="">Nobody</option>
              {dir.members.map((m) => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
            </select></label>
        </div>
        <ScopePicker catalog={catalog} value={scope} onChange={setScope} allowInherit id="wa-inv-scope"
                     ceilingScope={catalog.your_ceiling.record_scope} />
        <label className="wt-check"><input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} />
          Email the invitation now from this brand's address (otherwise copy the link and send it yourself)</label>
        {err ? <p className="wt-err" role="alert">{err}</p> : null}
        <div><button className="evo-btn evo-btn--primary" type="submit" disabled={busy || !email.trim() || !profileId}>
          {busy ? 'Creating…' : 'Create invitation'}</button></div>
        {done ? (
          <div className="wt-link" role="status">
            <p><strong>Invitation ready for {done.invite.email}.</strong>{' '}
              {done.delivery ? (done.delivery.state === 'sent' ? 'It was emailed.' : `Email not sent: ${done.delivery.error}`) : 'Not emailed - send this link yourself.'}
              {' '}It expires in 7 days{done.invite.kind === 'existing_login' ? ' and must be accepted by that login' : ''}.</p>
            <div className="wt-link__row">
              <input className="wt-input" readOnly value={done.link} aria-label="Invitation link" onFocus={(e) => e.target.select()} />
              <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => navigator.clipboard?.writeText(done.link)}>Copy</button>
            </div>
          </div>
        ) : null}
      </form>
      <section className="evo-panel">
        <EffectivePreview request={profileId ? { profile_id: profileId, record_scope: scope } : null} />
      </section>
      <PendingInvites dir={dir} reload={reload} />
    </div>
  )
}

function PendingInvites({ dir, reload }) {
  const [busy, setBusy] = useState(null)
  const [sure, setSure] = useState(null)
  const [note, setNote] = useState(null)
  const live = dir.invites.filter((i) => i.status === 'pending' || i.status === 'expired')
  async function act(i, what) {
    setBusy(i.id); setNote(null)
    try {
      const out = await api.post(`/wholesale/team/invites/${i.id}/${what}`, what === 'resend' ? { base_url: window.location.origin } : {})
      setNote(what === 'resend' ? { id: i.id, link: out.link } : null); reload()
    } catch (ex) { setNote({ id: i.id, err: msg(ex) }) } finally { setBusy(null); setSure(null) }
  }
  return (
    <section className="evo-panel wa-span">
      <h2 className="wt-h2">Invitations ({live.length} open)</h2>
      {!dir.invites.length ? <p className="evo-muted">No invitations yet.</p> : (
        <table className="wt-table">
          <thead><tr><th>Email</th><th>Profile</th><th>Status</th><th>Expires</th><th><span className="evo-sr">Actions</span></th></tr></thead>
          <tbody>{dir.invites.map((i) => (
            <tr key={i.id}>
              <td data-label="Email">{i.email}<div className="evo-muted evo-small">{i.kind === 'existing_login' ? 'Has a login' : 'New login'}</div></td>
              <td data-label="Profile">{i.profile ? i.profile.name : 'Individual permissions'}</td>
              <td data-label="Status"><span className={`evo-tag ${i.status === 'accepted' ? 'evo-tag--ok' : i.status === 'pending' ? 'evo-tag--info' : ''}`}>{i.status}</span></td>
              <td data-label="Expires">{i.status === 'pending' && i.expires_at ? new Date(i.expires_at).toLocaleDateString() : '—'}</td>
              <td data-label="" className="wt-actions">
                {(i.status === 'pending' || i.status === 'expired') ? (
                  <>
                    <button className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy === i.id} onClick={() => act(i, 'resend')}>New link</button>
                    {sure === i.id
                      ? <button className="evo-btn evo-btn--danger evo-btn--sm" disabled={busy === i.id} onClick={() => act(i, 'cancel')}>Yes, cancel</button>
                      : <button className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setSure(i.id)}>Cancel</button>}
                  </>
                ) : null}
                {note && note.id === i.id ? (note.err ? <div className="wt-err">{note.err}</div>
                  : <div className="wt-link__row"><input className="wt-input" readOnly value={note.link} aria-label="New invitation link" onFocus={(e) => e.target.select()} />
                    <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => navigator.clipboard?.writeText(note.link)}>Copy</button></div>) : null}
              </td>
            </tr>))}</tbody>
        </table>
      )}
    </section>
  )
}

// ── profiles ────────────────────────────────────────────────────────────────
export function ProfilesTab({ catalog, dir, reload }) {
  const [editing, setEditing] = useState(null)        // {id?, name, permissions, record_scope, description}
  const [template, setTemplate] = useState('')
  const [err, setErr] = useState(null)
  const [retiring, setRetiring] = useState(null)
  function startNew() {
    const t = catalog.templates.find((x) => x.key === template) || catalog.templates.find((x) => x.key === 'custom')
    setEditing({ name: t.key === 'custom' ? '' : t.name, template_key: t.key, permissions: t.permissions, record_scope: t.record_scope, description: t.description })
  }
  async function dup(p) {
    setErr(null)
    try { await api.post(`/wholesale/team/access/profiles/${p.id}/duplicate`, {}); reload() } catch (ex) { setErr(msg(ex)) }
  }
  return (
    <div className="wt-main">
      <section className="evo-panel">
        <h2 className="wt-h2">Access profiles ({dir.profiles.length})</h2>
        <p className="evo-muted evo-small" style={{ marginTop: 0 }}>Reusable sets of permissions. Starters are only a starting point - change anything.</p>
        <div className="wa-row">
          <label className="wt-lab" htmlFor="wa-tpl">Start from
            <select id="wa-tpl" className="wt-select" value={template} onChange={(e) => setTemplate(e.target.value)}>
              <option value="">Nothing (custom)</option>
              {catalog.templates.filter((t) => t.key !== 'custom').map((t) => <option key={t.key} value={t.key}>{t.name}</option>)}
            </select></label>
          <div style={{ alignSelf: 'end' }}><button type="button" className="evo-btn evo-btn--primary" onClick={startNew}>New profile</button></div>
        </div>
        {err ? <p className="wt-err" role="alert">{err}</p> : null}
        {dir.profiles.length ? (
          <table className="wt-table">
            <thead><tr><th>Profile</th><th>People</th><th>Records</th><th><span className="evo-sr">Actions</span></th></tr></thead>
            <tbody>{dir.profiles.map((p) => (
              <tr key={p.id}>
                <td data-label="Profile"><div className="wt-who">{p.name}</div><div className="evo-muted evo-small">{p.description}</div></td>
                <td data-label="People">{p.members}</td>
                <td data-label="Records">{(catalog.scopes.find((s) => s.key === p.record_scope) || {}).label}</td>
                <td data-label="" className="wt-actions">
                  <button className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => setEditing({ ...p })}>Edit</button>
                  {/* #439: see the dashboard this profile gets, before giving it to anyone */}
                  <Link className="evo-btn evo-btn--ghost evo-btn--sm" to={`/wholesale/home?preview=${p.id}`}>Preview dashboard</Link>
                  <button className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => dup(p)}>Duplicate</button>
                  <button className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setRetiring(p)}>Retire</button>
                </td>
              </tr>))}</tbody>
          </table>
        ) : <p className="evo-muted">No profiles yet.</p>}
      </section>
      {editing ? <ProfileEditor catalog={catalog} initial={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload() }} /> : null}
      {retiring ? <RetireProfile p={retiring} profiles={dir.profiles} onClose={() => setRetiring(null)} onDone={() => { setRetiring(null); reload() }} /> : null}
    </div>
  )
}

function ProfileEditor({ catalog, initial, onClose, onSaved }) {
  const [p, setP] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [impact, setImpact] = useState(null)
  async function save(extra = {}) {
    setBusy(true); setErr(null)
    try {
      const body = { name: p.name, permissions: p.permissions, record_scope: p.record_scope, description: p.description, ...extra }
      if (p.id) await api.put(`/wholesale/team/access/profiles/${p.id}`, body)
      else await api.post('/wholesale/team/access/profiles', { ...body, template_key: p.template_key })
      onSaved()
    } catch (ex) {
      if (ex.status === 409 && ex.detail && ex.detail.needs_confirmation) setImpact(ex.detail)
      else setErr(msg(ex))
    } finally { setBusy(false) }
  }
  return (
    <Drawer open wide title={p.id ? `Edit ${initial.name}` : 'New access profile'} sub="Tick what this profile may do. Seeing a module comes with any other action in it."
            onClose={onClose}
            footer={impact ? null : (
              <div className="wa-foot">
                <button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Cancel</button>
                <button type="button" className="evo-btn evo-btn--primary" disabled={busy || !p.name.trim()} onClick={() => save()}>{busy ? 'Saving…' : 'Save profile'}</button>
              </div>)}>
      {impact ? (
        <div className="wt-warn" role="alert">
          <p><strong>{impact.message}</strong></p>
          <p className="evo-small">Adds: {impact.impact.adds.join(', ') || 'wider record visibility'}</p>
          <div className="wa-foot">
            <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setImpact(null)}>Go back</button>
            <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={() => save({ keep_existing_members: true })}>Keep them on their current access</button>
            <button type="button" className="evo-btn evo-btn--danger" disabled={busy} onClick={() => save({ confirm: true })}>Give all {impact.impact.members} the new access</button>
          </div>
        </div>
      ) : (
        <div className="wa-editor">
          <div className="wt-grid">
            <label className="wt-lab" htmlFor="wa-pname">Profile name
              <input id="wa-pname" className="wt-input" value={p.name} onChange={(e) => setP({ ...p, name: e.target.value })} /></label>
            <ScopePicker catalog={catalog} value={p.record_scope} onChange={(v) => setP({ ...p, record_scope: v || 'assigned' })} id="wa-pscope"
                         ceilingScope={catalog.your_ceiling.record_scope} />
          </div>
          <label className="wt-lab" htmlFor="wa-pdesc">Description
            <input id="wa-pdesc" className="wt-input" value={p.description || ''} onChange={(e) => setP({ ...p, description: e.target.value })} /></label>
          {err ? <p className="wt-err" role="alert">{err}</p> : null}
          <PermissionBuilder catalog={catalog} value={p.permissions || {}} onChange={(v) => setP({ ...p, permissions: v })}
                             ceiling={catalog.your_ceiling.permissions} idPrefix="prof" />
          <EffectivePreview request={{ permissions: p.permissions || {}, record_scope: p.record_scope }} />
        </div>
      )}
    </Drawer>
  )
}

function RetireProfile({ p, profiles, onClose, onDone }) {
  const [to, setTo] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  async function go() {
    setBusy(true); setErr(null)
    try { await api.post(`/wholesale/team/access/profiles/${p.id}/retire`, { move_to: to || null }); onDone() } catch (ex) { setErr(msg(ex)) } finally { setBusy(false) }
  }
  return (
    <Drawer open title={`Retire ${p.name}?`} sub="Nobody loses access silently: anyone on it moves to the profile you pick." onClose={onClose}
            footer={<div className="wa-foot"><button type="button" className="evo-btn evo-btn--ghost" onClick={onClose}>Cancel</button>
              <button type="button" className="evo-btn evo-btn--danger" disabled={busy || (p.members > 0 && !to)} onClick={go}>Retire</button></div>}>
      <p>{p.members} {p.members === 1 ? 'person uses' : 'people use'} this profile.</p>
      {p.members > 0 ? (
        <label className="wt-lab" htmlFor="wa-move">Move them to
          <select id="wa-move" className="wt-select" value={to} onChange={(e) => setTo(e.target.value)}>
            <option value="">Choose…</option>
            {profiles.filter((x) => x.id !== p.id).map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
          </select></label>
      ) : null}
      {err ? <p className="wt-err" role="alert">{err}</p> : null}
    </Drawer>
  )
}

// ── one person ──────────────────────────────────────────────────────────────
// Read-only view of someone's access, for logins nobody can edit here (your
// own, an owner/admin, or someone above a delegated team admin). No controls:
// the server refuses every change to these people, so none are offered.
export function ReadOnlyAccess({ catalog, acc }) {
  const perms = acc.permissions || {}
  const rows = catalog.modules.filter((m) => (perms[m.key] || []).length)
  const scope = (catalog.scopes.find((s) => s.key === acc.record_scope) || {}).label
  return (
    <div className="wa-editor" data-testid="wa-readonly">
      <div className="wt-warn" role="note"><strong>{acc.protected.kind === 'owner' ? 'Protected owner access' : 'View only'}</strong>
        <p>{acc.protected.reason}</p></div>
      <p className="evo-small"><strong>Records:</strong> {acc.mode === 'admin' ? 'Everything in this workspace' : (scope || '—')}</p>
      {rows.length ? (
        <ul className="wa-preview__list">{rows.map((m) => (
          <li key={m.key}><strong>{m.label}</strong>
            <span className="evo-muted evo-small"> {(perms[m.key] || []).filter((a) => m.action_labels[a]).map((a) => m.action_labels[a]).join(', ')}</span></li>))}</ul>
      ) : <p className="evo-muted">No Wholesale access right now.</p>}
    </div>
  )
}

export function MemberAccess({ catalog, dir, member, onClose, onSaved }) {
  const [acc, setAcc] = useState(null)
  const [profileId, setProfileId] = useState('')
  const [grants, setGrants] = useState({})
  const [denies, setDenies] = useState({})
  const [scope, setScope] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [confirm, setConfirm] = useState(null)       // 'suspend' | 'remove'
  const [reassign, setReassign] = useState('')
  useEffect(() => {
    api.get(`/wholesale/team/members/${member.user_id}/access`).then((a) => {
      setAcc(a); setProfileId(a.profile ? a.profile.id : ''); setGrants(a.grants || {}); setDenies(a.denies || {}); setScope(a.record_scope_override || null)
    }).catch((e) => setErr(msg(e)))
  }, [member.user_id])
  const prof = dir.profiles.find((p) => p.id === profileId)
  async function save() {
    setBusy(true); setErr(null)
    try { await api.put(`/wholesale/team/members/${member.user_id}/access`, { profile_id: profileId || null, grants, denies, record_scope: scope }); onSaved() }
    catch (ex) { setErr(msg(ex)) } finally { setBusy(false) }
  }
  async function act(what) {
    setBusy(true); setErr(null)
    try {
      if (what === 'remove') await api.post(`/wholesale/team/members/${member.user_id}/remove`, { reassign_to: reassign || null })
      else await api.post(`/wholesale/team/members/${member.user_id}/${what}`, {})
      onSaved()
    } catch (ex) { setErr(msg(ex)) } finally { setBusy(false); setConfirm(null) }
  }
  const work = acc ? acc.owned_work || {} : {}
  return (
    <Drawer open wide title={member.name} sub={member.email} onClose={onClose}
            footer={acc && !confirm && !acc.protected ? (
              <div className="wa-foot">
                <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setConfirm('remove')}>Remove from team</button>
                {acc.status === 'active'
                  ? <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setConfirm('suspend')}>Suspend</button>
                  : <button type="button" className="evo-btn evo-btn--secondary" disabled={busy} onClick={() => act('reactivate')}>Reactivate</button>}
                <button type="button" className="evo-btn evo-btn--primary" disabled={busy || (!profileId && !Object.keys(grants).length)} onClick={save}>{busy ? 'Saving…' : 'Save access'}</button>
              </div>) : null}>
      {!acc ? (err ? <p className="wt-err">{err}</p> : <p className="evo-muted">Loading…</p>) : acc.protected ? (
        <ReadOnlyAccess catalog={catalog} acc={acc} />
      ) : confirm ? (
        <div className="wt-warn" role="alert">
          {confirm === 'suspend'
            ? <p><strong>Suspend {member.name}?</strong> They lose Wholesale on their next click. Their access settings and work are kept.</p>
            : <>
              <p><strong>Remove {member.name} from the team?</strong> Their login stays (it may belong to other workspaces); they can no longer open this one.</p>
              <p className="evo-small">They have {total(work)} assigned record{total(work) === 1 ? '' : 's'} here
                {total(work) ? ` (${Object.entries(work).filter(([, n]) => n).map(([k, n]) => `${n} ${k.replace('_', ' ')}`).join(', ')})` : ''}.</p>
              {total(work) ? (
                <label className="wt-lab" htmlFor="wa-reassign">Give their work to
                  <select id="wa-reassign" className="wt-select" value={reassign} onChange={(e) => setReassign(e.target.value)}>
                    <option value="">Leave it assigned to them</option>
                    {dir.members.filter((m) => m.user_id !== member.user_id).map((m) => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
                  </select></label>) : null}
            </>}
          {err ? <p className="wt-err">{err}</p> : null}
          <div className="wa-foot">
            <button type="button" className="evo-btn evo-btn--ghost" onClick={() => setConfirm(null)}>Go back</button>
            <button type="button" className="evo-btn evo-btn--danger" disabled={busy} onClick={() => act(confirm)}>{confirm === 'suspend' ? 'Yes, suspend' : 'Yes, remove'}</button>
          </div>
        </div>
      ) : (
        <div className="wa-editor">
          {acc.mode === 'legacy' ? <p className="wt-warn">They currently have the older “{acc.legacy_role}” login. Saving below switches them to exactly the access you choose here.</p> : null}
          <div className="wt-grid">
            <label className="wt-lab" htmlFor="wa-mprof">Access profile
              <select id="wa-mprof" className="wt-select" value={profileId} onChange={(e) => setProfileId(e.target.value)}>
                <option value="">None (only the extra permissions below)</option>
                {dir.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select></label>
            <ScopePicker catalog={catalog} value={scope} onChange={setScope} allowInherit id="wa-mscope"
                         inheritLabel={prof ? `Same as the profile (${(catalog.scopes.find((s) => s.key === prof.record_scope) || {}).label})` : 'Only records assigned to them'}
                         ceilingScope={catalog.your_ceiling.record_scope} />
          </div>
          {err ? <p className="wt-err" role="alert">{err}</p> : null}
          <details className="wa-details">
            <summary>Give {member.name.split(' ')[0]} extra permissions ({Object.keys(grants).length})</summary>
            <PermissionBuilder catalog={catalog} value={grants} onChange={setGrants} ceiling={catalog.your_ceiling.permissions} idPrefix="grant" />
          </details>
          {prof ? (
            <details className="wa-details">
              <summary>Take away part of the profile ({Object.keys(denies).length})</summary>
              <p className="evo-muted evo-small">Tick what to take away. Taking away View removes the whole module.</p>
              <PermissionBuilder catalog={catalog} value={denies} onChange={setDenies} limitTo={prof.permissions} idPrefix="deny" takeAway />
            </details>
          ) : null}
          <EffectivePreview request={{ profile_id: profileId || null, permissions: profileId ? null : {}, grants, denies, record_scope: scope }} />
        </div>
      )}
    </Drawer>
  )
}

// ── history ─────────────────────────────────────────────────────────────────
export function HistoryTab() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.get('/wholesale/team/history').then((d) => setRows(d.items)).catch((e) => setErr(msg(e))) }, [])
  return (
    <section className="evo-panel">
      <h2 className="wt-h2">Access history</h2>
      {err ? <p className="wt-err">{err}</p> : null}
      {!rows && !err ? <p className="evo-muted">Loading…</p> : null}
      {rows && !rows.length ? <p className="evo-muted">No access changes yet.</p> : null}
      {rows && rows.length ? (
        <table className="wt-table">
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>About</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id}>
              <td data-label="When">{r.at ? new Date(r.at).toLocaleString() : ''}</td>
              <td data-label="Who">{r.actor || '—'}</td>
              <td data-label="What"><span className={r.action === 'escalation_denied' ? 'wt-err' : ''}>{r.label}</span>{r.note ? <div className="evo-muted evo-small">{r.note}</div> : null}</td>
              <td data-label="About">{r.target_type === 'user' ? r.target : r.target_type.replace('_', ' ')}</td>
            </tr>))}</tbody>
        </table>
      ) : null}
    </section>
  )
}

// ── directory ───────────────────────────────────────────────────────────────
export function Directory({ catalog, dir, reload }) {
  const [open, setOpen] = useState(null)
  const [q, setQ] = useState('')
  const scopeLabel = (k) => (catalog.scopes.find((s) => s.key === k) || {}).label || '—'
  const people = dir.members.filter((m) => !q || `${m.name} ${m.email}`.toLowerCase().includes(q.toLowerCase()))
  return (
    <section className="evo-panel">
      <div className="wa-row wa-row--between">
        <h2 className="wt-h2">The team ({dir.members.length})</h2>
        <Search value={q} onChange={setQ} placeholder="Find a person" label="Find a person" />
      </div>
      {people.length ? (
        <table className="wt-table">
          <thead><tr><th>Person</th><th>Access</th><th>Records</th><th>Assigned work</th><th>Last active</th><th><span className="evo-sr">Actions</span></th></tr></thead>
          <tbody>{people.map((m) => (
            <tr key={m.user_id}>
              <td data-label="Person"><div className="wt-who">{m.name}</div><div className="evo-muted evo-small">{m.email}</div></td>
              <td data-label="Access">{m.protected && m.protected.kind === 'owner'
                ? <span className="evo-tag evo-tag--info">Protected owner access</span>
                : m.access.mode === 'custom'
                ? <><span className="evo-tag evo-tag--info">{m.access.profile ? m.access.profile.name : 'Individual'}</span>
                  {Object.keys(m.access.grants || {}).length || Object.keys(m.access.denies || {}).length ? <span className="evo-muted evo-small"> + changes</span> : null}</>
                : <span className="evo-tag">{m.role_label}</span>}</td>
              <td data-label="Records">{m.access.mode === 'custom' ? scopeLabel(m.access.record_scope) : 'Everything'}</td>
              <td data-label="Assigned work">{total(m.owned_work)}</td>
              <td data-label="Last active">{m.last_active_at ? new Date(m.last_active_at).toLocaleDateString() : 'Never signed in'}</td>
              <td data-label="" className="wt-actions">
                {m.protected
                  ? <button className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setOpen(m)}>View access</button>
                  : <button className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => setOpen(m)}>Manage access</button>}
              </td>
            </tr>))}</tbody>
        </table>
      ) : <p className="evo-muted">{dir.members.length ? 'Nobody matches.' : 'Nobody is on the team yet. Invite someone to get started.'}</p>}
      {dir.suspended.length ? (
        <>
          <h3 className="wt-h3">Suspended ({dir.suspended.length})</h3>
          <ul className="wa-plain">{dir.suspended.map((s) => (
            <li key={s.user_id}><strong>{s.name}</strong> <span className="evo-muted evo-small">{s.email}</span>{' '}
              <button className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setOpen({ ...s, role_label: 'Suspended' })}>{s.protected ? 'View' : 'Manage'}</button></li>))}</ul>
        </>
      ) : null}
      {open ? <MemberAccess catalog={catalog} dir={dir} member={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload() }} /> : null}
    </section>
  )
}

// ── the tabs ────────────────────────────────────────────────────────────────
export function useAccess() {
  const [catalog, setCatalog] = useState(null)
  const [dir, setDir] = useState(null)
  const [err, setErr] = useState(null)
  const reload = useCallback(() => {
    api.get('/wholesale/team/directory').then(setDir).catch((e) => setErr(e.status === 403
      ? 'Only an admin (or someone allowed to manage the team) can open team access.' : msg(e)))
  }, [])
  useEffect(() => {
    api.get('/wholesale/team/access/catalog').then(setCatalog).catch((e) => setErr(msg(e)))
    reload()
  }, [reload])
  return { catalog, dir, err, reload }
}

export const ACCESS_TABS = [
  { key: 'team', label: 'Team' }, { key: 'invite', label: 'Invite' },
  { key: 'profiles', label: 'Access profiles' }, { key: 'history', label: 'History' },
]

export { Tabs }
