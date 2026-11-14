import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'

/**
 * SALES ONBOARDING — /sales-onboarding
 *
 * A new member of the EvoSys Pro sales team fills in a short application and
 * uploads five documents. They can't sell until an owner approves them. Files
 * go to the platform's private storage; this page never shows file contents,
 * and the person sees only their own checklist.
 */

const card = { background: '#fff', border: '1px solid #dce5ea', borderRadius: 12, padding: 18, marginBottom: 14 }
const input = { width: '100%', boxSizing: 'border-box', border: '1px solid #c9d7df', borderRadius: 8, padding: '9px 10px', fontSize: 15, marginTop: 4 }
const btn = { border: '1px solid #087f83', background: '#087f83', color: '#fff', borderRadius: 8, padding: '9px 14px', fontSize: 14, cursor: 'pointer' }

function errText(e) { return (e && (e.detail || e.message)) || 'Something went wrong.' }

export default function SalesOnboarding() {
  const [d, setD] = useState(null)
  const [app, setApp] = useState({})
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')

  const load = useCallback(async () => {
    try {
      const r = await api.get('/sales-team/onboarding/me', { noOrgContext: true })
      setD(r); setApp(r.application || {}); setErr('')
    } catch (e) { setErr(errText(e)) }
  }, [])
  useEffect(() => { load() }, [load])

  if (err && !d) return <div style={{ padding: 24 }}><div style={card}><h2>Sales onboarding</h2><p>{err}</p><Link to="/workspaces">Back to Program Access</Link></div></div>
  if (!d) return <div style={{ padding: 24 }}>Loading…</div>

  const c = d.checklist
  const locked = c.status === 'submitted' || c.status === 'approved'
  const save = async (e) => {
    e && e.preventDefault()
    setBusy('save'); setErr(''); setNote('')
    try { const r = await api.put('/sales-team/onboarding/me/application', app); setD({ ...d, checklist: r.checklist }); setNote('Application saved.') }
    catch (x) { setErr(errText(x)) } finally { setBusy('') }
  }
  const upload = async (kind, file) => {
    if (!file) return
    setBusy(kind); setErr(''); setNote('')
    const fd = new FormData(); fd.append('file', file)
    try { const r = await api.post('/sales-team/onboarding/me/documents/' + kind, fd); setD({ ...d, checklist: r.checklist }); setNote('Uploaded.') }
    catch (x) { setErr(errText(x)) } finally { setBusy('') }
  }
  const submit = async () => {
    setBusy('submit'); setErr(''); setNote('')
    try { const r = await api.post('/sales-team/onboarding/me/submit', {}); setD({ ...d, checklist: r.checklist }); setNote('Sent for approval. You will be able to sell once an owner approves you.') }
    catch (x) { setErr(errText(x)) } finally { setBusy('') }
  }

  const appItem = c.items.find(i => i.key === 'application')
  const docs = c.items.filter(i => i.key !== 'application')

  return (
    <div style={{ minHeight: '100dvh', background: '#f3f6f8', padding: 16, fontFamily: 'system-ui, sans-serif', color: '#102b40' }}>
      <div style={{ maxWidth: 720, margin: '0 auto' }}>
        <div style={{ fontWeight: 800, fontSize: 22, margin: '8px 0 4px' }}>EvoSys <span style={{ color: '#087f83' }}>Pro</span></div>
        <h1 style={{ fontSize: 22, margin: '6px 0' }}>Sales onboarding</h1>
        <p style={{ color: '#607486', marginTop: 0 }}>
          {c.status === 'approved' ? 'You are approved. Your Deal Builder is on Program Access.'
            : c.status === 'submitted' ? 'Sent for approval. An owner will review it.'
              : 'Fill in the application and upload each document. Then send it for approval.'}
        </p>
        {c.returned_note && <div style={{ ...card, background: '#fff4df', borderColor: '#f1d59b' }}>Sent back for a fix: {c.returned_note}</div>}
        {note && <div style={{ ...card, background: '#f0faf7', borderColor: '#bfe6dc' }}>{note}</div>}
        {err && <div style={{ ...card, background: '#fff4df', borderColor: '#f1d59b' }}>{err}</div>}

        <form style={card} onSubmit={save}>
          <h3 style={{ marginTop: 0 }}>{appItem.done ? '✓' : '1.'} Application</h3>
          {Object.entries(d.fields).map(([k, label]) => (
            <label key={k} style={{ display: 'block', marginBottom: 10, fontSize: 14 }}>{label}
              {k === 'home_address'
                ? <textarea disabled={locked} rows={2} style={input} value={app[k] || ''} onChange={e => setApp({ ...app, [k]: e.target.value })} />
                : <input disabled={locked} type={k === 'start_date' ? 'date' : k === 'email' ? 'email' : 'text'} style={input} value={app[k] || ''} onChange={e => setApp({ ...app, [k]: e.target.value })} />}
            </label>
          ))}
          {!locked && <button style={btn} disabled={busy === 'save'}>{busy === 'save' ? 'Saving…' : 'Save application'}</button>}
        </form>

        <div style={card}>
          <h3 style={{ marginTop: 0 }}>2. Documents</h3>
          <p style={{ fontSize: 13, color: '#607486' }}>PDF or photo, up to 10 MB each. Only the company owners can open these.</p>
          {docs.map(i => (
            <div key={i.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 0', borderTop: '1px solid #eef2f4', flexWrap: 'wrap' }}>
              <div>{i.done ? '✓' : '○'} <b>{i.label}</b>{i.filename && <div style={{ fontSize: 12, color: '#607486' }}>{i.filename}</div>}</div>
              {!locked && <label style={{ ...btn, background: '#fff', color: '#087f83' }}>
                {busy === i.key ? 'Uploading…' : i.done ? 'Replace' : 'Upload'}
                <input type="file" accept="application/pdf,image/*" style={{ display: 'none' }} onChange={e => upload(i.key, e.target.files[0])} />
              </label>}
            </div>
          ))}
        </div>

        {!locked && (
          <div style={card}>
            <h3 style={{ marginTop: 0 }}>3. Send for approval</h3>
            {c.complete ? <p>Everything is in.</p> : <p style={{ color: '#79551a' }}>Still missing: {c.missing.join(', ')}</p>}
            <button style={btn} disabled={!c.complete || busy === 'submit'} onClick={submit}>Send for approval</button>
          </div>
        )}
        <p><Link to="/workspaces">Back to Program Access</Link></p>
      </div>
    </div>
  )
}
