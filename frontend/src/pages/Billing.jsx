import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../api/client';

const PLANS = [
  {
    key: 'starter',
    name: 'Starter',
    price: 497,
    onboarding: 1500,
    features: ['AI email cadence (8 emails / 14 days)', 'Up to 2 users', 'Up to 2,500 leads'],
    color: '#2fb6ff',
  },
  {
    key: 'growth',
    name: 'Growth',
    price: 997,
    onboarding: 2500,
    features: ['AI email + SMS 1,000/mo', 'AI voice 300 min/mo', 'Up to 3 users', 'Up to 5,000 leads'],
    color: '#1ef0a8',
    popular: true,
  },
  {
    key: 'professional',
    name: 'Professional',
    price: 1997,
    onboarding: 5000,
    features: ['AI email + SMS 3,000/mo', 'AI voice 750 min/mo', 'Up to 5 users / 3 locations', 'Priority support + 24-mo price lock'],
    color: '#f59e0b',
  },
];

const STATUS_COLORS = {
  active: '#1ef0a8',
  trialing: '#2fb6ff',
  past_due: '#f59e0b',
  canceled: '#ef4444',
};

export default function Billing() {
  const [sub, setSub] = useState(null);
  const [interval, setInterval] = useState('month');
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(null);
  const [err, setErr] = useState('');
  const [searchParams] = useSearchParams();

  const success = searchParams.get('success') === '1';
  const canceled = searchParams.get('canceled') === '1';

  useEffect(() => {
    api.get('/billing/subscription')
      .then(r => setSub(r))
      .catch(() => setSub(null))
      .finally(() => setLoading(false));
  }, []);

  async function handleCheckout(planKey) {
    setErr('');
    setActionLoading(planKey);
    try {
      const result = await api.post('/billing/checkout', { plan: planKey, interval });
      window.location.href = result.checkout_url;
    } catch (e) {
      setErr(e?.message || 'Plan selection failed. Contact your platform administrator to activate this plan.');
      setActionLoading(null);
    }
  }

  async function handlePortal() {
    setErr('');
    setActionLoading('portal');
    try {
      const result = await api.post('/billing/portal');
      window.location.href = result.portal_url;
    } catch (e) {
      setErr(e?.message || 'Could not open billing portal. Contact your platform administrator.');
      setActionLoading(null);
    }
  }

  const currentPlan = sub?.plan || 'trial';
  const billingStatus = sub?.billing_status || 'trialing';
  const periodEnd = sub?.current_period_end
    ? new Date(sub.current_period_end * 1000).toLocaleDateString()
    : null;

  if (loading) return (
    <div style={{ padding: '40px', color: '#aaa', textAlign: 'center' }}>Loading billing info…</div>
  );

  return (
    <div style={{ padding: '32px', maxWidth: '960px', margin: '0 auto' }}>
      <h1 style={{ fontSize: '24px', fontWeight: '700', marginBottom: '8px' }}>Billing & Plan</h1>
      <p style={{ color: '#aaa', marginBottom: '32px' }}>Manage your subscription and upgrade your plan.</p>

      {success && (
        <div style={{ background: '#1ef0a820', border: '1px solid #1ef0a8', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', color: '#1ef0a8', fontWeight: '600' }}>
          ✅ Subscription activated! Your plan is now live.
        </div>
      )}
      {canceled && (
        <div style={{ background: '#f59e0b20', border: '1px solid #f59e0b', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', color: '#f59e0b' }}>
          Checkout canceled. No changes were made.
        </div>
      )}
      {err && (
        <div style={{ background: '#ef444420', border: '1px solid #ef4444', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', color: '#ef4444', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <span>⚠️</span>
          <div>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Action required</div>
            <div style={{ fontSize: 14 }}>{err}</div>
            <div style={{ fontSize: 13, marginTop: 8, color: '#aaa' }}>
              To activate or change your plan, contact your platform administrator at{' '}
              <a href="mailto:support@bookaboost.live" style={{ color: '#ef4444' }}>support@bookaboost.live</a>.
            </div>
          </div>
        </div>
      )}

      {/* Current plan summary */}
      <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: '12px', padding: '24px', marginBottom: '32px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <div style={{ fontSize: '13px', color: '#888', marginBottom: '4px' }}>Current plan</div>
          <div style={{ fontSize: '22px', fontWeight: '700', textTransform: 'capitalize' }}>{currentPlan}</div>
          {periodEnd && (
            <div style={{ fontSize: '13px', color: '#888', marginTop: '4px' }}>
              {sub?.cancel_at_period_end ? 'Cancels' : 'Renews'} {periodEnd}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
          <span style={{ background: `${STATUS_COLORS[billingStatus]}22`, color: STATUS_COLORS[billingStatus], border: `1px solid ${STATUS_COLORS[billingStatus]}55`, borderRadius: '20px', padding: '4px 14px', fontSize: '13px', fontWeight: '600', textTransform: 'capitalize' }}>
            {billingStatus}
          </span>
          {sub?.stripe_customer_id && (
            <button onClick={handlePortal} disabled={actionLoading === 'portal'} style={{ background: '#2a2a4a', color: '#fff', border: 'none', borderRadius: '8px', padding: '10px 20px', cursor: 'pointer', fontWeight: '600', fontSize: '14px' }}>
              {actionLoading === 'portal' ? 'Opening…' : 'Manage Billing →'}
            </button>
          )}
        </div>
      </div>

      {/* Admin contact notice */}
      <div style={{ background: 'rgba(47,182,255,0.06)', border: '1px solid rgba(47,182,255,0.2)', borderRadius: '8px', padding: '14px 18px', marginBottom: '24px', fontSize: '13px', color: '#6aa8cc', display: 'flex', gap: 10, alignItems: 'center' }}>
        <span>ℹ️</span>
        <span>Plan changes are processed by your platform administrator. Click <strong style={{ color: '#2fb6ff' }}>Select Plan</strong> below to request a plan, or email <a href="mailto:support@bookaboost.live" style={{ color: '#2fb6ff' }}>support@bookaboost.live</a>.</span>
      </div>

      {/* Billing interval toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
        <span style={{ fontSize: '14px', color: interval === 'month' ? '#fff' : '#888' }}>Monthly</span>
        <div onClick={() => setInterval(i => i === 'month' ? 'year' : 'month')}
          style={{ width: '44px', height: '24px', background: interval === 'year' ? '#2fb6ff' : '#2a2a4a', borderRadius: '12px', cursor: 'pointer', position: 'relative', transition: 'background 0.2s' }}>
          <div style={{ position: 'absolute', top: '3px', left: interval === 'year' ? '23px' : '3px', width: '18px', height: '18px', background: '#fff', borderRadius: '50%', transition: 'left 0.2s' }} />
        </div>
        <span style={{ fontSize: '14px', color: interval === 'year' ? '#fff' : '#888' }}>Annual <span style={{ color: '#1ef0a8', fontSize: '12px' }}>Month 13 free</span></span>
      </div>

      {/* Plan cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '20px', marginBottom: '40px' }}>
        {PLANS.map(plan => {
          const isCurrent = currentPlan === plan.key;
          const price = interval === 'year' ? Math.round(plan.price * 11 / 12) : plan.price;
          return (
            <div key={plan.key} style={{ background: '#1a1a2e', border: `1px solid ${isCurrent ? plan.color : '#2a2a4a'}`, borderRadius: '12px', padding: '24px', position: 'relative', boxShadow: isCurrent ? `0 0 0 2px ${plan.color}` : 'none' }}>
              {plan.popular && !isCurrent && (
                <div style={{ position: 'absolute', top: '-12px', left: '50%', transform: 'translateX(-50%)', background: '#1ef0a8', color: '#000', fontSize: '11px', fontWeight: '700', padding: '3px 12px', borderRadius: '20px' }}>MOST POPULAR</div>
              )}
              {isCurrent && (
                <div style={{ position: 'absolute', top: '-12px', left: '50%', transform: 'translateX(-50%)', background: plan.color, color: '#000', fontSize: '11px', fontWeight: '700', padding: '3px 12px', borderRadius: '20px' }}>CURRENT PLAN</div>
              )}
              <div style={{ fontSize: '18px', fontWeight: '700', marginBottom: '8px' }}>{plan.name}</div>
              <div style={{ fontSize: '32px', fontWeight: '800', marginBottom: '4px' }}>${price.toLocaleString()}<span style={{ fontSize: '14px', fontWeight: '400', color: '#888' }}>/mo</span></div>
              {interval === 'year' && <div style={{ fontSize: '12px', color: '#888', marginBottom: '16px' }}>Billed ${(plan.price * 11).toLocaleString()}/yr · save ${plan.price.toLocaleString()}</div>}
              <div style={{ marginBottom: '20px' }}>
                {plan.features.map(f => <div key={f} style={{ fontSize: '13px', color: '#ccc', marginBottom: '6px' }}>✓ {f}</div>)}
              </div>
              <button onClick={() => handleCheckout(plan.key)} disabled={isCurrent || actionLoading === plan.key}
                style={{ width: '100%', padding: '12px', borderRadius: '8px', border: 'none', background: isCurrent ? '#2a2a4a' : plan.color, color: isCurrent ? '#888' : '#000', fontWeight: '700', fontSize: '14px', cursor: isCurrent ? 'not-allowed' : 'pointer' }}>
                {actionLoading === plan.key ? 'Processing…' : isCurrent ? 'Current Plan' : 'Select Plan'}
              </button>
            </div>
          );
        })}
      </div>

      {/* Enterprise CTA */}
      <div style={{ background: '#1a1a2e', border: '1px solid #2a2a4a', borderRadius: '12px', padding: '24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
        <div>
          <div style={{ fontWeight: '700', fontSize: '16px', marginBottom: '4px' }}>Enterprise</div>
          <div style={{ color: '#888', fontSize: '14px' }}>Unlimited leads, users, and locations. White-label available. Custom pricing.</div>
        </div>
        <a href="mailto:support@bookaboost.live?subject=Enterprise Plan Inquiry" style={{ background: '#2a2a4a', color: '#fff', padding: '12px 24px', borderRadius: '8px', textDecoration: 'none', fontWeight: '600', fontSize: '14px' }}>Contact Us →</a>
      </div>
    </div>
  );
}
