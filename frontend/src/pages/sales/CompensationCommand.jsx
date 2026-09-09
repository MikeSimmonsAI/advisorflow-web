/* COMPENSATION COMMAND CENTER — what we expect to owe, what we owe, and what
 * we have paid.
 *
 * THIS IS NOT THE CONFIGURATION SCREEN. God Mode → Pricing & Comp defines the
 * plans, rates, caps and holdbacks. This shows what those rules produced and
 * settles it. Two screens that could both change a rate would eventually
 * disagree about what somebody earns.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT CHANGED IN THIS PASS, AND WHY
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * THE OLD SCREEN WAS FIVE IDENTICAL RECTANGLES. Projected, earned, on hold,
 * payable and paid were drawn at the same size, in the same colour, on one
 * flat row — so the reader had to parse five 9px labels to find out which
 * number was a forecast and which was money leaving the business this week.
 * Every colour was a hard-coded light-theme literal, which is why the whole
 * thing rendered as white slabs when God Mode embedded it in a dark shell.
 *
 * NOW THE LAYOUT CARRIES THE MEANING:
 *
 *   PROJECTED sits ON ITS OWN, above a rule, dashed and muted, with the
 *   sentence explaining what it is beside it rather than buried underneath.
 *   It is the one figure on this page owed to nobody.
 *
 *   PAYABLE NOW is the biggest number on the screen and the only one wearing
 *   the accent, because it is the only one anybody acts on today.
 *
 *   EARNED / ON HOLD / PAID are peers below it, each with a coloured rule in
 *   its own state's hue.
 *
 * ATTENTION REQUIRED IS NEW AND IS COMPUTED, NOT DECORATIVE. Every row comes
 * from `ledger.attention()` and is counted from entries that exist. When there
 * is nothing outstanding the panel says so out loud — a finance screen that
 * can report "nothing to do" is more trustworthy than one that only ever
 * shows problems.
 *
 * EVERY HEADLINE STILL OPENS. Clicking a total filters the ledger to exactly
 * the rows that make it up, and the ledger prints its own sum beside the count
 * — so "PAYABLE NOW = $8,450" can be proved on the same screen rather than
 * taken on trust. A finance number nobody can reconcile is worse than no
 * number.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { api } from '../../api/client'
import SalesStyles from './SalesStyles'
import SalesShell from './SalesShell'

/* DELIBERATELY NOT INSIDE SalesShell.
 *
 * That shell loads `/sales/me` and, when the server refuses, renders "Sales
 * workspace unavailable" instead of its children. A finance administrator
 * holding `sales_comp_view` over a brand has NO sales membership — that is the
 * entire point of the capability — so wrapping this screen in that shell would
 * have made the Command Center unreachable for exactly the person it was built
 * for, while the API served them perfectly well.
 *
 * So this page carries its own chrome. A sales manager still reaches it from
 * the workspace nav; a finance user reaches the same URL and simply sees it.
 */
function CompShell({ children, embedded, fromSales }) {
  const nav = useNavigate()

  // FROM_SALES: manager navigated here via SalesShell's MY TEAM nav.
  // They hold a sales membership so SalesShell loads cleanly and gives
  // them the full left nav. Finance admins who access the URL directly
  // never have fromSales=true, so they continue to get CompShell chrome.
  if (fromSales) {
    return (
      <SalesShell
        title="Compensation"
        subtitle="What the compensation rules produced — and what can be settled."
      >
        {children}
      </SalesShell>
    )
  }

  // EMBEDDED means God Mode already drew the page frame and the rail. Drawing a
  // second header and a Back button inside it would give the owner two titles
  // and a button that leaves a shell they can already navigate out of.
  //
  // The `sw-scope` wrapper stays either way: this screen is built from the
  // sales workspace's `sw-*` classes, which are scoped under it. Dropping it
  // inside God Mode would render an unstyled table; keeping it cannot leak,
  // because every one of those rules is prefixed by the scope.
  //
  // Since this pass the scope's palette follows `data-appearance`, so the
  // embedded case now renders in God Mode's own dark family instead of
  // punching white rectangles through it.
  if (embedded) {
    return (
      <div className="sw-scope">
        <SalesStyles />
        {children}
      </div>
    )
  }

  return (
    <div className="sw-scope">
      <SalesStyles />
      <div style={{ maxWidth: 1180, margin: '0 auto', padding: '22px 18px' }}>
        <div className="sw-cc-band">
          <div>
            <h1 className="sw-cc-title">Compensation Command Center</h1>
            <p className="sw-cc-sub">
              What the compensation rules produced — and what can be settled.
              Every figure below opens the exact entries behind it.
            </p>
          </div>
          <button className="sw-btn" onClick={() => nav(-1)}>Back</button>
        </div>
        {children}
      </div>
    </div>
  )
}

function usd(n) {
  if (n === null || n === undefined) return '—'
  return '$' + Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

function day(v) {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function plural(n, one, many) { return n === 1 ? one : many }

/* THE FOUR LEDGER STATES, as a chip. Tones come from the stylesheet's tonal
 * tokens, so they are re-chosen for dark rather than dimmed. */
const VIEW_TONE = {
  on_hold: { cls: 'sw-chip sw-amber', label: 'ON HOLD' },
  payable_now: { cls: 'sw-chip sw-green', label: 'PAYABLE' },
  paid: { cls: 'sw-chip', label: 'PAID' },
  void: { cls: 'sw-chip sw-red', label: 'VOID' },
}

function Pill({ view }) {
  const t = VIEW_TONE[view] || VIEW_TONE.paid
  return <span className={t.cls}>{t.label}</span>
}

/** One headline figure. Clicking it filters the ledger to the rows behind it. */
function Tile({ label, value, sub, tone, lead, forecast, active, onClick }) {
  const cls = ['sw-tile']
  if (tone) cls.push('t-' + tone)
  if (lead) cls.push('is-lead')
  if (forecast) cls.push('is-forecast')
  if (active) cls.push('is-on')
  if (!onClick) cls.push('is-static')
  return (
    <button type="button" className={cls.join(' ')}
            onClick={onClick} disabled={!onClick}
            aria-pressed={onClick ? !!active : undefined}>
      <span className="sw-tile-label">{label}</span>
      <div className="sw-tile-value">{value}</div>
      {sub ? <div className="sw-tile-sub">{sub}</div> : null}
    </button>
  )
}

/** The severity pill. `severity` is the server's word, not one derived here. */
function Sev({ severity, label }) {
  return <span className={'sw-sev sv-' + (severity || 'no_data')}>{label}</span>
}

/* ── settling a payment run ───────────────────────────────────────────────── */

function PayDialog({ lines, onClose, onDone }) {
  const [reference, setReference] = useState('')
  const [batch, setBatch] = useState('')
  const [method, setMethod] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const ids = lines.flatMap(l => l.entry_ids)
  const total = lines.reduce((a, l) => a + (l.amount || 0), 0)

  async function submit() {
    setBusy(true); setErr('')
    try {
      await api.post('/sales/compensation/pay', {
        entry_ids: ids,
        payment_reference: reference,
        payment_batch_reference: batch || null,
        payment_method: method || null,
        payment_note: note || null,
      })
      onDone()
    } catch (e) {
      setErr(e?.detail || e?.message || 'Payment failed. Nothing was paid.')
    } finally { setBusy(false) }
  }

  return (
    <div className="sw-modal-back" onClick={onClose}>
      <div className="sw-modal" onClick={e => e.stopPropagation()}>
        <div className="sw-card-h">
          <div>
            <h3>Settle {ids.length} {plural(ids.length, 'entry', 'entries')} — {usd(total)}</h3>
            <small>
              Paying {lines.length} {plural(lines.length, 'person', 'people')}. All
              or nothing: if any entry cannot be settled, none of them are.
            </small>
          </div>
        </div>

        <div className="sw-card-b">
          {err ? <div className="sw-err"><b>NOT PAID.</b> {err}</div> : null}

          <div className="sw-field">
            <label htmlFor="pay-ref">Payment reference (required)</label>
            <input id="pay-ref" className="sw-input" value={reference}
                   onChange={e => setReference(e.target.value)}
                   placeholder="e.g. ACH-2026-09-06" />
          </div>
          <div className="sw-field">
            <label htmlFor="pay-batch">Batch reference — groups this week's run</label>
            <input id="pay-batch" className="sw-input" value={batch}
                   onChange={e => setBatch(e.target.value)}
                   placeholder="e.g. WK-2026-36" />
          </div>
          <div className="sw-field">
            <label htmlFor="pay-method">Method</label>
            <input id="pay-method" className="sw-input" value={method}
                   onChange={e => setMethod(e.target.value)}
                   placeholder="ACH, check, transfer" />
          </div>
          <div className="sw-field">
            <label htmlFor="pay-note">Note</label>
            <input id="pay-note" className="sw-input" value={note}
                   onChange={e => setNote(e.target.value)} />
          </div>

          <p className="sw-muted" style={{ marginTop: 12 }}>
            No bank details are stored here. A reference identifies a payment
            that happened elsewhere.
          </p>

          <div className="sw-flex sw-mt" style={{ gap: 8 }}>
            <button className="sw-btn sw-primary" onClick={submit}
                    disabled={busy || !reference.trim() || !ids.length}>
              {busy ? 'Settling…' : 'Mark ' + usd(total) + ' paid'}
            </button>
            <button className="sw-btn" onClick={onClose} disabled={busy}>Cancel</button>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ── the screen ───────────────────────────────────────────────────────────── */

export default function CompensationCommand({ embedded = false }) {
  const nav = useNavigate()
  const location = useLocation()
  // fromSales: true when a manager clicked "Compensation" in the SalesShell
  // MY TEAM nav. Causes CompShell to delegate to SalesShell for full nav chrome.
  // Finance admins navigating directly never carry this state.
  const fromSales = !embedded && location.state?.fromSales === true
  const [d, setD] = useState(null)
  const [payables, setPayables] = useState(null)
  const [rows, setRows] = useState(null)
  const [view, setView] = useState('all')
  const [brand, setBrand] = useState('')
  const [err, setErr] = useState('')
  const [paying, setPaying] = useState(false)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setErr('')
    const qs = brand ? '?brand_sales_org_id=' + brand : ''
    try {
      const [o, p] = await Promise.all([
        api.get('/sales/compensation/overview' + qs),
        api.get('/sales/compensation/payables' + qs),
      ])
      setD(o); setPayables(p)
    } catch (e) {
      setErr(e?.detail || e?.message || 'Could not load compensation.')
    }
  }, [brand])

  const loadLedger = useCallback(async () => {
    const p = new URLSearchParams({ view })
    if (brand) p.set('brand_sales_org_id', brand)
    try {
      setRows(await api.get('/sales/compensation/ledger?' + p.toString()))
    } catch (e) {
      setErr(e?.detail || e?.message || 'Could not load the ledger.')
    }
  }, [view, brand])

  useEffect(() => { load() }, [load])
  useEffect(() => { loadLedger() }, [loadLedger])

  async function promote() {
    setBusy(true)
    try {
      await api.post('/sales/compensation/promote-due', {})
      await load(); await loadLedger()
    } catch (e) {
      setErr(e?.detail || e?.message || 'Could not promote entries.')
    } finally { setBusy(false) }
  }

  const s = d?.summary
  const p = d?.projected
  const att = d?.attention
  const viewLabel = (d?.vocabulary?.views || []).find(v => v.key === view)?.label || view

  return (
    <CompShell embedded={embedded} fromSales={fromSales}>
      {err ? <div className="sw-err">{err}</div> : null}
      {!d && !err ? <div className="sw-muted">Loading…</div> : null}

      {d ? (
        <>
          {d.brands?.length > 1 ? (
            <div className="sw-filters">
              <select className="sw-select" value={brand} aria-label="Brand"
                      onChange={e => setBrand(e.target.value)}>
                <option value="">All brands I can see</option>
                {d.brands.map(b => (
                  <option key={b.id} value={b.id}>{b.name}</option>
                ))}
              </select>
            </div>
          ) : null}

          {/* ── THE FORECAST, HELD APART ────────────────────────────────────
              Not a smaller card in the same row — a different KIND of number,
              so it gets its own band with the sentence that qualifies it
              sitting beside it rather than three scrolls further down. */}
          <div className="sw-cc-forecast">
            <Tile forecast label="Projected" value={usd(p.amount)}
                  sub={p.deal_count + ' open ' + plural(p.deal_count, 'deal', 'deals')} />
            <p className="sw-cc-forecast-note">
              <span>
                <b>Projected is owed to nobody.</b> It is what today's open
                deals would pay if they closed on today's terms. Commission
                becomes real money only when a customer payment is actually
                collected — everything below this line already has.
              </span>
            </p>
          </div>

          {/* ── THE LEDGER FIGURES ──────────────────────────────────────────
              Four buckets, each one a filter on the table further down. */}
          <div className="sw-tiles">
            <Tile tone="earned" label="Earned" value={usd(s.earned_total)}
                  sub={s.earned_count + ' ' + plural(s.earned_count, 'entry', 'entries')
                       + ' · lifetime'}
                  active={view === 'all'} onClick={() => setView('all')} />
            <Tile tone="hold" label="On hold" value={usd(s.on_hold.amount)}
                  sub={s.on_hold.count + ' in holdback'}
                  active={view === 'on_hold'} onClick={() => setView('on_hold')} />
            <Tile tone="payable" lead label="Payable now" value={usd(s.payable_now.amount)}
                  sub={s.payable_now.count + ' ready to pay'}
                  active={view === 'payable_now'} onClick={() => setView('payable_now')} />
            <Tile tone="paid" label="Paid" value={usd(s.paid.amount)}
                  sub={s.paid.count + ' settled'}
                  active={view === 'paid'} onClick={() => setView('paid')} />
          </div>

          {/* ── BECOMING PAYABLE ────────────────────────────────────────────
              From rows that already exist. Nothing here is forecast from the
              pipeline — every entry was earned on a real collected payment and
              carries a stored payable date. */}
          <div className="sw-card sw-mt">
            <div className="sw-card-h">
              <div>
                <h3>BECOMING PAYABLE</h3>
                <small>
                  Commission already earned on collected payments, by the date
                  its holdback elapses. Open pipeline is not counted — a deal
                  with no collected payment has no payable date.
                </small>
              </div>
            </div>
            <div className="sw-card-b" style={{ paddingTop: 0 }}>
              <div className="sw-becoming">
                {s.upcoming.map(u => (
                  <div className="sw-becoming-cell" key={u.window_days}>
                    <span>Next {u.window_days} days</span>
                    <b>{usd(u.amount)}</b>
                    <small>{u.count} {plural(u.count, 'entry', 'entries')}</small>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* ── ATTENTION REQUIRED ──────────────────────────────────────────
              Server-computed, worst first, every row counted from entries that
              exist. An empty list is a real answer and says so. */}
          <div className="sw-card sw-mt">
            <div className="sw-card-h">
              <div>
                <h3>ATTENTION REQUIRED</h3>
                <small>
                  Conditions on this ledger that need a person. Each is counted
                  from real entries — nothing here is a reminder or a threshold.
                </small>
              </div>
              {att?.overall ? (
                <span style={{ marginLeft: 'auto' }}>
                  <Sev severity={att.overall} label={att.overall_label} />
                </span>
              ) : null}
            </div>
            <div className="sw-card-b" style={{ paddingTop: 0 }}>
              {!att || !att.items.length ? (
                <div className="sw-allclear">
                  <div>
                    <b>Nothing outstanding.</b>
                    <p>
                      No unrated packages, no capped or orphaned entries, and
                      nothing waiting on a decision. This is checked against the
                      ledger every time the page loads.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="sw-att">
                  {att.items.map(it => (
                    <div className={'sw-att-row sv-' + it.severity} key={it.key}>
                      <div className="sw-att-bar" />
                      <div className="sw-att-body">
                        <div className="sw-att-t">
                          <Sev severity={it.severity} label={it.severity_label} />
                          <b>{it.title}</b>
                        </div>
                        <p className="sw-att-d">{it.detail}</p>
                      </div>
                      <div className="sw-att-side">
                        {/* OPENING THE PROOF. `view` names the exact ledger
                            bucket the item was counted from, so the claim can
                            be checked in the table below rather than believed. */}
                        {it.view ? (
                          <button className="sw-tiny" onClick={() => setView(it.view)}>
                            Show {it.count}
                          </button>
                        ) : null}
                        {it.action_route ? (
                          <button className="sw-tiny sw-primary"
                                  onClick={() => nav(it.action_route)}>
                            {it.action_label}
                          </button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* ── THIS WEEK'S PAYMENT RUN ─────────────────────────────────────── */}
          {payables?.lines?.length ? (
            <div className="sw-card sw-mt">
              <div className="sw-card-h">
                <div>
                  <h3>
                    PAYABLES — {usd(payables.total)} across {payables.lines.length}{' '}
                    {plural(payables.lines.length, 'person', 'people')}
                  </h3>
                  <small>One line per person. This is what a payment run is made of.</small>
                </div>
                <div className="sw-flex" style={{ gap: 8, marginLeft: 'auto' }}>
                  <button className="sw-btn" onClick={promote} disabled={busy}>
                    {busy ? '…' : 'Release elapsed holdbacks'}
                  </button>
                  {payables.can_process_payments ? (
                    <button className="sw-btn sw-primary" onClick={() => setPaying(true)}>
                      Settle all payable
                    </button>
                  ) : null}
                </div>
              </div>

              <div className="sw-card-b">
                <div className="sw-tablewrap">
                  <table className="sw-table">
                    <thead>
                      <tr>
                        <th>Person</th>
                        <th className="sw-num">Seller</th>
                        <th className="sw-num">Override</th>
                        <th className="sw-num">Total</th>
                        <th className="sw-num">Entries</th>
                      </tr>
                    </thead>
                    <tbody>
                      {payables.lines.map(l => (
                        <tr key={l.payee_user_id}>
                          <td>{l.payee_name}</td>
                          <td className="sw-num">{usd(l.seller_amount)}</td>
                          <td className="sw-num">{usd(l.override_amount)}</td>
                          <td className="sw-num"><b>{usd(l.amount)}</b></td>
                          <td className="sw-num">{l.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!payables.can_process_payments ? (
                  <p className="sw-muted" style={{ marginTop: 10 }}>
                    You can see what is payable. Settling payments requires
                    compensation authority over the brand, which is granted
                    separately from being able to read these numbers.
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {/* ── THE LEDGER, AND ITS OWN SUM ─────────────────────────────────
              The proof line is why the headlines above can be trusted: the
              count and the total describe THIS EXACT SET of rows. */}
          <div className="sw-card sw-mt">
            <div className="sw-card-h">
              <div className="sw-ledger-h" style={{ marginBottom: 0, width: '100%' }}>
                <div>
                  <h3>LEDGER — {viewLabel.toUpperCase()}</h3>
                  <small className="sw-proof">
                    {(rows?.count ?? 0)} {plural(rows?.count, 'entry', 'entries')}{' '}
                    totalling {usd(rows?.total)}
                  </small>
                </div>
                <select className="sw-select" style={{ width: 'auto' }}
                        aria-label="Ledger view"
                        value={view} onChange={e => setView(e.target.value)}>
                  {(d.vocabulary?.views || []).map(v => (
                    <option key={v.key} value={v.key}>{v.label}</option>
                  ))}
                </select>
              </div>
            </div>

            <div className="sw-card-b">
              {!rows ? <div className="sw-muted">Loading…</div>
                : !rows.entries.length ? (
                  <div className="sw-blank">
                    <b>No entries in this view.</b>
                    <p>
                      Commission appears here when a customer payment on a won
                      deal is recorded as collected — never because a deal was
                      marked Won. Nothing is missing; nothing has been collected
                      that falls in this bucket.
                    </p>
                  </div>
                ) : (
                <div className="sw-tablewrap">
                  <table className="sw-table" style={{ minWidth: 940 }}>
                    <thead>
                      <tr>
                        <th>Person</th><th>Type</th><th>Customer</th><th>Deal</th>
                        <th>Package</th>
                        <th className="sw-num">Amount</th>
                        <th>Qualifying payment</th><th>Payable</th>
                        <th>Status</th><th>Rule</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.entries.map(e => (
                        <tr key={e.id}>
                          <td>{e.payee_name}</td>
                          <td>{e.compensation_type}</td>
                          <td>{e.customer_name
                            || <span className="sw-muted">not provisioned</span>}</td>
                          <td>{e.deal_name || '—'}</td>
                          <td>{e.package_name || '—'}</td>
                          <td className="sw-num">
                            <b>{usd(e.amount)}</b>
                            {e.capped_from_amount ? (
                              <span className="sw-cap">
                                capped from {usd(e.capped_from_amount)}
                              </span>
                            ) : null}
                          </td>
                          <td>
                            {e.collection_reference}
                            <span className="sw-muted" style={{ display: 'block' }}>
                              {day(e.collected_at)}
                            </span>
                          </td>
                          <td>
                            {day(e.payable_at)}
                            {e.paid_at ? (
                              <span className="sw-muted" style={{ display: 'block' }}>
                                paid {day(e.paid_at)}
                              </span>
                            ) : null}
                          </td>
                          <td><Pill view={e.view} /></td>
                          {/* THE SNAPSHOT — what the plan said at the time,
                              which is what answers "why this amount" years
                              later. A plan edit cannot rewrite it. */}
                          <td>
                            {e.rate_amount !== null && e.rate_amount !== undefined
                              ? usd(e.rate_amount) + ' fixed'
                              : e.rate_percent !== null && e.rate_percent !== undefined
                                ? e.rate_percent + '% ' + (e.basis_label || '')
                                : '—'}
                            <span className="sw-muted" style={{ display: 'block' }}>
                              {e.plan_name || 'plan removed'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </>
      ) : null}

      {paying && payables ? (
        <PayDialog lines={payables.lines}
                   onClose={() => setPaying(false)}
                   onDone={() => { setPaying(false); load(); loadLedger() }} />
      ) : null}
    </CompShell>
  )
}
