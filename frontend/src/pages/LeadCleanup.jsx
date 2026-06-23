import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './LeadCleanup.css'

function nameForLead(lead) {
  const name = `${lead.first_name || ''} ${lead.last_name || ''}`.trim()
  return name || 'Unnamed lead'
}

function statusLabel(value) {
  return value ? value.replaceAll('_', ' ') : 'unknown'
}

export default function LeadCleanup() {
  const [groups, setGroups] = useState([])
  const [selectedKeepByGroup, setSelectedKeepByGroup] = useState({})
  const [mergeResults, setMergeResults] = useState(null)
  const [fixLeadId, setFixLeadId] = useState('')
  const [fixLeadName, setFixLeadName] = useState('') // display-only, shown above the form once a lead is picked
  const [fixFirstName, setFixFirstName] = useState('')
  const [fixLastName, setFixLastName] = useState('')
  const [fixPhone, setFixPhone] = useState('')
  const [fixEmail, setFixEmail] = useState('')
  const [fixResult, setFixResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Per-group assignment — lets an admin route an entire duplicate cluster
  // to one advisor in a single action, same /admin/leads/reassign endpoint
  // the main Leads page and Lead Detail page already use.
  const currentUser = getCurrentUser()
  const canBulkAssign = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [assignTargetByGroup, setAssignTargetByGroup] = useState({})
  const [assigningGroupKey, setAssigningGroupKey] = useState('')
  const [assignResult, setAssignResult] = useState(null)

  useEffect(() => {
    if (!canBulkAssign) return
    api.get('/admin/users')
      .then((users) => setAssignableUsers(users.filter((u) => u.is_active && (u.role === 'advisor' || u.role === 'org_admin'))))
      .catch(() => {})
  }, [canBulkAssign])

  const fixPanelRef = useRef(null)

  // The actual fix for "I click on somebody and can't change anything
  // about that person" - this loads the clicked lead's current values
  // straight into the Fix Contact Info form and scrolls it into view,
  // instead of the lead name just being a dead-end link to Lead Detail
  // (which has no contact editing) while the real fix form sat in a
  // separate panel requiring a manually pasted Lead ID.
  function startFixingLead(lead) {
    setFixLeadId(lead.id)
    setFixLeadName(nameForLead(lead))
    setFixFirstName(lead.first_name || '')
    setFixLastName(lead.last_name || '')
    setFixPhone(lead.phone || '')
    setFixEmail(lead.email || '')
    setFixResult(null)
    fixPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  function clearFixForm() {
    setFixLeadId('')
    setFixLeadName('')
    setFixFirstName('')
    setFixLastName('')
    setFixPhone('')
    setFixEmail('')
  }

  const totalPotentialLeads = useMemo(() => {
    const ids = new Set()
    groups.forEach((group) => group.leads.forEach((lead) => ids.add(lead.id)))
    return ids.size
  }, [groups])

  async function loadPotentialDuplicates() {
    setLoading(true)
    setError('')
    try {
      const data = await api.get('/admin/leads/potential-duplicates')
      setGroups(data || [])
      setSelectedKeepByGroup((current) => {
        const next = { ...current }
        ;(data || []).forEach((group, index) => {
          const key = groupKey(group, index)
          if (!next[key] && group.leads?.[0]?.id) next[key] = group.leads[0].id
        })
        return next
      })
    } catch (err) {
      setError(err.message || 'Could not load potential duplicates.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadPotentialDuplicates()
  }, [])

  function groupKey(group, index) {
    return `${group.match_type}:${group.match_key}:${index}`
  }

  async function mergeGroup(group, index) {
    const key = groupKey(group, index)
    const keepLeadId = selectedKeepByGroup[key]
    const mergeLeadIds = group.leads.map((lead) => lead.id).filter((id) => id !== keepLeadId)

    if (!keepLeadId || mergeLeadIds.length === 0) {
      setError('Pick one lead to keep and at least one lead to merge.')
      return
    }

    const keepLead = group.leads.find((lead) => lead.id === keepLeadId)
    const confirmed = window.confirm(
      `Merge ${mergeLeadIds.length} lead(s) into ${nameForLead(keepLead)}? This deletes the merged lead records after moving their history.`,
    )
    if (!confirmed) return

    setBusy(true)
    setError('')
    setMergeResults(null)
    try {
      const result = await api.post('/admin/leads/merge', {
        keep_lead_id: keepLeadId,
        merge_lead_ids: mergeLeadIds,
      })
      setMergeResults(result)
      await loadPotentialDuplicates()
    } catch (err) {
      setError(err.message || 'Merge failed.')
    } finally {
      setBusy(false)
    }
  }

  async function assignGroup(group, index) {
    const key = groupKey(group, index)
    const targetId = assignTargetByGroup[key] || ''
    const leadIds = group.leads.map((lead) => lead.id)

    setAssigningGroupKey(key)
    setError('')
    setAssignResult(null)
    try {
      const result = await api.post('/admin/leads/reassign', {
        lead_ids: leadIds,
        new_assigned_to_id: targetId || null,
      })
      setAssignResult({ ...result, groupKey: key })
    } catch (err) {
      setError(err.message || 'Assignment failed.')
    } finally {
      setAssigningGroupKey('')
    }
  }

  async function fixContactInfo(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setFixResult(null)

    const payload = {}
    if (fixPhone.trim()) payload.phone = fixPhone.trim()
    if (fixEmail.trim()) payload.email = fixEmail.trim()
    if (fixFirstName.trim()) payload.first_name = fixFirstName.trim()
    if (fixLastName.trim()) payload.last_name = fixLastName.trim()

    try {
      const result = await api.patch(`/admin/leads/${fixLeadId.trim()}/fix-contact-info`, payload)
      setFixResult(result)
      clearFixForm()
      await loadPotentialDuplicates()
    } catch (err) {
      setError(err.message || 'Could not fix contact information.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="lead-cleanup-page">
      <header className="page-header lead-cleanup-header">
        <div>
          <p className="lead-cleanup-eyebrow">Data Integrity</p>
          <h1 className="page-title">Lead Cleanup Center</h1>
          <p className="page-subtitle">
            Resolve likely duplicates, preserve lead history, and correct bad phone or email values without touching unrelated organizations.
          </p>
        </div>
        <div className="panel lead-cleanup-command-card">
          <span>Potential Leads</span>
          <strong>{loading ? '—' : totalPotentialLeads}</strong>
          <small>{loading ? 'Scanning org data' : `${groups.length} duplicate group(s)`}</small>
        </div>
      </header>

      {error ? <div className="cleanup-alert cleanup-alert--error">{error}</div> : null}
      {mergeResults ? (
        <div className="cleanup-alert cleanup-alert--success">
          Merged {mergeResults.merged_count} lead(s). Moved {mergeResults.moved_messages} messages, {mergeResults.moved_replies} replies,
          {` ${mergeResults.moved_outcomes}`} outcomes, and {mergeResults.moved_cadence_states} cadence state(s).
        </div>
      ) : null}
      {fixResult ? (
        <div className="cleanup-alert cleanup-alert--success">
          Updated {nameForLead(fixResult)} — {fixResult.phone || 'no phone'} / {fixResult.email || 'no email'}.
        </div>
      ) : null}

      <section className="cleanup-grid">
        <section className="panel cleanup-groups-panel">
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Potential Duplicate Groups</h2>
              <p className="cleanup-panel-subtitle">Groups are found by shared normalized phone or normalized last name, excluding leads already flagged as duplicates.</p>
            </div>
            <button className="btn btn--secondary" type="button" onClick={loadPotentialDuplicates} disabled={busy || loading}>Refresh</button>
          </div>

          {loading ? <div className="cleanup-empty-state">Scanning for likely duplicates...</div> : null}
          {!loading && groups.length === 0 ? <div className="cleanup-empty-state">No uncaught potential duplicates found.</div> : null}

          <div className="cleanup-group-list">
            {groups.map((group, index) => {
              const key = groupKey(group, index)
              const keepLeadId = selectedKeepByGroup[key] || group.leads?.[0]?.id || ''
              return (
                <article className="cleanup-group-card" key={key}>
                  <div className="cleanup-group-header">
                    <div>
                      <span className={`cleanup-match-pill cleanup-match-pill--${group.match_type}`}>{group.match_type.replace('_', ' ')}</span>
                      <h3>{group.match_key}</h3>
                    </div>
                    <div className="cleanup-group-actions">
                      {canBulkAssign && (
                        <>
                          <select
                            className="filter-select cleanup-assign-select"
                            value={assignTargetByGroup[key] || ''}
                            onChange={(e) => setAssignTargetByGroup((current) => ({ ...current, [key]: e.target.value }))}
                          >
                            <option value="">Unassigned</option>
                            {assignableUsers.map((user) => (
                              <option key={user.id} value={user.id}>
                                {user.full_name} {user.role !== 'advisor' ? `(${user.role.replace('_', ' ')})` : ''}
                              </option>
                            ))}
                          </select>
                          <button
                            className="btn btn--secondary"
                            type="button"
                            onClick={() => assignGroup(group, index)}
                            disabled={assigningGroupKey === key}
                          >
                            {assigningGroupKey === key ? 'Assigning…' : 'Assign group'}
                          </button>
                        </>
                      )}
                      <button className="btn btn--danger" type="button" onClick={() => mergeGroup(group, index)} disabled={busy || group.leads.length < 2}>
                        Merge Selected
                      </button>
                    </div>
                  </div>

                  {assignResult?.groupKey === key && (
                    <div className="cleanup-alert cleanup-alert--success cleanup-group-assign-result">
                      Assigned {assignResult.reassigned_count} lead{assignResult.reassigned_count === 1 ? '' : 's'} in this group.
                      {assignResult.skipped_count > 0 && ` Skipped ${assignResult.skipped_count}.`}
                    </div>
                  )}

                  <div className="cleanup-lead-list">
                    {group.leads.map((lead) => (
                      <label className="cleanup-lead-row" key={lead.id}>
                        <input
                          type="radio"
                          name={`keep-${key}`}
                          checked={keepLeadId === lead.id}
                          onChange={() => setSelectedKeepByGroup((current) => ({ ...current, [key]: lead.id }))}
                        />
                        <div className="cleanup-lead-main">
                          <Link to={`/leads/${lead.id}`} onClick={(e) => e.stopPropagation()}>{nameForLead(lead)}</Link>
                          <span>{lead.phone || 'No phone'} · {lead.email || 'No email'}</span>
                        </div>
                        <span className="cleanup-status-pill">{statusLabel(lead.status)}</span>
                        <span className="cleanup-keep-label">{keepLeadId === lead.id ? 'Keep' : 'Merge'}</span>
                        <button
                          type="button"
                          className="btn btn--secondary cleanup-lead-edit-btn"
                          onClick={(e) => { e.preventDefault(); e.stopPropagation(); startFixingLead(lead) }}
                        >
                          Edit
                        </button>
                      </label>
                    ))}
                  </div>
                </article>
              )
            })}
          </div>
        </section>

        <aside className="panel cleanup-fix-panel" ref={fixPanelRef}>
          <div className="panel-header">
            <div>
              <h2 className="panel-title">Fix Lead Info</h2>
              <p className="cleanup-panel-subtitle">
                Click "Edit" on any lead in a duplicate group to load it here, or paste a Lead ID directly below.
                Phone values are normalized by the backend dedup service; correcting the last name re-checks for duplicate matches too.
              </p>
            </div>
          </div>

          {fixLeadName && (
            <div className="cleanup-fix-active-lead">
              Editing: <strong>{fixLeadName}</strong>
              <button type="button" className="back-link" onClick={clearFixForm}>Clear</button>
            </div>
          )}

          <form className="cleanup-fix-form" onSubmit={fixContactInfo}>
            <label>
              Lead ID
              <input value={fixLeadId} onChange={(event) => setFixLeadId(event.target.value)} placeholder="Paste lead UUID, or click Edit on a lead above" required />
            </label>
            <label>
              First name
              <input value={fixFirstName} onChange={(event) => setFixFirstName(event.target.value)} placeholder="First name" />
            </label>
            <label>
              Last name
              <input value={fixLastName} onChange={(event) => setFixLastName(event.target.value)} placeholder="Last name" />
            </label>
            <label>
              Correct phone
              <input value={fixPhone} onChange={(event) => setFixPhone(event.target.value)} placeholder="214-555-0199" />
            </label>
            <label>
              Correct email
              <input value={fixEmail} onChange={(event) => setFixEmail(event.target.value)} placeholder="family@example.com" />
            </label>
            <button
              className="btn btn--primary"
              type="submit"
              disabled={busy || !fixLeadId.trim() || (!fixPhone.trim() && !fixEmail.trim() && !fixFirstName.trim() && !fixLastName.trim())}
            >
              {busy ? 'Saving…' : 'Save Lead Info'}
            </button>
          </form>

          <div className="cleanup-warning-card">
            <strong>Merge safety</strong>
            <p>
              Merge runs as one transaction. If moving history or deleting merged records fails, the backend rolls the entire operation back.
            </p>
          </div>
        </aside>
      </section>
    </div>
  )
}
