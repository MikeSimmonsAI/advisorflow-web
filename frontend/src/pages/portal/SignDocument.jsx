/* EvoSys e-signature - the signer's page (/sign/:token).
 *
 * No account. The private link is the authorization; the server stores only a
 * hash of it. Steps: read the document -> confirm the 6-digit code emailed to
 * you -> agree to sign electronically -> type or draw your signature -> Sign.
 * Built for a phone first: most sellers will open this from a text or email.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE as API } from '../../api/client'

const C = {
  ink: '#0f172a', mute: '#64748b', line: '#e2e8f0', brand: '#1d4ed8', ok: '#047857', bad: '#b91c1c', bg: '#f8fafc',
}
const box = { background: '#fff', border: `1px solid ${C.line}`, borderRadius: 12, padding: 16, margin: '12px 0' }
const btn = (primary, disabled) => ({
  appearance: 'none', border: primary ? 'none' : `1px solid ${C.line}`, borderRadius: 10, padding: '13px 18px',
  fontSize: 16, fontWeight: 700, cursor: disabled ? 'default' : 'pointer', width: '100%',
  background: disabled ? '#cbd5e1' : primary ? C.brand : '#fff', color: primary ? '#fff' : C.ink, marginTop: 8,
})
const input = { width: '100%', boxSizing: 'border-box', fontSize: 18, padding: '12px 14px', borderRadius: 10,
  border: `1px solid ${C.line}`, marginTop: 6 }

async function call(path, method = 'GET', body) {
  let r
  try {
    r = await fetch(API + '/esign/sign/' + path, {
      method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error("We couldn't reach the signing service. Check your connection and try again.")
  }
  let j = null
  try { j = await r.json() } catch { j = null }
  if (!r.ok) throw new Error((j && j.detail) || 'Something went wrong. Please try again.')
  return j
}

function Pad({ onChange }) {
  const ref = useRef(null)
  const drawing = useRef(false)
  const strokes = useRef(0)

  useEffect(() => {
    const c = ref.current
    const ratio = window.devicePixelRatio || 1
    c.width = c.offsetWidth * ratio
    c.height = c.offsetHeight * ratio
    const g = c.getContext('2d')
    g.scale(ratio, ratio)
    g.lineWidth = 2.6; g.lineCap = 'round'; g.lineJoin = 'round'; g.strokeStyle = '#1e3a8a'
  }, [])

  function pos(e) {
    const r = ref.current.getBoundingClientRect()
    return [e.clientX - r.left, e.clientY - r.top]
  }
  function down(e) {
    e.preventDefault(); ref.current.setPointerCapture(e.pointerId)
    drawing.current = true
    const g = ref.current.getContext('2d'); const [x, y] = pos(e)
    g.beginPath(); g.moveTo(x, y)
  }
  function move(e) {
    if (!drawing.current) return
    const g = ref.current.getContext('2d'); const [x, y] = pos(e)
    g.lineTo(x, y); g.stroke()
  }
  function up() {
    if (!drawing.current) return
    drawing.current = false; strokes.current += 1
    onChange(strokes.current >= 1 ? ref.current.toDataURL('image/png') : null)
  }
  function clear() {
    const c = ref.current; c.getContext('2d').clearRect(0, 0, c.width, c.height)
    strokes.current = 0; onChange(null)
  }
  return (
    <div>
      <canvas ref={ref} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up}
              style={{ width: '100%', height: 150, border: `1px dashed ${C.mute}`, borderRadius: 10,
                       touchAction: 'none', background: '#fff' }} aria-label="Draw your signature here" />
      <button type="button" onClick={clear} style={{ ...btn(false), width: 'auto', padding: '8px 14px', fontSize: 14 }}>
        Clear</button>
    </div>
  )
}

const STATUS = { waiting: 'Waiting', sent: 'Link sent', viewed: 'Opened', verified: 'Verified', signed: 'Signed',
  declined: 'Declined' }

export default function SignDocument() {
  const { token } = useParams()
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [codeSent, setCodeSent] = useState(false)
  const [code, setCode] = useState('')
  const [mode, setMode] = useState('type')
  const [name, setName] = useState('')
  const [drawn, setDrawn] = useState(null)
  const [consent, setConsent] = useState(false)
  const [done, setDone] = useState(null)
  const [declining, setDeclining] = useState(false)
  const [reason, setReason] = useState('')

  const load = useCallback(async () => {
    try {
      const j = await call(token)
      setD(j); setErr(null)
      document.title = `Sign: ${j.title} · EvoSys`
      setName((n) => n || (j.signer && j.signer.name) || '')
    } catch (e) { setErr(e.message) }
  }, [token])
  useEffect(() => { load() }, [load])

  async function step(fn) {
    setBusy(true); setErr(null)
    try { await fn() } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const sendCode = () => step(async () => { await call(token + '/code', 'POST', {}); setCodeSent(true) })
  const verify = () => step(async () => { await call(token + '/verify', 'POST', { code }); await load() })
  const signIt = () => step(async () => {
    const r = await call(token + '/sign', 'POST', { consent, typed_name: name,
      drawn_png: mode === 'draw' ? drawn : null })
    setDone(r); await load()
  })
  const decline = () => step(async () => { await call(token + '/decline', 'POST', { reason }); await load() })

  const page = { minHeight: '100vh', background: C.bg, color: C.ink, padding: '16px 14px 40px',
    fontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif' }
  const wrap = { maxWidth: 760, margin: '0 auto' }

  if (!d) {
    return <div style={page}><div style={wrap}>
      <h1 style={{ fontSize: 22 }}>EvoSys e-signature</h1>
      <div style={{ ...box, color: err ? C.bad : C.mute }}>{err || 'Loading the document…'}</div>
    </div></div>
  }

  const verified = d.signer && d.signer.verified
  const canSign = d.can_sign
  const ready = consent && name.trim().length >= 2 && (mode === 'type' || drawn)

  return (
    <div style={page}><div style={wrap}>
      <div style={{ fontSize: 13, color: C.mute, fontWeight: 700, letterSpacing: 0.5 }}>EVOSYS E-SIGNATURE</div>
      <h1 style={{ fontSize: 22, margin: '6px 0 4px' }}>{d.title}</h1>
      <div style={{ color: C.mute }}>From {d.sender}{d.organization && d.organization !== d.sender ? ` · ${d.organization}` : ''}</div>
      {d.message ? <div style={{ ...box, background: '#eff6ff' }}>{d.message}</div> : null}

      <div style={{ ...box, padding: 12 }}>
        {d.parties.map((p) => (
          <div key={p.role} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', fontSize: 15 }}>
            <span><b>{p.role}</b>{p.name ? ` · ${p.name}` : ''}{p.you ? ' (you)' : ''}</span>
            <span style={{ color: p.status === 'signed' ? C.ok : p.status === 'declined' ? C.bad : C.mute }}>
              {STATUS[p.status] || p.status}</span>
          </div>
        ))}
      </div>

      {err ? <div style={{ ...box, borderColor: C.bad, color: C.bad }}>{err}</div> : null}

      {d.completed ? (
        <div style={{ ...box, borderColor: C.ok }}>
          <b style={{ color: C.ok }}>Everyone has signed.</b> A signed copy was emailed to you.
          <a href={`${API}/esign/sign/${token}/signed.pdf`} style={{ ...btn(true), display: 'block', textAlign: 'center',
            textDecoration: 'none', boxSizing: 'border-box' }}>Download the signed PDF</a>
        </div>
      ) : null}
      {done && !d.completed ? (
        <div style={{ ...box, borderColor: C.ok }}><b style={{ color: C.ok }}>Signed. Thank you.</b> We'll email you
          the fully signed copy once {done.next || 'everyone'} signs.</div>
      ) : null}
      {d.status === 'voided' ? <div style={box}>The sender withdrew this document. Nothing more is needed.</div> : null}
      {d.status === 'declined' ? <div style={box}>This document was declined.</div> : null}
      {d.status === 'expired' ? <div style={box}>This link has expired. Ask the sender to send it again.</div> : null}

      <div style={{ ...box, padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '10px 14px', borderBottom: `1px solid ${C.line}`, fontWeight: 700 }}>
          Read the document</div>
        <iframe title="Document" sandbox=""
                srcDoc={String(d.document_html || '').replace('</head>', '<style>body{padding:18px}</style></head>')}
                style={{ width: '100%', height: '62vh', border: 0, background: '#fff' }} />
      </div>

      {canSign && !done ? (
        !verified ? (
          <div style={box}>
            <b>Step 1 · Confirm it's you</b>
            <p style={{ color: C.mute, margin: '6px 0' }}>
              We'll email a 6-digit code to {d.signer.email_masked}.</p>
            {!codeSent ? (
              <button type="button" style={btn(true, busy)} disabled={busy} onClick={sendCode}>Email me a code</button>
            ) : (
              <>
                <input style={{ ...input, letterSpacing: 6, textAlign: 'center' }} inputMode="numeric"
                       autoComplete="one-time-code" maxLength={6} placeholder="123456" value={code}
                       onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} aria-label="6-digit code" />
                <button type="button" style={btn(true, busy || code.length !== 6)} disabled={busy || code.length !== 6}
                        onClick={verify}>Confirm code</button>
                <button type="button" style={btn(false, busy)} disabled={busy} onClick={sendCode}>Send a new code</button>
              </>
            )}
          </div>
        ) : (
          <div style={box}>
            <b>Step 2 · Sign as {d.signer.role}</b>
            <label style={{ display: 'block', marginTop: 10, fontSize: 14, color: C.mute }}>Your full legal name
              <input style={input} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
            </label>
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              {['type', 'draw'].map((m) => (
                <button key={m} type="button" onClick={() => setMode(m)}
                        style={{ ...btn(mode === m), marginTop: 0, padding: '9px 12px', fontSize: 15 }}>
                  {m === 'type' ? 'Use my typed name' : 'Draw my signature'}</button>
              ))}
            </div>
            {mode === 'type' ? (
              <div style={{ fontFamily: '"Brush Script MT","Segoe Script",cursive', fontSize: 34, color: '#1e3a8a',
                            borderBottom: `1px solid ${C.mute}`, padding: '14px 4px 4px', minHeight: 44, marginTop: 10 }}>
                {name || ' '}</div>
            ) : (
              <div style={{ marginTop: 10 }}><Pad onChange={setDrawn} /></div>
            )}
            <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', marginTop: 14, fontSize: 14, lineHeight: 1.45 }}>
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)}
                     aria-label="I agree to sign and receive this document electronically"
                     style={{ width: 22, height: 22, flex: '0 0 auto', marginTop: 2 }} />
              <span>{d.consent_text}</span>
            </label>
            <button type="button" style={btn(true, busy || !ready)} disabled={busy || !ready} onClick={signIt}>
              {busy ? 'Signing…' : 'Sign'}</button>
            <p style={{ fontSize: 12, color: C.mute, marginTop: 8 }}>
              By clicking Sign you are signing this document electronically. We record the time, your IP address
              and your device with your signature.</p>
          </div>
        )
      ) : null}

      {canSign && !done ? (
        <div style={{ textAlign: 'center', marginTop: 6 }}>
          {!declining ? (
            <button type="button" onClick={() => setDeclining(true)}
                    style={{ background: 'none', border: 'none', color: C.mute, textDecoration: 'underline', fontSize: 14 }}>
              I don't want to sign this</button>
          ) : (
            <div style={box}>
              <b>Decline to sign</b>
              <textarea style={{ ...input, fontSize: 15 }} rows={3} placeholder="Tell the sender why (optional)"
                        value={reason} onChange={(e) => setReason(e.target.value)} />
              <button type="button" style={{ ...btn(true, busy), background: C.bad }} disabled={busy} onClick={decline}>
                Decline</button>
              <button type="button" style={btn(false)} onClick={() => setDeclining(false)}>Go back</button>
            </div>
          )}
        </div>
      ) : null}

      <p style={{ fontSize: 11, color: C.mute, marginTop: 24, textAlign: 'center' }}>
        Secured by EvoSys e-signature · document fingerprint {String(d.document_sha256 || '').slice(0, 16)}…</p>
    </div></div>
  )
}
