/**
 * A contractor's onboarding, inside their My Team profile.
 *
 * Owners / God Mode: the steps from invite to selling, the next action and
 * who owns it, Resend Invite, the Documents section (View in this page,
 * Download, Accept, Needs correction), the agreement (send for signature,
 * countersign, signed copy), Send Back for Corrections and Approve.
 * The Sr EVP / an ASM: the steps and done/missing only - never files.
 *
 * Files are fetched with the signed-in session and shown inside this page
 * (no pop-up windows, which browsers block); nothing is ever a public link.
 * Every view and download is recorded on the server.
 */
import { useCallback, useEffect, useState } from 'react'
import { api, fetchObjectUrl } from '../../api/client'

const btn = { border: '1px solid #c9d7df', background: '#fff', borderRadius: 8, padding: '6px 10px', cursor: 'pointer', fontSize: 13, marginRight: 6, marginTop: 4 }
const primary = { ...btn, background: '#087f83', color: '#fff', borderColor: '#087f83' }
const sect = { marginTop: 12, paddingTop: 10, borderTop: '1px solid #e6edf1' }
const when = iso => { try { return iso ? new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '' } catch { return '' } }
const WHO = { owner: 'Owner', contractor: 'Contractor', '—': '' }
function errText(e) { return (e && (e.detail || e.message)) || 'Something went wrong.' }

export function Steps({ steps, next }) {
  return (
    <div>
      {next && (
        <p style={{ margin: '0 0 8px', padding: '8px 10px', background: '#fff8eb', border: '1px solid #f3dcae', borderRadius: 8 }}>
          <b>Next:</b> {next.what} <span style={{ color: '#8a5a10' }}>— {WHO[next.who] || next.who}</span>
        </p>
      )}
      <ol style={{ margin: 0, paddingLeft: 20 }}>
        {steps.map(s => (
          <li key={s.key} style={{ marginBottom: 3, color: s.done ? '#0b6b4a' : '#36505f' }}>
            <b>{s.done ? '✓ ' : ''}{s.label}</b>
            {s.detail ? <span style={{ color: '#607486' }}> — {s.detail}</span> : null}
            {s.at ? <span style={{ color: '#8aa0b1' }}> · {when(s.at)}</span> : null}
            {!s.done && s.owner && s.owner !== '—' ? <span style={{ color: '#8aa0b1' }}> ({WHO[s.owner]})</span> : null}
          </li>
        ))}
      </ol>
    </div>
  )
}

function Viewer({ doc, onClose }) {
  if (!doc) return null
  const isImg = (doc.type || '').startsWith('image/')
  return (
    <div role="dialog" aria-modal="true" aria-label={'Viewing ' + doc.name}
         style={{ position: 'fixed', inset: 0, background: 'rgba(10,25,40,.6)', zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}
         onClick={onClose}>
      <div style={{ background: '#fff', borderRadius: 12, width: 'min(960px, 100%)', height: 'min(90vh, 900px)', display: 'flex', flexDirection: 'column' }}
           onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 10, borderBottom: '1px solid #e6edf1' }}>
          <b style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{doc.name}</b>
          <a style={btn} href={doc.url} download={doc.name}>Download</a>
          <button style={btn} onClick={onClose} autoFocus>Close</button>
        </div>
        <div style={{ flex: 1, overflow: 'auto', background: '#f3f6f8' }}>
          {isImg
            ? <img src={doc.url} alt={doc.name} style={{ maxWidth: '100%', display: 'block', margin: '0 auto' }} />
            : <iframe title={doc.name} src={doc.url} style={{ width: '100%', height: '100%', border: 0 }} />}
        </div>
      </div>
    </div>
  )
}

export default function ContractorOnboardingPanel({ ob, userId, canResend, onChanged }) {
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [viewer, setViewer] = useState(null)
  const [notes, setNotes] = useState({})
  const [templates, setTemplates] = useState([])
  const [tpl, setTpl] = useState('')
  const [counter, setCounter] = useState('')
  const [backNote, setBackNote] = useState('')

  const admin = !!ob?.can_act
  useEffect(() => {
    if (!admin) return
    api.get('/sales-team/agreement-templates', { noOrgContext: true })
      .then(r => setTemplates((r.templates || []).filter(t => t.status === 'approved'))).catch(() => setTemplates([]))
  }, [admin])
  useEffect(() => () => { if (viewer?.url) URL.revokeObjectURL(viewer.url) }, [viewer])

  const run = useCallback(async (key, fn, ok) => {
    setBusy(key); setErr(''); setMsg('')
    try { const r = await fn(); setMsg(typeof ok === 'function' ? ok(r) : ok); if (onChanged) await onChanged() }
    catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }, [onChanged])

  if (!ob) return null
  const base = '/sales-team/onboarding/' + ob.onboarding_id
  const view = async (d) => {
    setErr('')
    try { setViewer({ url: await fetchObjectUrl(base + '/documents/' + d.document_id + '?inline=true'), name: d.filename, type: d.content_type }) }
    catch (e) { setErr(errText(e)) }
  }
  const download = async (d) => {
    try {
      const url = await fetchObjectUrl(base + '/documents/' + d.document_id)
      const a = document.createElement('a'); a.href = url; a.download = d.filename || 'document'
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000)
    } catch (e) { setErr(errText(e)) }
  }
  const signedCopy = async () => {
    try {
      const url = await fetchObjectUrl(base + '/signed-agreement')
      setViewer({ url, name: 'Signed contractor agreement.pdf', type: 'application/pdf' })
    } catch (e) { setErr(errText(e)) }
  }
  const ag = ob.agreement

  return (
    <div style={sect}>
      <b>Onboarding</b>
      {(err || msg) && <p role={err ? 'alert' : 'status'} style={{ color: err ? '#a1340c' : '#0b6b4a', fontWeight: 600 }}>{err || msg}</p>}
      <Steps steps={ob.steps} next={ob.next} />
      {admin && ob.account && <p style={{ margin: '6px 0 0', color: '#607486' }}>Account: {ob.account.how}{ob.account.at ? ' · ' + when(ob.account.at) : ''}</p>}
      {canResend && (
        <button style={btn} disabled={!!busy} onClick={() => run('resend', () => api.post('/sales-team/members/' + userId + '/resend-invite', {}),
          r => r.message + (r.setup_url ? ' ' + r.setup_url : ''))}>{busy === 'resend' ? 'Sending…' : 'Resend Invite'}</button>
      )}

      <div style={sect}>
        <b>Documents</b>
        <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 6 }}>
          <thead><tr style={{ textAlign: 'left', color: '#607486', fontSize: 12 }}><th>Item</th><th>Received</th><th>Review</th><th /></tr></thead>
          <tbody>
            {ob.documents.map(d => (
              <tr key={d.label} style={{ borderTop: '1px solid #e6edf1', verticalAlign: 'top' }}>
                <td style={{ padding: '6px 4px' }}>{d.label}</td>
                <td style={{ padding: '6px 4px' }}>{d.received ? (admin ? (d.filename + (d.version > 1 ? ' (v' + d.version + ')' : '') + ' · ' + when(d.uploaded_at)) : 'Yes') : 'Missing'}</td>
                <td style={{ padding: '6px 4px', color: d.review === 'needs_correction' ? '#a1340c' : d.review === 'accepted' ? '#0b6b4a' : '#607486' }}>
                  {d.review_label}{d.correction_note ? ': ' + d.correction_note : ''}
                </td>
                <td style={{ padding: '6px 4px' }}>
                  {admin && d.document_id && (
                    <>
                      <button style={btn} onClick={() => view(d)}>View</button>
                      <button style={btn} onClick={() => download(d)}>Download</button>
                      {d.review !== 'accepted' && <button style={btn} disabled={!!busy} onClick={() => run('acc' + d.kind,
                        () => api.post(base + '/documents/' + d.document_id + '/review', { status: 'accepted' }), d.label + ' accepted.')}>Accept</button>}
                      <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                        <input aria-label={'Correction note for ' + d.label} placeholder="What needs correcting?"
                               value={notes[d.kind] || ''} onChange={e => setNotes(n => ({ ...n, [d.kind]: e.target.value }))}
                               style={{ ...btn, cursor: 'text', flex: 1, minWidth: 140, marginTop: 0 }} />
                        <button style={{ ...btn, marginTop: 0 }} disabled={!!busy || !(notes[d.kind] || '').trim()} onClick={() => run('fix' + d.kind,
                          () => api.post(base + '/documents/' + d.document_id + '/review', { status: 'needs_correction', note: notes[d.kind] }),
                          d.label + ' marked for correction.')}>Needs correction</button>
                      </div>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {admin && <p style={{ fontSize: 12, color: '#607486', margin: '6px 0 0' }}>Files are kept in EvoSys Pro's private storage (not Google Drive). They never go out by email or public link. Every view and download is recorded. Review here is a manual check by an owner, not an identity-verification service.</p>}
      </div>

      {admin && (
        <div style={sect}>
          <b>Agreement</b>
          <p style={{ margin: '4px 0' }}>
            {ag ? <>{ag.title} · version {ag.version_label} · Exhibit A {ag.exhibit_version} — <b>{ag.status_label}</b>
              {ag.sent_at ? ' · sent ' + when(ag.sent_at) : ''}{ag.viewed_at ? ' · opened ' + when(ag.viewed_at) : ''}
              {ag.signed_at ? ' · signed ' + when(ag.signed_at) + ' as “' + ag.signer_typed_name + '”' : ''}
              {ag.note ? <span style={{ color: '#a1340c' }}> · {ag.note}</span> : null}</> : 'Not sent yet.'}
          </p>
          {ag?.has_signed_copy && <button style={btn} onClick={signedCopy}>View signed copy</button>}
          {ag?.status === 'awaiting_countersign' && (
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <input aria-label="Type your full name to countersign" placeholder="Type your full name" value={counter}
                     onChange={e => setCounter(e.target.value)} style={{ ...btn, cursor: 'text' }} />
              <button style={primary} disabled={!!busy || !counter.trim()} onClick={() => run('cs',
                () => api.post(base + '/agreement-countersign', { typed_name: counter }), 'Countersigned. The signed copy is saved.')}>Countersign</button>
            </div>
          )}
          {(!ag || ag.status === 'correction_needed' || ag.status === 'sent') && (
            templates.length ? (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginTop: 4 }}>
                <select aria-label="Agreement version" value={tpl} onChange={e => setTpl(e.target.value)} style={btn}>
                  <option value="">Choose the approved version…</option>
                  {templates.map(t => <option key={t.id} value={t.id}>{t.title} — v{t.version_label} / Exhibit A {t.exhibit_version}</option>)}
                </select>
                <button style={primary} disabled={!!busy || !tpl} onClick={() => run('send',
                  () => api.post(base + '/send-agreement', { template_id: tpl }),
                  r => r.email_sent ? 'Sent for signature. They were emailed a link to sign in and sign (no attachment).' : 'Ready to sign in their onboarding page. The email could not be sent - tell them to sign in.')}>
                  {ag ? 'Send new version' : 'Send for signature'}
                </button>
              </div>
            ) : <p style={{ color: '#79551a', margin: '4px 0' }}>No agreement version is approved for signing yet. Add one under Agreement versions on My Team.</p>
          )}
          {ag?.status === 'sent' && (
            <button style={btn} disabled={!!busy} onClick={() => { const n = window.prompt('What needs to change in the agreement?'); if (n) run('agfix', () => api.post(base + '/agreement-correction', { note: n }), 'Agreement marked as needing a correction.') }}>Agreement needs correction</button>
          )}
        </div>
      )}

      {admin && ob.status !== 'approved' && (
        <div style={sect}>
          <b>Decision</b>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginTop: 4 }}>
            <input aria-label="Note for corrections" placeholder="Note to the contractor (for corrections)" value={backNote}
                   onChange={e => setBackNote(e.target.value)} style={{ ...btn, cursor: 'text', minWidth: 240, flex: 1 }} />
            <button style={btn} disabled={!!busy} onClick={() => run('back', () => api.post(base + '/send-back', { note: backNote }),
              r => 'Sent back for corrections.' + (r.email_sent ? ' They were emailed a link to each item.' : ' The email could not be sent.'))}>Send Back for Corrections</button>
            <button style={primary} disabled={!!busy} onClick={() => run('approve', () => api.post(base + '/approve', {}),
              'Approved. Their Sales Workspace and Deal Builder are open now.')}>Approve</button>
          </div>
        </div>
      )}
      <Viewer doc={viewer} onClose={() => setViewer(null)} />
    </div>
  )
}
