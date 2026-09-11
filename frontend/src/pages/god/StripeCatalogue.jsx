import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client';

// ═══════════════════════════════════════════════════════════════════════════
// GOD MODE — STRIPE & CATALOGUE ADMINISTRATION
// ═══════════════════════════════════════════════════════════════════════════
//
// THE PROBLEM THIS SOLVES. The Stripe mapping work was real, deployed and
// completely invisible: the only way to know whether a brand's catalogue was
// actually connected to Stripe was to call a diagnostic endpoint by hand. An
// operator looking at Billing & Revenue saw customers and money and had no way
// to answer "is checkout going to work, and which price will it charge".
//
// SEPARATE FROM THE CUSTOMER ROSTER, DELIBERATELY. This is configuration —
// what the brand SELLS and whether Stripe knows about it. The table below it is
// operations — who is paying and who needs chasing. Mixing them would put a
// setup task in the middle of a work queue.
//
// SECRETS NEVER REACH THIS COMPONENT. The diagnostic reports presence, length
// and mode for each environment variable and never its value; the only Stripe
// strings rendered here are `prod_…` and `price_…`, which are public
// identifiers that appear on the customer's own receipt.
//
// NOTHING HERE IS A PRICE OF ITS OWN. Every amount displayed comes from the
// brand's `BrandBillingPlan` rows via the diagnostic. A figure typed into this
// file would be a second opinion about what a brand charges — the same mistake
// that put a hard-coded PLANS dict in the old billing router and a second copy
// of it in the React bundle, which had already drifted apart.

const CARD = {
  background: 'var(--gm-pill-blue-bg)', border: '1px solid var(--gm-blue)',
  borderRadius: '12px', padding: '20px',
};

const COMMITMENTS = [
  { key: 'term', label: 'Committed term', centsKey: 'term_cents',
    idKey: 'stripe_price_id_monthly', mappedKey: 'term_price_mapped' },
  { key: 'month_to_month', label: 'Month-to-month', centsKey: 'month_to_month_cents',
    idKey: 'stripe_price_id_month_to_month', mappedKey: 'month_to_month_price_mapped' },
];

function money(cents) {
  if (cents === null || cents === undefined) return null;
  const amount = cents / 100;
  return '$' + amount.toLocaleString(undefined, {
    minimumFractionDigits: amount % 1 === 0 ? 0 : 2, maximumFractionDigits: 2,
  });
}

function Dot({ ok, warn }) {
  const color = ok ? 'var(--gm-teal)' : warn ? 'var(--gm-amber)' : 'var(--gm-red)';
  return <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: 4,
                        background: color, marginRight: 8, flexShrink: 0 }} />;
}

/** One environment variable, as presence and shape. Never as a value. */
function EnvRow({ name, entry }) {
  const present = !!entry?.present && !entry?.empty;
  const ws = !!entry?.has_surrounding_whitespace;
  const mode = entry?.mode;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13,
                  padding: '5px 0' }}>
      <Dot ok={present && !ws} warn={present && ws} />
      <code style={{ color: 'var(--gm-blue)', fontSize: 12 }}>{name}</code>
      <span style={{ marginLeft: 'auto', color: present ? 'var(--gm-text)' : 'var(--gm-red)' }}>
        {!present ? 'not set'
          : ws ? 'set — has stray whitespace'
            : mode ? `set · ${mode.toUpperCase()} mode` : 'set'}
      </span>
    </div>
  );
}

function Pill({ tone, children }) {
  const colors = {
    good: ['var(--gm-teal-wash)', 'var(--gm-teal)'], warn: ['var(--gm-amber-wash)', 'var(--gm-amber)'],
    bad: ['var(--gm-red-wash)', 'var(--gm-red)'], mute: ['var(--gm-blue)', 'var(--gm-text)'],
  }[tone] || ['var(--gm-pill-off-bg)', 'var(--gm-pill-off-fg)'];
  return (
    <span style={{ background: colors[0], color: colors[1], borderRadius: 999,
                   padding: '2px 10px', fontSize: 11, fontWeight: 700,
                   letterSpacing: '0.03em', whiteSpace: 'nowrap' }}>
      {children}
    </span>
  );
}

export default function StripeCatalogue({ platformId, brands }) {
  const [diag, setDiag] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');

  // The diagnostic is platform-wide (it reports every brand); the SELECTED
  // brand is picked out of it here so this panel follows the page's scope
  // selector rather than owning a second one.
  const load = useCallback(async () => {
    setErr('');
    try {
      setDiag(await api.get('/god/billing/stripe-diagnostics'));
    } catch (e) {
      setErr(e?.message || 'The Stripe diagnostic could not be read.');
      setDiag(null);
    }
  }, []);

  useEffect(() => { setLoading(true); load().finally(() => setLoading(false)); }, [load]);

  const env = diag?.env || {};
  const credentialsReady = !!diag?.credentials_ready;
  const testMode = env?.STRIPE_SECRET_KEY?.mode === 'test';
  const webhookReady = !!env?.STRIPE_WEBHOOK_SECRET?.present
    && !env?.STRIPE_WEBHOOK_SECRET?.empty;

  const allBrands = diag?.brands || [];
  // With no brand selected the panel shows the first brand that actually has a
  // catalogue, rather than nothing — an empty panel reads as "Stripe is broken"
  // when the truth is "no brand is selected".
  const brand = platformId
    ? allBrands.find(b => b.platform_id === platformId)
    : allBrands.find(b => (b.plans || []).length) || allBrands[0];

  const brandName = brand?.name
    || brands?.find(b => (b.platform_id || b.id) === platformId)?.name
    || '—';

  const provision = useCallback(async (apply) => {
    if (!brand?.platform_id) return;
    setBusy(true); setErr(''); setResult(null);
    try {
      const r = await api.post(
        `/god/billing/brands/${encodeURIComponent(brand.platform_id)}/stripe/provision`,
        { apply });
      setResult(r);
      if (apply) await load();
    } catch (e) {
      // The server refuses a live key with a 409 and an explanation. Showing it
      // verbatim is correct: it names the problem and contains no credential.
      setErr(e?.detail || e?.message || 'Provisioning failed.');
    } finally {
      setBusy(false);
    }
  }, [brand, load]);

  if (loading) {
    return <div style={{ ...CARD, color: 'var(--gm-dim)', fontSize: 14 }}>Reading Stripe configuration…</div>;
  }

  const plans = (brand?.plans || []).filter(p => p.is_purchasable);
  const unmapped = plans.reduce((n, p) => n
    + COMMITMENTS.filter(c => p[c.centsKey] && !p[c.mappedKey]).length, 0);

  return (
    <div style={{ ...CARD, margin: '24px 0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start',
                    gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ fontSize: 16, fontWeight: 800, margin: 0, letterSpacing: '0.02em' }}>
            STRIPE &amp; CATALOGUE
          </h2>
          <p style={{ color: 'var(--gm-dim)', margin: '6px 0 0', fontSize: 13 }}>
            What this brand sells, and whether Stripe knows about it. Configuration —
            not the customer roster below.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Pill tone={credentialsReady ? 'good' : 'bad'}>
            {credentialsReady ? 'Connected' : 'Not connected'}
          </Pill>
          <Pill tone={testMode ? 'good' : credentialsReady ? 'bad' : 'mute'}>
            {testMode ? 'TEST MODE' : env?.STRIPE_SECRET_KEY?.mode
              ? String(env.STRIPE_SECRET_KEY.mode).toUpperCase() + ' MODE' : 'MODE UNKNOWN'}
          </Pill>
          <Pill tone={webhookReady ? 'good' : 'bad'}>
            {webhookReady ? 'Webhook configured' : 'Webhook missing'}
          </Pill>
        </div>
      </div>

      {err && (
        <div style={{ background: 'var(--gm-pill-red-bg)', border: '1px solid var(--gm-red)', borderRadius: 8,
                      padding: '12px 16px', margin: '16px 0', color: 'var(--gm-red)', fontSize: 13 }}>
          {err}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                    gap: 20, marginTop: 20 }}>
        {/* ── environment ────────────────────────────────────────────────── */}
        <div>
          <div style={{ fontSize: 11, color: 'var(--gm-dim)', textTransform: 'uppercase',
                        letterSpacing: '0.06em', marginBottom: 8 }}>
            Environment
          </div>
          <EnvRow name="STRIPE_SECRET_KEY" entry={env.STRIPE_SECRET_KEY} />
          <EnvRow name="STRIPE_PUBLISHABLE_KEY" entry={env.STRIPE_PUBLISHABLE_KEY} />
          <EnvRow name="STRIPE_WEBHOOK_SECRET" entry={env.STRIPE_WEBHOOK_SECRET} />
          <EnvRow name="APP_BASE_URL" entry={env.APP_BASE_URL} />
          <p style={{ color: 'var(--gm-dim)', fontSize: 11, margin: '10px 0 0', lineHeight: 1.5 }}>
            Presence and mode only. No key value is ever sent to this screen or
            stored in the database.
          </p>
        </div>

        {/* ── scope ──────────────────────────────────────────────────────── */}
        <div>
          <div style={{ fontSize: 11, color: 'var(--gm-dim)', textTransform: 'uppercase',
                        letterSpacing: '0.06em', marginBottom: 8 }}>
            Brand in scope
          </div>
          <div style={{ fontSize: 18, fontWeight: 700 }}>{brandName}</div>
          <div style={{ fontSize: 12, color: 'var(--gm-text)', marginTop: 4 }}>
            {plans.length} purchasable {plans.length === 1 ? 'tier' : 'tiers'}
            {brand?.fully_mapped
              ? ' · every price mapped'
              : unmapped ? ` · ${unmapped} price${unmapped === 1 ? '' : 's'} not mapped`
                : ''}
          </div>
          {!platformId && (
            <div style={{ fontSize: 11, color: 'var(--gm-dim)', marginTop: 8 }}>
              Showing the first brand with a catalogue. Use the brand selector above
              to switch.
            </div>
          )}
          {diag?.explanation && (
            <div style={{ fontSize: 12, color: 'var(--gm-text)', marginTop: 12, lineHeight: 1.5 }}>
              {diag.explanation}
            </div>
          )}
        </div>
      </div>

      {/* ── the catalogue ──────────────────────────────────────────────────── */}
      <div style={{ marginTop: 24 }}>
        <div style={{ fontSize: 11, color: 'var(--gm-dim)', textTransform: 'uppercase',
                      letterSpacing: '0.06em', marginBottom: 10 }}>
          Billing catalogue
        </div>

        {!plans.length ? (
          <div style={{ color: 'var(--gm-dim)', fontSize: 13, fontStyle: 'italic' }}>
            This brand has no purchasable plans configured, so there is nothing to
            map into Stripe yet.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ color: 'var(--gm-dim)', textAlign: 'left', fontSize: 11,
                             textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                  <th style={{ padding: '8px 10px 8px 0' }}>Tier</th>
                  <th style={{ padding: '8px 10px' }}>Commitment</th>
                  <th style={{ padding: '8px 10px' }}>Configured</th>
                  <th style={{ padding: '8px 10px' }}>Stripe price</th>
                  <th style={{ padding: '8px 0 8px 10px' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {plans.flatMap(p => COMMITMENTS.map((c, i) => {
                  const cents = p[c.centsKey];
                  const id = p[c.idKey];
                  const mapped = !!p[c.mappedKey];
                  return (
                    <tr key={`${p.key}-${c.key}`}
                        style={{ borderTop: i === 0 ? '1px solid var(--gm-row-line-strong)' : '1px solid var(--gm-row-line)' }}>
                      <td style={{ padding: '10px 10px 10px 0', fontWeight: i === 0 ? 700 : 400,
                                   color: i === 0 ? 'var(--gm-blue)' : 'var(--gm-dim)' }}>
                        {i === 0 ? (p.name || p.key) : ''}
                      </td>
                      <td style={{ padding: '10px', color: 'var(--gm-blue)' }}>{c.label}</td>
                      <td style={{ padding: '10px', fontVariantNumeric: 'tabular-nums' }}>
                        {cents ? <>{money(cents)}<span style={{ color: 'var(--gm-dim)' }}>/mo</span></>
                          : <span style={{ color: 'var(--gm-dim)', fontStyle: 'italic' }}>
                              not offered
                            </span>}
                      </td>
                      <td style={{ padding: '10px' }}>
                        {id
                          ? <code style={{ fontSize: 11, color: 'var(--gm-text)' }}>{id}</code>
                          : <span style={{ color: 'var(--gm-dim)' }}>—</span>}
                      </td>
                      <td style={{ padding: '10px 0 10px 10px' }}>
                        {!cents ? <Pill tone="mute">n/a</Pill>
                          : mapped ? <Pill tone="good">Mapped</Pill>
                            : <Pill tone="warn">Missing</Pill>}
                      </td>
                    </tr>
                  );
                }))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── the action ─────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap',
                    marginTop: 20, paddingTop: 20, borderTop: '1px solid var(--gm-blue)' }}>
        <button
          onClick={() => provision(false)}
          disabled={busy || !brand?.platform_id}
          style={{ background: 'transparent', color: 'var(--gm-blue)', border: '1px solid var(--gm-blue)',
                   borderRadius: 8, padding: '9px 16px', fontSize: 13, fontWeight: 600,
                   cursor: busy ? 'default' : 'pointer' }}>
          {busy ? 'Working…' : 'Preview'}
        </button>
        <button
          onClick={() => provision(true)}
          disabled={busy || !brand?.platform_id || !credentialsReady}
          title={!credentialsReady ? 'Stripe credentials are not configured.' : undefined}
          style={{ background: credentialsReady ? 'var(--gm-blue)' : 'var(--gm-blue)',
                   color: credentialsReady ? 'var(--gm-blue)' : 'var(--gm-dim)',
                   border: 'none', borderRadius: 8, padding: '9px 18px', fontSize: 13,
                   fontWeight: 800, cursor: busy || !credentialsReady ? 'default' : 'pointer' }}>
          Provision / sync Stripe TEST catalogue
        </button>
        <span style={{ color: 'var(--gm-dim)', fontSize: 12 }}>
          Creates only what is missing. Safe to run repeatedly — an already-mapped
          price is reused, never duplicated.
        </span>
      </div>

      {result && (
        <div style={{ marginTop: 16, background: 'var(--gm-pill-blue-bg)', border: '1px solid var(--gm-blue)',
                      borderRadius: 8, padding: '14px 16px' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>
            {result.dry_run ? 'Preview — nothing was created' : 'Provisioning complete'}
            <span style={{ color: 'var(--gm-dim)', fontWeight: 400 }}>
              {' · '}{result.summary?.prices_created ?? 0} created
              {' · '}{result.summary?.prices_reused ?? 0} reused
              {' · '}{result.summary?.plans_skipped ?? 0} skipped
            </span>
          </div>
          {(result.plans || []).map(p => (
            <div key={p.plan_key} style={{ fontSize: 12, color: 'var(--gm-text)', padding: '3px 0' }}>
              <b style={{ color: 'var(--gm-blue)' }}>{p.name || p.plan_key}</b>
              {p.skipped
                ? <span style={{ fontStyle: 'italic' }}> — {p.skipped}</span>
                : COMMITMENTS.map(c => {
                    const r = (p.commitments || {})[c.key];
                    if (!r) return null;
                    return (
                      <span key={c.key} style={{ marginLeft: 12 }}>
                        {c.label}: {r.status}
                        {r.price_id ? ` (${r.price_id})` : ''}
                      </span>
                    );
                  })}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
