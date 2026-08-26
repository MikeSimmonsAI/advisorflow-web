/**
 * The secure deal room — what the CUSTOMER sees.
 *
 * Public. No login, no account, no app chrome. The token in the URL is the
 * whole authorization, which is why the page renders only what
 * `/deal-room/:token` returns and never calls an authenticated endpoint.
 *
 * IT CANNOT LEAK INTERNAL DATA, because it has none. The payload is a
 * server-side whitelist; there is no opportunity object, no stage, no owner,
 * no internal note and no list price anywhere in this component's state. A
 * mistake here cannot expose what was never sent.
 *
 * Activity tracking is honest: we report opening the room, viewing the
 * proposal, and opening a link or document — each one a real request the
 * server handled. No scroll timers, no "time spent reading" estimates.
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE as API } from '../../api/client'

function money(amount, currency) {
  if (amount === null || amount === undefined) return null
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency: currency || 'USD', maximumFractionDigits: 0,
    }).format(amount)
  } catch { return (currency || 'USD') + ' ' + amount }
}

function md(text) {
  // Intentionally tiny: headings, bold, paragraphs. Not a markdown engine and
  // not dangerouslySetInnerHTML — proposal prose is typed by a salesperson,
  // and rendering it as HTML would make every proposal an XSS vector aimed at
  // the customer.
  const lines = String(text || '').split('\n')
  return lines.map((line, i) => {
    if (line.startsWith('## ')) return <h2 key={i}>{line.slice(3)}</h2>
    if (line.startsWith('# ')) return <h2 key={i}>{line.slice(2)}</h2>
    if (!line.trim()) return <div key={i} style={{ height: 8 }} />
    const bold = line.match(/^\*\*(.+)\*\*$/)
    if (bold) return <p key={i}><strong>{bold[1]}</strong></p>
    return <p key={i}>{line}</p>
  })
}


const CSS = `
.dr-wrap{min-height:100vh;background:#f6f8fb;padding:40px 16px;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#111827}
.dr-card{max-width:760px;margin:0 auto;background:#fff;border:1px solid #e5e7eb;
  border-radius:16px;box-shadow:0 6px 30px rgba(15,25,40,.06);overflow:hidden}
.dr-head{padding:30px 34px;border-bottom:1px solid #eef2f6}
.dr-brand{font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}
.dr-title{font-size:26px;font-weight:700;margin:10px 0 4px;line-height:1.25}
.dr-sub{color:#6b7280;font-size:14px}
.dr-body{padding:26px 34px}
.dr-body h2{font-size:15px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;
  color:#374151;margin:26px 0 10px}
.dr-body p{font-size:15px;line-height:1.65;margin:0 0 10px}
.dr-amount{background:#f8fafc;border:1px solid #e5e7eb;border-radius:12px;
  padding:18px 20px;margin:22px 0}
.dr-amount .n{font-size:30px;font-weight:800;letter-spacing:-.02em}
.dr-res{display:block;border:1px solid #e5e7eb;border-radius:12px;padding:15px 17px;
  margin:10px 0;text-decoration:none;color:inherit;background:#fff}
.dr-res:hover{border-color:#c7d2fe;background:#fbfcff}
.dr-res b{display:block;font-size:14px}
.dr-res span{font-size:12px;color:#6b7280}
.dr-actions{padding:24px 34px;border-top:1px solid #eef2f6;background:#fafbfd}
.dr-btn{font:inherit;font-weight:700;font-size:14px;padding:13px 22px;border-radius:9px;
  border:1px solid transparent;cursor:pointer;margin:0 10px 10px 0}
.dr-yes{color:#fff}
.dr-no{background:#fff;border-color:#d1d5db;color:#374151}
.dr-foot{text-align:center;color:#9ca3af;font-size:12px;margin:22px auto;max-width:760px}
.dr-note{width:100%;font:inherit;font-size:14px;padding:11px;border:1px solid #d1d5db;
  border-radius:9px;margin-bottom:12px;box-sizing:border-box}
.dr-done{padding:18px 20px;border-radius:12px;font-size:15px;margin-bottom:16px}
.dr-ok{background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46}
.dr-info{background:#eff6ff;border:1px solid #bfdbfe;color:#1e40af}
.dr-msg{max-width:520px;margin:80px auto;text-align:center;color:#374151}
iframe.dr-embed{width:100%;height:460px;border:1px solid #e5e7eb;border-radius:12px}
@media(max-width:640px){.dr-head,.dr-body,.dr-actions{padding-left:20px;padding-right:20px}}
`


export default function DealRoom() {
  const { token } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [showNote, setShowNote] = useState(null)
  const viewed = useRef(false)

  const track = useCallback((event_type, label, block_id) => {
    // Fire and forget. A tracking failure must never break the page the
    // customer is trying to read.
    fetch(API + '/deal-room/' + token + '/track', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ event_type, label, block_id }),
    }).catch(() => {})
  }, [token])

  useEffect(() => {
    let alive = true
    fetch(API + '/deal-room/' + token)
      .then(async r => {
        const body = await r.json().catch(() => ({}))
        if (!r.ok) throw new Error(body.detail || 'This link is not available.')
        return body
      })
      .then(d => {
        if (!alive) return
        setData(d)
        // The proposal is on screen the moment the room renders, so this is a
        // real observation rather than an assumption about what they read.
        if (!viewed.current) {
          viewed.current = true
          track('proposal_viewed')
        }
      })
      .catch(e => alive && setError(e.message))
    return () => { alive = false }
  }, [token, track])

  async function decide(action) {
    setBusy(true)
    try {
      const r = await fetch(API + '/deal-room/' + token + '/decision', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, note: note.trim() || null }),
      })
      const body = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(body.detail || 'That did not go through.')
      setData(d => ({ ...d, proposal: { ...d.proposal, ...body.proposal } }))
      setShowNote(null)
      setNote('')
    } catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }

  if (error) {
    return (
      <div className="dr-wrap">
        <style>{CSS}</style>
        <div className="dr-msg">
          <h2>{error}</h2>
          <p>Please contact whoever sent you this link and ask for a new one.</p>
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="dr-wrap"><style>{CSS}</style>
        <div className="dr-msg">Loading…</div>
      </div>
    )
  }

  const p = data.proposal
  const brand = data.brand || {}
  const accent = brand.accent || '#1d4ed8'
  const decided = p.accepted_at || p.declined_at || p.change_requested_at

  return (
    <div className="dr-wrap">
      <style>{CSS}</style>
      <div className="dr-card">
        <div className="dr-head">
          <div className="dr-brand" style={{ color: accent }}>{brand.name}</div>
          <div className="dr-title">{p.title}</div>
          <div className="dr-sub">
            {p.subtitle}
            {p.number ? ' · ' + p.number + (p.version > 1 ? ' (v' + p.version + ')' : '') : ''}
          </div>
        </div>

        <div className="dr-body">
          {decided && (
            <div className={'dr-done ' + (p.accepted_at ? 'dr-ok' : 'dr-info')}>
              {p.accepted_at && <b>You accepted this proposal. We'll be in touch shortly.</b>}
              {p.declined_at && <b>You let us know this isn't right for you. Thank you for looking.</b>}
              {p.change_requested_at && !p.accepted_at && !p.declined_at &&
                <b>You asked for a change. We're working on a revised version.</b>}
            </div>
          )}

          {(data.blocks || []).map(b => {
            if (b.block_type === 'text') {
              return <div key={b.id}>{md(b.content)}</div>
            }
            if (b.block_type === 'divider') {
              return <hr key={b.id} style={{ border: 0, borderTop: '1px solid #eef2f6', margin: '26px 0' }} />
            }
            if (b.block_type === 'image') {
              return <img key={b.id} src={b.file_url} alt={b.content || ''}
                          style={{ maxWidth: '100%', borderRadius: 12, margin: '14px 0' }} />
            }
            if (b.block_type === 'video') {
              return (
                <iframe key={b.id} className="dr-embed" src={b.file_url}
                        title={b.content || 'Video'} allowFullScreen
                        onLoad={() => track('link_opened', b.content || 'Video', b.id)} />
              )
            }
            if (b.block_type === 'website_url' || b.block_type === 'cta' || b.block_type === 'pdf') {
              const isDemo = b.block_type === 'website_url'
              const label = b.content || (isDemo ? 'Open the demo' : 'Open')
              return (
                <a key={b.id} className="dr-res" href={b.file_url}
                   target="_blank" rel="noopener noreferrer"
                   onClick={() => track(
                     isDemo ? 'demo_opened'
                            : (b.block_type === 'pdf' ? 'document_opened' : 'link_opened'),
                     label, b.id)}>
                  <b>{label}</b>
                  <span>{b.file_url}</span>
                </a>
              )
            }
            return null
          })}

          {p.amount !== null && p.amount !== undefined && (
            <div className="dr-amount">
              <div className="dr-sub">Investment</div>
              <div className="n">{money(p.amount, p.currency)}</div>
            </div>
          )}
        </div>

        {!decided && (
          <div className="dr-actions">
            {showNote && (
              <textarea className="dr-note" rows={3} value={note}
                        placeholder={showNote === 'request_change'
                          ? 'What would you like changed?'
                          : 'Anything you want us to know? (optional)'}
                        onChange={e => setNote(e.target.value)} />
            )}
            <button className="dr-btn dr-yes" style={{ background: accent }}
                    disabled={busy}
                    onClick={() => showNote === 'accept' ? decide('accept') : setShowNote('accept')}>
              {showNote === 'accept' ? 'Confirm — accept this proposal' : 'Accept proposal'}
            </button>
            <button className="dr-btn dr-no" disabled={busy}
                    onClick={() => showNote === 'request_change'
                      ? decide('request_change') : setShowNote('request_change')}>
              {showNote === 'request_change' ? 'Send my request' : 'Request a change'}
            </button>
            <button className="dr-btn dr-no" disabled={busy}
                    onClick={() => decide('decline')}>
              Not right for us
            </button>
          </div>
        )}
      </div>

      <div className="dr-foot">
        {brand.name}
        {brand.support_phone ? ' · ' + brand.support_phone : ''}
        {brand.support_email ? ' · ' + brand.support_email : ''}
        {p.expires_at && !decided && (
          <div style={{ marginTop: 6 }}>
            This proposal is valid until {new Date(p.expires_at + 'Z').toLocaleDateString()}.
          </div>
        )}
      </div>
    </div>
  )
}
