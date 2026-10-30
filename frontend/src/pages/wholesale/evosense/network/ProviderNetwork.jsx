/* PROJECT OVERDRIVE Phase B (#439) - DATA PROVIDERS (ATTOM, PropStream,
 * Tracerfy) on the existing Providers & Controls screen. No new menu item.
 *
 * Server: GET /wholesale/evosense/provider-network, POST .../{key}/test,
 * PATCH .../{key}. Every status is the server's; CONNECTED appears only after
 * a real answer. Test Connection exists only where the server says a real,
 * safe test exists, and says what it costs. No credential is ever shown. */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../../api/client'
import { errText } from '../../wsShared'
import { Alert, Panel, Skeleton, ago } from '../../ds/ds'
import * as N from './networkModel'
import './network.css'

function StatusBadge({ status }) {
  const s = N.safeStatus(status)
  return <span className={`pn-badge pn-badge--${N.toneOf(s)}`} data-status={s}>{s}</span>
}

function AttomSettings({ card, isAdmin, busy, save }) {
  const [price, setPrice] = useState(card.budget && card.budget.unit_cents != null ? (card.budget.unit_cents / 100).toFixed(2) : '')
  const cents = N.centsFromDollars(price)
  if (!isAdmin) return null
  return (
    <div className="pn-settings">
      <label className="pn-switch">
        <input type="checkbox" checked={!!card.enabled} disabled={busy}
               onChange={(e) => save('attom', { enabled: e.target.checked })} />
        <span>ATTOM on for this workspace</span>
      </label>
      <label className="pn-price">
        <span>Price per ATTOM request ($)</span>
        <input className="evo-input" inputMode="decimal" value={price} disabled={busy} placeholder="e.g. 0.25"
               aria-invalid={price !== '' && cents === null} onChange={(e) => setPrice(e.target.value)} />
        <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy || cents === null}
                onClick={() => save('attom', { unit_cents: cents })}>Save price</button>
      </label>
      <span className="pn-hint">From your ATTOM contract. Enter 0 for a flat-rate plan. A lookup makes up to 4 requests.</span>
    </div>
  )
}

function Card({ card, isAdmin, busy, onTest, save, result }) {
  const s = N.safeStatus(card.status)
  const usage = N.usageLine(card)
  const budget = N.budgetLine(card)
  return (
    <article className={`pn-card pn-card--${N.toneOf(s)}`} aria-labelledby={`pn-${card.key}`} data-provider={card.key}>
      <header className="pn-card__head">
        <div>
          <h3 id={`pn-${card.key}`} className="pn-card__title">{card.label}</h3>
          <p className="pn-card__role">{card.role}</p>
        </div>
        <StatusBadge status={s} />
      </header>
      <p className="pn-card__why">{card.why}</p>
      <p className="pn-card__meaning">{N.MEANING[s]}</p>

      <dl className="pn-kv">
        <dt>Can provide</dt>
        <dd><ul className="pn-caps">{(card.capabilities || []).map((c) => <li key={c.key}><b>{c.label}</b> <span>· {c.state}</span></li>)}</ul></dd>
        {(card.missing || []).length ? (
          <><dt>Still needed</dt><dd><ul className="pn-missing">{card.missing.map((m) => <li key={m.code}>{m.text}</li>)}</ul></dd></>
        ) : null}
        <dt>Last success</dt><dd>{card.last_success_at ? ago(card.last_success_at) : 'never'}</dd>
        <dt>Last failure</dt><dd>{card.last_failure_at ? <>{ago(card.last_failure_at)} · {card.last_failure}</> : 'none'}</dd>
        <dt>Coverage</dt><dd>{card.coverage}</dd>
        {usage ? <><dt>Usage</dt><dd>{usage}</dd></> : null}
        {budget ? <><dt>Budget</dt><dd>{budget}</dd></> : null}
        <dt>How it runs</dt><dd>{card.how_it_runs}</dd>
        {card.last_import ? <><dt>Last import</dt><dd>{ago(card.last_import.at)} · {card.last_import.summary}</dd></> : null}
        {card.shared_account_note ? <><dt>Accounts</dt><dd>{card.shared_account_note}</dd></> : null}
      </dl>

      <div className="pn-next"><b>Next:</b> {card.next_action}</div>

      <div className="pn-actions">
        {card.test && card.test.available ? (
          <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy || !isAdmin}
                  onClick={() => onTest(card.key)} data-action={`test-${card.key}`}
                  title={isAdmin ? card.test.cost_note : 'Only a workspace admin can test a connection.'}>
            {busy === card.key ? 'Testing…' : 'Test Connection'}</button>
        ) : null}
        {card.key === 'propstream' ? (
          <Link className="evo-btn evo-btn--secondary evo-btn--sm" to={card.import_path || '/wholesale/evosense/inbox'}>Import a PropStream CSV</Link>
        ) : null}
        <span className="pn-hint">{card.test ? card.test.cost_note : null}</span>
      </div>
      {result ? <Alert kind={result.ok ? 'ok' : 'warn'}>{result.message}
        {result.detail && result.detail.credit_balance != null ? ` Credit balance: ${result.detail.credit_balance}.` : ''}</Alert> : null}
      {card.key === 'attom' ? <AttomSettings card={card} isAdmin={isAdmin} busy={!!busy} save={save} /> : null}

      <details className="pn-setup">
        <summary>Connection steps</summary>
        <ol>{N.setupSteps(card).map((t, i) => <li key={i}>{t}</li>)}</ol>
      </details>
    </article>
  )
}

export default function ProviderNetwork({ isAdmin }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(null)
  const [results, setResults] = useState({})
  const load = useCallback(() => api.get('/wholesale/evosense/provider-network').then((d) => { setData(d); setError(null) })
    .catch((e) => setError(errText(e))), [])
  useEffect(() => { load() }, [load])

  async function onTest(key) {
    setBusy(key); setError(null)
    try {
      const r = await api.post(`/wholesale/evosense/provider-network/${key}/test`, {})
      setResults((x) => ({ ...x, [key]: r }))
      if (r.readiness) setData(r.readiness)
    } catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }
  async function save(key, body) {
    setBusy(key); setError(null)
    try { setData(await api.patch(`/wholesale/evosense/provider-network/${key}`, body)) }
    catch (e) { setError(errText(e)) } finally { setBusy(null) }
  }

  return (
    <Panel title="Data providers" hint="ATTOM · PropStream · Tracerfy — one honest status each">
      <Alert>{error}</Alert>
      {!data ? <Skeleton rows={4} /> : (
        <>
          <p className="pn-note">{data.notice} {data.stability_note}</p>
          <div className="pn-grid">
            {data.providers.map((c) => <Card key={c.key} card={c} isAdmin={isAdmin} busy={busy} onTest={onTest}
                                             save={save} result={results[c.key]} />)}
          </div>
        </>
      )}
    </Panel>
  )
}
