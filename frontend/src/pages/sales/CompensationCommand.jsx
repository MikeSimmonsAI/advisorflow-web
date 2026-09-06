/* COMPENSATION COMMAND CENTER — what we expect to owe, what we owe, and what
 * we have paid.
 *
 * THIS IS NOT THE CONFIGURATION SCREEN. God Mode → Pricing & Comp defines the
 * plans, rates, caps and holdbacks. This shows what those rules produced and
 * settles it. Two screens that could both change a rate would eventually
 * disagree about what somebody earns.
 *
 * EVERY HEADLINE OPENS. Clicking a total filters the ledger to exactly the
 * rows that make it up, and the ledger prints its own sum beside the count —
 * so "PAYABLE NOW = $8,450" can be proved on the same screen rather than taken
 * on trust. A finance number nobody can reconcile is worse than no number.
 *
 * PROJECTED SITS APART FROM THE REST, deliberately and visibly. It is computed
 * live from open deals and is owed to nobody; the other four are rows in a
 * ledger that only exist because money was collected.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import SalesShell from './SalesShell'

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

const VIEW_TONE = {
  on_hold: { bg: 'rgba(245,185,66,.14)', fg: '#8a6100', label: 'ON HOLD' },
  payable_now: { bg: 'rgba(63,185,80,.14)', fg: '#1c6b2b', label: 'PAYABLE' },
  paid: { bg: 'rgba(120,130,145,.14)', fg: '#4a5563', label: 'PAID' },
  void: { bg: 'rgba(248,81,73,.14)', fg: '#9a2820', label: 'VOID' },
}

function Pill({ view }) {
  const t = VIEW_TONE[view] || VIEW_TONE.paid
  return (
    <span style={{ background: t.bg, color: t.fg, fontSize: 10, fontWeight: 800,
                   letterSpacing: '.06em', padding: '2px 8px', borderRadius: 20 }}>
      {t.label}
    </span>
  )
}

function Stat({ label, value, sub, muted, active, onClick }) {
  return (
    <button type="button" onClick={onClick}
            className="sw-card"
            style={{ flex: '1 1 160px', minWidth: 160, textAlign: 'left',
                     cursor: onClick ? 'pointer' : 'default',
                     border: active ? '2px solid #1a5fa8' : undefined }}>
      <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.09em',
                    color: '#5c6b7a' }}>{label}</div>
      <div style={{ fontSize: 25, fontWeight: 700, marginTop: 4,
                    color: muted ? '#8496a4' : '#0f2338' }}>{value}</div>
      {sub ? <div className="sw-subtle" style={{ marginTop: 2 }}>{sub}</div> : null}
    </button>
  )
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
    <div style={{ position: 'fixed', inset: 0, zIndex: 90, display: 'flex',
                  alignItems: 'center', justifyContent: 'center',
                  background: 'rgba(8,16,26,.6)', padding: 16 }}
         onClick={onClose}>
      <div className="sw-card" style={{ maxWidth: 560, width: '100%' }}
           onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 12, fontWeight: 800, letterSpacing: '.08em',
                      color: '#16324f' }}>
          SETTLE {ids.length} {ids.length === 1 ? 'ENTRY' : 'ENTRIES'} — {usd(total)}
        </div>

        <div className="sw-subtle" style={{ marginTop: 8 }}>
          Paying {lines.length} {lines.length === 1 ? 'person' : 'people'}. This is
          all or nothing: if any entry cannot be settled, none of them are, so a
          half-finished run can never leave you unsure which cheques went out.
        </div>

        {err ? <div className="sw-notbuilt sw-mt"><b>NOT PAID</b><p>{err}</p></div> : null}

        <div style={{ marginTop: 14, display: 'grid', gap: 10 }}>
          <label style={{ display: 'block' }}>
            <div className="sw-subtle">Payment reference (required)</div>
            <input value={reference} onChange={e => setReference(e.target.value)}
                   placeholder="e.g. ACH-2026-09-06"
                   style={{ width: '100%', padding: 8 }} />
          </label>
          <label style={{ display: 'block' }}>
            <div className="sw-subtle">Batch reference — groups this week's run</div>
            <input value={batch} onChange={e => setBatch(e.target.value)}
                   placeholder="e.g. WK-2026-36"
                   style={{ width: '100%', padding: 8 }} />
          </label>
          <label style={{ display: 'block' }}>
            <div className="sw-subtle">Method</div>
            <input value={method} onChange={e => setMethod(e.target.value)}
                   placeholder="ACH, check, transfer"
                   style={{ width: '100%', padding: 8 }} />
          </label>
          <label style={{ display: 'block' }}>
            <div className="sw-subtle">Note</div>
            <input value={note} onChange={e => setNote(e.target.value)}
                   style={{ width: '100%', padding: 8 }} />
          </label>
        </div>

        <div className="sw-subtle" style={{ marginTop: 10 }}>
          No bank details are stored here. A reference identifies a payment that
          happened elsewhere.
        </div>

        <div className="sw-flex sw-mt" style={{ gap: 8 }}>
          <button className="sw-btn" onClick={submit}
                  disabled={busy || !reference.trim() || !ids.length}>
            {busy ? 'Settling…' : 'Mark ' + usd(total) + ' paid'}
          </button>
          <button className="sw-btn" onClick={onClose} disabled={busy}>Cancel</button>
        </div>
      </div>
    </div>
  )
}

/* ── the screen ───────────────────────────────────────────────────────────── */

export default function CompensationCommand() {
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

  return (
    <SalesShell title="Compensation Command Center"
                subtitle="What the compensation rules produced — and what can be settled.">
      {err ? <div className="sw-notbuilt"><b>PROBLEM</b><p>{err}</p></div> : null}
      {!d && !err ? <div className="sw-subtle">Loading…</div> : null}

      {d ? (
        <>
          {d.brands?.length > 1 ? (
            <div className="sw-flex" style={{ marginBottom: 12 }}>
              <select value={brand} onChange={e => setBrand(e.target.value)}
                      style={{ padding: 8 }}>
                <option value="">All brands I can see</option>
                {d.brands.map(b => (
                  <option key={b.id} value={b.id}>{b.name}</option>
                ))}
              </select>
            </div>
          ) : null}

          {/* PROJECTED IS SEPARATED FROM THE LEDGER FIGURES BY A LINE, not just
              by wording. It is a forecast; the rest is money. */}
          <div className="sw-flex" style={{ gap: 12, flexWrap: 'wrap' }}>
            <Stat label="PROJECTED" muted value={usd(p.amount)}
                  sub={p.deal_count + ' open deals · not owed'} />
          </div>

          <div className="sw-flex sw-mt" style={{ gap: 12, flexWrap: 'wrap' }}>
            <Stat label="EARNED" value={usd(s.earned_total)}
                  sub={s.earned_count + ' entries · lifetime'}
                  active={view === 'all'} onClick={() => setView('all')} />
            <Stat label="ON HOLD" value={usd(s.on_hold.amount)}
                  sub={s.on_hold.count + ' in holdback'}
                  active={view === 'on_hold'} onClick={() => setView('on_hold')} />
            <Stat label="PAYABLE NOW" value={usd(s.payable_now.amount)}
                  sub={s.payable_now.count + ' ready'}
                  active={view === 'payable_now'} onClick={() => setView('payable_now')} />
            <Stat label="PAID" value={usd(s.paid.amount)}
                  sub={s.paid.count + ' settled'}
                  active={view === 'paid'} onClick={() => setView('paid')} />
          </div>

          {/* UPCOMING LIABILITY, from rows that already exist. Nothing here is
              forecast from the pipeline — every entry was earned on a real
              collected payment and carries a stored payable date. */}
          <div className="sw-card sw-mt">
            <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.09em',
                          color: '#16324f' }}>
              BECOMING PAYABLE
            </div>
            <div className="sw-flex" style={{ gap: 18, marginTop: 8, flexWrap: 'wrap' }}>
              {s.upcoming.map(u => (
                <div key={u.window_days}>
                  <div className="sw-subtle">Next {u.window_days} days</div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>{usd(u.amount)}</div>
                  <div className="sw-subtle">{u.count} entries</div>
                </div>
              ))}
            </div>
            <div className="sw-subtle" style={{ marginTop: 8 }}>
              From commissions already earned on collected payments. Open pipeline
              is not counted — a deal with no collected payment has no payable date.
            </div>
          </div>

          {/* WEEKLY PAYABLES */}
          {payables?.lines?.length ? (
            <div className="sw-card sw-mt">
              <div className="sw-flex" style={{ justifyContent: 'space-between',
                                                alignItems: 'center' }}>
                <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.09em',
                              color: '#16324f' }}>
                  PAYABLES — {usd(payables.total)} across {payables.lines.length}{' '}
                  {payables.lines.length === 1 ? 'person' : 'people'}
                </div>
                <div className="sw-flex" style={{ gap: 8 }}>
                  <button className="sw-btn" onClick={promote} disabled={busy}>
                    {busy ? '…' : 'Release elapsed holdbacks'}
                  </button>
                  {payables.can_process_payments ? (
                    <button className="sw-btn" onClick={() => setPaying(true)}>
                      Settle all payable
                    </button>
                  ) : null}
                </div>
              </div>

              <div className="sw-tablewrap" style={{ marginTop: 10 }}>
                <table className="sw-table">
                  <thead>
                    <tr>
                      <th>Person</th>
                      <th style={{ textAlign: 'right' }}>Seller</th>
                      <th style={{ textAlign: 'right' }}>Override</th>
                      <th style={{ textAlign: 'right' }}>Total</th>
                      <th style={{ textAlign: 'right' }}>Entries</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payables.lines.map(l => (
                      <tr key={l.payee_user_id}>
                        <td>{l.payee_name}</td>
                        <td style={{ textAlign: 'right' }}>{usd(l.seller_amount)}</td>
                        <td style={{ textAlign: 'right' }}>{usd(l.override_amount)}</td>
                        <td style={{ textAlign: 'right', fontWeight: 700 }}>
                          {usd(l.amount)}
                        </td>
                        <td style={{ textAlign: 'right' }}>{l.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!payables.can_process_payments ? (
                <div className="sw-subtle" style={{ marginTop: 8 }}>
                  You can see what is payable. Settling payments requires platform
                  finance authority.
                </div>
              ) : null}
            </div>
          ) : null}

          {/* THE LEDGER — and its own sum, so the headline above is provable. */}
          <div className="sw-card sw-mt">
            <div className="sw-flex" style={{ justifyContent: 'space-between',
                                              alignItems: 'center' }}>
              <div style={{ fontSize: 10, fontWeight: 800, letterSpacing: '.09em',
                            color: '#16324f' }}>
                LEDGER — {(rows?.count ?? 0)}{' '}
                {rows?.count === 1 ? 'ENTRY' : 'ENTRIES'} TOTALLING {usd(rows?.total)}
              </div>
              <select value={view} onChange={e => setView(e.target.value)}
                      style={{ padding: 6 }}>
                {(d.vocabulary?.views || []).map(v => (
                  <option key={v.key} value={v.key}>{v.label}</option>
                ))}
              </select>
            </div>

            {!rows ? <div className="sw-subtle" style={{ marginTop: 10 }}>Loading…</div>
              : !rows.entries.length ? (
                <div className="sw-subtle" style={{ marginTop: 10 }}>
                  No entries. Commission appears here when a customer payment on a
                  won deal is recorded as collected — never because a deal was
                  marked Won.
                </div>
              ) : (
              <div className="sw-tablewrap" style={{ marginTop: 10 }}>
                <table className="sw-table" style={{ minWidth: 940 }}>
                  <thead>
                    <tr>
                      <th>Person</th><th>Type</th><th>Customer</th><th>Deal</th>
                      <th>Package</th>
                      <th style={{ textAlign: 'right' }}>Amount</th>
                      <th>Qualifying payment</th><th>Payable</th>
                      <th>Status</th><th>Rule</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.entries.map(e => (
                      <tr key={e.id}>
                        <td>{e.payee_name}</td>
                        <td>{e.compensation_type}</td>
                        <td>{e.customer_name || <span className="sw-subtle">—</span>}</td>
                        <td>{e.deal_name || '—'}</td>
                        <td>{e.package_name || '—'}</td>
                        <td style={{ textAlign: 'right', fontWeight: 600 }}>
                          {usd(e.amount)}
                          {e.capped_from_amount ? (
                            <div style={{ fontSize: 10, color: '#8a6100' }}>
                              capped from {usd(e.capped_from_amount)}
                            </div>
                          ) : null}
                        </td>
                        <td>
                          <div style={{ fontSize: 11 }}>{e.collection_reference}</div>
                          <div className="sw-subtle">{day(e.collected_at)}</div>
                        </td>
                        <td>
                          {day(e.payable_at)}
                          {e.paid_at ? (
                            <div className="sw-subtle">paid {day(e.paid_at)}</div>
                          ) : null}
                        </td>
                        <td><Pill view={e.view} /></td>
                        {/* THE SNAPSHOT — what the plan said at the time, which
                            is what answers "why this amount" years later. */}
                        <td style={{ fontSize: 11 }}>
                          {e.rate_amount !== null && e.rate_amount !== undefined
                            ? usd(e.rate_amount) + ' fixed'
                            : e.rate_percent !== null && e.rate_percent !== undefined
                              ? e.rate_percent + '% ' + (e.basis_label || '')
                              : '—'}
                          <div className="sw-subtle">{e.plan_name || 'plan removed'}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {p.unconfigured_deals > 0 ? (
            <div className="sw-notbuilt sw-mt"
                 style={{ borderColor: 'rgba(255,170,60,.45)' }}>
              <b>{p.unconfigured_deals} OPEN{' '}
                {p.unconfigured_deals === 1 ? 'DEAL HAS' : 'DEALS HAVE'} NO
                CONFIGURED COMMISSION</b>
              <p>
                Their packages have no rate set, so they are excluded from
                projected compensation rather than counted as $0 — which would
                read as a decision to pay nothing. Set the rates in God Mode →
                Pricing &amp; Comp.
              </p>
            </div>
          ) : null}
        </>
      ) : null}

      {paying && payables ? (
        <PayDialog lines={payables.lines}
                   onClose={() => setPaying(false)}
                   onDone={() => { setPaying(false); load(); loadLedger() }} />
      ) : null}
    </SalesShell>
  )
}
