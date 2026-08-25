/**
 * Opportunity Detail — the salesperson's working record for the deal.
 *
 * Not a contact card. This is where discovery is captured, the demo build is
 * tracked, the package is chosen, the value is derived (or overridden, with a
 * reason), the stage is moved, and the whole history is readable in one place.
 *
 * Every write goes to the real API and the timeline reloads from the server —
 * the record on screen is always what the database actually holds.
 */
import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import {
  Card, Chip, Info, Empty, NotBuilt, ErrorBar,
  money, dateTime, dueLabel,
} from './parts'

const DEMO_STATUSES = [
  ['', '—'],
  ['not_requested', 'Not requested'],
  ['requested', 'Requested'],
  ['in_progress', 'In progress'],
  ['ready', 'Ready'],
  ['delivered', 'Delivered'],
]

/** The continuous lifecycle, rendered from real timestamps on the record. */
function Lifecycle({ opp }) {
  const L = opp.lifecycle || {}
  const steps = [
    ['Prospect created', L.created_at],
    ['Contacted', L.contacted_at],
    ['Discovery completed', L.discovery_completed_at],
    ['Demo requested', L.demo_requested_at],
    ['Demo ready', L.demo_ready_at],
    ['Proposal sent', L.proposal_sent_at],
    ['Won', L.won_at],
    ['Handoff / onboarding', opp.customer_organization_id ? L.won_at : null],
  ]
  return (
    <div className="sw-life">
      {steps.map(([label, at]) => (
        <div key={label} className={'sw-life-step ' + (at ? 'sw-done' : 'sw-pending')}>
          <b>{label.toUpperCase()}</b>
          <small>{at ? dateTime(at) : 'Not yet'}</small>
        </div>
      ))}
    </div>
  )
}

function Discovery({ opp, onSave, saving }) {
  const [vals, setVals] = useState({})
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    const seed = {}
    ;(opp.discovery_fields || []).forEach(f => {
      seed[f.key] = (opp.discovery && opp.discovery[f.key]) || ''
    })
    setVals(seed)
    setDirty(false)
  }, [opp.id, opp.discovery])

  function set(k, v) { setVals(s => ({ ...s, [k]: v })); setDirty(true) }

  const completed = opp.discovery?.completed_at

  return (
    <Card
      title="DISCOVERY"
      sub={completed
        ? 'Completed ' + dateTime(completed)
            + (opp.discovery.completed_by_name ? ' by ' + opp.discovery.completed_by_name : '')
        : 'Structured, because these answers feed the demo build'}
      right={completed ? <Chip tone="green">Complete</Chip> : <Chip tone="amber">In progress</Chip>}
    >
      {(opp.discovery_fields || []).map(f => (
        <div className="sw-field" key={f.key}>
          <label>{f.label.toUpperCase()}</label>
          {f.key === 'team_size'
            ? <input className="sw-input" value={vals[f.key] || ''}
                     onChange={e => set(f.key, e.target.value)} />
            : <textarea className="sw-textarea" value={vals[f.key] || ''}
                        onChange={e => set(f.key, e.target.value)} />}
        </div>
      ))}
      <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
        <button className="sw-btn" disabled={!dirty || saving}
                onClick={() => onSave(vals, false)}>
          {saving ? 'Saving…' : 'Save discovery'}
        </button>
        {!completed && (
          <button className="sw-btn sw-primary" disabled={saving}
                  onClick={() => onSave(vals, true)}>
            Save &amp; mark complete
          </button>
        )}
      </div>
    </Card>
  )
}

function DemoBuild({ opp, team, onPatch, saving }) {
  const d = opp.demo || {}
  const [form, setForm] = useState({})
  useEffect(() => {
    setForm({
      demo_status: d.status || '',
      demo_owner_user_id: d.owner_user_id || '',
      demo_due_at: d.due_at ? String(d.due_at).slice(0, 10) : '',
      demo_requirements: d.requirements || '',
      demo_url: d.url || '',
      demo_notes: d.notes || '',
    })
  }, [opp.id, d.status, d.owner_user_id, d.due_at, d.requirements, d.url, d.notes])

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  function save() {
    const body = { ...form }
    body.demo_due_at = form.demo_due_at ? new Date(form.demo_due_at).toISOString() : null
    if (!body.demo_status) delete body.demo_status
    if (!body.demo_owner_user_id) body.demo_owner_user_id = null
    onPatch(body)
  }

  return (
    <Card title="DEMO BUILD"
          sub="See whether your demo is being built without asking anyone"
          right={d.status ? <Chip tone={d.status === 'ready' ? 'green' : 'amber'}>{d.status}</Chip> : null}>
      <div className="sw-grid-even">
        <div className="sw-field">
          <label>STATUS</label>
          <select className="sw-select" value={form.demo_status || ''}
                  onChange={e => set('demo_status', e.target.value)}>
            {DEMO_STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </div>
        <div className="sw-field">
          <label>BUILDER</label>
          <select className="sw-select" value={form.demo_owner_user_id || ''}
                  onChange={e => set('demo_owner_user_id', e.target.value)}>
            <option value="">Unassigned</option>
            {team.map(t => <option key={t.id} value={t.id}>{t.full_name}</option>)}
          </select>
        </div>
        <div className="sw-field">
          <label>TARGET COMPLETION</label>
          <input className="sw-input" type="date" value={form.demo_due_at || ''}
                 onChange={e => set('demo_due_at', e.target.value)} />
        </div>
        <div className="sw-field">
          <label>DEMO URL</label>
          <input className="sw-input" value={form.demo_url || ''}
                 placeholder="set when the environment exists"
                 onChange={e => set('demo_url', e.target.value)} />
        </div>
      </div>
      <div className="sw-field">
        <label>REQUIREMENTS</label>
        <textarea className="sw-textarea" value={form.demo_requirements || ''}
                  onChange={e => set('demo_requirements', e.target.value)} />
      </div>
      <div className="sw-field">
        <label>INTERNAL NOTES</label>
        <textarea className="sw-textarea" value={form.demo_notes || ''}
                  onChange={e => set('demo_notes', e.target.value)} />
      </div>
      <div className="sw-subtle sw-mt">
        Requested {d.requested_at ? dateTime(d.requested_at) : '—'}
        {d.ready_at ? ' · Ready ' + dateTime(d.ready_at) : ''}
      </div>
      <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
        <button className="sw-btn sw-primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save demo build'}
        </button>
      </div>
    </Card>
  )
}

function PackageDeal({ opp, packages, onPatch, saving }) {
  const [pkgId, setPkgId] = useState(opp.selected_package_id || '')
  const [interestId, setInterestId] = useState(opp.package_interest_id || '')
  const [value, setValue] = useState(opp.deal_value != null ? String(opp.deal_value) : '')
  const [reason, setReason] = useState('')

  useEffect(() => {
    setPkgId(opp.selected_package_id || '')
    setInterestId(opp.package_interest_id || '')
    setValue(opp.deal_value != null ? String(opp.deal_value) : '')
    setReason('')
  }, [opp.id, opp.selected_package_id, opp.package_interest_id, opp.deal_value])

  const selected = packages.find(p => p.id === pkgId)
  const derived = selected && selected.price != null ? Number(selected.price) : null
  const isOverride = value !== '' && derived != null && Math.abs(Number(value) - derived) > 0.005
  const needsReason = (isOverride || (value !== '' && derived == null)) && !opp.deal_value_override

  return (
    <Card title="PACKAGE &amp; DEAL VALUE"
          sub="Value derives from the package; an override is recorded and audited">
      <div className="sw-grid-even">
        <div className="sw-field">
          <label>PACKAGE INTEREST</label>
          <select className="sw-select" value={interestId}
                  onChange={e => { setInterestId(e.target.value); onPatch({ package_interest_id: e.target.value || null }) }}>
            <option value="">Not yet known</option>
            {packages.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <div className="sw-field">
          <label>SELECTED PACKAGE</label>
          <select className="sw-select" value={pkgId}
                  onChange={e => { setPkgId(e.target.value); onPatch({ selected_package_id: e.target.value || null }) }}>
            <option value="">None selected</option>
            {packages.map(p => (
              <option key={p.id} value={p.id}>
                {p.name}{p.price != null ? ' · $' + Number(p.price).toLocaleString() : ' · custom'}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="sw-field">
        <label>DEAL VALUE {derived != null && <span style={{ fontWeight: 400 }}>(derived {money(derived)})</span>}</label>
        <input className="sw-input" type="number" step="0.01" value={value}
               onChange={e => setValue(e.target.value)} />
      </div>

      {needsReason && (
        <div className="sw-field">
          <label>OVERRIDE REASON (REQUIRED)</label>
          <input className="sw-input" value={reason} onChange={e => setReason(e.target.value)}
                 placeholder="Why is this different from the package price?" />
          {!opp.can_override_value && (
            <div className="sw-subtle" style={{ marginTop: 6 }}>
              Only a sales manager can override the derived value.
            </div>
          )}
        </div>
      )}

      {opp.deal_value_override && (
        <div className="sw-notbuilt sw-mt">
          <b>VALUE OVERRIDDEN</b>
          <p>
            {opp.deal_value_override_by_name || 'A manager'} set this value
            {opp.deal_value_override_at ? ' on ' + dateTime(opp.deal_value_override_at) : ''}.
            {opp.deal_value_override_reason ? ' Reason: ' + opp.deal_value_override_reason : ''}
          </p>
        </div>
      )}

      <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
        <button className="sw-btn sw-primary" disabled={saving || (needsReason && !reason.trim())}
                onClick={() => onPatch({
                  deal_value: value === '' ? null : Number(value),
                  deal_value_override_reason: reason.trim() || undefined,
                })}>
          {saving ? 'Saving…' : 'Save value'}
        </button>
      </div>
    </Card>
  )
}

export default function OpportunityDetail() {
  const { oppId } = useParams()
  const nav = useNavigate()
  const [opp, setOpp] = useState(null)
  const [packages, setPackages] = useState([])
  const [team, setTeam] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [note, setNote] = useState('')
  const [nextAction, setNextAction] = useState('')
  const [nextDue, setNextDue] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const o = await api.get('/sales/opportunities/' + oppId)
      setOpp(o)
      setNextAction(o.next_action || '')
      setNextDue(o.next_action_due_at ? String(o.next_action_due_at).slice(0, 10) : '')
    } catch (e) {
      setError(e.message || 'Could not load this opportunity.')
    } finally { setLoading(false) }
  }, [oppId])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.get('/sales/packages').then(setPackages).catch(() => setPackages([]))
    api.get('/sales/team').then(setTeam).catch(() => setTeam([]))
  }, [])

  async function patch(body) {
    setSaving(true); setError(null)
    try { setOpp(await api.patch('/sales/opportunities/' + oppId, body)) }
    catch (e) { setError(e.message || 'Save failed.') }
    finally { setSaving(false) }
  }

  async function saveDiscovery(vals, complete) {
    setSaving(true); setError(null)
    try { setOpp(await api.put('/sales/opportunities/' + oppId + '/discovery',
                               { ...vals, mark_complete: !!complete })) }
    catch (e) { setError(e.message || 'Save failed.') }
    finally { setSaving(false) }
  }

  async function addNote() {
    if (!note.trim()) return
    setSaving(true)
    try { await api.post('/sales/opportunities/' + oppId + '/notes', { summary: note.trim() }); setNote(''); await load() }
    catch (e) { setError(e.message || 'Could not add the note.') }
    finally { setSaving(false) }
  }

  if (loading && !opp) {
    return <SalesShell title="Opportunity"><div className="sw-subtle">Loading…</div></SalesShell>
  }
  if (!opp) {
    return (
      <SalesShell title="Opportunity">
        <ErrorBar error={error} onRetry={load} />
        <div className="sw-card">
          <Empty title="Not available">
            This opportunity does not exist, or it belongs to another representative.
          </Empty>
        </div>
      </SalesShell>
    )
  }

  const due = dueLabel(opp.next_action_due_at)

  return (
    <SalesShell
      title={opp.company_name}
      subtitle={[opp.contact_name, opp.industry].filter(Boolean).join(' · ') || 'Opportunity record'}
      actions={
        <>
          <button className="sw-btn" onClick={() => nav('/sales/pipeline')}>← Pipeline</button>
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      <div className="sw-card">
        <div className="sw-card-b sw-head">
          <div>
            <div className="sw-chips" style={{ marginBottom: 8 }}>
              <Chip tone="green">{opp.stage_label}</Chip>
              {opp.owner_name && <Chip>Owner: {opp.owner_name}</Chip>}
              {opp.attention && <Chip tone="amber">{opp.attention}</Chip>}
              {opp.days_in_stage != null && <Chip>{opp.days_in_stage}d in stage</Chip>}
            </div>
            <h2>{opp.company_name}</h2>
            <p>{[opp.contact_name, opp.industry, opp.source].filter(Boolean).join(' · ')}</p>
          </div>
          <div className="sw-chips">
            <StageMover opp={opp} onMove={s => patch({ stage: s })} saving={saving} />
          </div>
        </div>
      </div>

      <div className="sw-mt sw-grid2">
        <div>
          <Card title="RECORD">
            <div className="sw-infogrid">
              <Info label="PHONE" value={opp.phone} />
              <Info label="EMAIL" value={opp.email} />
              <Info label="WEBSITE" value={opp.website} />
              <Info label="TIMEZONE" value={opp.timezone} />
              <Info label="SALES OWNER" value={opp.owner_name} />
              <Info label="BRAND" value={opp.brand_sales_org?.name} />
              <Info label="PACKAGE INTEREST" value={opp.package_interest?.name} />
              <Info label="SELECTED PACKAGE" value={opp.selected_package?.name} />
              <Info label="DEAL VALUE" value={opp.deal_value != null ? money(opp.deal_value) : null} />
            </div>

            <div className="sw-grid-even sw-mt">
              <div className="sw-field">
                <label>NEXT ACTION</label>
                <input className="sw-input" value={nextAction}
                       onChange={e => setNextAction(e.target.value)} />
              </div>
              <div className="sw-field">
                <label>DUE {due.text && <Chip tone={due.tone}>{due.text}</Chip>}</label>
                <input className="sw-input" type="date" value={nextDue}
                       onChange={e => setNextDue(e.target.value)} />
              </div>
            </div>
            <div className="sw-flex" style={{ justifyContent: 'flex-end', marginTop: 10 }}>
              <button className="sw-btn" disabled={saving}
                      onClick={() => patch({
                        next_action: nextAction,
                        next_action_due_at: nextDue ? new Date(nextDue).toISOString() : null,
                      })}>Save next action</button>
            </div>
          </Card>

          <div className="sw-mt">
            <Card title="LIFECYCLE" sub="One continuous record — never re-created to change stage">
              <Lifecycle opp={opp} />
              {opp.customer_organization_id
                ? <div className="sw-subtle sw-mt">
                    Customer organization: {opp.customer_organization_id}
                  </div>
                : <div className="sw-subtle sw-mt">
                    No customer organization yet — provisioning happens when the deal is Won.
                  </div>}
            </Card>
          </div>

          <div className="sw-mt"><Discovery opp={opp} onSave={saveDiscovery} saving={saving} /></div>
          <div className="sw-mt"><DemoBuild opp={opp} team={team} onPatch={patch} saving={saving} /></div>
          <div className="sw-mt">
            <PackageDeal opp={opp} packages={packages} onPatch={patch} saving={saving} />
          </div>
        </div>

        <div>
          <Card title="NEXT MEETING" sub="Scheduling">
            <NotBuilt label="SCHEDULING NOT BUILT YET" block={opp.scheduling} />
          </Card>

          <div className="sw-mt">
            <Card title="ADD TO TIMELINE" sub="Logged against this record, permanently">
              <div className="sw-field">
                <input className="sw-input" value={note} placeholder="What happened?"
                       onChange={e => setNote(e.target.value)}
                       onKeyDown={e => { if (e.key === 'Enter') addNote() }} />
              </div>
              <div className="sw-flex" style={{ justifyContent: 'flex-end', marginTop: 10 }}>
                <button className="sw-btn sw-primary" onClick={addNote}
                        disabled={saving || !note.trim()}>Add note</button>
              </div>
            </Card>
          </div>

          <div className="sw-mt">
            <Card title="ACTIVITY" sub="Append-only — corrections are new entries" bodyless>
              {opp.timeline?.length
                ? <div className="sw-card-b">
                    <div className="sw-timeline">
                      {opp.timeline.map(e => (
                        <div className="sw-event" key={e.id}>
                          <b>{e.summary}</b>
                          <p>
                            {dateTime(e.occurred_at)}
                            {e.actor_name ? ' · ' + e.actor_name : ''}
                            {e.detail ? ' · ' + e.detail : ''}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                : <Empty title="No activity yet" />}
            </Card>
          </div>
        </div>
      </div>
    </SalesShell>
  )
}

/** Stage control. Options come from /sales/me so the client never hardcodes the lifecycle. */
function StageMover({ opp, onMove, saving }) {
  const [stages, setStages] = useState([])
  useEffect(() => {
    api.get('/sales/me').then(me => setStages(me.stages || [])).catch(() => setStages([]))
  }, [])
  return (
    <select className="sw-select" style={{ width: 200 }} value={opp.stage}
            disabled={saving || !stages.length}
            onChange={e => onMove(e.target.value)}>
      {stages.length
        ? stages.map(s => <option key={s.key} value={s.key}>{s.label}</option>)
        : <option value={opp.stage}>{opp.stage_label}</option>}
      <option value="lost">Lost</option>
    </select>
  )
}
