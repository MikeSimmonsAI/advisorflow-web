import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';

// ═══════════════════════════════════════════════════════════════════════════
// GOD MODE — BILLING & REVENUE OPERATIONS
// ═══════════════════════════════════════════════════════════════════════════
//
// /god/billing has had seven working routes — revenue, the event ledger, brand
// catalogues, policy — and NOT ONE LINE OF FRONTEND. The whole surface was
// built, deployed, and unreachable: no page, no route, no fetch anywhere in
// the bundle. This is that page.
//
// It answers the question a totals dashboard cannot: WHICH CUSTOMER NEEDS
// SOMETHING DOING ABOUT IT. A number tells you something is wrong; only a
// roster tells you whose card failed.
//
// TWO CONVENTIONS CARRIED FROM THE SERVER, because breaking either would make
// this screen lie:
//
//   A NULL FIGURE IS NOT ZERO. The API returns null plus a reason for anything
//   it cannot source. "No payment has ever been recorded" and "we collected
//   nothing" look identical as $0 and mean opposite things, so a null renders
//   as an explained placeholder and never as a number.
//
//   BRAND SCOPE IS THE POINT. Every request carries the selected platform_id,
//   and the server filters on it. Brand A's billing must never appear in a
//   Brand B view.

const CARD = {
  background: '#1a1a2e', border: '1px solid #2a2a4a',
  borderRadius: '12px', padding: '20px',
};

const STATUS_COLORS = {
  active: '#1ef0a8', trialing: '#2fb6ff', past_due: '#f59e0b',
  unpaid: '#f59e0b', paused: '#f59e0b', incomplete: '#f59e0b',
  canceled: '#ef4444', incomplete_expired: '#ef4444',
};

// Label, and the one-line explanation of what the filter is actually asking.
// Written as the operator's question, not as Stripe's vocabulary.
const FILTER_TABS = [
  ['all', 'All'],
  ['active', 'Active'],
  ['trialing', 'Trial'],
  ['payment_problem', 'Payment problem'],
  ['pending_downgrade', 'Pending downgrade'],
  ['pending_cancellation', 'Pending cancellation'],
  ['no_payment_method', 'No payment method'],
  ['held_capacity', 'Held capacity'],
  ['commercial_data_incomplete', 'No commercial data'],
  ['canceled', 'Canceled'],
];

function money(cents, currency) {
  if (cents === null || cents === undefined) return null;
  const amount = cents / 100;
  const symbol = (currency || 'usd').toLowerCase() === 'usd' ? '$' : '';
  return symbol + amount.toLocaleString(undefined, {
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2, maximumFractionDigits: 2,
  });
}

function when(v) {
  if (!v) return null;
  const d = new Date(v);
  return isNaN(d.getTime()) ? null
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

// A figure the server could not produce. Rendered as the REASON, never as a
// zero — this is the component that keeps the screen honest. A $0 here would
// claim a business earning nothing rather than a figure nobody can compute.
//
// THE WORDS CHANGED IN THE EXPERIENCE PASS, THE BEHAVIOUR DID NOT. It used to
// read "no source", which describes our data model. An owner reading it cannot
// tell whether the gap is in his setup or in our code. "not tracked" says the
// same true thing in words that are about his business, and the reason is
// still one hover away.
function NoSource({ reason }) {
  return (
    <span title={reason || 'This figure is not being recorded anywhere yet.'}
          style={{ color: '#7a7a95', fontSize: 13, fontStyle: 'italic' }}>
      not tracked
    </span>
  );
}

function Tile({ label, value, sub, reason }) {
  return (
    <div style={{ ...CARD, minWidth: 0 }}>
      <div style={{ fontSize: 12, color: '#888', marginBottom: 6, textTransform: 'uppercase',
                    letterSpacing: '0.04em' }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 800, fontVariantNumeric: 'tabular-nums' }}>
        {value === null || value === undefined ? <NoSource reason={reason} /> : value}
      </div>
      {sub && <div style={{ fontSize: 12, color: '#888', marginTop: 6 }}>{sub}</div>}
    </div>
  );
}

export default function GodBillingOps() {
  const [brands, setBrands] = useState([]);
  const [platformId, setPlatformId] = useState('');
  const [filter, setFilter] = useState('all');
  const [q, setQ] = useState('');
  const [revenue, setRevenue] = useState(null);
  const [roster, setRoster] = useState(null);
  const [events, setEvents] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.get('/god/billing/brands')
      .then(r => setBrands(r.brands || r || []))
      .catch(() => setBrands([]));
  }, []);

  const load = useCallback(async () => {
    setErr('');
    const scope = platformId ? `?platform_id=${encodeURIComponent(platformId)}` : '';
    const rosterQs = new URLSearchParams();
    if (platformId) rosterQs.set('platform_id', platformId);
    rosterQs.set('filter', filter);
    if (q.trim()) rosterQs.set('q', q.trim());

    // Each read is allowed to fail on its own. A revenue call that errors must
    // not blank the roster somebody is working through.
    const [rev, cust, evt] = await Promise.allSettled([
      api.get(`/god/billing/revenue${scope}`),
      api.get(`/god/billing/customers?${rosterQs.toString()}`),
      api.get(`/god/billing/events${scope}${scope ? '&' : '?'}limit=15`),
    ]);
    setRevenue(rev.status === 'fulfilled' ? rev.value : null);
    setRoster(cust.status === 'fulfilled' ? cust.value : null);
    setEvents(evt.status === 'fulfilled' ? evt.value : null);
    if (cust.status === 'rejected') {
      setErr(cust.reason?.message || 'The customer roster could not be loaded.');
    }
  }, [platformId, filter, q]);

  useEffect(() => { setLoading(true); load().finally(() => setLoading(false)); }, [load]);

  const mrr = revenue?.mrr;
  const collected = revenue?.collected_30d;
  const counts = roster?.counts || {};
  const rows = roster?.customers || [];

  return (
    <div style={{ padding: '28px', maxWidth: 1500, margin: '0 auto', color: '#e8e8f0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
                    flexWrap: 'wrap', gap: 16, marginBottom: 8 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, margin: 0 }}>Billing &amp; Revenue Operations</h1>
          <p style={{ color: '#888', margin: '6px 0 0', fontSize: 14 }}>
            Every customer, what they pay, and what needs doing about it. Read from the local
            mirror the Stripe webhook maintains — nothing on this page calls Stripe.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <select value={platformId} onChange={e => setPlatformId(e.target.value)}
            style={{ background: '#1a1a2e', color: '#e8e8f0', border: '1px solid #2a2a4a',
                     borderRadius: 8, padding: '9px 12px', fontSize: 14 }}>
            <option value="">All brands</option>
            {brands.map(b => (
              <option key={b.platform_id || b.id} value={b.platform_id || b.id}>
                {b.name || b.brand_name}
              </option>
            ))}
          </select>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search customer…"
            style={{ background: '#1a1a2e', color: '#e8e8f0', border: '1px solid #2a2a4a',
                     borderRadius: 8, padding: '9px 12px', fontSize: 14, minWidth: 180 }} />
        </div>
      </div>

      {err && (
        <div style={{ background: '#ef444420', border: '1px solid #ef4444', borderRadius: 8,
                      padding: '12px 16px', margin: '16px 0', color: '#ef4444', fontSize: 14 }}>
          {err}
        </div>
      )}

      {/* ── Revenue band ─────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))',
                    gap: 16, margin: '24px 0' }}>
        <Tile label="MRR" reason={mrr?.no_source}
          value={mrr && mrr.value_cents !== null && mrr.value_cents !== undefined
            ? money(mrr.value_cents, mrr.currency) : null}
          sub={mrr?.no_source ? null
            : `${mrr?.priced_organizations ?? 0} of ${mrr?.active_organizations ?? 0} active priced`} />
        <Tile label="Collected · 30d" reason={collected?.no_source}
          value={collected && collected.value_cents !== null && collected.value_cents !== undefined
            ? money(collected.value_cents, 'usd') : null}
          sub={collected?.no_source ? null
            : `${collected?.payments_counted ?? 0} payments${collected?.refunded_cents
              ? ` · ${money(collected.refunded_cents, 'usd')} refunded` : ''}`} />
        <Tile label="Active" value={revenue?.active_count ?? 0} sub="subscriptions" />
        <Tile label="Trialing" value={revenue?.trialing_count ?? 0} sub="not counted in MRR" />
        <Tile label="Past due" value={revenue?.past_due_count ?? 0}
          sub={revenue?.past_due_count ? 'needs attention' : 'none'} />
        <Tile label="Invoices" value={revenue?.invoices_recorded ?? 0} sub="recorded locally" />
      </div>

      {/* MRR that is understated because a plan would not price. Named next to
          the number rather than buried, because an unexplained subscription is
          a customer being charged something the platform cannot account for. */}
      {mrr?.unpriced_organizations?.length > 0 && (
        <div style={{ ...CARD, borderColor: '#f59e0b55', marginBottom: 24 }}>
          <div style={{ color: '#f59e0b', fontWeight: 700, fontSize: 14, marginBottom: 8 }}>
            {mrr.unpriced_organizations.length} active subscription(s) could not be priced —
            MRR above is understated
          </div>
          {mrr.unpriced_organizations.slice(0, 6).map(u => (
            <div key={u.organization_id} style={{ fontSize: 13, color: '#c9a15a', marginTop: 4 }}>
              {u.name} — plan “{u.plan_key}” · {u.reason}
            </div>
          ))}
        </div>
      )}

      {/* ── Filter tabs, with real counts ────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 18 }}>
        {FILTER_TABS.map(([key, label]) => {
          const n = counts[key];
          const on = filter === key;
          const attention = ['payment_problem', 'no_payment_method'].includes(key) && n > 0;
          return (
            <button key={key} onClick={() => setFilter(key)}
              style={{ background: on ? '#2fb6ff' : '#1a1a2e',
                       color: on ? '#04121c' : attention ? '#f59e0b' : '#bbb',
                       border: `1px solid ${on ? '#2fb6ff' : attention ? '#f59e0b55' : '#2a2a4a'}`,
                       borderRadius: 20, padding: '7px 14px', fontSize: 13,
                       fontWeight: on ? 700 : 600, cursor: 'pointer' }}>
              {label}{n !== undefined ? ` · ${n}` : ''}
            </button>
          );
        })}
      </div>

      {/* ── The roster ───────────────────────────────────────────────────── */}
      <div style={{ ...CARD, padding: 0, overflow: 'hidden' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline',
                      padding: '16px 20px', borderBottom: '1px solid #2a2a4a', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontWeight: 700, fontSize: 15 }}>
            {roster?.shown ?? 0} customer{(roster?.shown ?? 0) === 1 ? '' : 's'}
            {roster?.total_in_scope !== undefined && filter !== 'all'
              ? <span style={{ color: '#888', fontWeight: 400 }}> of {roster.total_in_scope} in scope</span>
              : null}
          </div>
          <div style={{ fontSize: 13, color: '#888' }}>
            {roster?.visible_mrr_cents !== null && roster?.visible_mrr_cents !== undefined
              ? <>Visible MRR <strong style={{ color: '#1ef0a8' }}>
                  {money(roster.visible_mrr_cents, 'usd')}</strong>
                  {roster.visible_unpriced > 0
                    ? ` · ${roster.visible_unpriced} unpriced` : ''}</>
              : 'No priced subscriptions in view'}
          </div>
        </div>

        {loading ? (
          <div style={{ padding: 40, textAlign: 'center', color: '#888' }}>Loading…</div>
        ) : rows.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: '#888', fontSize: 14 }}>
            No customer matches this filter.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: '#888', textAlign: 'left', fontSize: 12 }}>
                  <th style={{ padding: '10px 16px' }}>Customer</th>
                  <th style={{ padding: '10px 12px' }}>Plan</th>
                  <th style={{ padding: '10px 12px' }}>Status</th>
                  <th style={{ padding: '10px 12px', textAlign: 'right' }}>MRR</th>
                  <th style={{ padding: '10px 12px' }}>Next bill</th>
                  <th style={{ padding: '10px 12px' }}>Card</th>
                  <th style={{ padding: '10px 12px' }}>Last payment</th>
                  <th style={{ padding: '10px 12px', textAlign: 'right' }}>Held</th>
                  <th style={{ padding: '10px 12px' }}>Source</th>
                  <th style={{ padding: '10px 16px' }}></th>
                </tr>
              </thead>
              <tbody>
                {rows.map(c => {
                  const sc = STATUS_COLORS[c.billing_status] || '#888';
                  return (
                    <tr key={c.organization_id} style={{ borderTop: '1px solid #2a2a4a' }}>
                      <td style={{ padding: '12px 16px', fontWeight: 600 }}>
                        {c.name}
                        {c.implementation_status && (
                          <div style={{ fontSize: 11, color: '#7a7a95', marginTop: 2 }}>
                            Implementation: {String(c.implementation_status).replace(/_/g, ' ')}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '12px' }}>
                        {c.plan_name || c.plan_key || '—'}
                        {c.pending_plan && (
                          <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 2 }}>
                            → {c.pending_plan}{when(c.pending_effective_at)
                              ? ` ${when(c.pending_effective_at)}` : ''}
                          </div>
                        )}
                        {c.cancel_at_period_end && (
                          <div style={{ fontSize: 11, color: '#ef4444', marginTop: 2 }}>Cancelling</div>
                        )}
                      </td>
                      <td style={{ padding: '12px' }}>
                        <span style={{ background: `${sc}22`, color: sc, border: `1px solid ${sc}55`,
                                       borderRadius: 20, padding: '2px 10px', fontSize: 12,
                                       fontWeight: 600, textTransform: 'capitalize',
                                       whiteSpace: 'nowrap' }}>
                          {(c.billing_status || 'none').replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td style={{ padding: '12px', textAlign: 'right',
                                   fontVariantNumeric: 'tabular-nums' }}>
                        {c.mrr_cents === null || c.mrr_cents === undefined
                          ? <NoSource reason={c.mrr_unavailable_reason} />
                          : money(c.mrr_cents, c.currency)}
                      </td>
                      <td style={{ padding: '12px', color: '#bbb' }}>
                        {when(c.current_period_end) || '—'}
                        {c.trial_end && (
                          <div style={{ fontSize: 11, color: '#2fb6ff' }}>
                            Trial to {when(c.trial_end)}
                          </div>
                        )}
                      </td>
                      <td style={{ padding: '12px', color: c.card_last4 ? '#bbb' : '#7a7a95' }}>
                        {c.card_last4
                          ? <span style={{ textTransform: 'capitalize' }}>
                              {c.card_brand || 'card'} ••{c.card_last4}</span>
                          : c.has_subscription ? 'none recorded' : '—'}
                      </td>
                      <td style={{ padding: '12px', color: '#bbb' }}>
                        {when(c.last_payment_at) || '—'}
                        {c.last_payment_cents ? (
                          <div style={{ fontSize: 11, color: '#7a7a95' }}>
                            {money(c.last_payment_cents, c.currency)} · {c.invoice_count} inv
                          </div>
                        ) : null}
                      </td>
                      <td style={{ padding: '12px', textAlign: 'right',
                                   fontVariantNumeric: 'tabular-nums',
                                   color: c.held_lead_count > 0 ? '#f59e0b' : '#555',
                                   fontWeight: c.held_lead_count > 0 ? 700 : 400 }}>
                        {c.held_lead_count || '—'}
                      </td>
                      <td style={{ padding: '12px', fontSize: 12 }}>
                        {c.commercial_source?.from_pipeline ? (
                          <>
                            <div style={{ color: '#1ef0a8' }}>From pipeline</div>
                            {c.commercial_source.sales_rep && (
                              <div style={{ color: '#7a7a95' }}>{c.commercial_source.sales_rep}</div>
                            )}
                          </>
                        ) : (
                          // Stated, never fabricated. An invented salesperson on
                          // a self-serve signup eventually pays somebody a
                          // commission they did not earn.
                          <span style={{ color: '#7a7a95' }}
                            title={c.commercial_source?.note || ''}>Outside pipeline</span>
                        )}
                      </td>
                      <td style={{ padding: '12px 16px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <Link to={`/god/customers/${c.organization_id}/360`}
                          style={{ color: '#2fb6ff', textDecoration: 'none', fontWeight: 600 }}>
                          360 →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── The webhook ledger ───────────────────────────────────────────── */}
      <div style={{ ...CARD, marginTop: 24 }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Recent Stripe events</div>
        <div style={{ fontSize: 12, color: '#888', marginBottom: 14 }}>
          Did Stripe tell us, and what did we do. An outcome of <strong>duplicate</strong> is
          Stripe having retried and this platform correctly doing nothing a second time — that is
          idempotency working, not a lost payment.
        </div>
        {!events?.events?.length ? (
          <div style={{ color: '#888', fontSize: 13 }}>
            No Stripe webhook has been recorded yet.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: '#888', textAlign: 'left', fontSize: 12 }}>
                  <th style={{ padding: '8px 12px 8px 0' }}>Received</th>
                  <th style={{ padding: '8px 12px' }}>Event</th>
                  <th style={{ padding: '8px 12px' }}>Outcome</th>
                  <th style={{ padding: '8px 12px' }}>Compensation</th>
                </tr>
              </thead>
              <tbody>
                {events.events.map(e => (
                  <tr key={e.id} style={{ borderTop: '1px solid #2a2a4a', color: '#bbb' }}>
                    <td style={{ padding: '10px 12px 10px 0', whiteSpace: 'nowrap' }}>
                      {when(e.received_at) || '—'}
                    </td>
                    <td style={{ padding: '10px 12px', fontFamily: 'ui-monospace, monospace',
                                 fontSize: 12 }}>{e.event_type}</td>
                    <td style={{ padding: '10px 12px',
                                 color: e.outcome === 'duplicate' ? '#7a7a95' : '#bbb' }}>
                      {e.outcome}
                      {e.detail && (
                        <div style={{ fontSize: 11, color: '#7a7a95' }}>{e.detail}</div>
                      )}
                    </td>
                    <td style={{ padding: '10px 12px', fontSize: 12 }}>
                      {e.earned_compensation
                        ? <span style={{ color: '#1ef0a8' }}>earned</span>
                        : <span style={{ color: '#7a7a95' }}>
                            {e.compensation_note || '—'}
                          </span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
