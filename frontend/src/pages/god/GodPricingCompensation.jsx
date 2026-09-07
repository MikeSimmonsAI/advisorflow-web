/**
 * God Mode — PRICING & COMPENSATION.
 *
 * The discount floors and the commission rates, editable by the person who
 * decides them. Before this screen both lived only in the database, which made
 * every rate change a developer task; a business rule that needs an engineer is
 * a business rule that stops being maintained.
 *
 * THREE THINGS THIS SCREEN IS CAREFUL ABOUT
 * -----------------------------------------
 * 1. UNCONFIGURED IS NOT ZERO. A package with no commission rule reads
 *    "UNCONFIGURED", never "$0". $0 says somebody decided this pays nothing;
 *    unconfigured says somebody still has to decide. Growth and Professional
 *    are unconfigured today and must keep saying so until Mike sets them.
 * 2. NO CEILING IS NOT ZERO EITHER. A null discount ceiling means unlimited,
 *    and is labelled that way rather than rendered as 0%.
 * 3. NOTHING HERE TOUCHES A PAST PAYOUT. Editing a plan changes what future
 *    deals earn. Existing entries carry the rate that produced them, so the
 *    banner on the compensation panel says so where somebody is about to edit.
 *
 * No permission is decided in this file. Every route behind it is require_god
 * and refuses anybody else server-side; this screen renders what it is given.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { Panel, Empty, money, whenExact, errText } from './GodOpsShared'
import './GodOps.css'

/* ── small helpers ───────────────────────────────────────────────────────── */

function num(v) {
  if (v === '' || v === null || v === undefined) return null
  const x = Number(v)
  return Number.isFinite(x) ? x : null
}

/** A percentage ceiling. Null is UNLIMITED and must not print as 0%. */
function pct(v) {
  if (v === null || v === undefined) return <span className="pc-none">no ceiling</span>
  return <span>{Number(v)}%</span>
}

function day(v) {
  if (!v) return '—'
  const d = new Date(v + 'T00:00:00')
  return Number.isNaN(d.getTime())
    ? String(v)
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function todayISO() {
  const d = new Date()
  return [d.getFullYear(),
          String(d.getMonth() + 1).padStart(2, '0'),
          String(d.getDate()).padStart(2, '0')].join('-')
}

const ROLE_LABEL = {
  sales_rep: 'Sales rep',
  sales_manager: 'Sales manager',
}

/** ACTIVE / SCHEDULED / EXPIRED / INACTIVE — computed, not stored.
 *  A row that is switched on but does not start until March is not "active"
 *  today, and saying so avoids somebody wondering why it is not biting. */
function windowState(row) {
  if (!row.is_active) return { key: 'inactive', label: 'INACTIVE', tone: 'blocked' }
  const t = todayISO()
  if (row.effective_from && row.effective_from > t)
    return { key: 'scheduled', label: 'SCHEDULED', tone: 'new' }
  if (row.effective_to && row.effective_to < t)
    return { key: 'expired', label: 'EXPIRED', tone: 'warn' }
  return { key: 'active', label: 'ACTIVE', tone: 'live' }
}

function State({ row }) {
  const s = windowState(row)
  return <span className={'go-badge ' + s.tone}>{s.label}</span>
}

/* ── pricing policies ────────────────────────────────────────────────────── */

const EMPTY_POLICY = {
  brand_sales_org_id: '', role: '',
  max_discount_pct_setup: '', max_discount_pct_monthly: '',
  min_term_months: '', below_floor_requires_approval: true,
  effective_from: '', effective_to: '', is_active: true, note: '',
}

function PolicyForm({ brands, value, onChange, onSave, onCancel, busy, err, saveLabel }) {
  const v = value
  const set = (k) => (e) => {
    const el = e.target
    onChange({ ...v, [k]: el.type === 'checkbox' ? el.checked : el.value })
  }
  return (
    <div style={{ background: 'var(--go-panel-2)', padding: '14px 15px' }}>
      <div className="go-fields">
        <div className="go-field">
          <label>Applies to</label>
          <select value={v.brand_sales_org_id} onChange={set('brand_sales_org_id')}>
            <option value="">Every sales organization (platform-wide)</option>
            {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <div className="hint">A brand's own policy wins over the platform-wide one.</div>
        </div>
        <div className="go-field">
          <label>Role</label>
          <select value={v.role} onChange={set('role')}>
            <option value="">Every role</option>
            <option value="sales_rep">Sales rep</option>
            <option value="sales_manager">Sales manager</option>
          </select>
          <div className="hint">A role-specific policy wins over the all-roles one.</div>
        </div>
        <div className="go-field">
          <label>Max discount — setup / implementation (%)</label>
          <input type="number" min="0" max="100" step="0.5"
                 value={v.max_discount_pct_setup}
                 onChange={set('max_discount_pct_setup')} placeholder="blank = no ceiling" />
        </div>
        <div className="go-field">
          <label>Max discount — monthly rate (%)</label>
          <input type="number" min="0" max="100" step="0.5"
                 value={v.max_discount_pct_monthly}
                 onChange={set('max_discount_pct_monthly')} placeholder="blank = no ceiling" />
          <div className="hint">
            Judged independently of setup, so a rep cannot destroy the monthly
            rate because the one-time total still looks acceptable.
          </div>
        </div>
        <div className="go-field">
          <label>Minimum term (months)</label>
          <input type="number" min="0" step="1" value={v.min_term_months}
                 onChange={set('min_term_months')} placeholder="blank = no minimum" />
        </div>
        <div className="go-field">
          <label>Effective from</label>
          <input type="date" value={v.effective_from} onChange={set('effective_from')} />
        </div>
        <div className="go-field">
          <label>Effective to</label>
          <input type="date" value={v.effective_to} onChange={set('effective_to')} />
          <div className="hint">Blank means it stays in force until superseded.</div>
        </div>
        <div className="go-field full">
          <label className="go-check" style={{ textTransform: 'none', letterSpacing: 0 }}>
            <input type="checkbox" checked={!!v.below_floor_requires_approval}
                   onChange={set('below_floor_requires_approval')} />
            <span>
              <strong style={{ fontSize: 13 }}>Below the floor may be requested, pending manager approval</strong>
              <small style={{ display: 'block', color: 'var(--go-dim)', fontSize: 11, marginTop: 3 }}>
                Unticked makes the floor HARD — a deal below it is refused
                outright rather than queued for approval.
              </small>
            </span>
          </label>
        </div>
        <div className="go-field full">
          <label>Note</label>
          <input type="text" value={v.note} onChange={set('note')}
                 placeholder="Why this ceiling exists" />
        </div>
      </div>

      {err ? <div className="go-note err" style={{ marginTop: 12 }}>{err}</div> : null}

      <div className="go-actions" style={{ marginTop: 12, justifyContent: 'flex-start' }}>
        <button className="go-btn sm" onClick={onSave} disabled={busy}>
          {busy ? 'Saving…' : (saveLabel || 'Save policy')}
        </button>
        <button className="go-btn sm ghost" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  )
}

function PolicyRow({ p, brands, onSaved }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [form, setForm] = useState(null)

  function begin() {
    setForm({
      brand_sales_org_id: p.brand_sales_org_id || '',
      role: p.role || '',
      max_discount_pct_setup: p.max_discount_pct_setup ?? '',
      max_discount_pct_monthly: p.max_discount_pct_monthly ?? '',
      min_term_months: p.min_term_months ?? '',
      below_floor_requires_approval: !!p.below_floor_requires_approval,
      effective_from: p.effective_from || '',
      effective_to: p.effective_to || '',
      is_active: !!p.is_active,
      note: p.note || '',
    })
    setErr(''); setOpen(true)
  }

  async function save() {
    setBusy(true); setErr('')
    try {
      await api.patch('/god/pricing/policies/' + p.id, {
        brand_sales_org_id: form.brand_sales_org_id || null,
        role: form.role || null,
        max_discount_pct_setup: num(form.max_discount_pct_setup),
        max_discount_pct_monthly: num(form.max_discount_pct_monthly),
        min_term_months: num(form.min_term_months),
        below_floor_requires_approval: !!form.below_floor_requires_approval,
        effective_from: form.effective_from || null,
        effective_to: form.effective_to || null,
        note: form.note || null,
      })
      setOpen(false)
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function toggleActive() {
    setBusy(true); setErr('')
    try {
      await api.patch('/god/pricing/policies/' + p.id, { is_active: !p.is_active })
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const brand = brands.find(b => b.id === p.brand_sales_org_id)

  return (
    <>
      <tr>
        <td data-label="Applies to">
          {brand ? brand.name : <em style={{ color: 'var(--go-dim)' }}>Platform-wide</em>}
        </td>
        <td data-label="Role">
          {p.role ? (ROLE_LABEL[p.role] || p.role)
                  : <em style={{ color: 'var(--go-dim)' }}>Every role</em>}
        </td>
        <td data-label="Setup ceiling" className="num">{pct(p.max_discount_pct_setup)}</td>
        <td data-label="Monthly ceiling" className="num">{pct(p.max_discount_pct_monthly)}</td>
        <td data-label="Min term" className="num">
          {p.min_term_months ? p.min_term_months + ' mo' : '—'}
        </td>
        <td data-label="Below floor">
          {p.below_floor_requires_approval
            ? <span className="go-badge warn">needs approval</span>
            : <span className="go-badge blocked">hard floor</span>}
        </td>
        <td data-label="In force">
          <div style={{ fontSize: 12 }}>{day(p.effective_from)} → {p.effective_to ? day(p.effective_to) : 'open'}</div>
        </td>
        <td data-label="Status"><State row={p} /></td>
        <td data-label="">
          <div className="go-actions">
            <button className="go-btn sm ghost" onClick={() => (open ? setOpen(false) : begin())}>
              {open ? 'Cancel' : 'Edit'}
            </button>
            <button className="go-btn sm ghost" onClick={toggleActive} disabled={busy}>
              {p.is_active ? 'Deactivate' : 'Reactivate'}
            </button>
          </div>
        </td>
      </tr>
      {open && form ? (
        <tr><td colSpan={9} style={{ padding: 0 }}>
          <PolicyForm brands={brands} value={form} onChange={setForm}
                      onSave={save} onCancel={() => setOpen(false)}
                      busy={busy} err={err} saveLabel="Save changes" />
        </td></tr>
      ) : null}
      {!open && err ? (
        <tr><td colSpan={9}><div className="go-note err" style={{ margin: 0 }}>{err}</div></td></tr>
      ) : null}
    </>
  )
}

function PoliciesPanel({ data, onSaved }) {
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState(EMPTY_POLICY)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const policies = data.policies || []

  async function create() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/pricing/policies', {
        brand_sales_org_id: form.brand_sales_org_id || null,
        role: form.role || null,
        max_discount_pct_setup: num(form.max_discount_pct_setup),
        max_discount_pct_monthly: num(form.max_discount_pct_monthly),
        min_term_months: num(form.min_term_months),
        below_floor_requires_approval: !!form.below_floor_requires_approval,
        effective_from: form.effective_from || null,
        effective_to: form.effective_to || null,
        is_active: true,
        note: form.note || null,
      })
      setAdding(false); setForm(EMPTY_POLICY)
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <Panel title="Pricing policy — discount floors" count={policies.length}
           actions={
             <button className="go-btn sm" onClick={() => { setErr(''); setAdding(!adding) }}>
               {adding ? 'Close' : 'New policy'}
             </button>
           }>
      {adding ? (
        <PolicyForm brands={data.brands || []} value={form} onChange={setForm}
                    onSave={create} onCancel={() => setAdding(false)}
                    busy={busy} err={err} saveLabel="Create policy" />
      ) : null}

      {!policies.length ? (
        <Empty>
          No discount ceiling is configured. Until one exists a rep may not
          discount at all and a manager is unconstrained — the built-in fallback,
          not a policy anybody set.
        </Empty>
      ) : (
        <table className="go-table">
          <thead>
            <tr>
              <th>Applies to</th><th>Role</th>
              <th className="num">Setup ceiling</th>
              <th className="num">Monthly ceiling</th>
              <th className="num">Min term</th>
              <th>Below floor</th><th>In force</th><th>Status</th><th></th>
            </tr>
          </thead>
          <tbody>
            {policies.map(p => (
              <PolicyRow key={p.id} p={p} brands={data.brands || []} onSaved={onSaved} />
            ))}
          </tbody>
        </table>
      )}

      <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
        <p style={{ margin: 0, fontSize: 12, color: 'var(--go-dim)' }}>
          A ceiling is a percentage off the catalogue price, applied
          independently to the setup fee and to the monthly rate. Leaving one
          blank means no ceiling on that component. Most specific policy wins:
          brand + role, then brand, then role, then platform-wide — and among
          equals, the one that started most recently.
        </p>
      </div>
    </Panel>
  )
}

/* ── compensation ────────────────────────────────────────────────────────── */

const EMPTY_RULE = {
  package_id: '', payee_kind: 'seller', override_level: '',
  basis: 'fixed', amount: '', percent: '', recurring_months: '',
  eligible_payment: 'initial', max_amount: '', note: '',
}

function RuleForm({ data, value, onChange, onSave, onCancel, busy, err }) {
  const v = value
  const set = (k) => (e) => onChange({ ...v, [k]: e.target.value })
  const isPercent = v.basis !== 'fixed'
  const bases = (data.vocabulary && data.vocabulary.bases) || []
  return (
    <div style={{ background: 'var(--go-panel-2)', padding: '14px 15px' }}>
      <div className="go-fields">
        <div className="go-field">
          <label>Package</label>
          <select value={v.package_id} onChange={set('package_id')}>
            <option value="">Every package</option>
            {(data.packages || []).map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <div className="go-field">
          <label>Paid to</label>
          <select value={v.payee_kind} onChange={set('payee_kind')}>
            <option value="seller">The salesperson</option>
            <option value="override">A manager / upline override</option>
          </select>
        </div>
        <div className="go-field">
          <label>Override level</label>
          <input type="number" min="1" step="1" value={v.override_level}
                 onChange={set('override_level')}
                 disabled={v.payee_kind !== 'override'}
                 placeholder={v.payee_kind === 'override' ? '1 = direct manager' : 'n/a'} />
          <div className="hint">
            An override is paid only where somebody eligible actually exists in
            the org chart. Nobody eligible means no override expense at all.
          </div>
        </div>
        <div className="go-field">
          <label>Basis</label>
          <select value={v.basis} onChange={set('basis')}>
            {bases.map(b => <option key={b.key} value={b.key}>{b.label}</option>)}
          </select>
        </div>
        <div className="go-field">
          <label>{isPercent ? 'Percent' : 'Fixed amount'}</label>
          {isPercent
            ? <input type="number" min="0" max="100" step="0.1" value={v.percent}
                     onChange={set('percent')} placeholder="leave blank to list as unconfigured" />
            : <input type="number" min="0" step="1" value={v.amount}
                     onChange={set('amount')} placeholder="leave blank to list as unconfigured" />}
          <div className="hint">
            Blank is deliberate and supported — the package is listed as a
            decision nobody has made, rather than as paying nothing.
          </div>
        </div>
        <div className="go-field">
          <label>Eligible payment</label>
          <select value={v.eligible_payment} onChange={set('eligible_payment')}>
            <option value="initial">Initial payment only</option>
            <option value="recurring">Recurring payments only</option>
            <option value="both">Initial and recurring</option>
          </select>
        </div>
        <div className="go-field">
          <label>Recurring months</label>
          <input type="number" min="0" step="1" value={v.recurring_months}
                 onChange={set('recurring_months')} placeholder="blank = for the life of the account" />
        </div>
        <div className="go-field">
          <label>Cap on this rule</label>
          <input type="number" min="0" step="1" value={v.max_amount}
                 onChange={set('max_amount')} placeholder="blank = uncapped" />
        </div>
        <div className="go-field full">
          <label>Note</label>
          <input type="text" value={v.note} onChange={set('note')} />
        </div>
      </div>
      {err ? <div className="go-note err" style={{ marginTop: 12 }}>{err}</div> : null}
      <div className="go-actions" style={{ marginTop: 12, justifyContent: 'flex-start' }}>
        <button className="go-btn sm" onClick={onSave} disabled={busy}>
          {busy ? 'Saving…' : 'Save rule'}
        </button>
        <button className="go-btn sm ghost" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  )
}

function RuleRow({ r, data, onSaved }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  function begin() {
    setForm({
      package_id: r.package_id || '',
      payee_kind: r.payee_kind || 'seller',
      override_level: r.override_level ?? '',
      basis: r.basis || 'fixed',
      amount: r.amount ?? '',
      percent: r.percent ?? '',
      recurring_months: r.recurring_months ?? '',
      eligible_payment: r.eligible_payment || 'initial',
      max_amount: r.max_amount ?? '',
      note: r.note || '',
    })
    setErr(''); setOpen(true)
  }

  async function save() {
    setBusy(true); setErr('')
    try {
      await api.patch('/god/pricing/rules/' + r.id, {
        package_id: form.package_id || null,
        payee_kind: form.payee_kind,
        override_level: form.payee_kind === 'override' ? num(form.override_level) : null,
        basis: form.basis,
        amount: form.basis === 'fixed' ? num(form.amount) : null,
        percent: form.basis === 'fixed' ? null : num(form.percent),
        recurring_months: num(form.recurring_months),
        eligible_payment: form.eligible_payment || null,
        max_amount: num(form.max_amount),
        note: form.note || null,
      })
      setOpen(false)
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function deactivate() {
    setBusy(true); setErr('')
    try {
      await api.delete('/god/pricing/rules/' + r.id)
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const rate = !r.configured
    ? <span className="go-badge warn">UNCONFIGURED</span>
    : r.amount !== null && r.amount !== undefined
      ? money(r.amount)
      : Number(r.percent) + '%'

  return (
    <>
      <tr>
        <td data-label="Package">
          {r.package_name || <em style={{ color: 'var(--go-dim)' }}>Every package</em>}
        </td>
        <td data-label="Paid to">
          {r.payee_kind === 'override'
            ? 'Override — level ' + (r.override_level || '?')
            : 'Salesperson'}
        </td>
        <td data-label="Basis">{r.basis_label || r.basis}</td>
        <td data-label="Rate" className="num">{rate}</td>
        <td data-label="Applies to">
          {r.eligible_payment === 'both' ? 'Initial + recurring'
            : r.eligible_payment === 'recurring' ? 'Recurring'
            : 'Initial payment'}
          {r.recurring_months ? ' · ' + r.recurring_months + ' mo' : ''}
        </td>
        <td data-label="Status">
          {r.is_active ? <span className="go-badge live">ACTIVE</span>
                       : <span className="go-badge blocked">INACTIVE</span>}
        </td>
        <td data-label="">
          <div className="go-actions">
            <button className="go-btn sm ghost" onClick={() => (open ? setOpen(false) : begin())}>
              {open ? 'Cancel' : 'Edit'}
            </button>
            {r.is_active ? (
              <button className="go-btn sm ghost" onClick={deactivate} disabled={busy}>
                Deactivate
              </button>
            ) : null}
          </div>
        </td>
      </tr>
      {open && form ? (
        <tr><td colSpan={7} style={{ padding: 0 }}>
          <RuleForm data={data} value={form} onChange={setForm} onSave={save}
                    onCancel={() => setOpen(false)} busy={busy} err={err} />
        </td></tr>
      ) : null}
      {!open && err ? (
        <tr><td colSpan={7}><div className="go-note err" style={{ margin: 0 }}>{err}</div></td></tr>
      ) : null}
    </>
  )
}

function CapEditor({ plan, data, onSaved }) {
  const [open, setOpen] = useState(false)
  const [pkg, setPkg] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function save() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/pricing/plans/' + plan.id + '/caps', {
        package_id: pkg, max_total_payout: num(amount),
      })
      setOpen(false); setPkg(''); setAmount('')
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
      <div className="go-row-between" style={{ marginBottom: (plan.caps || []).length ? 8 : 0 }}>
        <strong style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.06em',
                         color: 'var(--go-dim)' }}>
          Payout caps
        </strong>
        <button className="go-btn sm ghost" onClick={() => { setErr(''); setOpen(!open) }}>
          {open ? 'Cancel' : 'Set a cap'}
        </button>
      </div>

      {(plan.caps || []).length ? (
        <ul className="go-plain-list" style={{ margin: '4px 0 0 18px', fontSize: 13 }}>
          {plan.caps.map(c => (
            <li key={c.id}>
              {c.package_name || c.package_id} — {money(c.max_total_payout)} total
              across everyone on this package
            </li>
          ))}
        </ul>
      ) : (
        <p style={{ margin: '4px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>
          No cap set. Total payout on a package is whatever the rules add up to.
        </p>
      )}

      {open ? (
        <div className="go-fields" style={{ marginTop: 12 }}>
          <div className="go-field">
            <label>Package</label>
            <select value={pkg} onChange={e => setPkg(e.target.value)}>
              <option value="">Choose a package…</option>
              {(data.packages || []).map(p => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="go-field">
            <label>Maximum total payout</label>
            <input type="number" min="0" step="1" value={amount}
                   onChange={e => setAmount(e.target.value)} />
            <div className="hint">
              Everyone's commission on this package together. Where the rules
              exceed it, each is reduced proportionally.
            </div>
          </div>
        </div>
      ) : null}

      {err ? <div className="go-note err" style={{ marginTop: 10 }}>{err}</div> : null}

      {open ? (
        <div className="go-actions" style={{ marginTop: 10, justifyContent: 'flex-start' }}>
          <button className="go-btn sm" onClick={save} disabled={busy || !pkg || num(amount) === null}>
            {busy ? 'Saving…' : 'Save cap'}
          </button>
        </div>
      ) : null}
    </div>
  )
}

function PlanCard({ plan, data, onSaved }) {
  const [addRule, setAddRule] = useState(false)
  const [form, setForm] = useState(EMPTY_RULE)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [editHead, setEditHead] = useState(false)
  const [head, setHead] = useState(null)

  const brand = (data.brands || []).find(b => b.id === plan.brand_sales_org_id)

  async function createRule() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/pricing/plans/' + plan.id + '/rules', {
        package_id: form.package_id || null,
        payee_kind: form.payee_kind,
        override_level: form.payee_kind === 'override' ? num(form.override_level) : null,
        basis: form.basis,
        amount: form.basis === 'fixed' ? num(form.amount) : null,
        percent: form.basis === 'fixed' ? null : num(form.percent),
        recurring_months: num(form.recurring_months),
        eligible_payment: form.eligible_payment || null,
        max_amount: num(form.max_amount),
        note: form.note || null,
      })
      setAddRule(false); setForm(EMPTY_RULE)
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  function beginHead() {
    setHead({
      name: plan.name || '',
      effective_from: plan.effective_from || '',
      effective_to: plan.effective_to || '',
      holdback_days: plan.holdback_days ?? '',
      max_override_levels: plan.max_override_levels ?? '',
      note: plan.note || '',
    })
    setErr(''); setEditHead(true)
  }

  async function saveHead() {
    setBusy(true); setErr('')
    try {
      await api.patch('/god/pricing/plans/' + plan.id, {
        name: head.name,
        effective_from: head.effective_from || null,
        effective_to: head.effective_to || null,
        holdback_days: num(head.holdback_days),
        max_override_levels: num(head.max_override_levels),
        note: head.note || null,
      })
      setEditHead(false)
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  async function togglePlan() {
    setBusy(true); setErr('')
    try {
      await api.patch('/god/pricing/plans/' + plan.id, { is_active: !plan.is_active })
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const rules = plan.rules || []

  return (
    <section className="go-panel" style={{ marginBottom: 14 }}>
      <h2>
        {plan.name}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <State row={plan} />
          <button className="go-btn sm ghost" onClick={() => (editHead ? setEditHead(false) : beginHead())}>
            {editHead ? 'Cancel' : 'Edit plan'}
          </button>
          <button className="go-btn sm ghost" onClick={togglePlan} disabled={busy}>
            {plan.is_active ? 'Deactivate' : 'Reactivate'}
          </button>
        </span>
      </h2>

      <div className="go-body" style={{ paddingBottom: 4 }}>
        <div className="go-facts">
          <div className="go-fact">
            <div className="k">Sales organization</div>
            <div className={'v' + (brand ? '' : ' none')}>
              {brand ? brand.name : 'Platform-wide'}
            </div>
          </div>
          <div className="go-fact">
            <div className="k">In force</div>
            <div className="v">{day(plan.effective_from)} → {plan.effective_to ? day(plan.effective_to) : 'open'}</div>
          </div>
          <div className="go-fact">
            <div className="k">Holdback</div>
            <div className="v">{plan.holdback_days} days</div>
          </div>
          <div className="go-fact">
            <div className="k">Override levels</div>
            <div className="v">{plan.max_override_levels}</div>
          </div>
        </div>
        {plan.note ? (
          <p style={{ margin: '10px 0 0', fontSize: 12, color: 'var(--go-dim)' }}>{plan.note}</p>
        ) : null}
      </div>

      {editHead && head ? (
        <div style={{ background: 'var(--go-panel-2)', padding: '14px 15px' }}>
          <div className="go-fields">
            <div className="go-field">
              <label>Plan name</label>
              <input type="text" value={head.name}
                     onChange={e => setHead({ ...head, name: e.target.value })} />
            </div>
            <div className="go-field">
              <label>Effective from</label>
              <input type="date" value={head.effective_from}
                     onChange={e => setHead({ ...head, effective_from: e.target.value })} />
            </div>
            <div className="go-field">
              <label>Effective to</label>
              <input type="date" value={head.effective_to}
                     onChange={e => setHead({ ...head, effective_to: e.target.value })} />
            </div>
            <div className="go-field">
              <label>Holdback (days)</label>
              <input type="number" min="0" step="1" value={head.holdback_days}
                     onChange={e => setHead({ ...head, holdback_days: e.target.value })} />
              <div className="hint">
                Earned commission becomes payable this many days after
                collection. EvoSys pays weekly after a 14-day holdback.
              </div>
            </div>
            <div className="go-field">
              <label>Override levels</label>
              <input type="number" min="0" step="1" value={head.max_override_levels}
                     onChange={e => setHead({ ...head, max_override_levels: e.target.value })} />
            </div>
            <div className="go-field full">
              <label>Note</label>
              <input type="text" value={head.note}
                     onChange={e => setHead({ ...head, note: e.target.value })} />
            </div>
          </div>
          {err ? <div className="go-note err" style={{ marginTop: 12 }}>{err}</div> : null}
          <div className="go-actions" style={{ marginTop: 12, justifyContent: 'flex-start' }}>
            <button className="go-btn sm" onClick={saveHead} disabled={busy}>
              {busy ? 'Saving…' : 'Save plan'}
            </button>
            <button className="go-btn sm ghost" onClick={() => setEditHead(false)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {!rules.length ? (
        <Empty>This plan has no rules yet. Nobody earns anything under it.</Empty>
      ) : (
        <table className="go-table">
          <thead>
            <tr>
              <th>Package</th><th>Paid to</th><th>Basis</th>
              <th className="num">Rate</th><th>Applies to</th><th>Status</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rules.map(r => <RuleRow key={r.id} r={r} data={data} onSaved={onSaved} />)}
          </tbody>
        </table>
      )}

      <div className="go-body" style={{ borderTop: '1px solid var(--go-line)', paddingBottom: 12 }}>
        <button className="go-btn sm ghost" onClick={() => { setErr(''); setAddRule(!addRule) }}>
          {addRule ? 'Cancel' : 'Add a commission rule'}
        </button>
      </div>
      {addRule ? (
        <RuleForm data={data} value={form} onChange={setForm} onSave={createRule}
                  onCancel={() => setAddRule(false)} busy={busy} err={err} />
      ) : null}

      <CapEditor plan={plan} data={data} onSaved={onSaved} />
    </section>
  )
}

const EMPTY_PLAN = {
  brand_sales_org_id: '', name: '', effective_from: todayISO(),
  holdback_days: 14, max_override_levels: 1, note: '',
}

function NewPlan({ data, onSaved }) {
  const [open, setOpen] = useState(false)
  const [v, setV] = useState(EMPTY_PLAN)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k) => (e) => setV({ ...v, [k]: e.target.value })

  async function create() {
    setBusy(true); setErr('')
    try {
      await api.post('/god/pricing/plans', {
        brand_sales_org_id: v.brand_sales_org_id || null,
        name: v.name,
        effective_from: v.effective_from || null,
        holdback_days: num(v.holdback_days),
        max_override_levels: num(v.max_override_levels),
        note: v.note || null,
      })
      setOpen(false); setV(EMPTY_PLAN)
      await onSaved()
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  if (!open) {
    return (
      <div style={{ marginBottom: 20 }}>
        <button className="go-btn sm" onClick={() => { setErr(''); setOpen(true) }}>
          New compensation plan
        </button>
      </div>
    )
  }

  return (
    <section className="go-panel" style={{ marginBottom: 20 }}>
      <h2>New compensation plan</h2>
      <div style={{ background: 'var(--go-panel-2)', padding: '14px 15px' }}>
        <div className="go-fields">
          <div className="go-field">
            <label>Sales organization</label>
            <select value={v.brand_sales_org_id} onChange={set('brand_sales_org_id')}>
              <option value="">Every sales organization (platform-wide)</option>
              {(data.brands || []).map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>
          <div className="go-field">
            <label>Plan name</label>
            <input type="text" value={v.name} onChange={set('name')}
                   placeholder="e.g. EvoSys Direct Sales 2027" />
          </div>
          <div className="go-field">
            <label>Effective from</label>
            <input type="date" value={v.effective_from} onChange={set('effective_from')} />
            <div className="hint">
              Required. Without a start date this plan could not be superseded
              later without rewriting history.
            </div>
          </div>
          <div className="go-field">
            <label>Holdback (days)</label>
            <input type="number" min="0" step="1" value={v.holdback_days}
                   onChange={set('holdback_days')} />
          </div>
          <div className="go-field">
            <label>Override levels</label>
            <input type="number" min="0" step="1" value={v.max_override_levels}
                   onChange={set('max_override_levels')} />
          </div>
          <div className="go-field full">
            <label>Note</label>
            <input type="text" value={v.note} onChange={set('note')} />
          </div>
        </div>
        {err ? <div className="go-note err" style={{ marginTop: 12 }}>{err}</div> : null}
        <div className="go-actions" style={{ marginTop: 12, justifyContent: 'flex-start' }}>
          <button className="go-btn sm" onClick={create} disabled={busy || !v.name.trim()}>
            {busy ? 'Creating…' : 'Create plan'}
          </button>
          <button className="go-btn sm ghost" onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </button>
        </div>
      </div>
    </section>
  )
}

/* ── the seed ────────────────────────────────────────────────────────────── */

function SeedPanel({ data, onSaved }) {
  const brands = data.brands || []
  const [brand, setBrand] = useState(data.selected_brand_sales_org_id || '')
  const [preview, setPreview] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(null)

  useEffect(() => {
    if (!brand && brands.length === 1) setBrand(brands[0].id)
  }, [brands, brand])

  async function run(confirm) {
    setBusy(true); setErr('')
    try {
      const r = await api.post('/god/pricing/seed/evosys', {
        brand_sales_org_id: brand, confirm,
      })
      if (confirm) { setDone(r); setPreview(null); await onSaved() }
      else { setPreview(r); setDone(null) }
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  return (
    <Panel title="Seed the decided EvoSys rules">
      <div className="go-body">
        <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--go-dim)', maxWidth: '70ch' }}>
          Writes only the figures that have actually been decided: Starter at
          $500 to the salesperson, $100 to an eligible manager, $800 total cap,
          14-day holdback; and the multi-tenant SaaS package at 10% of the
          eligible initial payment and 10% of eligible recurring payments.
          Growth and Professional are deliberately left UNCONFIGURED — no
          amount is invented for them. Re-running keeps whatever already exists
          and never overwrites a figure set by hand.
        </p>

        <div className="go-fields">
          <div className="go-field">
            <label>Sales organization</label>
            <select value={brand} onChange={e => { setBrand(e.target.value); setPreview(null); setDone(null) }}>
              <option value="">Choose a sales organization…</option>
              {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>
        </div>

        {err ? <div className="go-note err" style={{ marginTop: 12 }}>{err}</div> : null}

        <div className="go-actions" style={{ marginTop: 12, justifyContent: 'flex-start' }}>
          <button className="go-btn sm ghost" onClick={() => run(false)} disabled={busy || !brand}>
            {busy ? 'Working…' : 'Preview — writes nothing'}
          </button>
          {preview ? (
            <button className="go-btn sm" onClick={() => run(true)} disabled={busy}>
              Apply these rules
            </button>
          ) : null}
        </div>
      </div>

      {preview ? (
        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          <strong style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.06em',
                           color: 'var(--go-dim)' }}>
            What this would do
          </strong>
          <ul className="go-plain-list" style={{ marginTop: 8, fontSize: 13 }}>
            {(preview.actions || []).map((a, i) => (
              <li key={i} style={{ margin: '4px 0' }}>
                <span className={'go-badge ' + (a.action === 'create' ? 'ready'
                                : a.action === 'leave_unconfigured' ? 'warn'
                                : a.action === 'skip' ? 'blocked' : '')}>
                  {a.action.replace(/_/g, ' ')}
                </span>{' '}
                <strong>{a.what}</strong> — {a.detail}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {done ? (
        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          <div className="go-note ok" style={{ marginBottom: 10 }}>
            Written. {(done.written || []).filter(w => w.status === 'created').length} created,
            {' '}{(done.written || []).filter(w => w.status === 'kept').length} already present and left alone.
          </div>
          <ul className="go-plain-list" style={{ fontSize: 13 }}>
            {(done.written || []).map((w, i) => (
              <li key={i}>{w.what} — {w.status}</li>
            ))}
          </ul>
          {(done.unconfigured_packages || []).length ? (
            <p style={{ marginTop: 10, fontSize: 12, color: 'var(--go-amber)' }}>
              Still unconfigured: {done.unconfigured_packages.map(p => p.name).join(', ')}.
            </p>
          ) : null}
        </div>
      ) : null}
    </Panel>
  )
}

/* ── who may see and settle this brand's compensation ─────────────────────── */

/* WHY THIS PANEL IS ON THE PRICING & COMP SCREEN. The person deciding who runs
 * a commission run is already here deciding what a commission IS. Users &
 * Identity administers PEOPLE across the platform; this administers one
 * commercial authority over one brand.
 *
 * THERE IS NO "FINANCE MANAGER" ROLE TO PICK. A named role beside the
 * capability model would be a second permission system, and the first time the
 * two disagreed nobody would know which was authoritative. Capabilities and a
 * scope are the whole answer.
 */
function BrandAccessPanel({ data }) {
  const brands = data.brands || []
  const [brand, setBrand] = useState(data.selected_brand_sales_org_id || '')
  const [d, setD] = useState(null)
  const [health, setHealth] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    if (!brand) { setD(null); return }
    try {
      setD(await api.get('/god/pricing/brand-access?brand_sales_org_id=' + brand))
      setErr('')
    } catch (e) { setErr(errText(e)) }
  }, [brand])

  // THE MIGRATION CHECK, ON THE SCREEN RATHER THAN IN A CONSOLE. Adding scope
  // columns to the grant table could have stripped every existing customer-org
  // administrator's capabilities without anything visibly breaking. This says
  // whether it did, in one line, where somebody will actually see it.
  useEffect(() => {
    api.get('/god/pricing/capability-scope-health')
      .then(setHealth).catch(() => setHealth(null))
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!brand && brands.length === 1) setBrand(brands[0].id)
  }, [brands, brand])

  async function toggle(person, key) {
    const next = person.capabilities.includes(key)
      ? person.capabilities.filter(k => k !== key)
      : [...person.capabilities, key]
    setBusy(person.user_id + key); setErr('')
    try {
      await api.put('/god/pricing/brand-access', {
        brand_sales_org_id: brand, user_id: person.user_id,
        capabilities: next,
      })
      await load()
    } catch (e) { setErr(errText(e)) } finally { setBusy('') }
  }

  return (
    <Panel title="Who may see and settle compensation">
      <div className="go-body">
        <p style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--go-dim)',
                    maxWidth: '72ch' }}>
          These are granted <strong>per brand</strong>. Seeing compensation and
          settling it are separate: a finance administrator can be given either
          or both without becoming a sales manager, and a sales manager does not
          get settlement authority just by running a team. The platform owner
          always has both.
        </p>

        {health ? (
          <div className={'go-note ' + (health.healthy ? 'ok' : 'err')}
               style={{ marginBottom: 14 }}>
            <b>
              {health.healthy
                ? 'EXISTING CAPABILITY GRANTS SURVIVED THE SCOPE MIGRATION'
                : 'CAPABILITY GRANTS NEED ATTENTION'}
            </b>
            <p style={{ margin: '6px 0 0' }}>{health.explanation}</p>
            <p style={{ margin: '6px 0 0', fontSize: 12 }}>
              {Object.entries(health.grants_by_scope)
                .map(([k, v]) => v + ' ' + k.replace(/_/g, ' '))
                .join(' · ') || 'no capability grants exist yet'}
              {health.active_customer_grants_sampled > 0
                ? ' · ' + health.active_customer_grants_still_resolving + ' of ' +
                  health.active_customer_grants_sampled +
                  ' sampled customer grants re-resolved through the live permission gate'
                : ''}
            </p>
          </div>
        ) : null}

        <div className="go-fields">
          <div className="go-field">
            <label>Sales organization</label>
            <select value={brand} onChange={e => setBrand(e.target.value)}>
              <option value="">Choose a sales organization…</option>
              {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
            </select>
          </div>
        </div>

        {err ? <div className="go-note err" style={{ marginTop: 12 }}>{err}</div> : null}
      </div>

      {d ? (
        !d.people.length ? (
          <Empty>Nobody is a member of this sales organization yet.</Empty>
        ) : (
          <table className="go-table">
            <thead>
              <tr>
                <th>Person</th><th>In this brand</th>
                {d.available_capabilities.map(c => (
                  <th key={c.key} style={{ textAlign: 'center' }}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.people.map(p => (
                <tr key={p.user_id}>
                  <td data-label="Person">
                    {p.name}
                    <div style={{ fontSize: 11, color: 'var(--go-dim)' }}>{p.email}</div>
                  </td>
                  <td data-label="In this brand">
                    {p.is_brand_member
                      ? <span className="go-badge">sales member</span>
                      : <span className="go-badge new">not a sales member</span>}
                  </td>
                  {d.available_capabilities.map(c => (
                    <td key={c.key} data-label={c.label} style={{ textAlign: 'center' }}>
                      <input type="checkbox"
                             checked={p.capabilities.includes(c.key)}
                             disabled={busy === p.user_id + c.key}
                             onChange={() => toggle(p, c.key)} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}

      {d ? (
        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          {d.available_capabilities.map(c => (
            <p key={c.key} style={{ margin: '0 0 8px', fontSize: 12,
                                    color: 'var(--go-dim)' }}>
              <strong>{c.label}</strong> — {c.why}
            </p>
          ))}
          <p style={{ margin: 0, fontSize: 12, color: 'var(--go-dim)' }}>
            A grant applies to this brand only, and grants nothing else — not a
            sales team, not another brand, and not access to any customer
            workspace. Every change is audited.
          </p>
        </div>
      ) : null}
    </Panel>
  )
}


/* ── audit ───────────────────────────────────────────────────────────────── */

function AuditPanel() {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [open, setOpen] = useState({})

  useEffect(() => {
    api.get('/god/pricing/audit?limit=50', { noOrgContext: true })
      .then(r => setRows(r.entries || []))
      .catch(e => setErr(errText(e)))
  }, [])

  if (err) return <Panel title="Change history"><div className="go-note err">{err}</div></Panel>
  if (rows === null) return <Panel title="Change history"><Empty>Loading…</Empty></Panel>

  return (
    <Panel title="Change history" count={rows.length}>
      {!rows.length ? (
        <Empty>Nothing has been changed here yet.</Empty>
      ) : (
        <ul className="go-tl">
          {rows.map(r => (
            <li key={r.id}>
              <div className="go-row-between">
                <div>
                  <strong>{r.action.replace(/[._]/g, ' ')}</strong>
                  {' '}by <span className="who">{r.actor_name}</span>
                  <div className="when">{whenExact(r.created_at)}{r.note ? ' · ' + r.note : ''}</div>
                </div>
                <button className="go-btn sm ghost"
                        onClick={() => setOpen({ ...open, [r.id]: !open[r.id] })}>
                  {open[r.id] ? 'Hide' : 'Before / after'}
                </button>
              </div>
              {open[r.id] ? (
                <div style={{ display: 'grid', gap: 10, marginTop: 10,
                              gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))' }}>
                  <div>
                    <div className="go-label">Before</div>
                    <pre className="go-code" style={{ whiteSpace: 'pre-wrap', fontSize: 11,
                                                      padding: 8, borderRadius: 6, margin: 0 }}>
                      {r.before ? JSON.stringify(typeof r.before === 'string'
                        ? JSON.parse(r.before) : r.before, null, 1) : 'did not exist'}
                    </pre>
                  </div>
                  <div>
                    <div className="go-label">After</div>
                    <pre className="go-code" style={{ whiteSpace: 'pre-wrap', fontSize: 11,
                                                      padding: 8, borderRadius: 6, margin: 0 }}>
                      {r.after ? JSON.stringify(typeof r.after === 'string'
                        ? JSON.parse(r.after) : r.after, null, 1) : '—'}
                    </pre>
                  </div>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

/* ── the screen ──────────────────────────────────────────────────────────── */

export default function GodPricingCompensation() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [brand, setBrand] = useState('')

  const load = useCallback(async () => {
    try {
      const qs = brand ? '?brand_sales_org_id=' + encodeURIComponent(brand) : ''
      const r = await api.get('/god/pricing/overview' + qs, { noOrgContext: true })
      setData(r); setErr('')
    } catch (e) { setErr(errText(e)) }
  }, [brand])

  useEffect(() => { load() }, [load])

  if (err && !data) return <div className="go-scope"><div className="go-note err">{err}</div></div>
  if (!data) return <div className="go-scope"><div className="go-empty">Loading…</div></div>

  const unconfigured = (data.packages || []).filter(p => !p.compensation_configured)
  const plans = data.plans || []

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god')}>← Command Center</button>
          <h1 style={{ marginTop: 8 }}>Pricing &amp; compensation</h1>
          <p>
            The discount floors a salesperson works within, and what the team
            earns when a deal closes. Both are commercial decisions, so both are
            editable here rather than in the database.
          </p>
        </div>
      </div>

      {err ? <div className="go-note err">{err}</div> : null}

      <div className="go-filters">
        <select value={brand} onChange={e => { setBrand(e.target.value); setData(null) }}>
          <option value="">All sales organizations</option>
          {(data.brands || []).map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
      </div>

      {unconfigured.length ? (
        <div className="go-note warn">
          <strong>{unconfigured.length} package{unconfigured.length === 1 ? '' : 's'} pay
          no commission because nobody has decided one yet:</strong>{' '}
          {unconfigured.map(p => p.name).join(', ')}.
          <div style={{ marginTop: 6, fontSize: 12 }}>
            These are shown as UNCONFIGURED rather than $0 everywhere in the
            product. $0 would read as a deliberate no-commission package; this
            reads as an open decision, which is what it is.
          </div>
        </div>
      ) : null}

      <PoliciesPanel data={data} onSaved={load} />

      <div className="go-note">
        Editing a plan changes what FUTURE deals earn. It cannot change what a
        past one earned — every commission entry carries the rate that produced
        it, so nothing on this screen reaches a payout that already exists.
      </div>

      <NewPlan data={data} onSaved={load} />

      {!plans.length ? (
        <Panel title="Compensation plans">
          <Empty>
            No compensation plan exists. Nothing is projected on the pipeline and
            nothing can be earned until one does.
          </Empty>
        </Panel>
      ) : (
        plans.map(p => <PlanCard key={p.id} plan={p} data={data} onSaved={load} />)
      )}

      <SeedPanel data={data} onSaved={load} />

      <BrandAccessPanel data={data} />

      <AuditPanel />

      <style>{`
        .go-scope .pc-none { color: var(--go-dim); font-style: italic; }
      `}</style>
    </div>
  )
}
