import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { detectTheme, BRAND_CONFIG } from '../theme.js';

// THE SUPPORT ADDRESS BELONGS TO WHICHEVER BRAND THE CUSTOMER IS ON.
//
// This page hard-coded `support@bookaboost.live` in three places, so an
// EvoSys Pro customer opening Billing was told to email BookaBoost — a company
// they have no relationship with, about their own invoice. `theme.js` already
// resolves the brand from the hostname and every other screen uses it; this one
// simply never did.
const BRAND = BRAND_CONFIG[detectTheme()] || {};
const SUPPORT_EMAIL = BRAND.supportEmail || 'support@evosyspro.live';

// ═══════════════════════════════════════════════════════════════════════════
// THE HARDCODED `PLANS` ARRAY THAT LIVED HERE IS GONE.
// ═══════════════════════════════════════════════════════════════════════════
//
// It was a second, hand-maintained copy of the price list, and the browser
// rendered THAT — `GET /billing/plans` was never called at all. Three things
// were wrong with it and only deletion fixes any of them:
//
//   IT MEANT THE BROWSER BELIEVED IT KNEW THE PRICE. A price on the client is
//   a price a customer can edit. Every amount on this screen is now display
//   only, computed from what the server sent, and no request below carries an
//   amount or a Stripe price id — checkout and change-plan take a plan KEY and
//   an interval, and the server resolves the money from the brand's own
//   catalogue.
//
//   IT HAD ALREADY DRIFTED. This file rendered annual as `price * 11 / 12`
//   and labelled it "Month 13 free", while the server's copy did something
//   else again. Nothing here derives an annual price any more: a plan is shown
//   as annual only when the catalogue carries a real `annual_cents`.
//
//   IT WAS THE SAME FOR EVERY BRAND. `brand_billing_plans` is scoped per
//   brand, so a white-label customer now sees their own brand's tiers and
//   names rather than EvoSys Pro's.
// ═══════════════════════════════════════════════════════════════════════════

const STATUS_COLORS = {
  active: '#1ef0a8',
  trialing: '#2fb6ff',
  past_due: '#f59e0b',
  unpaid: '#f59e0b',
  paused: '#f59e0b',
  incomplete: '#f59e0b',
  canceled: '#ef4444',
  incomplete_expired: '#ef4444',
};

// THE STATUSES IN WHICH A SUBSCRIPTION ALREADY EXISTS — mirrored exactly from
// SubscriptionStatus.OCCUPIED on the server, which is what /billing/checkout
// refuses a second checkout on. The list is duplicated here ONLY to decide
// which button to draw; the server refuses regardless, so a stale copy costs a
// clearer message and never a double charge.
const OCCUPIED_STATUSES = [
  'trialing', 'active', 'past_due', 'unpaid', 'incomplete', 'paused',
];

const COMMITMENT_LABEL = {
  month_to_month: 'Month-to-month',
  term_agreement: 'Term agreement',
};

// ═══════════════════════════════════════════════════════════════════════════
// THE PLAN CHANGE DIALOG — replacing window.confirm()
// ═══════════════════════════════════════════════════════════════════════════
//
// The browser's own confirm box was doing the work of a billing confirmation.
// It could show one string of text, it could not show a price, and everything
// it DID say about proration and timing was written into the sentence rather
// than read from the server — so it stated the EvoSys policy to every brand,
// whatever each brand had actually configured.
//
// This shows the customer what changes and what it costs, side by side, and
// every line of it comes from data the server sent. Where a figure is not
// authoritative it is not shown: Stripe computes the exact proration at the
// moment of the change, and a number this page invented would be a quote the
// customer's card is not going to match.
function PlanChangeDialog({ open, change, busy, error, onConfirm, onCancel }) {
  if (!open || !change) return null;

  const { fromName, toName, fromAmount, toAmount, intervalLabel,
          commitmentLabel, commitmentChanging, timingText, prorationText,
          actionLabel } = change;

  const Row = ({ label, children }) => (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16,
                  padding: '10px 0', borderBottom: '1px solid #2a2a4a',
                  fontSize: 14 }}>
      <span style={{ color: '#888' }}>{label}</span>
      <span style={{ fontWeight: 600, textAlign: 'right' }}>{children}</span>
    </div>
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Confirm plan change"
      onClick={busy ? undefined : onCancel}
      style={{ position: 'fixed', inset: 0, background: '#000000aa',
               display: 'flex', alignItems: 'center', justifyContent: 'center',
               padding: 16, zIndex: 1000 }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{ background: '#14142b', border: '1px solid #2a2a4a',
                 borderRadius: 12, padding: '24px', width: '100%',
                 maxWidth: 460, maxHeight: '90vh', overflowY: 'auto',
                 boxShadow: '0 18px 60px #0009' }}
      >
        <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 4px' }}>
          Confirm your plan change
        </h2>
        <p style={{ color: '#888', fontSize: 13, margin: '0 0 18px' }}>
          Nothing changes until you confirm.
        </p>

        <Row label="Current plan">{fromName}</Row>
        <Row label="New plan">{toName}</Row>
        <Row label="Current amount">{fromAmount || '—'}</Row>
        <Row label="New amount">{toAmount || '—'}</Row>
        <Row label="Billing frequency">{intervalLabel}</Row>
        <Row label="Commitment">
          {commitmentLabel || 'Not recorded'}
          {commitmentChanging && (
            <div style={{ fontSize: 11, color: '#f59e0b', fontWeight: 600,
                          marginTop: 2 }}>
              This change also changes your commitment
            </div>
          )}
        </Row>
        <Row label="Takes effect">{timingText}</Row>

        {prorationText && (
          <p style={{ color: '#888', fontSize: 13, margin: '16px 0 0',
                      lineHeight: 1.5 }}>
            {prorationText}
          </p>
        )}

        {error && (
          <div style={{ background: '#ef444422', border: '1px solid #ef4444',
                        color: '#ef4444', borderRadius: 8, padding: '10px 12px',
                        fontSize: 13, marginTop: 16 }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end',
                      marginTop: 22, flexWrap: 'wrap' }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            style={{ background: 'transparent', color: '#aaa',
                     border: '1px solid #2a2a4a', borderRadius: 8,
                     padding: '10px 18px', fontSize: 14,
                     cursor: busy ? 'default' : 'pointer' }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={busy}
            style={{ background: busy ? '#1ef0a855' : '#1ef0a8', color: '#04120c',
                     border: 'none', borderRadius: 8, padding: '10px 20px',
                     fontSize: 14, fontWeight: 700,
                     cursor: busy ? 'default' : 'pointer' }}
          >
            {busy ? 'Working…' : actionLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function money(cents, currency) {
  if (cents === null || cents === undefined) return null;
  const amount = cents / 100;
  const symbol = (currency || 'usd').toLowerCase() === 'usd' ? '$' : '';
  return symbol + amount.toLocaleString(undefined, {
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  }) + (symbol ? '' : ` ${(currency || '').toUpperCase()}`);
}

// The API sends real datetimes (ISO strings). The previous version multiplied
// them by 1000 as though they were unix seconds, which rendered every date as
// "Invalid Date" — so a period end and a trial end were never actually shown.
function when(value) {
  if (value === null || value === undefined || value === '') return null;
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleDateString(undefined, {
    year: 'numeric', month: 'long', day: 'numeric',
  });
}

// A recurring amount with its period attached. "$597" and "$597/mo" are the
// same number and different facts, and a confirmation dialog comparing two
// plans is precisely where the period must not be left to inference.
function fmtRate(cents, currency, interval) {
  const amount = money(cents, currency);
  if (!amount) return null;
  return amount + (interval === 'year' ? '/yr' : '/mo');
}

const CARD = {
  background: '#1a1a2e', border: '1px solid #2a2a4a',
  borderRadius: '12px', padding: '24px',
};

export default function Billing() {
  const [sub, setSub] = useState(null);
  const [catalog, setCatalog] = useState(null);   // GET /billing/plans
  const [interval, setInterval] = useState('month');
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [err, setErr] = useState('');
  const [errStatus, setErrStatus] = useState(null);
  const [notice, setNotice] = useState('');
  // The plan change the customer is being ASKED about — null when no dialog is
  // open. Kept separate from `actionLoading` because they answer different
  // questions: one is "which button is spinning", the other is "what exactly
  // has this customer been shown and not yet agreed to".
  const [pendingChange, setPendingChange] = useState(null);
  const [changeBusy, setChangeBusy] = useState(false);
  const [changeError, setChangeError] = useState('');
  const [searchParams] = useSearchParams();

  const success = searchParams.get('success') === '1';
  const canceled = searchParams.get('canceled') === '1';

  // ══ WHICH OBLIGATION DID THEY JUST PAY? ═══════════════════════════════
  //
  // THE FALSE CLAIM THIS FIXES, caught on the first real TEST payment. A
  // customer paid a $1,500 ONE-TIME implementation fee and this page told
  // them "Subscription activated! Your plan is now live." No subscription
  // existed, none was created, and none was going to be — the banner keyed
  // off `success=1` alone and assumed every checkout was a subscription.
  //
  // That is the same class of error as the $3,500 combined charge: the
  // billing engine kept the two obligations separate and the screen merged
  // them back together. The customer's own receipt page is the worst place
  // to be wrong about what they bought.
  //
  // The return URL has carried `part` since the split shipped. It is read
  // here rather than inferred. An unrecognised or absent `part` gets neutral
  // wording that claims nothing specific, because the honest failure of a
  // confirmation message is vagueness, not a confident wrong answer.
  const part = searchParams.get('part');
  const successMessage =
    part === 'setup'
      ? '✅ Setup fee paid. Thank you — your onboarding and build are covered. '
        + 'This was a one-time charge; it does not start a subscription.'
      : part === 'subscription'
        ? '✅ Subscription started! Your plan is now live.'
        : '✅ Payment received. Thank you.';

  const load = useCallback(async () => {
    // Both reads, both allowed to fail independently. A subscription that will
    // not load must not hide the catalogue, and vice versa — a page that shows
    // nothing because one of two calls failed is a page nobody can act on.
    const [subRes, planRes] = await Promise.allSettled([
      api.get('/billing/subscription'),
      api.get('/billing/plans'),
    ]);
    setSub(subRes.status === 'fulfilled' ? subRes.value : null);
    if (planRes.status === 'fulfilled') {
      setCatalog(planRes.value);
    } else {
      setCatalog({ plans: [], configured: false, unreachable: true,
                   detail: planRes.reason?.message || 'Plans could not be loaded.' });
    }
  }, []);

  useEffect(() => {
    load().finally(() => setLoading(false));
  }, [load]);

  function fail(e) {
    // The server's own words. `detail` is where "no policy is configured for
    // this brand" and "this organization already has a subscription" arrive,
    // and both are things the customer needs to read verbatim rather than
    // behind a generic failure message.
    setErrStatus(e?.status ?? null);
    setErr(e?.message || 'The request could not be completed.');
    setActionLoading(null);
  }

  // A NEW SUBSCRIPTION. Only ever offered when there is not one already.
  async function handleCheckout(planKey) {
    setErr(''); setErrStatus(null); setNotice('');
    setActionLoading(planKey);
    try {
      // PLAN KEY AND INTERVAL. NOTHING ELSE. No price, no Stripe price id.
      const result = await api.post('/billing/checkout', { plan: planKey, interval });
      window.location.href = result.checkout_url;
    } catch (e) { fail(e); }
  }

  // AN EXISTING SUBSCRIPTION, MOVED IN PLACE.
  //
  // This is the half that was missing, and its absence is the defect: every
  // plan card used to be a live "Select Plan" that called checkout, which
  // created a SECOND Stripe subscription without cancelling the first. A
  // customer on Starter who clicked Growth was billed for both, every month.
  //
  // ── STEP 1: ASK THE SERVER WHAT WOULD HAPPEN ──────────────────────────
  //
  // This replaces a `window.confirm` whose sentence was written here in the
  // browser. That sentence stated the EvoSys upgrade/downgrade policy to every
  // brand on the platform regardless of what each brand had configured, showed
  // no price at all, and — because it ran before any server call — could
  // cheerfully describe a change the server was about to refuse.
  //
  // Nothing is changed by opening the dialog. The preview is read-only and
  // touches neither the database nor Stripe.
  async function handleChangePlan(planKey, planName) {
    setErr(''); setErrStatus(null); setNotice('');
    setChangeError('');
    setActionLoading(planKey);
    try {
      const p = await api.post('/billing/change-plan/preview',
                               { plan: planKey, interval });
      setPendingChange({
        planKey,
        planName,
        fromName: p.from_plan_name || p.from_plan_key || 'your current plan',
        toName: p.to_plan_name || planName,
        fromAmount: fmtRate(p.from_cents, p.currency, p.interval),
        toAmount: fmtRate(p.to_cents, p.currency, p.interval),
        intervalLabel: p.interval === 'year' ? 'Billed yearly' : 'Billed monthly',
        commitmentLabel: p.commitment_label
          || COMMITMENT_LABEL[p.commitment] || null,
        commitmentChanging: Boolean(p.commitment_changed),
        timingText: p.effective_at
          ? `${p.effective} (${when(p.effective_at)})`
          : (p.effective || 'immediately'),
        prorationText: p.proration_note || '',
        actionLabel: p.direction === 'downgrade'
          ? 'Confirm change' : 'Confirm upgrade',
      });
      setActionLoading(null);
    } catch (e) {
      // A refusal at preview time is the honest moment to show it — before the
      // customer has agreed to anything.
      fail(e);
    }
  }

  // ── STEP 2: APPLY IT, AND SAY NOTHING UNTIL THE SERVER HAS ────────────
  async function confirmChangePlan() {
    if (!pendingChange) return;
    setChangeError('');
    setChangeBusy(true);
    try {
      const r = await api.post('/billing/change-plan', {
        plan: pendingChange.planKey, interval,
      });
      // Only now, with the server's own answer in hand.
      setChangeBusy(false);
      setPendingChange(null);
      setNotice(
        r.pending_plan
          ? `Change accepted. You keep your current plan until the end of this billing period, then move to ${pendingChange.planName}.`
          : `Change applied ${r.effective || 'immediately'}.`);
      await load();
      setActionLoading(null);
    } catch (e) {
      // The dialog STAYS OPEN and shows why. Closing it and dropping a message
      // on the page behind would read like the change went through.
      setChangeBusy(false);
      setChangeError(
        (e && (e.detail || e.message)) ||
        'The change could not be applied. Nothing was charged.');
    }
  }

  async function handlePortal() {
    setErr(''); setErrStatus(null); setNotice('');
    setActionLoading('portal');
    try {
      const result = await api.post('/billing/portal');
      window.location.href = result.portal_url;
    } catch (e) { fail(e); }
  }

  // CANCELLING A SCHEDULED CHANGE THAT HAS NOT HAPPENED YET.
  //
  // The endpoint existed and nothing on this page called it, so a customer who
  // scheduled a downgrade by mistake had no way to undo it and would simply
  // lose the tier at period end. This is that button.
  async function handleCancelPending(pendingName) {
    setErr(''); setErrStatus(null); setNotice('');
    if (!window.confirm(
      `Keep your current plan and cancel the scheduled change to ${pendingName}?`)) return;
    setActionLoading('cancel-pending');
    try {
      await api.post('/billing/cancel-pending-change');
      setNotice('The scheduled change was cancelled. You stay on your current plan.');
      await load();
      setActionLoading(null);
    } catch (e) { fail(e); }
  }

  // ── Everything below is DERIVED FROM THE SERVER'S ANSWER ────────────────
  const plans = catalog?.plans || [];
  const configured = Boolean(catalog?.configured) && plans.length > 0;
  const currentKey = sub?.plan || catalog?.current_plan || 'trial';
  const billingStatus = (sub?.billing_status || 'trialing').toLowerCase();
  const currentInterval = sub?.stripe_plan_interval || catalog?.current_interval || 'month';

  // WHETHER A SUBSCRIPTION ALREADY EXISTS DECIDES WHICH ENDPOINT THIS PAGE MAY
  // CALL. Same test the server applies, so the button drawn is the operation
  // that will actually be permitted.
  const hasSubscription = Boolean(sub?.stripe_subscription_id)
    && OCCUPIED_STATUSES.includes(billingStatus);

  const planFor = (key) => plans.find(p => p.key === key) || null;
  const nameFor = (key) => planFor(key)?.name || key;
  const periodEnd = when(sub?.current_period_end);
  const trialEnd = when(sub?.trial_end);
  const pendingPlan = sub?.pending_plan || null;
  const annualOffered = plans.some(p => p.annual_cents !== null && p.annual_cents !== undefined);
  const invoices = sub?.invoices || [];
  const statusColor = STATUS_COLORS[billingStatus] || '#888';

  // ── What the plan enforces, and how much of it is in use ────────────────
  //
  // The server has returned all of this since plan limits were built and this
  // page rendered NONE of it, so a customer could hit a ceiling with no way to
  // have seen it coming. `limits` reports the CURRENT plan's ceilings even
  // when a downgrade is scheduled - the customer keeps what they paid for -
  // and `pending_limits` previews what they drop to.
  const limits = sub?.limits || null;
  const heldLeads = limits?.capacity_hold?.held || 0;
  const pm = sub?.payment_method || null;
  const customerSince = when(sub?.customer_since);
  const recurring = money(sub?.recurring_cents, sub?.currency);

  const USAGE_ROWS = limits ? [
    { key: 'max_users', label: 'Users' },
    { key: 'max_leads', label: 'Leads' },
  ].map(({ key, label }) => {
    const cur = limits.limits?.[key] || {};
    const pend = limits.pending_limits?.[key] || null;
    return {
      label,
      used: cur.used ?? null,
      limit: cur.limit ?? null,
      unlimited: cur.unlimited !== false && (cur.limit === null || cur.limit === undefined),
      pendingLimit: pend?.limit ?? null,
    };
  }) : [];

  // Start on the interval the customer is actually billed on, so the prices
  // shown are the ones they are paying.
  useEffect(() => {
    if (sub?.stripe_plan_interval) setInterval(sub.stripe_plan_interval);
  }, [sub?.stripe_plan_interval]);

  if (loading) return (
    <div style={{ padding: '40px', color: '#aaa', textAlign: 'center' }}>Loading billing info…</div>
  );

  return (
    <div style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <PlanChangeDialog
        open={Boolean(pendingChange)}
        change={pendingChange}
        busy={changeBusy}
        error={changeError}
        onConfirm={confirmChangePlan}
        onCancel={() => {
          // A cancelled dialog must leave nothing behind — no spinner on a
          // plan card, no half-set error, and above all no change.
          if (changeBusy) return;
          setPendingChange(null);
          setChangeError('');
          setActionLoading(null);
        }}
      />
      <h1 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '8px' }}>Billing & Plan</h1>
      <p style={{ color: '#aaa', marginBottom: '32px' }}>Manage your subscription and see what you have been charged.</p>

      {success && (
        <div style={{ background: '#1ef0a820', border: '1px solid #1ef0a8', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', color: '#1ef0a8', fontWeight: '600' }}>
          {successMessage}
        </div>
      )}
      {canceled && (
        <div style={{ background: '#f59e0b20', border: '1px solid #f59e0b', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', color: '#f59e0b' }}>
          Checkout canceled. No changes were made.
        </div>
      )}
      {notice && (
        <div style={{ background: '#2fb6ff20', border: '1px solid #2fb6ff', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', color: '#2fb6ff' }}>
          {notice}
        </div>
      )}
      {err && (
        <div style={{ background: '#ef444420', border: '1px solid #ef4444', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', color: '#ef4444', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <span>⚠️</span>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              {errStatus === 409 ? 'This change was not applied' : 'Action required'}
            </div>
            {/* THE SERVER'S OWN detail, VERBATIM. A 409 here is either "you
                already have a subscription" or "no plan-change policy is
                configured for this brand", and paraphrasing either one leaves
                the customer with no idea what to do next. */}
            <div style={{ fontSize: 14 }}>{err}</div>
            <div style={{ fontSize: 13, marginTop: 8, color: '#aaa' }}>
              Need help? Contact your platform administrator at{' '}
              <a href={`mailto:${SUPPORT_EMAIL}`} style={{ color: '#ef4444' }}>{SUPPORT_EMAIL}</a>.
            </div>
          </div>
        </div>
      )}

      {/* ── Current plan summary ─────────────────────────────────────────── */}
      <div style={{ ...CARD, marginBottom: '32px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <div style={{ fontSize: '13px', color: '#888', marginBottom: '4px' }}>Current plan</div>
          <div style={{ fontSize: '22px', fontWeight: '700' }}>{nameFor(currentKey)}</div>
          <div style={{ fontSize: '13px', color: '#888', marginTop: '4px' }}>
            Billed {currentInterval === 'year' ? 'annually' : 'monthly'}
          </div>
          {periodEnd && (
            <div style={{ fontSize: '13px', color: '#888', marginTop: '4px' }}>
              {sub?.cancel_at_period_end ? 'Cancels' : 'Renews'} {periodEnd}
            </div>
          )}
          {trialEnd && (
            <div style={{ fontSize: '13px', color: '#2fb6ff', marginTop: '4px' }}>
              Trial ends {trialEnd}
            </div>
          )}
          {/* A DOWNGRADE THAT HAS NOT HAPPENED YET. Both answers are true at
              once — the tier paid for until the period ends, and the tier it
              drops to afterwards — so the screen says both, in words. */}
          {pendingPlan && (
            <div style={{ fontSize: '13px', color: '#f59e0b', marginTop: '8px', maxWidth: 420 }}>
              {periodEnd
                ? `${nameFor(currentKey)} until ${periodEnd}, then ${nameFor(pendingPlan)}.`
                : `${nameFor(currentKey)} until the end of your current billing period, then ${nameFor(pendingPlan)}.`}
              {' '}You keep the plan you have already paid for until then.
              <div style={{ marginTop: 8 }}>
                <button onClick={() => handleCancelPending(nameFor(pendingPlan))}
                  disabled={actionLoading === 'cancel-pending'}
                  style={{ background: 'transparent', color: '#f59e0b', border: '1px solid #f59e0b66',
                           borderRadius: 6, padding: '6px 12px', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>
                  {actionLoading === 'cancel-pending' ? 'Cancelling…' : 'Keep my current plan'}
                </button>
              </div>
            </div>
          )}
          {sub?.cancel_at_period_end && !pendingPlan && (
            <div style={{ fontSize: '13px', color: '#ef4444', marginTop: '8px' }}>
              Your subscription is set to cancel{periodEnd ? ` on ${periodEnd}` : ' at the end of this period'}.
            </div>
          )}
        </div>

        {/* WHAT THEY PAY AND SINCE WHEN. Both come from the server: the amount
            is the catalogue's price for the plan, interval AND COMMITMENT they
            are on, and nothing on this page derives money any more.

            The commitment matters here more than anywhere else. A tier has two
            monthly rates and both are the `month` interval, so this figure was
            resolvable only to the committed-term price — meaning a
            month-to-month customer read a number LOWER than their card is
            charged, on their own billing screen. The label under it says which
            terms produced the figure, so the two are never mistaken again. */}
        <div style={{ minWidth: 170 }}>
          <div style={{ fontSize: '13px', color: '#888', marginBottom: '4px' }}>Recurring</div>
          <div style={{ fontSize: '22px', fontWeight: '700' }}>
            {recurring
              ? <>{recurring}<span style={{ fontSize: 13, fontWeight: 400, color: '#888' }}>
                  {currentInterval === 'year' ? '/yr' : '/mo'}</span></>
              : <span style={{ fontSize: 15, color: '#888' }}>Not priced</span>}
          </div>
          {recurring && sub?.commitment_label && (
            <div style={{ fontSize: '13px', color: '#888', marginTop: '4px' }}>
              {sub.commitment_label}
            </div>
          )}
          {customerSince && (
            <div style={{ fontSize: '13px', color: '#888', marginTop: '8px' }}>
              Customer since {customerSince}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
          <span style={{ background: `${statusColor}22`, color: statusColor, border: `1px solid ${statusColor}55`, borderRadius: '20px', padding: '4px 14px', fontSize: '13px', fontWeight: '600', textTransform: 'capitalize' }}>
            {billingStatus.replace(/_/g, ' ')}
          </span>
          {sub?.stripe_customer_id && (
            <button onClick={handlePortal} disabled={actionLoading === 'portal'} style={{ background: '#2a2a4a', color: '#fff', border: 'none', borderRadius: '8px', padding: '10px 20px', cursor: 'pointer', fontWeight: '600', fontSize: '14px' }}>
              {actionLoading === 'portal' ? 'Opening…' : 'Payment method & invoices →'}
            </button>
          )}
        </div>
      </div>

      {/* ── Plan usage / entitlements ────────────────────────────────────── */}
      {limits && USAGE_ROWS.length > 0 && (
        <div style={{ ...CARD, marginBottom: '32px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'baseline', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
            <div style={{ fontWeight: '700', fontSize: '16px' }}>Plan usage</div>
            <div style={{ fontSize: 12, color: '#888' }}>
              Against your current plan{pendingPlan ? ' — not the scheduled one' : ''}
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 20 }}>
            {USAGE_ROWS.map(row => {
              // An unlimited entitlement has no bar to draw. Drawing an empty
              // one would imply a ceiling that does not exist.
              const pct = (!row.unlimited && row.limit)
                ? Math.min(100, Math.round(((row.used || 0) / row.limit) * 100))
                : null;
              const near = pct !== null && pct >= 80;
              const full = pct !== null && pct >= 100;
              const bar = full ? '#ef4444' : near ? '#f59e0b' : '#1ef0a8';
              return (
                <div key={row.label}>
                  <div style={{ display: 'flex', justifyContent: 'space-between',
                                fontSize: 13, marginBottom: 6 }}>
                    <span style={{ color: '#ccc', fontWeight: 600 }}>{row.label}</span>
                    <span style={{ color: full ? '#ef4444' : '#888',
                                   fontVariantNumeric: 'tabular-nums' }}>
                      {row.used ?? '—'}{row.unlimited
                        ? ' used · unlimited'
                        : ` of ${row.limit}`}
                    </span>
                  </div>
                  {pct !== null && (
                    <div style={{ height: 6, background: '#2a2a4a', borderRadius: 3, overflow: 'hidden' }}>
                      <div style={{ width: `${pct}%`, height: '100%', background: bar }} />
                    </div>
                  )}
                  {/* What a scheduled downgrade will reduce this to. Shown as a
                      preview, never applied early — they paid for the tier they
                      are on until the period ends. */}
                  {pendingPlan && row.pendingLimit !== null && row.pendingLimit !== undefined && (
                    <div style={{ fontSize: 12, color: '#f59e0b', marginTop: 6 }}>
                      Drops to {row.pendingLimit} on {nameFor(pendingPlan)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* HELD PROSPECTS. Real inbound business that arrived while the plan
              was full — kept, not discarded, and not consuming the plan or any
              paid automation until there is room. This is the number that makes
              an upgrade conversation concrete. */}
          {heldLeads > 0 && (
            <div style={{ marginTop: 20, padding: '14px 16px', borderRadius: 8,
                          background: '#f59e0b14', border: '1px solid #f59e0b44' }}>
              <div style={{ color: '#f59e0b', fontWeight: 700, fontSize: 14, marginBottom: 4 }}>
                {heldLeads} inbound {heldLeads === 1 ? 'prospect is' : 'prospects are'} held over plan capacity
              </div>
              <div style={{ color: '#c9a15a', fontSize: 13 }}>
                They arrived after your plan was full and have been kept safely — nothing was
                discarded. They are not counted against your plan and no messages are sent to
                them. Upgrade to release {heldLeads === 1 ? 'it' : 'them'} into your leads.
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Payment method ───────────────────────────────────────────────── */}
      {(pm?.on_file || pm?.manageable) && (
        <div style={{ ...CARD, marginBottom: '32px', display: 'flex',
                      justifyContent: 'space-between', alignItems: 'center',
                      flexWrap: 'wrap', gap: 16 }}>
          <div>
            <div style={{ fontSize: '13px', color: '#888', marginBottom: '4px' }}>Payment method</div>
            {pm.on_file ? (
              <>
                <div style={{ fontSize: '16px', fontWeight: 700, textTransform: 'capitalize' }}>
                  {pm.brand || 'Card'} •••• {pm.last4}
                </div>
                {pm.exp_month && pm.exp_year && (
                  <div style={{ fontSize: 13, color: '#888', marginTop: 4 }}>
                    Expires {String(pm.exp_month).padStart(2, '0')}/{pm.exp_year}
                  </div>
                )}
              </>
            ) : (
              // NEVER A FABRICATED CARD. Until Stripe has told us what paid,
              // this says so plainly rather than inventing a brand.
              <div style={{ fontSize: 14, color: '#aaa', maxWidth: 460 }}>
                No card details have been recorded yet. Your payment method is held securely
                by Stripe and can be viewed or changed in the billing portal.
              </div>
            )}
          </div>
          {pm.manageable && (
            <button onClick={handlePortal} disabled={actionLoading === 'portal'}
              style={{ background: '#2a2a4a', color: '#fff', border: 'none', borderRadius: '8px',
                       padding: '10px 20px', cursor: 'pointer', fontWeight: '600', fontSize: '14px' }}>
              {actionLoading === 'portal' ? 'Opening…' : 'Manage payment method →'}
            </button>
          )}
        </div>
      )}

      {/* ── The catalogue, or an honest empty state ──────────────────────── */}
      {!configured ? (
        // NOT A SPINNER FOREVER. A brand with no plan catalogue configured is a
        // real, reachable state, and it is the administrator's problem rather
        // than the customer's — so it says so instead of pretending to load.
        <div style={{ ...CARD, marginBottom: '32px' }}>
          <div style={{ fontWeight: 700, fontSize: '16px', marginBottom: '6px' }}>
            No plans are available on this account yet
          </div>
          <div style={{ color: '#aaa', fontSize: '14px', marginBottom: '10px' }}>
            {catalog?.detail
              || 'Your brand has no subscription plans configured, so there is nothing to choose from here yet.'}
          </div>
          <div style={{ color: '#888', fontSize: '13px' }}>
            Contact your platform administrator at{' '}
            <a href={`mailto:${SUPPORT_EMAIL}`} style={{ color: '#2fb6ff' }}>{SUPPORT_EMAIL}</a>.
          </div>
        </div>
      ) : (
        <>
          {hasSubscription && (
            <div style={{ background: 'rgba(47,182,255,0.06)', border: '1px solid rgba(47,182,255,0.2)', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', fontSize: '13px', color: '#6aa8cc', display: 'flex', gap: 10, alignItems: 'center' }}>
              <span>ℹ️</span>
              <span>You already have a subscription, so these are <strong style={{ color: '#2fb6ff' }}>plan changes</strong> — your existing subscription is moved rather than a second one started. Upgrades apply immediately and are charged pro rata; downgrades apply at the end of the period you have already paid for.</span>
            </div>
          )}

          {/* Interval toggle — shown ONLY when the catalogue actually carries an
              annual price. The previous version offered "Annual · Month 13
              free" against a price it computed itself, describing a discount
              no server agreed with. */}
          {annualOffered && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
              <span style={{ fontSize: '14px', color: interval === 'month' ? '#fff' : '#888' }}>Monthly</span>
              <div onClick={() => setInterval(i => (i === 'month' ? 'year' : 'month'))}
                style={{ width: '44px', height: '24px', background: interval === 'year' ? '#2fb6ff' : '#2a2a4a', borderRadius: '12px', cursor: 'pointer' }}>
                <div style={{ position: 'relative', top: '3px', left: interval === 'year' ? '23px' : '3px', width: '18px', height: '18px', background: '#fff', borderRadius: '50%' }} />
              </div>
              <span style={{ fontSize: '14px', color: interval === 'year' ? '#fff' : '#888' }}>Annual</span>
            </div>
          )}

          {/* ── Plan cards ─────────────────────────────────────────────── */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '20px', marginBottom: '40px' }}>
            {plans.map(plan => {
              const isCurrent = plan.key === currentKey;
              const isCurrentExactly = isCurrent && currentInterval === interval;
              // DISPLAY ONLY, AND NEVER DERIVED. The annual figure is whatever
              // the catalogue holds; this page no longer invents one from the
              // monthly price.
              const cents = interval === 'year' ? plan.annual_cents : plan.monthly_cents;
              const priced = cents !== null && cents !== undefined;
              const accent = isCurrent ? '#1ef0a8' : '#2fb6ff';
              const busy = actionLoading === plan.key;

              // ── WHAT THIS BUTTON ACTUALLY DOES, SAID ON THE BUTTON ──────
              //
              // Every card used to read "Select Plan", which is wrong in three
              // separate ways once a subscription exists: it is not a
              // selection, an upgrade and a downgrade behave completely
              // differently, and one of the cards is the plan they are already
              // on. The direction is decided the same way the server decides
              // it — by comparing monthly-equivalent price — so the label
              // matches the operation that will actually happen.
              const currentCents = (() => {
                const cp = planFor(currentKey);
                if (!cp) return null;
                const c = currentInterval === 'year' ? cp.annual_cents : cp.monthly_cents;
                return (c === null || c === undefined)
                  ? null : (currentInterval === 'year' ? c / 12 : c);
              })();
              const thisMonthly = (cents === null || cents === undefined)
                ? null : (interval === 'year' ? cents / 12 : cents);
              const direction = (currentCents === null || thisMonthly === null)
                ? null
                : (thisMonthly > currentCents ? 'upgrade'
                  : thisMonthly < currentCents ? 'downgrade' : 'lateral');

              const isPending = pendingPlan === plan.key;

              let label = hasSubscription ? 'Change plan' : 'Select plan';
              let disabled = busy;
              if (isCurrentExactly) { label = 'Current plan'; disabled = true; }
              else if (isPending) { label = 'Scheduled'; disabled = true; }
              else if (!plan.is_purchasable) { label = 'Contact sales'; }
              else if (!priced) { label = `No ${interval === 'year' ? 'annual' : 'monthly'} price`; disabled = true; }
              else if (hasSubscription && direction === 'upgrade') { label = 'Upgrade'; }
              else if (hasSubscription && direction === 'downgrade') { label = 'Schedule downgrade'; }
              else if (hasSubscription && direction === 'lateral') { label = 'Switch'; }
              if (busy) label = 'Working…';

              return (
                <div key={plan.key} style={{ ...CARD, border: `1px solid ${isPending ? '#f59e0b' : isCurrent ? accent : '#2a2a4a'}`, position: 'relative' }}>
                  {isCurrent && (
                    <div style={{ position: 'absolute', top: '-12px', left: '50%', transform: 'translateX(-50%)', background: accent, color: '#000', fontSize: '11px', fontWeight: '700', padding: '3px 12px', borderRadius: '20px', whiteSpace: 'nowrap' }}>CURRENT PLAN</div>
                  )}
                  {isPending && (
                    <div style={{ position: 'absolute', top: '-12px', left: '50%', transform: 'translateX(-50%)', background: '#f59e0b', color: '#000', fontSize: '11px', fontWeight: '700', padding: '3px 12px', borderRadius: '20px', whiteSpace: 'nowrap' }}>
                      SCHEDULED{periodEnd ? ` · ${periodEnd}` : ''}
                    </div>
                  )}
                  <div style={{ fontSize: '18px', fontWeight: '700', marginBottom: '8px' }}>{plan.name}</div>
                  <div style={{ fontSize: '32px', fontWeight: '800', marginBottom: '4px' }}>
                    {priced
                      ? <>{money(cents, plan.currency)}<span style={{ fontSize: '14px', fontWeight: '400', color: '#888' }}>{interval === 'year' ? '/yr' : '/mo'}</span></>
                      : <span style={{ fontSize: '18px', fontWeight: '600', color: '#888' }}>
                          {plan.is_purchasable ? 'No price set' : 'Custom pricing'}
                        </span>}
                  </div>
                  {plan.description && (
                    <div style={{ fontSize: '13px', color: '#888', marginBottom: '12px' }}>{plan.description}</div>
                  )}
                  <div style={{ marginBottom: '20px', marginTop: '12px' }}>
                    {(plan.features || []).map(f => (
                      <div key={f} style={{ fontSize: '13px', color: '#ccc', marginBottom: '6px' }}>✓ {f}</div>
                    ))}
                  </div>

                  {/* A tier that is listed but not self-serve (Enterprise) gets
                      a contact link, never a checkout button — the server
                      refuses it, and a button that always fails is worse than
                      no button. */}
                  {!plan.is_purchasable ? (
                    <a href={`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(plan.name + ' plan enquiry')}`}
                       style={{ display: 'block', textAlign: 'center', width: '100%', padding: '12px', borderRadius: '8px', background: '#2a2a4a', color: '#fff', fontWeight: '700', fontSize: '14px', textDecoration: 'none', boxSizing: 'border-box' }}>
                      Contact us →
                    </a>
                  ) : (
                    <button
                      onClick={() => (hasSubscription
                        ? handleChangePlan(plan.key, plan.name)
                        : handleCheckout(plan.key))}
                      disabled={disabled}
                      style={{ width: '100%', padding: '12px', borderRadius: '8px', border: 'none', background: disabled ? '#2a2a4a' : accent, color: disabled ? '#888' : '#000', fontWeight: '700', fontSize: '14px', cursor: disabled ? 'not-allowed' : 'pointer' }}>
                      {label}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ── Invoice history ──────────────────────────────────────────────── */}
      <div style={{ ...CARD }}>
        <div style={{ fontWeight: '700', fontSize: '16px', marginBottom: '12px' }}>Invoices</div>
        {invoices.length === 0 ? (
          // No invoice yet is a fact, not a failure — and it is not a $0 row.
          <div style={{ color: '#888', fontSize: '13px' }}>
            No invoices have been issued on this account yet.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ color: '#888', textAlign: 'left' }}>
                  <th style={{ padding: '8px 10px 8px 0' }}>Period</th>
                  <th style={{ padding: '8px 10px' }}>Status</th>
                  <th style={{ padding: '8px 10px', textAlign: 'right' }}>Amount</th>
                  <th style={{ padding: '8px 0 8px 10px', textAlign: 'right' }}>Receipt</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map(inv => (
                  <tr key={inv.id} style={{ borderTop: '1px solid #2a2a4a', color: '#ccc' }}>
                    <td style={{ padding: '10px 10px 10px 0' }}>
                      {when(inv.period_start) || when(inv.paid_at) || '—'}
                    </td>
                    <td style={{ padding: '10px', textTransform: 'capitalize' }}>{inv.status || '—'}</td>
                    <td style={{ padding: '10px', textAlign: 'right' }}>
                      {money(inv.amount_paid_cents ?? inv.amount_due_cents, inv.currency) || '—'}
                    </td>
                    <td style={{ padding: '10px 0 10px 10px', textAlign: 'right' }}>
                      {inv.hosted_invoice_url
                        ? <a href={inv.hosted_invoice_url} target="_blank" rel="noreferrer" style={{ color: '#2fb6ff' }}>View →</a>
                        : <span style={{ color: '#666' }}>—</span>}
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
