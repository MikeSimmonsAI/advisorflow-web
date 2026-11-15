import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fetchObjectUrl } from '../api/client'
import { Steps } from './sales/ContractorOnboardingPanel'

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
  const [ag, setAg] = useState(null)
  const [sig, setSig] = useState({ typed_name: '', consent: false })

  const load = useCallback(async () => {
    try {
      const r = await api.get('/sales-team/onboarding/me', { noOrgContext: true })
      setD(r); setApp(r.application || {}); setErr('')
      try { setAg(await api.get('/sales-team/onboarding/me/agreement', { noOrgContext: true })) } catch { setAg(null) }
    } catch (e) { setErr(errText(e)) }
  }, [])
  // A link from an email (#agreement, #doc-w9 …) lands on that exact step.
  useEffect(() => {
    if (!d || !window.location.hash) return
    const el = document.getElementById(window.location.hash.slice(1))
    if (el) { el.scrollIntoView({ block: 'start' }); el.focus && el.focus() }
  }, [d])
  useEffect(() => { load() }, [load])
  // Coming back to this tab (e.g. after an owner sent the agreement) refreshes it.
  useEffect(() => {
    const again = () => { if (document.visibilityState !== 'hidden') load() }
    window.addEventListener('focus', again)
    return () => window.removeEventListener('focus', again)
  }, [load])

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
  const docs = c.items.filter(i => i.key !== 'application' && !(i.key === 'contractor_agreement' && ag?.agreement))
  const review = Object.fromEntries((d.documents || []).map(x => [x.kind, x]))
  const a = ag?.agreement
  const sign = async () => {
    setBusy('sign'); setErr(''); setNote('')
    try { await api.post('/sales-team/onboarding/me/agreement/sign', sig); setNote('Signed. Your signed copy is saved with your onboarding.'); await load() }
    catch (x) { setErr(errText(x)) } finally { setBusy('') }
  }
  const getFile = async (path, name) => {
    try {
      const url = await fetchObjectUrl(path)
      const el = document.createElement('a'); el.href = url; el.download = name
      document.body.appendChild(el); el.click(); el.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000)
    } catch (x) { setErr(errText(x)) }
  }
  const askFix = async () => {
    const n = window.prompt('What needs to change in the agreement?')
    if (!n) return
    try { await api.post('/sales-team/onboarding/me/agreement/correction', { note: n }); setNote('Sent to the owners. They will send a corrected version.'); await load() }
    catch (x) { setErr(errText(x)) }
  }

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
        {d.journey && <div style={card}><h3 style={{ marginTop: 0 }}>Your steps</h3><Steps steps={d.journey.steps} next={d.journey.next} /></div>}
        {(c.returned_note || d.journey?.corrections?.length > 0) && (
          <div style={{ ...card, background: '#fff4df', borderColor: '#f1d59b' }} role="alert">
            <b>Please correct and resend</b>
            {c.returned_note && <p style={{ margin: '6px 0' }}>{c.returned_note}</p>}
            <ul style={{ margin: 0 }}>
              {(d.journey?.corrections || []).map(x => <li key={x.step_link}><a href={x.step_link}>{x.label}</a>{x.note ? ': ' + x.note : ''}</li>)}
            </ul>
          </div>
        )}
        {note && <div style={{ ...card, background: '#f0faf7', borderColor: '#bfe6dc' }}>{note}</div>}
        {err && <div style={{ ...card, background: '#fff4df', borderColor: '#f1d59b' }}>{err}</div>}

        <form style={card} onSubmit={save} id="application" tabIndex={-1}>
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

        {a && (
          <div style={card} id="agreement" tabIndex={-1}>
            <h3 style={{ marginTop: 0 }}>{a.status === 'completed' ? '✓' : '2.'} Contractor agreement</h3>
            <p style={{ margin: '0 0 8px' }}>{a.title} · version {a.version_label} · Exhibit A {a.exhibit_version} — <b>{a.status_label}</b></p>
            {a.note && <p style={{ color: '#79551a' }}>{a.note}</p>}
            <div style={{ maxHeight: 380, overflow: 'auto', border: '1px solid #e6edf1', borderRadius: 8, padding: '4px 14px', background: '#fbfcfd' }}
                 dangerouslySetInnerHTML={{ __html: a.document_html }} />
            <p style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
              <button type="button" style={{ ...btn, background: '#fff', color: '#087f83' }} onClick={() => getFile('/sales-team/onboarding/me/agreement/document', 'contractor-agreement.html')}>Download to read</button>
              {a.has_signed_copy && <button type="button" style={{ ...btn, background: '#fff', color: '#087f83' }} onClick={() => getFile('/sales-team/onboarding/me/agreement/signed-copy', 'signed-contractor-agreement.pdf')}>Download my signed copy</button>}
              {a.status === 'sent' && <button type="button" style={{ ...btn, background: '#fff', color: '#79551a', borderColor: '#d9b46b' }} onClick={askFix}>Something is wrong</button>}
            </p>
            {a.status === 'sent' && (
              <div style={{ borderTop: '1px solid #eef2f4', paddingTop: 10 }}>
                <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13 }}>
                  <input type="checkbox" checked={sig.consent} onChange={e => setSig({ ...sig, consent: e.target.checked })} />
                  <span>{ag.consent_text}</span>
                </label>
                <label style={{ display: 'block', marginTop: 8, fontSize: 14 }}>Type your full legal name to sign{ag.legal_name ? ' (' + ag.legal_name + ')' : ''}
                  <input style={input} value={sig.typed_name} onChange={e => setSig({ ...sig, typed_name: e.target.value })} autoComplete="name" />
                </label>
                <button style={{ ...btn, marginTop: 8 }} disabled={!sig.consent || !sig.typed_name.trim() || busy === 'sign'} onClick={sign}>
                  {busy === 'sign' ? 'Signing…' : 'Sign agreement'}
                </button>
              </div>
            )}
          </div>
        )}

        <div style={card}>
          <h3 style={{ marginTop: 0 }}>{a ? '3.' : '2.'} Documents</h3>
          <p style={{ fontSize: 13, color: '#607486' }}>PDF or photo, up to 10 MB each. Files are kept privately in EvoSys Pro; only the company owners can open them, and every view is recorded. Never email these documents to anyone.</p>
          {docs.map(i => (
            <div key={i.key} id={'doc-' + i.key} tabIndex={-1} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '8px 0', borderTop: '1px solid #eef2f4', flexWrap: 'wrap' }}>
              <div>{i.done ? '✓' : '○'} <b>{i.label}</b>{i.filename && <div style={{ fontSize: 12, color: '#607486' }}>{i.filename}</div>}
                {review[i.key]?.review === 'needs_correction' && <div style={{ fontSize: 13, color: '#a1340c' }}>Needs correction: {review[i.key].correction_note}</div>}
                {review[i.key]?.review === 'accepted' && <div style={{ fontSize: 12, color: '#0b6b4a' }}>Accepted</div>}
              </div>
              {!locked && <label style={{ ...btn, background: '#fff', color: '#087f83' }}>
                {busy === i.key ? 'Uploading…' : i.done ? 'Replace' : 'Upload'}
                <input type="file" accept="application/pdf,image/*" style={{ display: 'none' }} onChange={e => upload(i.key, e.target.files[0])} />
              </label>}
            </div>
          ))}
        </div>

        {!locked && (
          <div style={card}>
            <h3 style={{ marginTop: 0 }}>{a ? '4.' : '3.'} Send for review</h3>
            {c.complete ? <p>Everything is in.</p> : <p style={{ color: '#79551a' }}>Still missing: {c.missing.join(', ')}</p>}
            <button style={btn} disabled={!c.complete || busy === 'submit'} onClick={submit}>Send for approval</button>
          </div>
        )}
        <p><Link to="/workspaces">Back to Program Access</Link></p>
      </div>
    </div>
  )
}
