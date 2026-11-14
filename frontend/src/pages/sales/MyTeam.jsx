import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fetchObjectUrl } from '../../api/client'

/**
 * MY TEAM — /sales/my-team (Back Office)
 *
 * The EvoSys Pro sales team: Sr EVP → Area Sales Managers → reps. Owners and
 * God Mode see everyone; the Sr EVP sees his own team. Every button here is
 * re-checked on the server (app/services/sales_team.py) - hiding a button is
 * convenience, not security.
 *
 * Onboarding files are listed (and openable) only for owners and God Mode.
 * The Sr EVP gets done / missing, never file names or contents.
 */

const card = { background: '#fff', border: '1px solid #dce5ea', borderRadius: 12, padding: 16, marginBottom: 14 }
const btn = { border: '1px solid #c9d7df', background: '#fff', borderRadius: 8, padding: '6px 10px', cursor: 'pointer', fontSize: 13, marginRight: 6, marginTop: 6 }
const primary = { ...btn, background: '#087f83', color: '#fff', borderColor: '#087f83' }
const pill = (bg, fg) => ({ display: 'inline-block', fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 6, background: bg, color: fg, marginLeft: 6 })
const SEAT_NAME = { sevp: 'Sr EVP', asm: 'Area Sales Manager', rep: 'Salesperson', owner: 'Owner' }

function errText(e) { return (e && (e.detail || e.message)) || 'Something went wrong.' }

function Status({ p }) {
  if (p.departed_at) return <span style={pill('#f1e4e4', '#8a2b2b')}>Left the company</span>
  if (!p.active) return <span style={pill('#fff4df', '#79551a')}>Suspended</span>
  const o = p.onboarding
  if (!o) return null
  if (o.status === 'approved') return <span style={pill('#e5f6f1', '#087f83')}>Approved</span>
  if (o.status === 'submitted') return <span style={pill('#e8eefc', '#2b4a8a')}>Waiting for approval</span>
  if (o.status === 'returned') return <span style={pill('#fff4df', '#79551a')}>Sent back for a fix</span>
  return <span style={pill('#f3f6f8', '#607486')}>Onboarding: {o.missing.length} missing</span>
}

export default function MyTeam() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [form, setForm] = useState({ full_name: '', email: '', seat: '', manager_id: '' })
  const [open, setOpen] = useState(null)

  const load = useCallback(async () => {
    try { setD(await api.get('/sales-team/roster', { noOrgContext: true })); setErr('') } catch (e) { setErr(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  if (err && !d) return <div style={{ padding: 24 }}><div style={card}><h2>My Team</h2><p>{err}</p></div></div>
  if (!d) return <div style={{ padding: 24 }}>Loading your team…</div>

  const r = d.rights
  const people = d.people
  const byId = Object.fromEntries(people.map(p => [p.user_id, p]))
  const managers = people.filter(p => (p.seat === 'asm' || p.seat === 'sevp') && p.active && !p.departed_at)
  const seats = r.invite_seats || []

  const act = async (key, fn, done) => {
    setBusy(key); setErr(''); setNote('')
    try { await fn(); if (done) setNote(done); await load() } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }

  const invite = (e) => {
    e.preventDefault()
    act('invite', async () => {
      const res = await api.post('/sales-team/invite', { ...form, manager_id: form.manager_id || null, base_url: window.location.origin })
      if (res.email_sent) setNote('Invite sent to ' + res.email + '.')
      else setNote('The email could not be sent. Send this one-time link to ' + res.email + ' yourself: ' + (res.setup_url || '(they already have a login - ask them to sign in)'))
      setForm({ full_name: '', email: '', seat: '', manager_id: '' })
    })
  }

  const docUrl = (p, item) => '/sales-team/onboarding/' + p.onboarding_id + '/documents/' + item.document_id
  const openDoc = async (p, item) => {
    try { window.open(await fetchObjectUrl(docUrl(p, item) + '?inline=true'), '_blank', 'noopener') } catch (e) { setErr(errText(e)) }
  }

  const kids = (parentId) => people.filter(p => parentId === null
    ? (!p.manager_id || !byId[p.manager_id])
    : p.manager_id === parentId)
  const tree = (parentId, depth) => kids(parentId).map(p => (
      <div key={p.user_id} style={{ marginLeft: depth * 22 }}>
        <div style={{ ...card, padding: 12, marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 6 }}>
            <div>
              <b>{p.name}</b> <span style={{ color: '#607486', fontSize: 13 }}>{p.email}</span>
              <div style={{ fontSize: 13, color: '#36505f' }}>{p.seat_label}{p.is_self ? ' (you)' : ''}<Status p={p} /></div>
            </div>
            <div>
              {p.onboarding && <button style={btn} onClick={() => setOpen(open === p.user_id ? null : p.user_id)}>{open === p.user_id ? 'Hide checklist' : 'Checklist'}</button>}
              {!p.is_self && p.seat !== 'owner' && !p.departed_at && (p.active
                ? <button style={btn} disabled={!!busy} onClick={() => act('s' + p.user_id, () => api.post('/sales-team/members/' + p.user_id + '/suspend', {}), p.name + ' is suspended.')}>Suspend</button>
                : <button style={btn} disabled={!!busy} onClick={() => act('r' + p.user_id, () => api.post('/sales-team/members/' + p.user_id + '/reactivate', {}), p.name + ' is active again.')}>Reactivate</button>)}
              {p.seat === 'rep' && !p.departed_at && (
                <select style={{ ...btn, padding: '5px 6px' }} value="" onChange={e => e.target.value && act('m' + p.user_id, () => api.post('/sales-team/members/' + p.user_id + '/move', { manager_id: e.target.value }), p.name + ' moved. Deals already sold stay with their original manager.')}>
                  <option value="">Move to…</option>
                  {managers.filter(m => m.user_id !== p.manager_id).map(m => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
                </select>)}
              {r.can_record_departure && !p.is_self && p.seat !== 'owner' && !p.departed_at && (
                <button style={btn} onClick={() => { if (window.confirm(p.name + ' left the company? From today they are paid nothing more; their share of later collections is tracked as unclaimed.')) act('d' + p.user_id, () => api.post('/sales-team/members/' + p.user_id + '/depart', {}), p.name + ' is recorded as left the company.') }}>Left the company</button>)}
            </div>
          </div>
          {open === p.user_id && p.onboarding && (
            <div style={{ marginTop: 10, fontSize: 13 }}>
              {p.onboarding.items.map(i => (
                <div key={i.key} style={{ padding: '3px 0' }}>
                  {i.done ? '✓' : '○'} {i.label}
                  {i.missing_fields && i.missing_fields.length > 0 && <span style={{ color: '#79551a' }}> — missing {i.missing_fields.join(', ')}</span>}
                  {r.can_open_files && i.document_id && <button style={{ ...btn, marginTop: 0, padding: '2px 8px' }} onClick={() => openDoc(p, i)}>Open {i.filename}</button>}
                </div>
              ))}
              {p.onboarding.returned_note && <p style={{ color: '#79551a' }}>Sent back: {p.onboarding.returned_note}</p>}
              {r.can_approve && p.onboarding.status !== 'approved' && (
                <div>
                  <button style={primary} disabled={!p.onboarding.complete || !!busy} onClick={() => act('a' + p.user_id, () => api.post('/sales-team/onboarding/' + p.onboarding_id + '/approve', {}), p.name + ' is approved and can sell now.')}>Approve</button>
                  <button style={btn} disabled={!!busy} onClick={() => { const n = window.prompt('What needs fixing?'); if (n !== null) act('b' + p.user_id, () => api.post('/sales-team/onboarding/' + p.onboarding_id + '/send-back', { note: n }), 'Sent back to ' + p.name + '.') }}>Send back</button>
                </div>
              )}
            </div>
          )}
        </div>
        {tree(p.user_id, depth + 1)}
      </div>
    ))

  return (
    <div style={{ padding: 20, maxWidth: 980, margin: '0 auto', fontFamily: 'system-ui, sans-serif', color: '#102b40' }}>
      <div style={{ fontWeight: 800, fontSize: 20 }}>EvoSys <span style={{ color: '#087f83' }}>Pro</span> <span style={{ fontSize: 13, color: '#607486', fontWeight: 600 }}>· Back Office</span></div>
      <p style={{ margin: '4px 0 0' }}><Link to="/workspaces">← Program Access</Link></p>
      <h1 style={{ fontSize: 24, marginBottom: 4 }}>My Team</h1>
      <p style={{ color: '#607486', marginTop: 0 }}>{d.brand.name} · you are {r.seat_label || 'viewing'}{r.is_admin ? ' — you can approve onboarding and open documents' : ''}</p>
      {note && <div style={{ ...card, background: '#f0faf7', borderColor: '#bfe6dc', wordBreak: 'break-all' }}>{note}</div>}
      {err && <div style={{ ...card, background: '#fff4df', borderColor: '#f1d59b' }}>{err}</div>}

      {r.can_invite && (
        <form onSubmit={invite} style={card}>
          <h3 style={{ marginTop: 0 }}>Invite someone</h3>
          <p style={{ fontSize: 13, color: '#607486' }}>They get an email to set their own password, then fill in the application and upload their documents. They can sell once an owner approves them.</p>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <input required placeholder="Full name" value={form.full_name} onChange={e => setForm({ ...form, full_name: e.target.value })} style={{ ...btn, flex: '1 1 180px' }} />
            <input required type="email" placeholder="Email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} style={{ ...btn, flex: '1 1 200px' }} />
            <select required value={form.seat} onChange={e => setForm({ ...form, seat: e.target.value })} style={btn}>
              <option value="">Seat…</option>
              {seats.map(s => <option key={s} value={s}>{SEAT_NAME[s]}</option>)}
            </select>
            {form.seat === 'rep' && (
              <select value={form.manager_id} onChange={e => setForm({ ...form, manager_id: e.target.value })} style={btn}>
                <option value="">{r.is_leader ? 'Reports to me' : 'Manager…'}</option>
                {managers.map(m => <option key={m.user_id} value={m.user_id}>{m.name} ({SEAT_NAME[m.seat]})</option>)}
              </select>)}
            {form.seat === 'asm' && r.is_admin && (
              <select value={form.manager_id} onChange={e => setForm({ ...form, manager_id: e.target.value })} style={btn}>
                <option value="">Reports to…</option>
                {managers.filter(m => m.seat === 'sevp').map(m => <option key={m.user_id} value={m.user_id}>{m.name}</option>)}
              </select>)}
            <button style={primary} disabled={busy === 'invite'}>{busy === 'invite' ? 'Sending…' : 'Send invite'}</button>
          </div>
        </form>
      )}

      {people.length === 0 ? <div style={card}>Nobody on the team yet.</div> : tree(null, 0)}
      {r.can_open_files && <p style={{ fontSize: 12, color: '#607486' }}>{d.drive_status} Every document you open is recorded.</p>}
    </div>
  )
}
