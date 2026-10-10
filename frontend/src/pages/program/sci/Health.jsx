/* Health: compact service tiles first, technical detail below
   (GET /program/health, refreshed every minute). Texting is shown as four
   separate facts - configured, approved, switched on, seen working live - so
   "configured" is never mistaken for "tested". A block the server did not
   report is shown as Unknown. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, Loading, PageHead, STATUS_TONE, errText, when } from './ui'

const TILES = [
  ['email_system', 'Email'], ['sender_domain', 'Sender domain'], ['mailbox', 'Reply mailbox'], ['aliases', 'Location emails'],
  ['sms', 'Texting · 844 line'], ['webhook', 'Delivery events'], ['campaigns', 'Campaigns'], ['held_contacts', 'Held contacts'],
]
const CHECKS = [
  ['failed_sends', 'Failed sends'], ['bounces', 'Bounces'], ['complaints', 'Complaints'], ['unmatched_replies', 'Unmatched replies'],
  ['hot_responses', 'HOT responses (open)'], ['unhandled_hot', 'Unhandled HOT (past SLA)'], ['response_time', 'Response time'],
  ['automation_errors', 'Automation errors'], ['outlook_filing', 'Outlook filing'],
]
const MARK = { ok: '✓', warn: '!', fail: '!', off: '–' }
const LABEL = { ok: 'Working', warn: 'Check', fail: 'Needs attention', off: 'Off' }

const detailOf = b => b?.detail ?? (b?.count !== undefined ? `${b.count}` : b?.open !== undefined ? `${b.open} open` : '')

export default function Health() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    setBusy(true)
    api.get('/program/health').then(x => { setD(x); setErr('') }).catch(e => setErr(errText(e))).finally(() => setBusy(false))
  }, [])
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [load])
  if (!d) return <><PageHead title="System health" />{err ? <ErrorLine text={err} /> : <Loading label="Checking…" />}</>

  const sms = d.sms || null
  return (
    <>
      <PageHead title="System health" sub="Operational clarity first. Technical diagnostics when needed.">
        <Chip tone={STATUS_TONE[d.overall]}>{d.overall === 'ok' ? 'All working' : d.overall === 'fail' ? 'Needs attention' : 'Check warnings'}</Chip>
        <button type="button" className="sci-btn sm" disabled={busy} onClick={load}>{busy ? 'Checking…' : 'Check now'}</button>
      </PageHead>
      <p className="sci-micro sci-muted" style={{ marginTop: -12, marginBottom: 16 }}>Checked {when(d.generated_at)} · refreshes every minute</p>
      <ErrorLine text={err} />

      <section className="sci-tiles" aria-label="Services">
        {TILES.map(([k, label]) => {
          const b = d[k]
          const st = b?.status
          const tone = st ? (st === 'fail' ? 'bad' : st) : 'off'
          return (
            <div className="sci-tile" key={k}>
              <span className={`sci-tile-icon ${tone}`} aria-hidden="true">{st ? MARK[st] || '•' : '?'}</span>
              <h3>{label}</h3>
              <p><Chip tone={STATUS_TONE[st] || ''} plain>{st ? LABEL[st] || st : 'Unknown'}</Chip></p>
              <p style={{ marginTop: 6 }}>{b ? (k === 'sms' ? smsLine(b) : detailOf(b) || '—') : 'Not reported by the server'}</p>
            </div>
          )
        })}
      </section>

      {sms && (
        <section className="sci-panel sci-pad" style={{ marginBottom: 16 }} aria-labelledby="sci-texting">
          <h2 id="sci-texting" className="sci-h2" style={{ marginBottom: 6 }}>Texting, fact by fact</h2>
          <div className="sci-rows">
            <Fact k="Number" v={sms.number || 'None'} ok={!!sms.number} />
            <Fact k="Account configured on this server" v={sms.configured ? 'Yes' : 'No'} ok={!!sms.configured} />
            <Fact k="Approved by the carrier / Twilio" v={sms.approved == null ? 'Unknown' : sms.approved ? `Yes${sms.approved_scope ? ` · ${sms.approved_scope}` : ''}` : 'No'} ok={sms.approved} />
            <Fact k="Sending switched on" v={sms.sending_enabled ? 'On' : 'Off'} ok={!!sms.sending_enabled} />
            <Fact k="Seen working live" v={sms.last_inbound_at ? `Last inbound text ${when(sms.last_inbound_at)}` : 'No inbound text seen yet'} ok={sms.last_inbound_at ? true : null} />
          </div>
        </section>
      )}

      <div className="sci-grid-even">
        <section className="sci-panel sci-pad" aria-labelledby="sci-opchecks">
          <h2 id="sci-opchecks" className="sci-h2" style={{ marginBottom: 6 }}>Operational checks</h2>
          <div className="sci-rows">
            {CHECKS.map(([k, label]) => d[k] ? (
              <div className="sci-kv" key={k}><span>{label}</span><span><Chip tone={STATUS_TONE[d[k].status] || ''} plain>{d[k].status}</Chip> {detailOf(d[k])}</span></div>
            ) : null)}
            <div className="sci-kv"><span>Last successful</span>
              <span>inbound sync {when(d.last_successful?.inbound_sync)} · email send {when(d.last_successful?.email_send)} · webhook event {when(d.last_successful?.webhook_event)}</span>
            </div>
          </div>
        </section>
        <section className="sci-panel sci-pad" aria-labelledby="sci-tech">
          <h2 id="sci-tech" className="sci-h2" style={{ marginBottom: 6 }}>Technical diagnostics</h2>
          <div className="sci-rows">
            {[...TILES, ...CHECKS].filter(([k]) => d[k]).map(([k, label]) => (
              <details className="sci-details" key={k}>
                <summary>{label}</summary>
                <div className="sci-details-body">
                  <div>{detailOf(d[k]) || 'No detail.'}</div>
                  <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, margin: '8px 0 0', background: '#f6f8f8', padding: 10, borderRadius: 8 }}>{JSON.stringify(d[k], null, 2)}</pre>
                </div>
              </details>
            ))}
          </div>
        </section>
      </div>

      <Placement />

      <section className="sci-panel sci-pad" style={{ marginTop: 16 }} aria-labelledby="sci-decisions-feed">
        <h2 id="sci-decisions-feed" className="sci-h2" style={{ marginBottom: 6 }}>Recent automated decisions</h2>
        {(d.decisions || []).length === 0 ? <Empty>Nothing yet.</Empty> : (
          <div className="sci-rows">
            {d.decisions.map((x, i) => (
              <div className="sci-kv" key={i}><span>{x.kind.replace(/_/g, ' ')} · {x.text}</span><span>{when(x.at)}</span></div>
            ))}
          </div>
        )}
      </section>
    </>
  )
}

function smsLine(b) {
  if (b.configured === undefined) return detailOf(b)
  const bits = [b.number || 'No number', b.configured ? 'configured' : 'not configured',
    b.approved == null ? 'approval unknown' : b.approved ? 'approved' : 'not approved', b.sending_enabled ? 'sending on' : 'sending off']
  return bits.join(' · ')
}

function Fact({ k, v, ok }) {
  return (
    <div className="sci-kv">
      <span>{k}</span>
      <span><Chip tone={ok === true ? 'ok' : ok === false ? 'bad' : ''} plain>{v}</Chip></span>
    </div>
  )
}

/** Inbox placement checks: measured, never guaranteed. Seeds are inboxes Mike owns. */
function Placement() {
  const [d, setD] = useState(null)
  const [seeds, setSeeds] = useState({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => {
    api.get('/program/placement-checks').then(x => { setD(x); setSeeds(x.seeds || {}) }).catch(e => setMsg(errText(e)))
  }, [])
  useEffect(() => { load() }, [load])
  if (!d) return null
  const saveSeeds = async () => {
    try { await api.patch('/program/settings', { placement_seed_addresses: seeds }); setMsg('Seed inboxes saved.'); load() } catch (e) { setMsg(errText(e)) }
  }
  const send = async () => {
    setBusy(true)
    try { const r = await api.post('/program/placement-checks/send', {}); setMsg(`Sent ${r.checks.filter(c => c.sent_at).length} sample(s). Open each seed inbox and record where it landed.`); load() } catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }
  const mark = async (id, folder) => {
    try { await api.post(`/program/placement-checks/${id}/result`, { folder }); load() } catch (e) { setMsg(errText(e)) }
  }
  return (
    <section className="sci-panel sci-pad" style={{ marginTop: 16 }} aria-labelledby="sci-placement">
      <div className="sci-panel-head"><h2 id="sci-placement">Inbox placement</h2><Chip tone={d.readiness.ok ? 'ok' : 'warn'}>{d.readiness.ok ? 'Ready' : 'Not proven'}</Chip></div>
      <p className="sci-small sci-muted" style={{ marginTop: 0 }}>{d.readiness.detail}. Seeds are inboxes you own — never customers.</p>
      <div className="sci-form">
        {d.providers.map(p => (
          <label key={p} className="sci-field">{p} seed inbox
            <input value={seeds[p] || ''} onChange={e => setSeeds({ ...seeds, [p]: e.target.value })} placeholder={`${p} address`} />
          </label>
        ))}
      </div>
      <div className="sci-row-gap" style={{ marginTop: 12 }}>
        <button type="button" className="sci-btn" onClick={saveSeeds}>Save seeds</button>
        <button type="button" className="sci-btn primary" disabled={busy} onClick={send}>{busy ? 'Sending…' : 'Send placement check'}</button>
      </div>
      {msg && <p className="sci-small" role="status">{msg}</p>}
      {d.checks.length > 0 && (
        <div className="sci-rows" style={{ marginTop: 10 }}>
          {d.checks.map(c => (
            <div className="sci-kv" key={c.id}>
              <span>{c.provider} · {c.seed_address} · {c.sent_at ? when(c.sent_at) : (c.send_error || 'not sent')}</span>
              <select className="sci-input" style={{ width: 'auto', minHeight: 34, fontSize: 13 }} aria-label={`Where it landed at ${c.provider}`} value={c.folder || ''} onChange={e => mark(c.id, e.target.value)}>
                <option value="">where did it land?</option>
                {d.folders.map(f => <option key={f} value={f}>{f.replace('_', ' ')}</option>)}
              </select>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
