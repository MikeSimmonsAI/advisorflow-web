/**
 * God Mode — Provision Customer review (Checkpoint 6 §6, §7).
 *
 * The last screen before a customer tenant exists. It shows what was sold, who
 * sold it, and what was agreed, and it lets the operator correct the CUSTOMER
 * ORGANISATION's details before creating it.
 *
 * WHAT IS EDITABLE AND WHAT IS NOT, ON PURPOSE.
 * Editable: the tenant's name, slug, industry, timezone, phone, address, plan,
 * target launch date, implementation owner, notes, and which milestones apply.
 * Read-only: everything from the sale. The opportunity's company name, the
 * accepted proposal and the agreed amounts are history, and correcting a
 * spelling for the customer's account must not rewrite what the buyer signed.
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Panel, Fact, Empty, money, when, errText } from './god/GodOpsShared'
import './god/GodOps.css'

export default function GodProvision() {
  const { oppId } = useParams()
  const nav = useNavigate()
  const [rev, setRev] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [owners, setOwners] = useState([])
  const [form, setForm] = useState(null)
  const [skipped, setSkipped] = useState({})

  useEffect(() => {
    api.get('/god/ops/opportunities/' + oppId + '/provisioning-review')
      .then(r => {
        setRev(r)
        setForm({
          org_name: r.suggested_org_name || '',
          slug: r.suggested_slug || '',
          industry: r.suggested_industry || '',
          timezone: r.suggested_timezone || '',
          org_phone: r.contact_phone || '',
          org_address: '',
          plan: 'standard',
          target_launch_date: '',
          owner_user_id: '',
          notes: '',
        })
      })
      .catch(e => setErr(errText(e)))
    // /god/ops/staff, not /god/users: the latter lists god/super/org admins,
    // which is mostly CUSTOMER administrators and almost none of the internal
    // people who do implementations.
    api.get('/god/ops/staff').then(r => setOwners(r.staff || []))
      .catch(() => setOwners([]))
  }, [oppId])

  if (err) return <div className="go-scope"><div className="go-note err">{err}</div></div>
  if (!rev || !form) return <div className="go-scope"><div className="go-empty">Loading…</div></div>

  if (rev.already_provisioned) {
    return (
      <div className="go-scope">
        <div className="go-head"><div>
          <button className="go-back" onClick={() => nav('/god/sales-operations')}>← Sales Operations</button>
          <h1 style={{ marginTop: 8 }}>Already provisioned</h1>
        </div></div>
        <div className="go-note ok">
          This deal became <strong>{rev.existing.organization_name}</strong> on{' '}
          {when(rev.existing.created_at)}. Provisioning is idempotent — asking again
          returns this same customer rather than creating a second one.
        </div>
        <button className="go-btn"
                onClick={() => nav('/god/implementations/' + rev.existing.implementation_id)}>
          Open the implementation
        </button>
      </div>
    )
  }

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))
  const template = rev.milestone_template || []

  async function submit() {
    setBusy(true); setErr('')
    try {
      const keys = template.filter(m => !skipped[m.key]).map(m => m.key)
      const body = {
        org_name: form.org_name.trim(),
        slug: form.slug.trim() || null,
        industry: form.industry.trim() || null,
        timezone: form.timezone.trim() || null,
        org_phone: form.org_phone.trim() || null,
        org_address: form.org_address.trim() || null,
        plan: form.plan,
        target_launch_date: form.target_launch_date
          ? new Date(form.target_launch_date + 'T12:00:00').toISOString() : null,
        owner_user_id: form.owner_user_id || null,
        notes: form.notes.trim() || null,
        milestone_keys: keys.length === template.length ? null : keys,
      }
      const r = await api.post('/god/ops/opportunities/' + oppId + '/provision', body)
      nav('/god/implementations/' + r.implementation.implementation_id)
    } catch (e) {
      setErr(errText(e))
      setBusy(false)
    }
  }

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/sales-operations')}>← Sales Operations</button>
          <h1 style={{ marginTop: 8 }}>Provision customer</h1>
          <p>Creating a real customer organisation on the{' '}
             <strong>{rev.platform ? rev.platform.name : 'unknown'}</strong> platform.
             This cannot be undone from this screen.</p>
        </div>
      </div>

      {err ? <div className="go-note err">{err}</div> : null}
      {!rev.is_won ? (
        <div className="go-note warn">This opportunity is not Won. Provisioning will be refused.</div>
      ) : null}

      <Panel title="The sale — read only">
        <div className="go-body">
          <div className="go-facts">
            <Fact k="Company (as sold)" v={rev.suggested_org_name} />
            <Fact k="Primary contact" v={rev.contact_name} />
            <Fact k="Contact email" v={rev.contact_email} />
            <Fact k="Contact phone" v={rev.contact_phone} />
            <Fact k="Website" v={rev.website} />
            <Fact k="Platform / brand"
                  v={(rev.platform ? rev.platform.name : '—') + ' · ' +
                     (rev.brand_sales_org ? rev.brand_sales_org.name : '—')} />
            <Fact k="Salesperson" v={rev.salesperson ? rev.salesperson.name : null} />
            <Fact k="Won" v={when(rev.won_at)} />
            <Fact k="Package" v={rev.package ? rev.package.name : null} />
            <Fact k="Deal value"
                  v={rev.deal_value !== null ? money(rev.deal_value) +
                     (rev.deal_value_was_overridden ? ' (set by a manager)' : '') : null} />
            <Fact k="Accepted proposal"
                  v={rev.accepted_proposal
                     ? rev.accepted_proposal.number + ' v' + rev.accepted_proposal.version +
                       ' · ' + money(rev.accepted_proposal.final_amount, rev.accepted_proposal.currency)
                     : null} />
            <Fact k="Accepted on"
                  v={rev.accepted_proposal ? when(rev.accepted_proposal.accepted_at) : null} />
          </div>

          {rev.accepted_proposal && rev.accepted_proposal.implementation_plan ? (
            <div style={{ marginTop: 14 }}>
              <div className="go-fact">
                <div className="k">Implementation plan, as proposed</div>
                <div className="v" style={{ whiteSpace: 'pre-wrap' }}>
                  {rev.accepted_proposal.implementation_plan}
                </div>
              </div>
            </div>
          ) : null}

          {rev.discovery ? (
            <div style={{ marginTop: 14 }}>
              <div className="go-facts">
                {Object.keys(rev.discovery).map(k => (
                  <Fact key={k} k={rev.discovery[k].label} v={rev.discovery[k].value} />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Panel>

      <Panel title="The customer organisation — you may correct this">
        <div className="go-body">
          <div className="go-fields">
            <div className="go-field full">
              <label>Organisation name</label>
              <input value={form.org_name} onChange={e => set('org_name', e.target.value)} />
              <div className="hint">
                Changing this names the customer's tenant. It does not rewrite the
                opportunity, the proposal, or anything else in the sales record.
              </div>
            </div>
            <div className="go-field">
              <label>Slug</label>
              <input value={form.slug} onChange={e => set('slug', e.target.value)} />
              <div className="hint">Unique across every platform. Left blank, one is derived.</div>
            </div>
            <div className="go-field">
              <label>Industry</label>
              <input value={form.industry} onChange={e => set('industry', e.target.value)} />
            </div>
            <div className="go-field">
              <label>Timezone</label>
              <input value={form.timezone} onChange={e => set('timezone', e.target.value)}
                     placeholder="America/Chicago" />
            </div>
            <div className="go-field">
              <label>Plan</label>
              <select value={form.plan} onChange={e => set('plan', e.target.value)}>
                <option value="trial">trial</option>
                <option value="standard">standard</option>
                <option value="enterprise">enterprise</option>
              </select>
            </div>
            <div className="go-field">
              <label>Phone</label>
              <input value={form.org_phone} onChange={e => set('org_phone', e.target.value)} />
            </div>
            <div className="go-field">
              <label>Address</label>
              <input value={form.org_address} onChange={e => set('org_address', e.target.value)} />
            </div>
            <div className="go-field">
              <label>Target launch date</label>
              <input type="date" value={form.target_launch_date}
                     onChange={e => set('target_launch_date', e.target.value)} />
            </div>
            <div className="go-field">
              <label>Implementation owner</label>
              <select value={form.owner_user_id} onChange={e => set('owner_user_id', e.target.value)}>
                <option value="">assign later</option>
                {owners.filter(u => u.is_active !== false).map(u => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </select>
              <div className="hint">
                Not defaulted to the salesperson. Selling and implementing are different jobs.
              </div>
            </div>
            <div className="go-field full">
              <label>Handoff notes</label>
              <textarea rows={3} value={form.notes} onChange={e => set('notes', e.target.value)}
                        placeholder="Anything the implementation team needs that is not already above." />
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="Onboarding milestones" count={template.filter(m => !skipped[m.key]).length}>
        {!template.length ? <Empty>No milestone template for this package.</Empty> : (
          <ul className="go-ms">
            {template.map(m => (
              <li key={m.key}>
                <input type="checkbox" checked={!skipped[m.key]}
                       onChange={e => setSkipped(s => ({ ...s, [m.key]: !e.target.checked }))} />
                <div className="lab">
                  {m.label}{m.required ? <span className="go-badge warn" style={{ marginLeft: 8 }}>required</span> : null}
                  <small>{m.description}</small>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--go-dim)' }}>
            Derived from the package that was sold. Uncheck anything this customer
            does not need — you can add more later.
          </p>
        </div>
      </Panel>

      <div className="go-actions" style={{ marginBottom: 30 }}>
        <button className="go-btn go" disabled={busy || !form.org_name.trim()} onClick={submit}>
          {busy ? 'Creating…' : 'Create customer organisation'}
        </button>
        <button className="go-btn ghost" onClick={() => nav('/god/sales-operations')}>Cancel</button>
      </div>
    </div>
  )
}
