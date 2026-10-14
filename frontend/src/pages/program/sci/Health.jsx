/* Health: eight compact service modules first, then texting fact by fact,
   operational checks and technical diagnostics on demand
   (GET /program/health, refreshed every minute). Texting is four separate
   facts - configured, carrier approved, switched on, proven by a live inbound
   text - so "configured" is never mistaken for "tested". A block the server
   did not report is "Unknown". When delivery events are off, bounce/complaint
   counts are "Not tracked" - never a green zero. No secret ever reaches the
   browser: the server reports status and detail only. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../../api/client'
import SelfCheck from '../SelfCheck'
import { Chip, Drawer, Empty, ErrorLine, Icon, Kpi, Loading, PageHead, Panel, STATUS_TONE, errText, ago, when } from './ui'

const TILES = [
  ['email_system', 'Email system', 'mail'], ['sender_domain', 'Sender domain', 'globe'], ['mailbox', 'Reply mailbox', 'inbox'],
  ['aliases', 'Location aliases', 'pin'], ['sms', 'Texting line', 'sms'], ['webhook', 'Delivery events', 'bolt'],
  ['campaigns', 'Campaign runner', 'campaigns'], ['held_contacts', 'Held contacts', 'pause'],
]
const CHECKS = [
  ['failed_sends', 'Failed sends'], ['bounces', 'Bounces', true], ['complaints', 'Complaints', true], ['unmatched_replies', 'Unmatched replies'],
  ['hot_responses', 'HOT responses (open)'], ['unhandled_hot', 'HOT past SLA'], ['response_time', 'Response time'],
  ['automation_errors', 'Automation errors'], ['outlook_filing', 'Outlook filing'],
]
const MARK = { ok: 'check', warn: 'alert', fail: 'alert', off: 'pause' }
const LABEL = { ok: 'Working', warn: 'Check', fail: 'Needs attention', off: 'Off' }

const detailOf = b => b?.detail ?? (b?.count !== undefined ? `${b.count}` : b?.open !== undefined ? `${b.open} open` : '')

export default function Health({ goTab }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(null)
  const load = useCallback(() => {
    setBusy(true)
    api.get('/program/health').then(x => { setD(x); setErr('') }).catch(e => setErr(errText(e))).finally(() => setBusy(false))
  }, [])
  useEffect(() => { load(); const t = setInterval(load, 60000); return () => clearInterval(t) }, [load])
  if (!d) return <><PageHead title="System Health" />{err ? <ErrorLine text={err} /> : <Loading label="Checking…" />}</>

  const sms = d.sms || null
  const eventsOn = d.webhook ? d.webhook.status !== 'off' && d.webhook.status !== 'fail' : false
  const tiles = TILES.map(([k, label, icon]) => ({ k, label, icon, b: d[k] }))
  const attention = tiles.filter(t => t.b?.status === 'fail').length
  const warn = tiles.filter(t => t.b?.status === 'warn').length
  const working = tiles.filter(t => t.b?.status === 'ok').length
  const openTile = open ? tiles.find(t => t.k === open) || { k: open, label: (CHECKS.find(c => c[0] === open) || [])[1], b: d[open] } : null

  return (
    <>
      <PageHead title="System Health" sub="Operational truth first, technical diagnostics on demand.">
        <Chip tone={STATUS_TONE[d.overall]}>{d.overall === 'ok' ? 'All working' : d.overall === 'fail' ? 'Some services need attention' : 'Check warnings'}</Chip>
        <button type="button" className="sci-btn" disabled={busy} onClick={load}><Icon name="refresh" />{busy ? 'Checking…' : 'Refresh checks'}</button>
      </PageHead>
      <SelfCheck />

      <section className="sci-kpis four" aria-label="Health summary">
        <Kpi icon="check" tone="green" value={working} label="Modules working" note={`of ${TILES.length}`} />
        <Kpi icon="alert" tone="red" value={attention} label="Need attention" note={warn ? `${warn} more to check` : 'Failing now'} />
        <Kpi icon="bolt" tone={eventsOn ? 'blue' : 'gold'} value={eventsOn ? 'On' : 'Off'} label="Delivery events" note={eventsOn ? 'Bounces and complaints tracked' : 'Delivery not tracked'} />
        <Kpi icon="clock" tone="slate" value={ago(d.generated_at) || '—'} label="Last checked" note="Refreshes every minute" />
      </section>
      <ErrorLine text={err} />

      <section className="sci-tiles" aria-label="Services">
        {tiles.map(({ k, label, icon, b }) => {
          const st = b?.status
          const tone = st ? (st === 'fail' ? 'bad' : st) : 'off'
          return (
            <button type="button" className="sci-tile" key={k} onClick={() => setOpen(k)} aria-expanded={open === k}>
              <span className={`sci-tile-icon ${tone}`} aria-hidden="true"><Icon name={st ? icon : 'info'} /></span>
              <span className="sci-tile-body">
                <h3>{label}</h3>
                <p><Chip tone={STATUS_TONE[st] || ''} plain>{st ? LABEL[st] || st : 'Unknown'}</Chip></p>
                <p style={{ marginTop: 4, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                  {b ? (k === 'sms' ? smsLine(b) : detailOf(b) || '—') : 'Not reported by the server'}
                </p>
              </span>
            </button>
          )
        })}
      </section>

      <div className="sci-grid-even" style={{ marginBottom: 14 }}>
        <Panel title="Texting — verify each fact separately" labelledBy="sci-texting">
          {!sms ? <Empty icon="sms">Texting was not reported by the server.</Empty> : (
            <>
              <div className="sci-rows">
                <Fact k="Line" v={sms.number || 'None'} ok={!!sms.number} />
                <Fact k="Configured on this server" v={sms.configured ? 'Yes' : 'No'} ok={!!sms.configured} />
                <Fact k="Approved by the carrier" v={sms.approved == null ? 'Unknown' : sms.approved ? `Yes${sms.approved_scope ? ` · ${sms.approved_scope}` : ''}` : 'No'} ok={sms.approved} />
                <Fact k="Sending switched on" v={sms.sending_enabled ? 'On' : 'Off'} ok={!!sms.sending_enabled} />
                <Fact k="Proven by a live inbound text" v={sms.last_inbound_at ? `Last inbound ${when(sms.last_inbound_at)}` : 'Not seen yet'} ok={sms.last_inbound_at ? true : null} />
              </div>
              <div className="sci-banner" style={{ margin: '10px 0 0' }}>
                <span className="sci-banner-icon" aria-hidden="true"><Icon name="info" /></span>
                <div className="sci-banner-body sci-micro">Configured does not mean approved, switched on or tested. Each fact is checked separately.</div>
              </div>
            </>
          )}
        </Panel>
        <Panel title="Operational checks" labelledBy="sci-opchecks">
          <div className="sci-rows">
            {CHECKS.map(([k, label, needsEvents]) => {
              const b = d[k]
              if (!b) return <div className="sci-kv" key={k}><span>{label}</span><span><Chip plain>Unknown</Chip></span></div>
              const untracked = needsEvents && !eventsOn
              return (
                <button type="button" className="sci-kv sci-kvbtn" key={k} onClick={() => setOpen(k)}>
                  <span>{label}</span>
                  <span>{untracked ? <Chip tone="warn" plain title="Delivery events are off, so this cannot be counted">Not tracked</Chip>
                    : <><Chip tone={STATUS_TONE[b.status] || ''} plain>{LABEL[b.status] || b.status}</Chip> <span className="sci-micro">{shortOf(k, b)}</span></>}</span>
                </button>
              )
            })}
          </div>
          <p className="sci-footnote"><Icon name="info" />"Not tracked" means the server cannot see it - it is not zero delivered or zero bounced.</p>
        </Panel>
      </div>

      <Panel title="Technical diagnostics" labelledBy="sci-tech" aside={<span className="sci-micro sci-muted">Checked {when(d.generated_at)}</span>}>
        <details className="sci-details">
          <summary>Last successful events</summary>
          <div className="sci-details-body">
            Inbound sync {when(d.last_successful?.inbound_sync)} · email send {when(d.last_successful?.email_send)} · delivery event {when(d.last_successful?.webhook_event)}
          </div>
        </details>
        <details className="sci-details">
          <summary>Recent automated decisions ({(d.decisions || []).length})</summary>
          <div className="sci-details-body">
            {(d.decisions || []).length === 0 ? 'Nothing yet.' : (
              <div className="sci-rows">
                {d.decisions.map((x, i) => (
                  <div className="sci-kv" key={i}><span>{x.kind.replace(/_/g, ' ')} · {x.text}</span><span>{when(x.at)}</span></div>
                ))}
              </div>
            )}
          </div>
        </details>
        <details className="sci-details">
          <summary>Inbox placement</summary>
          <div className="sci-details-body"><Placement /></div>
        </details>
        <details className="sci-details">
          <summary>Raw module output (no secrets)</summary>
          <div className="sci-details-body">
            {[...TILES, ...CHECKS].filter(([k]) => d[k]).map(([k, label]) => (
              <div key={k} style={{ marginBottom: 8 }}><b>{label}</b><pre className="sci-pre">{JSON.stringify(d[k], null, 2)}</pre></div>
            ))}
          </div>
        </details>
      </Panel>

      <Drawer open={!!openTile} onClose={() => setOpen(null)} title={openTile?.label || ''} sub={openTile?.b ? `Status: ${LABEL[openTile.b.status] || openTile.b.status || 'Unknown'}` : 'Not reported by the server'}
        footer={openTile && ['campaigns'].includes(openTile.k) ? <button type="button" className="sci-btn" onClick={() => goTab('campaigns')}>Open Campaigns</button>
          : openTile && ['held_contacts'].includes(openTile.k) ? <button type="button" className="sci-btn" onClick={() => goTab('review', { queue: 'on_hold' })}>Open held records</button>
            : openTile && ['unmatched_replies', 'hot_responses', 'unhandled_hot'].includes(openTile.k) ? <button type="button" className="sci-btn" onClick={() => goTab('responses')}>Open Responses</button>
              : openTile && openTile.k === 'aliases' ? <button type="button" className="sci-btn" onClick={() => goTab('settings')}>Location email settings</button> : null}>
        {openTile && (
          openTile.b ? (
            <div className="sci-stack" style={{ gap: 10 }}>
              <p className="sci-small">{openTile.k === 'sms' ? smsLine(openTile.b) : detailOf(openTile.b) || 'No detail.'}</p>
              {(openTile.k === 'bounces' || openTile.k === 'complaints') && !eventsOn && (
                <div className="sci-banner"><div className="sci-banner-body sci-micro">Delivery events are off, so this count is not tracked.</div></div>
              )}
              <pre className="sci-pre">{JSON.stringify(openTile.b, null, 2)}</pre>
            </div>
          ) : <Empty>Not reported by the server.</Empty>
        )}
      </Drawer>
    </>
  )
}

function shortOf(k, b) {
  if (k === 'response_time') return b.median_minutes_all == null ? 'not measured yet' : `${b.median_minutes_all} min median`
  if (k === 'hot_responses') return `${b.open ?? 0} open`
  if (k === 'unhandled_hot') return `${b.count ?? 0} · SLA ${b.sla_minutes} min`
  if (k === 'failed_sends') return `${b.provider_failed_7d ?? 0} in 7 days`
  if (k === 'outlook_filing') return b.folder_root ? `${b.filed} filed · ${b.failed} failed` : 'off'
  if (b.count !== undefined) return `${b.count}`
  return ''
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
    <section aria-labelledby="sci-placement">
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
