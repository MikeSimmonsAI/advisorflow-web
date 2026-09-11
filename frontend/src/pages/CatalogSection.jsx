import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';

// ═══════════════════════════════════════════════════════════════════════════
// YOUR ADD-ONS · AVAILABLE ADD-ONS & SERVICES · ONE-TIME PURCHASES
// ═══════════════════════════════════════════════════════════════════════════
//
// Three sections rather than one list, because a customer asking "what am I
// paying for" and a customer asking "what else can I get" are asking different
// questions, and a one-time purchase is not a thing they are paying for at all
// — it is a thing they paid for once.
//
// RECURRING AND ONE-TIME ARE NEVER SHOWN AS ONE NUMBER. That separation is the
// rule the whole billing system is built on: a $750 migration and $25/month of
// extra seats are different obligations, and a screen that adds them together
// is the same error as a $3,500 combined charge.
//
// NOTHING IS BOUGHT WITHOUT AN EXPLICIT CONFIRMATION that states what changes
// and what it costs. A one-click purchase on a billing screen is a charge
// somebody did not agree to.

const money = (cents, currency) => {
  if (cents === null || cents === undefined) return null;
  const amount = cents / 100;
  const symbol = (currency || 'usd').toLowerCase() === 'usd' ? '$' : '';
  return symbol + amount.toLocaleString(undefined, {
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  });
};

const CARD = {
  background: '#1a1a2e', border: '1px solid #2a2a4a',
  borderRadius: 12, padding: 24, marginBottom: 24,
};

const STATUS_TONE = {
  active: ['#1ef0a8', 'Active'],
  paid: ['#1ef0a8', 'Paid'],
  pending: ['#f59e0b', 'Awaiting payment'],
  canceled: ['#7a7a95', 'Removed'],
};

// The confirmation. Same shape and same discipline as the plan-change dialog:
// every figure comes from the server, success is claimed only after the server
// answers, and a failure keeps the dialog open with the reason.
function ConfirmPurchase({ open, pending, busy, error, onConfirm, onCancel }) {
  if (!open || !pending) return null;
  const { item, quantity } = pending;
  const unit = money(item.amount_cents, item.currency);
  const total = money((item.amount_cents || 0) * quantity, item.currency);
  const recurring = item.kind === 'recurring_addon';

  return (
    <div role="dialog" aria-modal="true" aria-label="Confirm purchase"
      onClick={busy ? undefined : onCancel}
      style={{ position: 'fixed', inset: 0, background: '#000000aa',
               display: 'flex', alignItems: 'center', justifyContent: 'center',
               padding: 16, zIndex: 1000 }}>
      <div onClick={e => e.stopPropagation()}
        style={{ background: '#14142b', border: '1px solid #2a2a4a',
                 borderRadius: 12, padding: 24, width: '100%', maxWidth: 460,
                 maxHeight: '90vh', overflowY: 'auto' }}>
        <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 4px' }}>
          Confirm your purchase
        </h2>
        <p style={{ color: '#888', fontSize: 13, margin: '0 0 18px' }}>
          Nothing is charged until you confirm.
        </p>

        {[['Item', item.name],
          ['Quantity', String(quantity)],
          [recurring ? 'Price each' : 'Price', unit || '—'],
          [recurring ? 'Added to your monthly bill' : 'One-time charge',
           total || '—'],
          // NAMED, not implied. "Recurring" and "one-time" are the two things
          // a customer must not have to infer from a screen about money.
          ['Type', recurring
            ? `Recurring — billed every ${item.billing_interval || 'month'}`
            : 'One-time — charged once, does not start a subscription']]
          .map(([k, v]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between',
                                gap: 16, padding: '10px 0',
                                borderBottom: '1px solid #2a2a4a', fontSize: 14 }}>
            <span style={{ color: '#888' }}>{k}</span>
            <span style={{ fontWeight: 600, textAlign: 'right' }}>{v}</span>
          </div>
        ))}

        <p style={{ color: '#888', fontSize: 13, margin: '16px 0 0',
                    lineHeight: 1.5 }}>
          {recurring
            ? 'This is added to your existing subscription. Your next invoice '
              + 'includes it, prorated for the rest of this period by the '
              + 'payment processor. It does not create a second subscription.'
            : 'You will be taken to a secure payment page. This is a single '
              + 'charge and does not start or change any subscription.'}
        </p>

        {error && (
          <div style={{ background: '#ef444422', border: '1px solid #ef4444',
                        color: '#ef4444', borderRadius: 8, padding: '10px 12px',
                        fontSize: 13, marginTop: 16 }}>{error}</div>
        )}

        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end',
                      marginTop: 22, flexWrap: 'wrap' }}>
          <button type="button" onClick={onCancel} disabled={busy}
            style={{ background: 'transparent', color: '#aaa',
                     border: '1px solid #2a2a4a', borderRadius: 8,
                     padding: '10px 18px', fontSize: 14,
                     cursor: busy ? 'default' : 'pointer' }}>Cancel</button>
          <button type="button" onClick={onConfirm} disabled={busy}
            style={{ background: busy ? '#1ef0a855' : '#1ef0a8',
                     color: '#04120c', border: 'none', borderRadius: 8,
                     padding: '10px 20px', fontSize: 14, fontWeight: 700,
                     cursor: busy ? 'default' : 'pointer' }}>
            {busy ? 'Working…' : (recurring ? 'Add to my plan' : 'Continue to payment')}
          </button>
        </div>
      </div>
    </div>
  );
}


export default function CatalogSection() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [note, setNote] = useState('');
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [removing, setRemoving] = useState(null);

  const load = useCallback(async () => {
    try {
      setData(await api.get('/billing/catalog'));
    } catch {
      // A brand with no catalogue is the common case, not an error worth
      // shouting about on a billing page. The section simply does not render.
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function confirmPurchase() {
    if (!pending) return;
    setError(''); setBusy(true);
    try {
      const r = await api.post('/billing/catalog/purchase', {
        item: pending.item.key, quantity: pending.quantity,
      });
      setBusy(false); setPending(null);
      // A one-time purchase comes back with somewhere to pay. Going there is
      // the honest next step — the purchase is PENDING until the money lands,
      // and saying "purchased" here would be a claim the server has not made.
      if (r.checkout_url) { window.location.href = r.checkout_url; return; }
      setNote(`${r.item_name} added. It appears on your next invoice.`);
      await load();
    } catch (e) {
      setBusy(false);
      setError(e?.detail || e?.message
        || 'The purchase could not be completed. Nothing was charged.');
    }
  }

  async function remove(p) {
    setNote(''); setRemoving(p.id);
    try {
      await api.post(`/billing/catalog/purchase/${p.id}/remove`, {});
      setNote(`${p.item_name} removed. Your subscription is unchanged.`);
      await load();
    } catch (e) {
      setNote(e?.detail || e?.message || 'That could not be removed.');
    } finally {
      setRemoving(null);
    }
  }

  if (loading) return null;
  const mine = data?.mine || [];
  const available = data?.available || [];
  // Nothing held and nothing on offer means this brand does not sell add-ons.
  // Rendering an empty section would be clutter that never resolves.
  if (!mine.length && !available.length) return null;

  const recurringHeld = mine.filter(p => p.is_recurring
                                    && p.status === 'active');
  const oneTimeHeld = mine.filter(p => !p.is_recurring);
  const recurringTotal = recurringHeld.reduce(
    (n, p) => n + (p.total_cents || 0), 0);

  const Row = ({ p }) => {
    const [tone, label] = STATUS_TONE[p.status] || ['#7a7a95', p.status];
    return (
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'center', gap: 16, padding: '12px 0',
                    borderTop: '1px solid #2a2a4a', flexWrap: 'wrap' }}>
        <div style={{ minWidth: 180 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>
            {p.item_name}{p.quantity > 1 ? ` × ${p.quantity}` : ''}
          </div>
          <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>
            <span style={{ color: tone }}>{label}</span>
            {p.paid_at ? ` · ${new Date(p.paid_at).toLocaleDateString()}` : ''}
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 14, whiteSpace: 'nowrap' }}>
            {money(p.total_cents, p.currency)}
            {p.is_recurring && (
              <span style={{ fontSize: 12, fontWeight: 400, color: '#888' }}>
                /{p.billing_interval === 'year' ? 'yr' : 'mo'}
              </span>
            )}
          </div>
          {/* Only an ACTIVE recurring add-on can be removed, and the server
              says which — the screen does not decide. */}
          {p.removable && (
            <button type="button" onClick={() => remove(p)}
              disabled={removing === p.id}
              style={{ background: 'transparent', color: '#888',
                       border: '1px solid #2a2a4a', borderRadius: 6,
                       padding: '4px 12px', fontSize: 12,
                       cursor: removing === p.id ? 'default' : 'pointer' }}>
              {removing === p.id ? 'Removing…' : 'Remove'}
            </button>
          )}
          {/* A pending one-time purchase still has somewhere to pay. */}
          {p.status === 'pending' && p.checkout_url && (
            <a href={p.checkout_url}
              style={{ color: '#2fb6ff', fontSize: 12, fontWeight: 600,
                       textDecoration: 'none' }}>Pay now →</a>
          )}
        </div>
      </div>
    );
  };

  return (
    <>
      <ConfirmPurchase open={Boolean(pending)} pending={pending} busy={busy}
        error={error} onConfirm={confirmPurchase}
        onCancel={() => { if (!busy) { setPending(null); setError(''); } }} />

      {note && (
        <div style={{ background: '#2fb6ff20', border: '1px solid #2fb6ff',
                      borderRadius: 8, padding: '12px 16px', marginBottom: 24,
                      color: '#2fb6ff', fontSize: 14 }}>{note}</div>
      )}

      {recurringHeld.length > 0 && (
        <div style={CARD}>
          <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'baseline', gap: 16, flexWrap: 'wrap' }}>
            <h2 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
              Your add-ons
            </h2>
            {/* The add-on total ONLY. Never combined with the plan — they are
                separate obligations and a single blended figure is how a
                customer loses track of what they are actually paying for. */}
            <div style={{ fontSize: 13, color: '#888' }}>
              Add-ons total{' '}
              <strong style={{ color: '#e8e8f0' }}>
                {money(recurringTotal, 'usd')}/mo
              </strong>{' '}
              on top of your plan
            </div>
          </div>
          {recurringHeld.map(p => <Row key={p.id} p={p} />)}
        </div>
      )}

      {oneTimeHeld.length > 0 && (
        <div style={CARD}>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 4px' }}>
            One-time purchases
          </h2>
          <p style={{ color: '#888', fontSize: 13, margin: '0 0 4px' }}>
            Charged once. These do not affect your subscription.
          </p>
          {oneTimeHeld.map(p => <Row key={p.id} p={p} />)}
        </div>
      )}

      {available.length > 0 && (
        <div style={CARD}>
          <h2 style={{ fontSize: 18, fontWeight: 700, margin: '0 0 4px' }}>
            Available add-ons &amp; services
          </h2>
          <p style={{ color: '#888', fontSize: 13, margin: '0 0 8px' }}>
            Recurring add-ons join your existing subscription. One-time
            services are charged once.
          </p>
          {available.map(item => {
            const recurring = item.kind === 'recurring_addon';
            // An add-on has nothing to attach to without a subscription, and
            // the server says so rather than the screen guessing.
            const blocked = recurring && data?.can_buy_addons === false;
            return (
              <div key={item.key}
                style={{ display: 'flex', justifyContent: 'space-between',
                         alignItems: 'center', gap: 16, padding: '14px 0',
                         borderTop: '1px solid #2a2a4a', flexWrap: 'wrap' }}>
                <div style={{ minWidth: 200, flex: '1 1 260px' }}>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{item.name}</div>
                  {item.description && (
                    <div style={{ fontSize: 13, color: '#888', marginTop: 3 }}>
                      {item.description}
                    </div>
                  )}
                  <div style={{ fontSize: 12, color: '#7a7a95', marginTop: 3 }}>
                    {recurring
                      ? `Recurring · billed every ${item.billing_interval || 'month'}`
                      : 'One-time charge'}
                    {item.category ? ` · ${item.category}` : ''}
                  </div>
                  {blocked && (
                    <div style={{ fontSize: 12, color: '#f59e0b', marginTop: 4 }}>
                      Needs an active subscription to attach to.
                    </div>
                  )}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                  <div style={{ fontWeight: 700, fontSize: 15, whiteSpace: 'nowrap' }}>
                    {money(item.amount_cents, item.currency)}
                    {recurring && (
                      <span style={{ fontSize: 12, fontWeight: 400, color: '#888' }}>
                        /{item.billing_interval === 'year' ? 'yr' : 'mo'}
                      </span>
                    )}
                  </div>
                  <button type="button" disabled={blocked}
                    onClick={() => { setError(''); setPending({ item, quantity: 1 }); }}
                    style={{ background: blocked ? '#2a2a4a' : '#2fb6ff',
                             color: blocked ? '#666' : '#04121c', border: 'none',
                             borderRadius: 8, padding: '9px 18px', fontSize: 13,
                             fontWeight: 700,
                             cursor: blocked ? 'default' : 'pointer' }}>
                    {recurring ? 'Add' : 'Buy'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
}
