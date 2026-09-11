import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';

// ═══════════════════════════════════════════════════════════════════════════
// THE BRAND CATALOGUE — everything a brand sells that is not the subscription
// ═══════════════════════════════════════════════════════════════════════════
//
// Recurring add-ons, one-time products and services, and quoted work. God Mode
// configures it here; the seller and customer surfaces consume it.
//
// THIS SCREEN'S JOB IS TO MAKE "NOT SELLABLE" VISIBLE. A row that looks
// finished — it has a name, a kind, a price — can still be unbuyable because
// nobody enabled an audience for it, or because its Stripe Price was never
// created. That is invisible from the columns alone, so the server computes it
// and this screen shows it as a blocker rather than leaving an operator to
// infer it from a customer complaint.
//
// NO PRICES ARE SUGGESTED ANYWHERE IN THIS FILE. A price that appears without
// somebody typing it is a price nobody agreed to.

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

const INPUT = {
  background: '#14142b', color: '#e8e8f0', border: '1px solid #2a2a4a',
  borderRadius: 6, padding: '6px 10px', fontSize: 13, minWidth: 0,
};

export default function BrandCatalogue({ platformId }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(null);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(null);
  const [provision, setProvision] = useState(null);

  const load = useCallback(async () => {
    if (!platformId) { setData(null); return; }
    setLoading(true); setErr('');
    try {
      setData(await api.get(
        `/god/catalog/brands/${encodeURIComponent(platformId)}/items`));
    } catch (e) {
      setErr(e?.detail || e?.message || 'The catalogue could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [platformId]);

  useEffect(() => { load(); }, [load]);

  // One field on one row. The server re-validates the WHOLE row, so a change
  // that would leave the item incoherent is refused there rather than here —
  // this screen reports the refusal instead of trying to predict it.
  async function patch(item, changes) {
    setNote(''); setBusy(item.id);
    try {
      await api.patch(
        `/god/catalog/brands/${encodeURIComponent(platformId)}/items/${item.id}`,
        changes);
      await load();
    } catch (e) {
      setNote(`${item.key}: ${e?.detail || e?.message || 'the change was refused.'}`);
    } finally {
      setBusy(null);
    }
  }

  async function createItem() {
    if (!draft?.key || !draft?.name) return;
    setNote(''); setBusy('new');
    try {
      await api.post(
        `/god/catalog/brands/${encodeURIComponent(platformId)}/items`, {
          key: draft.key.trim(),
          name: draft.name.trim(),
          kind: draft.kind,
          pricing_mode: draft.pricing_mode,
          // Blank stays NULL. An empty box is "not decided", which is not the
          // same as zero and must not become it.
          amount_cents: draft.amount === '' || draft.amount === undefined
            ? null : Math.round(Number(draft.amount) * 100),
          billing_interval: draft.kind === 'recurring_addon'
            ? (draft.billing_interval || 'month') : null,
          category: draft.category || null,
          customer_description: draft.customer_description || null,
        });
      setAdding(false); setDraft(null);
      await load();
    } catch (e) {
      setNote(e?.detail || e?.message || 'The item could not be created.');
    } finally {
      setBusy(null);
    }
  }

  // Preview first, like every other Stripe-touching action in God Mode.
  async function runProvision(apply) {
    setNote(''); setBusy('provision');
    try {
      const r = await api.post(
        `/god/catalog/brands/${encodeURIComponent(platformId)}/stripe/provision`,
        { apply });
      setProvision(r);
      if (apply) { setProvision(null); setNote('Stripe catalogue synced.'); await load(); }
    } catch (e) {
      setNote(e?.detail || e?.message || 'Provisioning was refused.');
    } finally {
      setBusy(null);
    }
  }

  if (!platformId) return null;

  const items = data?.items || [];
  const counts = data?.counts || {};

  return (
    <div style={CARD}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>
            PRODUCTS &amp; SERVICES
          </h2>
          <p style={{ color: '#888', margin: '6px 0 0', fontSize: 13,
                      maxWidth: 640 }}>
            Recurring add-ons and one-time services this brand sells alongside
            the subscription. Nothing here is sellable until it is priced and
            enabled for an audience.
          </p>
        </div>
        <button type="button" onClick={() => { setAdding(a => !a); setDraft({
          key: '', name: '', kind: 'recurring_addon', pricing_mode: 'fixed',
          amount: '', billing_interval: 'month', category: '',
          customer_description: '' }); }}
          style={{ background: '#2fb6ff', color: '#04121c', border: 'none',
                   borderRadius: 6, padding: '8px 16px', fontSize: 13,
                   fontWeight: 700, cursor: 'pointer' }}>
          {adding ? 'Close' : '+ Add item'}
        </button>
      </div>

      {(err || note) && (
        <div style={{ marginTop: 14, padding: '10px 14px', borderRadius: 8,
                      fontSize: 13, background: '#ef444418',
                      border: '1px solid #ef444455', color: '#ef8a8a' }}>
          {err || note}
        </div>
      )}

      {adding && draft && (
        <div style={{ marginTop: 16, padding: 16, background: '#2fb6ff0e',
                      border: '1px solid #2a2a4a', borderRadius: 8 }}>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <input style={{ ...INPUT, flex: '1 1 150px' }} placeholder="key (extra_users)"
              value={draft.key}
              onChange={e => setDraft(d => ({ ...d, key: e.target.value }))} />
            <input style={{ ...INPUT, flex: '1 1 200px' }} placeholder="Name the customer reads"
              value={draft.name}
              onChange={e => setDraft(d => ({ ...d, name: e.target.value }))} />
            <select style={INPUT} value={draft.kind}
              onChange={e => setDraft(d => ({ ...d, kind: e.target.value }))}>
              {(data?.kinds || []).map(k => (
                <option key={k.key} value={k.key}>{k.label}</option>
              ))}
            </select>
            <select style={INPUT} value={draft.pricing_mode}
              onChange={e => setDraft(d => ({
                ...d, pricing_mode: e.target.value,
                // A quoted item must carry no catalogue price — clearing it
                // here means the server never has to refuse the save.
                amount: e.target.value === 'quoted' ? '' : d.amount }))}>
              {(data?.pricing_modes || []).map(m => (
                <option key={m.key} value={m.key}>{m.label}</option>
              ))}
            </select>
            {draft.pricing_mode !== 'quoted' && (
              <input style={{ ...INPUT, width: 120 }} type="number" min="0" step="0.01"
                placeholder="Price (blank = unset)"
                value={draft.amount}
                onChange={e => setDraft(d => ({ ...d, amount: e.target.value }))} />
            )}
            <input style={{ ...INPUT, width: 140 }} placeholder="Category"
              value={draft.category}
              onChange={e => setDraft(d => ({ ...d, category: e.target.value }))} />
          </div>
          <div style={{ color: '#888', fontSize: 12, margin: '10px 0' }}>
            Created enabled for nobody. Price it and switch on an audience when
            you are ready — leaving the price blank is fine and says plainly
            that it cannot be sold yet.
          </div>
          <button type="button" onClick={createItem}
            disabled={!draft.key || !draft.name || busy === 'new'}
            style={{ background: draft.key && draft.name ? '#1ef0a8' : '#2a2a4a',
                     color: draft.key && draft.name ? '#04120c' : '#666',
                     border: 'none', borderRadius: 6, padding: '7px 16px',
                     fontSize: 13, fontWeight: 700,
                     cursor: draft.key && draft.name ? 'pointer' : 'default' }}>
            {busy === 'new' ? 'Adding…' : 'Add to catalogue'}
          </button>
        </div>
      )}

      {loading ? (
        <div style={{ padding: 30, textAlign: 'center', color: '#888' }}>Loading…</div>
      ) : items.length === 0 ? (
        <div style={{ padding: 30, textAlign: 'center', color: '#888', fontSize: 14 }}>
          This brand has no catalogue items yet.
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap',
                        margin: '18px 0 10px', fontSize: 12, color: '#888' }}>
            <span>{counts.total} item{counts.total === 1 ? '' : 's'}</span>
            <span style={{ color: '#1ef0a8' }}>{counts.sellable} sellable</span>
            {counts.blocked > 0 && (
              <span style={{ color: '#f59e0b' }}>{counts.blocked} blocked</span>
            )}
            {counts.needs_stripe_mapping > 0 && (
              <span style={{ color: '#f59e0b' }}>
                {counts.needs_stripe_mapping} need a Stripe price
              </span>
            )}
          </div>

          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: '#888', textAlign: 'left', fontSize: 12 }}>
                  <th style={{ padding: '8px 12px 8px 0' }}>Item</th>
                  <th style={{ padding: '8px 12px' }}>Type</th>
                  <th style={{ padding: '8px 12px', textAlign: 'right' }}>Price</th>
                  <th style={{ padding: '8px 12px' }}>Stripe</th>
                  <th style={{ padding: '8px 12px' }}>Customer</th>
                  <th style={{ padding: '8px 12px' }}>Seller</th>
                  <th style={{ padding: '8px 12px' }}>Active</th>
                </tr>
              </thead>
              <tbody>
                {items.map(it => (
                  <tr key={it.id} style={{ borderTop: '1px solid #2a2a4a',
                                           opacity: it.is_active ? 1 : 0.55 }}>
                    <td style={{ padding: '10px 12px 10px 0' }}>
                      <div style={{ fontWeight: 600 }}>{it.name}</div>
                      <div style={{ fontSize: 11, color: '#7a7a95' }}>
                        {it.key}{it.category ? ` · ${it.category}` : ''}
                      </div>
                      {/* WHY THIS ROW CANNOT BE SOLD, in the row itself. */}
                      {it.blockers?.length > 0 && (
                        <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 3,
                                      maxWidth: 420 }}>
                          {it.blockers.join(' ')}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '10px 12px', whiteSpace: 'nowrap' }}>
                      {it.kind_label}
                      {it.billing_interval && (
                        <div style={{ fontSize: 11, color: '#7a7a95' }}>
                          per {it.billing_interval}
                        </div>
                      )}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'right',
                                 whiteSpace: 'nowrap' }}>
                      {it.is_quoted || it.pricing_mode === 'quoted'
                        ? <span style={{ color: '#7a7a95' }}>Quoted</span>
                        : money(it.amount_cents, it.currency)
                          ?? <span style={{ color: '#f59e0b' }}>Not priced</span>}
                    </td>
                    <td style={{ padding: '10px 12px', fontSize: 12 }}>
                      {it.stripe_mapped
                        ? <span style={{ color: '#1ef0a8' }}>Mapped</span>
                        : it.needs_stripe_mapping
                          ? <span style={{ color: '#f59e0b' }}>Needed</span>
                          : <span style={{ color: '#555' }}>—</span>}
                    </td>
                    {[['self_service', it.self_service],
                      ['seller_assisted', it.seller_assisted],
                      ['is_active', it.is_active]].map(([field, value]) => (
                      <td key={field} style={{ padding: '10px 12px' }}>
                        <input type="checkbox" checked={Boolean(value)}
                          disabled={busy === it.id}
                          onChange={e => patch(it, { [field]: e.target.checked })} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── Stripe provisioning, preview first ──────────────────────────── */}
      <div style={{ marginTop: 20, paddingTop: 16, borderTop: '1px solid #2a2a4a' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                      flexWrap: 'wrap' }}>
          <button type="button" onClick={() => runProvision(false)}
            disabled={busy === 'provision'}
            style={{ background: 'transparent', color: '#2fb6ff',
                     border: '1px solid #2fb6ff55', borderRadius: 6,
                     padding: '7px 14px', fontSize: 13, cursor: 'pointer' }}>
            {busy === 'provision' ? 'Checking…' : 'Preview Stripe sync'}
          </button>
          <span style={{ color: '#888', fontSize: 12 }}>
            Creates only what is missing. An already-mapped price is reused,
            never duplicated. Quoted and unpriced items are skipped.
          </span>
        </div>

        {provision && (
          <div style={{ marginTop: 14, padding: 14, background: '#f59e0b0e',
                        border: '1px solid #2a2a4a', borderRadius: 8 }}>
            <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>
              Would create {provision.summary?.products_created} product(s) and{' '}
              {provision.summary?.prices_created} price(s) · reuse{' '}
              {provision.summary?.reused} · skip {provision.summary?.skipped}
            </div>
            <table style={{ fontSize: 12, borderCollapse: 'collapse',
                            marginBottom: 12 }}>
              <tbody>
                {(provision.items || []).map(r => (
                  <tr key={r.key}>
                    <td style={{ padding: '3px 14px 3px 0', color: '#888' }}>
                      {r.key}
                    </td>
                    <td style={{ padding: '3px 0' }}>
                      {r.skipped
                        ? <span style={{ color: '#7a7a95' }}>skipped — {r.reason}</span>
                        : <span style={{ color: '#1ef0a8' }}>
                            {r.price?.action?.replace(/_/g, ' ')}
                          </span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ display: 'flex', gap: 8 }}>
              <button type="button" onClick={() => runProvision(true)}
                disabled={busy === 'provision'}
                style={{ background: '#1ef0a8', color: '#04120c', border: 'none',
                         borderRadius: 6, padding: '6px 14px', fontSize: 12,
                         fontWeight: 700, cursor: 'pointer' }}>
                {busy === 'provision' ? 'Syncing…' : 'Apply sync'}
              </button>
              <button type="button" onClick={() => setProvision(null)}
                style={{ background: 'transparent', color: '#aaa',
                         border: '1px solid #2a2a4a', borderRadius: 6,
                         padding: '6px 14px', fontSize: 12, cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
