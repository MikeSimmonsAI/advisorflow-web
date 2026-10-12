/**
 * GOD MODE - SCI & WHOLESALE ACCESS.
 *
 * 1. Set up the SCI workspace for a login (GET = dry run, POST = apply with
 *    "SET UP SCI"). Shows the login's current workspaces first, so the right
 *    login (the one with Atlantis and the Wholesaler) is easy to confirm.
 * 2. EvoSys Wholesale: the named logins who may use it. God mode always can.
 *    The lock turns on with the first name and never reopens by itself.
 * 3. SCI contacts: the owner picks the source file, previews it, then loads
 *    it (POST /god/programs/{org}/load-source). Loading stages every row,
 *    holds anything that needs review and makes the clean contacts live.
 *    It never sends, never enrols and never records consent.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

const box = { background: '#fff', border: '1px solid #e2e8e9', borderRadius: 14, padding: 20, marginBottom: 18 }
const input = { border: '1px solid #cfd9db', borderRadius: 8, padding: '9px 12px', fontSize: 14, minWidth: 280 }
const btn = { border: '1px solid #16455d', background: '#fff', color: '#16455d', borderRadius: 8, padding: '9px 14px', fontWeight: 600, cursor: 'pointer' }
const primary = { ...btn, background: '#16455d', color: '#fff' }
const errText = e => e?.detail || e?.message || 'Request failed'

export default function GodAccessSetup() {
  return (
    <div style={{ maxWidth: 980, padding: '8px 4px' }}>
      <h1 style={{ margin: '0 0 6px', fontSize: 24 }}>SCI & Wholesale access</h1>
      <p style={{ margin: '0 0 20px', color: '#5b6b75' }}>Put SCI under a login, and decide who may use EvoSys Wholesale.</p>
      <SciSetup />
      <WholesaleAccess />
      <SciContacts />
    </div>
  )
}

function SciSetup() {
  const [email, setEmail] = useState('')
  const [plan, setPlan] = useState(null)
  const [result, setResult] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const check = async e => {
    e?.preventDefault(); setErr(''); setResult(null); setBusy(true)
    try { setPlan(await api.get(`/god/access-setup/sci?email=${encodeURIComponent(email.trim())}`)) } catch (er) { setErr(errText(er)) } finally { setBusy(false) }
  }
  const apply = async () => {
    const c = window.prompt('This sets up the SCI workspace and adds this login to it. Type SET UP SCI to confirm.')
    if (c == null) return
    setErr(''); setBusy(true)
    try { setResult(await api.post('/god/access-setup/sci', { email: email.trim(), confirm: c })); await check() } catch (er) { setErr(errText(er)) } finally { setBusy(false) }
  }
  return (
    <section style={box} aria-labelledby="sci-setup-h">
      <h2 id="sci-setup-h" style={{ margin: '0 0 6px', fontSize: 18 }}>1. SCI workspace under a login</h2>
      <p style={{ margin: '0 0 14px', color: '#5b6b75', fontSize: 14 }}>
        Creates "Service Corporation International" here if it doesn't exist yet: the 39 locations, Kerry Allan as the sender, every campaign off, the 844 line.
        Then adds the login. No contacts are created and nothing is sent.
      </p>
      <form onSubmit={check} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <label htmlFor="sci-email" style={{ fontWeight: 600, fontSize: 14 }}>Login email</label>
        <input id="sci-email" style={input} value={email} onChange={e => setEmail(e.target.value)} placeholder="the login that opens Atlantis and the Wholesaler" />
        <button type="submit" style={btn} disabled={busy || !email.trim()}>Check (changes nothing)</button>
      </form>
      {err && <p role="alert" style={{ color: '#a9423c', fontSize: 14 }}>{err}</p>}
      {plan && (
        <div style={{ marginTop: 16, fontSize: 14 }}>
          <p style={{ margin: '0 0 6px' }}><b>Login:</b> {plan.login.found ? plan.login.email : 'not found'}</p>
          {plan.login.found && (
            <p style={{ margin: '0 0 6px' }}><b>Workspaces this login opens now:</b>{' '}
              {plan.login.current_workspaces.length ? plan.login.current_workspaces.map(w => `${w.name} (${w.role})`).join(', ') : 'none'}</p>
          )}
          <p style={{ margin: '0 0 6px' }}><b>SCI workspace:</b> {plan.organization.exists ? 'already exists' : 'will be created'}{plan.login.already_in_sci ? ' · this login is already in it' : ''}</p>
          <p style={{ margin: '0 0 6px' }}><b>844 line:</b> {plan.toll_free.state === 'another organization' ? `owned by ${plan.toll_free.owner || 'another organization'} — left unchanged` : plan.toll_free.state === 'absent' ? 'will be added to SCI' : 'already on SCI'}</p>
          {plan.login.found && <p style={{ margin: '0 0 6px' }}><b>Role in SCI:</b> {plan.login.role_to_grant}</p>}
          {plan.blockers.length > 0 && <p role="alert" style={{ color: '#a9423c' }}>{plan.blockers.join(' ')}</p>}
          {plan.ready && <button type="button" style={primary} disabled={busy} onClick={apply}>{busy ? 'Working…' : 'Set up SCI for this login'}</button>}
        </div>
      )}
      {result && (
        <div role="status" style={{ marginTop: 14, padding: 12, borderRadius: 10, background: '#f0f8f3', border: '1px solid #cfe6da', fontSize: 14 }}>
          Done. {result.organization_created ? 'SCI workspace created' : 'SCI workspace already existed'} · {result.locations} locations · {result.campaigns_on} campaigns on.
          {' '}{result.login.email} now opens: {result.workspaces_now.map(w => w.name).join(', ')}.
        </div>
      )}
    </section>
  )
}

function WholesaleAccess() {
  const [d, setD] = useState(null)
  const [email, setEmail] = useState('')
  const [err, setErr] = useState('')
  const load = useCallback(() => { api.get('/god/access-setup/wholesale').then(setD).catch(e => setErr(errText(e))) }, [])
  useEffect(() => { load() }, [load])
  const grant = async e => {
    e.preventDefault(); setErr('')
    try { const r = await api.post('/god/access-setup/wholesale/grant', { email: email.trim() }); setD(x => ({ ...x, lock_on: r.lock_on, holders: r.holders })); setEmail('') } catch (er) { setErr(errText(er)) }
  }
  const revoke = async who => {
    if (!window.confirm(`Remove EvoSys Wholesale from ${who}?`)) return
    setErr('')
    try { const r = await api.post('/god/access-setup/wholesale/revoke', { email: who }); setD(x => ({ ...x, lock_on: r.lock_on, holders: r.holders })) } catch (er) { setErr(errText(er)) }
  }
  const active = (d?.holders || []).filter(h => h.active)
  return (
    <section style={box} aria-labelledby="wh-h">
      <h2 id="wh-h" style={{ margin: '0 0 6px', fontSize: 18 }}>2. Who may use EvoSys Wholesale</h2>
      {!d ? <p>{err || 'Loading…'}</p> : (
        <>
          <p style={{ margin: '0 0 12px', fontSize: 14, color: d.lock_on ? '#245e46' : '#8d6024' }}>
            {d.lock_on
              ? 'Locked: only the logins below, plus God mode.'
              : 'Not locked yet: any workspace entitled to Wholesale can still open it. Add the first login to lock it to named logins only.'}
          </p>
          {active.length === 0 ? <p style={{ fontSize: 14 }}>{d.lock_on ? 'No named logins — God mode only.' : 'No named logins yet.'}</p> : (
            <ul style={{ paddingLeft: 18, fontSize: 14 }}>
              {active.map(h => (
                <li key={h.user_id} style={{ marginBottom: 6 }}>
                  {h.email}{h.name ? ` (${h.name})` : ''}{' '}
                  <button type="button" style={{ ...btn, padding: '3px 10px', fontSize: 12 }} onClick={() => revoke(h.email)}>Remove</button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={grant} style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginTop: 8 }}>
            <label htmlFor="wh-email" style={{ fontWeight: 600, fontSize: 14 }}>Add login</label>
            <input id="wh-email" style={input} value={email} onChange={e => setEmail(e.target.value)} placeholder="login email" />
            <button type="submit" style={primary} disabled={!email.trim()}>{d.lock_on ? 'Add' : 'Add and lock'}</button>
          </form>
          {err && <p role="alert" style={{ color: '#a9423c', fontSize: 14 }}>{err}</p>}
        </>
      )}
    </section>
  )
}


const SUMMARY_LABELS = {
  rows: 'Rows in the file', unique_lead_ids: 'Lead IDs', staged: 'Rows saved',
  mapped: 'Matched to a location', location_review: 'Location review', duplicate_review: 'Duplicate review',
  data_review: 'Data review', auto_linked: 'Same person, linked', errors: 'Errors',
}

function Counts({ data }) {
  const rows = Object.entries(data || {}).filter(([, v]) => typeof v === 'number' || typeof v === 'boolean')
  if (!rows.length) return null
  return (
    <table style={{ borderCollapse: 'collapse', fontSize: 14, margin: '6px 0 10px' }}>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}><td style={{ padding: '2px 16px 2px 0', color: '#5b6b75' }}>{SUMMARY_LABELS[k] || k.replace(/_/g, ' ')}</td>
            <td style={{ padding: '2px 0', fontWeight: 600 }}>{String(v)}</td></tr>
        ))}
      </tbody>
    </table>
  )
}

function SciContacts() {
  const [orgId, setOrgId] = useState(null)
  const [file, setFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [done, setDone] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    api.get('/god/access-setup/sci').then(r => setOrgId(r.organization?.exists ? r.organization.id : null)).catch(e => setErr(errText(e)))
  }, [])
  const send = async apply => {
    const fd = new FormData()
    fd.append('file', file, file.name)
    fd.append('apply', apply ? 'true' : 'false')
    return api.upload(`/god/programs/${orgId}/load-source`, fd)
  }
  const check = async () => {
    setErr(''); setDone(null); setBusy(true)
    try { setPreview(await send(false)) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const load = async () => {
    const c = window.prompt('This loads the contacts into SCI. Nothing is sent and every campaign stays off. Type LOAD to confirm.')
    if (c !== 'LOAD') return
    setErr(''); setBusy(true)
    try { setDone(await send(true)) } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <section style={box} aria-labelledby="sci-load-h">
      <h2 id="sci-load-h" style={{ margin: '0 0 6px', fontSize: 18 }}>3. SCI contacts: load the file</h2>
      <p style={{ margin: '0 0 14px', color: '#5b6b75', fontSize: 14 }}>
        Pick the SCI contact file (CSV, the same columns as the SCI export). Check it first; nothing is saved by the check.
        Loading saves every row, puts on hold anything that needs a person to look at it (unknown location, duplicates,
        Not Interested / Disqualified) and makes the clean contacts live. <b>Nothing is sent</b> - every campaign stays off.
      </p>
      {!orgId ? <p style={{ fontSize: 14 }}>{err || 'Finding the SCI workspace…'}</p> : (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <label htmlFor="sci-file" style={{ fontWeight: 600, fontSize: 14 }}>Contact file</label>
            <input id="sci-file" type="file" accept=".csv,text/csv" onChange={e => { setFile(e.target.files[0] || null); setPreview(null); setDone(null) }} />
            <button type="button" style={btn} disabled={busy || !file} onClick={check}>{busy && !preview ? 'Checking…' : 'Check (saves nothing)'}</button>
          </div>
          {err && <p role="alert" style={{ color: '#a9423c', fontSize: 14 }}>{err}</p>}
          {preview && (
            <div style={{ marginTop: 14 }}>
              <h3 style={{ fontSize: 15, margin: '0 0 4px' }}>What loading would do</h3>
              <Counts data={preview.stage} />
              <Counts data={preview.promotion_plan} />
              {preview.stage?.refused
                ? <p role="alert" style={{ color: '#a9423c', fontSize: 14 }}>{preview.stage.refused}</p>
                : <button type="button" style={primary} disabled={busy} onClick={load}>{busy ? 'Loading…' : 'Load into SCI (sends nothing)'}</button>}
            </div>
          )}
          {done && (
            <div role="status" style={{ marginTop: 14, padding: 12, borderRadius: 10, background: '#f0f8f3', border: '1px solid #cfe6da', fontSize: 14 }}>
              <b>Loaded.</b> {done.stage?.staged} rows saved · {done.held?.held} put on hold for review · {done.promotion?.created} contacts now live.
              Nothing was sent.
              <Counts data={done.held?.by_reason} />
            </div>
          )}
        </>
      )}
    </section>
  )
}
