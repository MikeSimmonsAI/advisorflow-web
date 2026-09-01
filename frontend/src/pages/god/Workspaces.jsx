/**
 * WORKSPACES — the doorway into every brand and every customer.
 *
 * This replaces a single hardcoded jump: God Mode's rail carried one entry,
 * `{ label: 'Sales Workspace', path: '/sales', hint: 'EvoSys Pro brand sales' }`,
 * with the brand named in a literal and no way to choose another. `/sales`
 * takes no brand parameter, and for a god_admin the server returned EVERY
 * brand's sales org — so with one brand seeded it looked like a sensible
 * default, and the day a second brand existed it would have blended two
 * companies' pipelines onto one screen under one brand's name.
 *
 * Everything here is driven by the platform records: brands come from
 * GET /god/platform/overview, customers from GET /god/platform/brands/{id}/
 * customers, and each brand renders in its OWN accent and mark from the columns
 * consolidated onto the platforms table. A new brand appears in this doorway the
 * moment its row exists — no navigation entry to add.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { enterCustomer } from './enterCustomer'
import { enterBrand } from './enterBrand'

const S = {
  wrap:   { padding: '28px 32px 60px', maxWidth: 1180, margin: '0 auto' },
  h1:     { fontSize: 24, fontWeight: 700, margin: '0 0 6px', letterSpacing: '-0.01em' },
  sub:    { color: 'var(--text-secondary)', fontSize: 14, margin: '0 0 26px', maxWidth: '70ch' },
  grid:   { display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))' },
  card:   { background: 'var(--bg-panel)', border: '1px solid var(--border-subtle)',
            borderRadius: 12, overflow: 'hidden', display: 'flex', flexDirection: 'column' },
  head:   { display: 'flex', alignItems: 'center', gap: 12, padding: '16px 18px',
            borderBottom: '1px solid var(--border-subtle)' },
  mark:   { width: 40, height: 40, borderRadius: 9, display: 'grid', placeItems: 'center',
            fontWeight: 700, fontSize: 15, color: '#fff', flex: 'none' },
  name:   { fontWeight: 700, fontSize: 16, lineHeight: 1.2 },
  slug:   { fontSize: 11, color: 'var(--text-tertiary)', fontFamily: 'ui-monospace, monospace' },
  body:   { padding: '12px 18px 16px', display: 'flex', flexDirection: 'column', gap: 10 },
  label:  { fontSize: 10, letterSpacing: '0.09em', textTransform: 'uppercase',
            color: 'var(--text-tertiary)', fontWeight: 600 },
  row:    { display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            gap: 10, padding: '9px 11px', borderRadius: 8,
            border: '1px solid var(--border-subtle)', background: 'var(--bg-field-soft)' },
  rowName:{ fontSize: 13, fontWeight: 600 },
  rowMeta:{ fontSize: 11, color: 'var(--text-tertiary)' },
  btn:    { fontSize: 12, fontWeight: 600, padding: '5px 11px', borderRadius: 6,
            border: '1px solid var(--border-strong)', background: 'transparent',
            color: 'var(--text-primary)', cursor: 'pointer', whiteSpace: 'nowrap' },
  empty:  { fontSize: 12, color: 'var(--text-tertiary)', fontStyle: 'italic', padding: '4px 2px' },
  err:    { color: 'var(--signal-red)', fontSize: 13, margin: '10px 0' },
}

export default function Workspaces() {
  const navigate = useNavigate()
  const [brands, setBrands] = useState(null)
  const [customers, setCustomers] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  const loadBrands = useCallback(() => {
    api.get('/god/platform/overview', { noOrgContext: true })
      .then(r => {
        const list = r?.platforms || r?.brands || (Array.isArray(r) ? r : [])
        setBrands(list)
        list.forEach(b => {
          api.get(`/god/platform/brands/${b.id}/customers`, { noOrgContext: true })
            .then(c => setCustomers(prev => ({
              ...prev, [b.id]: c?.customers || (Array.isArray(c) ? c : []),
            })))
            .catch(() => setCustomers(prev => ({ ...prev, [b.id]: [] })))
        })
      })
      .catch(e => setError(e.message || 'Could not load brands.'))
  }, [])

  useEffect(() => { loadBrands() }, [loadBrands])

  // Does this brand have a sales team at all?
  //
  // `/god/platform/overview` has always returned `brand_sales_orgs` per
  // platform and this page ignored it, drawing "Sales -> Enter" on every card
  // unconditionally. Only one brand has ever had a sales team - nothing outside
  // the demo seeder created one - so every other card offered a door onto a
  // screen that said "No active brand sales membership", which was neither true
  // nor fixable by granting a membership.
  //
  // Read from the platform record, so a brand that gains a sales team gains a
  // working Enter with no change here.
  const salesOrgOf = (b) => (b?.brand_sales_orgs || []).find(s => s.is_active !== false)
                            || (b?.brand_sales_orgs || [])[0] || null

  async function goBrand(b) {
    setBusy('brand:' + b.id); setError('')
    try {
      await enterBrand(b.id, b.name)
      navigate('/sales')
    } catch (e) {
      setError(e.message || 'Could not enter that brand.')
    } finally { setBusy('') }
  }

  async function createSalesTeam(b) {
    setBusy('mksales:' + b.id); setError('')
    try {
      await api.post(`/god/platform/brands/${b.id}/sales-org`, {},
                     { noOrgContext: true })
      // Re-read rather than patching state locally: the server decides the
      // name and slug, and this page should show what it decided.
      loadBrands()
    } catch (e) {
      setError(e.message || 'Could not create the sales team.')
    } finally { setBusy('') }
  }

  async function goCustomer(b, c) {
    setBusy('cust:' + c.id); setError('')
    try {
      // The brand is entered first so the trail reads
      // AdvisorFlow -> Brand -> Customer rather than skipping the middle.
      await enterBrand(b.id, b.name)
      await enterCustomer(c.id, c.name)
      navigate('/')
    } catch (e) {
      setError(e.message || 'Could not enter that organization.')
    } finally { setBusy('') }
  }

  return (
    <div style={S.wrap}>
      <h1 style={S.h1}>Workspaces</h1>
      <p style={S.sub}>
        Every brand AdvisorFlow operates, and everyone inside it. Choose a brand's
        own sales workspace, or one of its customer organizations. You keep full
        access to all of them — this only decides where what you do next belongs.
      </p>

      {error && <div style={S.err}>{error}</div>}
      {brands === null && !error && <div style={S.empty}>Loading brands…</div>}
      {brands && brands.length === 0 && (
        <div style={S.empty}>No brands exist yet. Create one in Platform.</div>
      )}

      <div style={S.grid}>
        {(brands || []).map(b => {
          const accent = b.accent_color || 'var(--accent)'
          const initial = b.logo_initial || b.short_name ||
                          (b.name || '?').slice(0, 2).toUpperCase()
          const list = customers[b.id]
          return (
            <div key={b.id} style={S.card}>
              <div style={S.head}>
                <div style={{ ...S.mark, background: accent }}>{initial}</div>
                <div style={{ minWidth: 0 }}>
                  <div style={S.name}>{b.name}</div>
                  <div style={S.slug}>{b.slug}</div>
                </div>
              </div>

              <div style={S.body}>
                <div style={S.label}>Brand workspace</div>
                <div style={S.row}>
                  <div>
                    <div style={S.rowName}>{b.name} Sales Workspace</div>
                    <div style={S.rowMeta}>
                      {salesOrgOf(b)
                        ? 'Pipeline, proposals, scheduling'
                        : 'No sales team yet — nothing to open'}
                    </div>
                  </div>
                  {/* Enter only when there is somewhere to go. Otherwise offer
                      the thing that makes it possible, rather than a button
                      that lands on an error. */}
                  {salesOrgOf(b) ? (
                    <button style={S.btn} onClick={() => goBrand(b)}
                            disabled={busy === 'brand:' + b.id}>
                      {busy === 'brand:' + b.id ? 'Entering…' : 'Enter'}
                    </button>
                  ) : (
                    <button style={S.btn} onClick={() => createSalesTeam(b)}
                            disabled={busy === 'mksales:' + b.id}>
                      {busy === 'mksales:' + b.id ? 'Creating…' : 'Create sales team'}
                    </button>
                  )}
                </div>

                <div style={{ ...S.label, marginTop: 6 }}>
                  Customer organizations{list ? ` (${list.length})` : ''}
                </div>
                {list === undefined && <div style={S.empty}>Loading…</div>}
                {list && list.length === 0 && (
                  <div style={S.empty}>No customers on this brand yet.</div>
                )}
                {(list || []).map(c => (
                  <div key={c.id} style={S.row}>
                    <div style={{ minWidth: 0 }}>
                      <div style={S.rowName}>{c.name}</div>
                      <div style={S.rowMeta}>{c.slug}</div>
                    </div>
                    <button style={S.btn} onClick={() => goCustomer(b, c)}
                            disabled={busy === 'cust:' + c.id}>
                      {busy === 'cust:' + c.id ? 'Entering…' : 'Enter'}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
