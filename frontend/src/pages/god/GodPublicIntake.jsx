/**
 * PUBLIC INTAKE DESTINATION — where a brand's marketing site sends its leads.
 *
 * WHY THIS SCREEN EXISTS. `GET/PUT /god/platform/public-intake` have been
 * live for a while and NOTHING in the frontend called either of them. The
 * demo-request and SMS opt-in routes refuse until a destination is set, and
 * the only way to set one was a shell. So a brand whose website had a working
 * form was returning 503 to real prospects, and the fix was unreachable from
 * the product.
 *
 * IT IS GENERIC BY CONSTRUCTION. Every brand the platform has is listed, each
 * with a picker over ITS OWN organizations. No brand, organization or customer
 * is named in this file — the list comes from the server, and the server
 * refuses a destination belonging to a different brand whether or not this
 * screen offers it.
 */
import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'

/* ── primitives, matching the other God ops screens ───────────────────────── */

function Well({ children, tone = 'default' }) {
  const border = tone === 'good' ? 'var(--gm-teal)'
    : tone === 'warn' ? 'var(--gm-amber)'
      : tone === 'bad' ? 'var(--gm-red)'
        : 'var(--gm-card-line)'
  return (
    <div style={{
      border: `1px solid ${border}`, borderLeftWidth: 3, borderRadius: 6,
      background: 'var(--gm-panel)', padding: '10px 14px', fontSize: 12.5,
      color: 'var(--gm-blue)', lineHeight: 1.6,
    }}>{children}</div>
  )
}

function Btn({ onClick, disabled, children, variant = 'default' }) {
  const bg = variant === 'primary' ? 'var(--gm-blue)'
    : variant === 'danger' ? 'var(--gm-red)'
      : 'var(--gm-panel-3)'
  return (
    <button
      type="button" onClick={onClick} disabled={disabled}
      style={{
        padding: '7px 16px', borderRadius: 6, border: 'none',
        background: disabled ? 'var(--gm-panel-3)' : bg,
        color: 'var(--gm-head)', fontSize: 12.5, fontWeight: 600,
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
    >{children}</button>
  )
}

/* ── one brand ────────────────────────────────────────────────────────────── */

function BrandRow({ brand, onSaved }) {
  const [orgs, setOrgs] = useState(null)
  const [choice, setChoice] = useState(brand.organization_id || '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => { setChoice(brand.organization_id || '') }, [brand.organization_id])

  // The organizations are loaded ONLY when the row is opened. A platform with
  // many brands would otherwise fire one request per brand on mount to
  // populate pickers nobody is looking at.
  const loadOrgs = useCallback(async () => {
    if (orgs) return
    try {
      const r = await api.get(`/god/platform/brands/${brand.platform_id}/customers`)
      setOrgs(r.customers || [])
    } catch (e) {
      setErr(e?.message || 'Could not load this brand\'s organizations.')
      setOrgs([])
    }
  }, [orgs, brand.platform_id])

  async function save(orgId) {
    setBusy(true); setErr(null); setNote(null)
    try {
      const r = await api.put(`/god/platform/public-intake/${brand.platform_id}`,
        { organization_id: orgId || null })
      setNote(r.message || 'Saved.')
      onSaved && onSaved()
    } catch (e) {
      // The server makes the same three checks this screen does not: the
      // organization must exist, be active, and belong to THIS brand. Its
      // refusal is the message worth showing.
      setErr(e?.message || 'Could not save.')
    } finally { setBusy(false) }
  }

  const state = brand.problem ? 'bad' : brand.configured ? 'good' : 'warn'
  const stateText = brand.problem
    ? brand.problem
    : brand.configured
      ? `Open — public leads land in ${brand.organization_name}.`
      : 'Closed — the demo request and SMS opt-in routes refuse for this brand.'

  return (
    <div style={{
      border: '1px solid var(--gm-card-line)', borderRadius: 8,
      background: 'var(--gm-card)', padding: 14, display: 'grid', gap: 10,
    }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 14, color: 'var(--gm-head)' }}>{brand.name}</b>
        <code style={{ fontSize: 11, color: 'var(--gm-dim)' }}>{brand.slug}</code>
        {brand.website_url
          ? <span style={{ fontSize: 11, color: 'var(--gm-dim)' }}>{brand.website_url}</span>
          : null}
      </div>

      <Well tone={state}>{stateText}</Well>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select
          value={choice}
          onFocus={loadOrgs}
          onChange={e => { setChoice(e.target.value); loadOrgs() }}
          style={{
            flex: '1 1 260px', minWidth: 0, padding: '7px 10px', borderRadius: 6,
            border: '1px solid var(--gm-card-line)', background: 'var(--gm-panel)',
            color: 'var(--gm-blue)', fontSize: 12.5,
          }}
        >
          <option value="">— no destination (public intake closed) —</option>
          {/* Before the list loads, the currently configured organization is
              still shown by name, so an unopened row never looks unset. */}
          {!orgs && brand.organization_id
            ? <option value={brand.organization_id}>{brand.organization_name}</option>
            : null}
          {(orgs || []).map(o => (
            <option key={o.id} value={o.id} disabled={!o.is_active}>
              {o.name}{o.is_active ? '' : ' (inactive)'}
            </option>
          ))}
        </select>
        <Btn variant="primary" disabled={busy || choice === (brand.organization_id || '')}
             onClick={() => save(choice)}>
          {busy ? 'Saving…' : 'Save destination'}
        </Btn>
        {brand.configured || brand.organization_id
          ? <Btn variant="danger" disabled={busy} onClick={() => { setChoice(''); save('') }}>
              Close intake
            </Btn>
          : null}
      </div>

      {note ? <Well tone="good">{note}</Well> : null}
      {err ? <Well tone="bad">{err}</Well> : null}
    </div>
  )
}

/* ── page ─────────────────────────────────────────────────────────────────── */

export default function GodPublicIntake() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState(null)

  const load = useCallback(async () => {
    setErr(null)
    try {
      const r = await api.get('/god/platform/public-intake')
      setRows(r.platforms || [])
    } catch (e) {
      setErr(e?.message || 'Could not load public intake configuration.')
      setRows([])
    }
  }, [])

  useEffect(() => { load() }, [load])

  const unset = (rows || []).filter(r => !r.configured)

  return (
    <div style={{ padding: '22px 26px', display: 'grid', gap: 16, maxWidth: 900 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 19, color: 'var(--gm-head)' }}>
          Public Intake Destination
        </h1>
        <p style={{ margin: '6px 0 0', fontSize: 12.5, color: 'var(--gm-dim)', lineHeight: 1.65 }}>
          Which customer workspace receives a brand's unauthenticated website
          leads — the demo request form, the SMS opt-in page and the waitlist.
          A brand with no destination set does not fall back to anything: those
          routes refuse, which is why a brand whose site has a live form and no
          destination here is turning real prospects away right now.
        </p>
      </div>

      {err ? <Well tone="bad">{err}</Well> : null}

      {rows === null
        ? <Well>Loading…</Well>
        : rows.length === 0
          ? <Well tone="warn">No brands exist yet.</Well>
          : (
            <>
              {unset.length
                ? <Well tone="warn">
                    {unset.length === 1
                      ? '1 brand has no destination configured.'
                      : `${unset.length} brands have no destination configured.`}
                  </Well>
                : null}
              {rows.map(b => (
                <BrandRow key={b.platform_id} brand={b} onSaved={load} />
              ))}
            </>
          )}
    </div>
  )
}
