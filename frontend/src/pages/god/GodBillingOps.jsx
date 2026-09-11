import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import StripeCatalogue from './StripeCatalogue';
// The brand's OTHER catalogue: recurring add-ons and one-time services. A
// separate component from StripeCatalogue because they configure two different
// commercial objects — that one is the subscription tiers, this one is
// everything sold alongside them.
import BrandCatalogue from './BrandCatalogue';

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

// THE SETUP FEE IS THE OTHER HALF OF A CUSTOMER'S MONEY. Every other column
// on this roster describes the subscription, so a customer paying $1,000/mo
// whose $2,500 implementation fee was never collected read exactly like one
// who had paid it. Shown as its own column because it is its own bill.
const SETUP_TONE = {
  paid: ['#1ef0a8', 'Paid'],
  checkout_pending: ['#f59e0b', 'Sent'],
  not_sent: ['#7a7a95', 'Not sent'],
  failed: ['#ef4444', 'Failed'],
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
  // Which customer's refresh is in flight, and what the last one reported.
  const [resyncing, setResyncing] = useState(null);
  const [resyncNote, setResyncNote] = useState('');
  // The differences a refresh WOULD make, waiting to be approved. Nothing has
  // been written while this is set.
  const [resyncPreview, setResyncPreview] = useState(null);
  // The assisted plan change: the form being filled in, the preview awaiting
  // approval, and the last outcome. Nothing is written while either is set.
  const [changeForm, setChangeForm] = useState(null);
  const [changePreview, setChangePreview] = useState(null);
  const [changing, setChanging] = useState(null);
  const [changeNote, setChangeNote] = useState('');

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

  // ── REFRESH ONE CUSTOMER'S MIRROR FROM STRIPE ──────────────────────────
  //
  // PREVIEW FIRST, ALWAYS. The dry run reports what would change and writes
  // nothing, so the reflex of clicking a new button on a billing screen is to
  // look rather than to alter a customer's commercial record. Only a listed
  // difference the operator then confirms is applied.
  //
  // Nothing here writes to Stripe. It re-reads the subscription and re-applies
  // it through the same function the webhook uses, which is what makes it a
  // refresh rather than a correction.
  // STEP 1 — look. Read-only; writes nothing here and nothing at Stripe.
  //
  // The differences are shown IN THE PAGE rather than in a `window.confirm`.
  // A native dialog cannot render a field-by-field table, and an operator
  // approving a change to a customer's commercial record from a wall of
  // newline-joined text is approving something they have not really read —
  // the same objection that took the confirm box off the customer's own
  // billing screen.
  async function handleResync(row) {
    setResyncNote(''); setResyncPreview(null);
    setResyncing(row.organization_id);
    try {
      const preview = await api.post(
        `/god/billing/customers/${row.organization_id}/resync`, { apply: false });

      if (!preview.changed?.length) {
        setResyncNote(`${row.name}: already matches Stripe — nothing to change.`);
        return;
      }
      setResyncPreview({ row, changed: preview.changed });
    } catch (e) {
      setResyncNote(`${row.name}: ${e?.detail || e?.message || 'could not read from Stripe. Nothing was changed.'}`);
    } finally {
      setResyncing(null);
    }
  }

  // ── CHANGE A CUSTOMER'S PLAN FOR THEM ──────────────────────────────────
  //
  // Same two steps, same reasoning, and behind it the SAME functions the
  // customer's own Billing page calls — so an operator cannot produce an
  // outcome the customer could not have produced themselves.
  //
  // This is here because a customer provisioned from a signed deal has a live
  // subscription and, until somebody is invited, no user account at all.
  // Nobody can log in to change it, and the only other route is the Stripe
  // dashboard, which writes nothing back here.
  // Open the form. Its plan list comes from the customer's OWN brand, so one
  // brand's operator view cannot offer another brand's tiers.
  async function handleAssistedChange(row) {
    setChangeNote(''); setChangePreview(null);
    setChanging(row.organization_id);
    try {
      const detail = await api.get(
        `/god/billing/brands/${encodeURIComponent(row.platform_id)}`);
      setChangeForm({
        row,
        plans: (detail.plans || []).filter(p => p.is_active),
        planKey: row.plan_key || '',
        // Blank means KEEP the commitment they are on. Offered explicitly so
        // an operator can also change it on purpose.
        commitmentKey: '',
      });
    } catch (e) {
      setChangeNote(`${row.name}: ${e?.detail || e?.message || 'the brand catalogue could not be loaded.'}`);
    } finally {
      setChanging(null);
    }
  }

  // Ask the server what would happen. Read-only; touches neither Stripe nor
  // the database.
  async function previewAssistedChange() {
    if (!changeForm) return;
    const { row, planKey, commitmentKey } = changeForm;
    setChangeNote('');
    setChanging(row.organization_id);
    try {
      const preview = await api.post(
        `/god/billing/customers/${row.organization_id}/change-plan`,
        { plan: planKey, commitment: commitmentKey || null, apply: false });
      setChangePreview({ row, planKey, commitmentKey: commitmentKey || null,
                         preview });
      setChangeForm(null);
    } catch (e) {
      setChangeNote(`${row.name}: ${e?.detail || e?.message || 'the change could not be previewed. Nothing was changed.'}`);
    } finally {
      setChanging(null);
    }
  }

  async function applyAssistedChange() {
    if (!changePreview) return;
    const { row, planKey, commitmentKey } = changePreview;
    setChanging(row.organization_id);
    try {
      const r = await api.post(
        `/god/billing/customers/${row.organization_id}/change-plan`,
        { plan: planKey, commitment: commitmentKey, apply: true });
      setChangePreview(null);
      setChangeNote(
        r.pending_plan
          ? `${row.name}: change accepted and scheduled — they keep their current plan until the end of this billing period, then move to ${r.pending_plan}.`
          : `${row.name}: change applied ${r.effective || 'immediately'}.`);
      await load();
    } catch (e) {
      setChangeNote(`${row.name}: ${e?.detail || e?.message || 'the change failed. Nothing was charged.'}`);
    } finally {
      setChanging(null);
    }
  }

  // STEP 2 — apply, only what was shown, only when it is confirmed.
  async function applyResync() {
    if (!resyncPreview) return;
    const { row } = resyncPreview;
    setResyncing(row.organization_id);
    try {
      const applied = await api.post(
        `/god/billing/customers/${row.organization_id}/resync`, { apply: true });
      setResyncPreview(null);
      setResyncNote(
        `${row.name}: refreshed — ${applied.changed.length} field(s) updated from Stripe.`);
      await load();
    } catch (e) {
      setResyncNote(`${row.name}: ${e?.detail || e?.message || 'refresh failed. Nothing was changed.'}`);
    } finally {
      setResyncing(null);
    }
  }

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

      {/* ── Stripe & catalogue administration ───────────────────────────────
          CONFIGURATION, ABOVE THE OPERATIONS. This is "what does this brand
          sell and does Stripe know about it" — a setup question with a setup
          answer. It sits above the roster and outside it: putting a
          provisioning button inside a work queue would make a one-off task
          look like part of somebody's daily list. */}
      <StripeCatalogue platformId={platformId} brands={brands} />

      {/* Everything sold ALONGSIDE the tiers. Directly below the tier
          catalogue because they are two halves of one question — what does
          this brand sell — and separated because they are different objects
          with different rules: a tier is what the customer IS, an add-on is
          something they also have. */}
      <BrandCatalogue platformId={platformId} />

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

        {resyncNote && (
          <div style={{ padding: '10px 20px', fontSize: 13, color: '#2fb6ff',
                        background: '#2fb6ff11',
                        borderBottom: '1px solid #2a2a4a' }}>
            {resyncNote}
          </div>
        )}

        {changeNote && (
          <div style={{ padding: '10px 20px', fontSize: 13, color: '#2fb6ff',
                        background: '#2fb6ff11',
                        borderBottom: '1px solid #2a2a4a' }}>
            {changeNote}
          </div>
        )}

        {/* STEP 1 — WHICH PLAN, AND ON WHAT TERMS.
            The commitment defaults to blank, meaning KEEP the one they are on:
            an operator moving somebody between tiers must not move them
            between rates by accident. */}
        {changeForm && (
          <div style={{ padding: '14px 20px', background: '#2fb6ff0e',
                        borderBottom: '1px solid #2a2a4a' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 10 }}>
              Change plan — {changeForm.row.name}
            </div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap',
                          alignItems: 'center', marginBottom: 10 }}>
              <select
                value={changeForm.planKey}
                onChange={e => setChangeForm(f => ({ ...f, planKey: e.target.value }))}
                style={{ background: '#14142b', color: '#e8e8f0',
                         border: '1px solid #2a2a4a', borderRadius: 6,
                         padding: '6px 10px', fontSize: 13 }}
              >
                <option value="">Select a plan…</option>
                {changeForm.plans.map(p => (
                  <option key={p.key} value={p.key}>{p.name || p.key}</option>
                ))}
              </select>
              <select
                value={changeForm.commitmentKey}
                onChange={e => setChangeForm(f => ({ ...f, commitmentKey: e.target.value }))}
                style={{ background: '#14142b', color: '#e8e8f0',
                         border: '1px solid #2a2a4a', borderRadius: 6,
                         padding: '6px 10px', fontSize: 13 }}
              >
                <option value="">Keep their commitment
                  {changeForm.row.commitment_label
                    ? ` (${changeForm.row.commitment_label})` : ''}</option>
                <option value="month_to_month">Month-to-month</option>
                <option value="term_agreement">Committed term</option>
              </select>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                onClick={previewAssistedChange}
                disabled={!changeForm.planKey || Boolean(changing)}
                style={{ background: changeForm.planKey ? '#2fb6ff' : '#2a2a4a',
                         color: changeForm.planKey ? '#04121c' : '#666',
                         border: 'none', borderRadius: 6, padding: '6px 14px',
                         fontSize: 12, fontWeight: 700,
                         cursor: changeForm.planKey && !changing ? 'pointer' : 'default' }}
              >
                {changing ? 'Checking…' : 'Preview change'}
              </button>
              <button
                type="button"
                onClick={() => setChangeForm(null)}
                style={{ background: 'transparent', color: '#aaa',
                         border: '1px solid #2a2a4a', borderRadius: 6,
                         padding: '6px 14px', fontSize: 12, cursor: 'pointer' }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* STEP 2 — WHAT IT WOULD DO, from the brand's own configuration. */}
        {changePreview && (
          <div style={{ padding: '14px 20px', background: '#f59e0b0e',
                        borderBottom: '1px solid #2a2a4a' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
              {changePreview.row.name} — {changePreview.preview.direction}
            </div>
            <table style={{ fontSize: 12, borderCollapse: 'collapse',
                            marginBottom: 12 }}>
              <tbody>
                {[
                  ['From', `${changePreview.preview.from_plan_name || '—'} · ${money(changePreview.preview.from_cents, changePreview.preview.currency) || '—'}/mo`],
                  ['To', `${changePreview.preview.to_plan_name} · ${money(changePreview.preview.to_cents, changePreview.preview.currency) || '—'}/mo`],
                  ['Commitment', changePreview.preview.commitment_label || 'Not recorded'],
                  ['Takes effect', changePreview.preview.effective_at
                    ? `${changePreview.preview.effective} (${when(changePreview.preview.effective_at)})`
                    : changePreview.preview.effective],
                ].map(([k, v]) => (
                  <tr key={k}>
                    <td style={{ padding: '3px 16px 3px 0', color: '#888' }}>{k}</td>
                    <td style={{ padding: '3px 0', fontWeight: 600 }}>{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {changePreview.preview.commitment_changed && (
              <div style={{ fontSize: 12, color: '#f59e0b', fontWeight: 600,
                            marginBottom: 8 }}>
                This also changes their commitment.
              </div>
            )}
            <div style={{ fontSize: 12, color: '#888', marginBottom: 10,
                          maxWidth: 620, lineHeight: 1.5 }}>
              {changePreview.preview.proration_note}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                onClick={applyAssistedChange}
                disabled={Boolean(changing)}
                style={{ background: '#1ef0a8', color: '#04120c', border: 'none',
                         borderRadius: 6, padding: '6px 14px', fontSize: 12,
                         fontWeight: 700,
                         cursor: changing ? 'default' : 'pointer' }}
              >
                {changing ? 'Applying…' : 'Apply change'}
              </button>
              <button
                type="button"
                onClick={() => setChangePreview(null)}
                disabled={Boolean(changing)}
                style={{ background: 'transparent', color: '#aaa',
                         border: '1px solid #2a2a4a', borderRadius: 6,
                         padding: '6px 14px', fontSize: 12,
                         cursor: changing ? 'default' : 'pointer' }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* WHAT A REFRESH WOULD CHANGE — shown before anything is written.
            Field by field, old value to new value, so the operator approves
            what they have actually read. */}
        {resyncPreview && (
          <div style={{ padding: '14px 20px', background: '#f59e0b0e',
                        borderBottom: '1px solid #2a2a4a' }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
              {resyncPreview.row.name} — {resyncPreview.changed.length} field(s)
              differ from Stripe
            </div>
            <table style={{ fontSize: 12, borderCollapse: 'collapse',
                            marginBottom: 12 }}>
              <tbody>
                {resyncPreview.changed.map(c => (
                  <tr key={c.field}>
                    <td style={{ padding: '3px 14px 3px 0', color: '#888' }}>
                      {c.field}
                    </td>
                    <td style={{ padding: '3px 10px 3px 0', color: '#7a7a95' }}>
                      {c.from ?? 'not set'}
                    </td>
                    <td style={{ padding: '3px 10px 3px 0', color: '#888' }}>→</td>
                    <td style={{ padding: '3px 0', color: '#1ef0a8',
                                 fontWeight: 600 }}>
                      {c.to ?? 'not set'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ fontSize: 12, color: '#888', marginBottom: 10 }}>
              This updates this platform's copy from Stripe. Nothing is written
              to Stripe and no money moves.
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                onClick={applyResync}
                disabled={Boolean(resyncing)}
                style={{ background: '#1ef0a8', color: '#04120c', border: 'none',
                         borderRadius: 6, padding: '6px 14px', fontSize: 12,
                         fontWeight: 700,
                         cursor: resyncing ? 'default' : 'pointer' }}
              >
                {resyncing ? 'Refreshing…' : 'Apply refresh'}
              </button>
              <button
                type="button"
                onClick={() => setResyncPreview(null)}
                disabled={Boolean(resyncing)}
                style={{ background: 'transparent', color: '#aaa',
                         border: '1px solid #2a2a4a', borderRadius: 6,
                         padding: '6px 14px', fontSize: 12,
                         cursor: resyncing ? 'default' : 'pointer' }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

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
                  <th style={{ padding: '10px 12px' }}>Setup fee</th>
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
                        {/* WHICH RATE, under the tier name. A Starter row
                            reading $500 and a Starter row reading $597 are
                            both correct and differ only by commitment, so a
                            column that shows the tier alone makes one of them
                            look like a mistake. Absent when the subscription
                            predates the column — "not recorded" is honest,
                            naming a commitment nobody stored would not be. */}
                        {c.commitment_label && (
                          <div style={{ fontSize: 11, color: '#7a7a95', marginTop: 2 }}>
                            {c.commitment_label}
                          </div>
                        )}
                        {/* A scheduled change names the RATE as well as the
                            tier. A commitment-only downgrade has a pending
                            plan identical to the current one, so "→ growth"
                            on a Growth customer read as a change to nothing. */}
                        {c.pending_plan && (
                          <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 2 }}>
                            → {c.pending_plan === (c.plan_key || c.plan_name)
                                 && c.pending_commitment_label
                                 ? c.pending_commitment_label
                                 : `${c.pending_plan}${c.pending_commitment_label
                                      ? ` · ${c.pending_commitment_label}` : ''}`}
                            {when(c.pending_effective_at)
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
                      {/* NO IMPLEMENTATION RECORD MEANS NO SETUP FEE IS OWED,
                          which is not the same as one that has not been sent.
                          A dash, never a fabricated "Not sent". */}
                      <td style={{ padding: '12px' }}>
                        {(() => {
                          if (!c.setup_fee) {
                            return <span style={{ color: '#555' }}>—</span>;
                          }
                          const [tone, label] =
                            SETUP_TONE[c.setup_fee.status]
                            || ['#7a7a95', String(c.setup_fee.status)];
                          return (
                            <>
                              <span style={{ background: `${tone}22`, color: tone,
                                             border: `1px solid ${tone}55`,
                                             borderRadius: 20, padding: '2px 10px',
                                             fontSize: 12, fontWeight: 600,
                                             whiteSpace: 'nowrap' }}>
                                {label}
                              </span>
                              {c.setup_fee.paid_cents ? (
                                <div style={{ fontSize: 11, color: '#7a7a95',
                                              marginTop: 2 }}>
                                  {money(c.setup_fee.paid_cents, c.currency)}
                                  {when(c.setup_fee.paid_at)
                                    ? ` · ${when(c.setup_fee.paid_at)}` : ''}
                                </div>
                              ) : null}
                            </>
                          );
                        })()}
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
                        {/* Offered only where there is a subscription to
                            re-read. On a customer who has never subscribed the
                            endpoint refuses, and a button that always errors
                            teaches an operator to ignore the errors. */}
                        {c.has_subscription && (
                          <button
                            type="button"
                            onClick={() => handleAssistedChange(c)}
                            disabled={changing === c.organization_id}
                            title="Move this customer to a different plan, through the same path their own Billing page uses."
                            style={{ background: 'transparent', color: '#7a7a95',
                                     border: '1px solid #2a2a4a', borderRadius: 6,
                                     padding: '4px 10px', fontSize: 12,
                                     marginRight: 8,
                                     cursor: changing === c.organization_id
                                       ? 'default' : 'pointer' }}
                          >
                            Change plan
                          </button>
                        )}
                        {c.has_subscription && (
                          <button
                            type="button"
                            onClick={() => handleResync(c)}
                            disabled={resyncing === c.organization_id}
                            title="Re-read this subscription from Stripe and refresh this platform's copy. Nothing is written to Stripe."
                            style={{ background: 'transparent', color: '#7a7a95',
                                     border: '1px solid #2a2a4a', borderRadius: 6,
                                     padding: '4px 10px', fontSize: 12,
                                     marginRight: 10,
                                     cursor: resyncing === c.organization_id
                                       ? 'default' : 'pointer' }}
                          >
                            {resyncing === c.organization_id ? 'Checking…' : 'Refresh'}
                          </button>
                        )}
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
