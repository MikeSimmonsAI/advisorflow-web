import { useEffect, useMemo, useRef, useState } from 'react'
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
  const [deletingDuplicates, setDeletingDuplicates] = useState(false)
  const [deleteResult, setDeleteResult] = useState(null)
  const [selectedKeepByGroup, setSelectedKeepByGroup] = useState({})
  const [mergeResults, setMergeResults] = useState(null)
  const [fixLeadId, setFixLeadId] = useState('')
  const [fixLeadName, setFixLeadName] = useState('')
  const [fixFirstName, setFixFirstName] = useState('')
  const [fixLastName, setFixLastName] = useState('')
  const [fixPhone, setFixPhone] = useState('')
  const [fixEmail, setFixEmail] = useState('')
  const [fixResult, setFixResult] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [advisors, setAdvisors] = useState([])
  const [reassignAdvisorId, setReassignAdvisorId] = useState('')
  const [selectedForReassign, setSelectedForReassign] = useState(new Set())
  const [reassignResult, setReassignResult] = useState(null)
  const fixRef = useRef(null)
  const user = getCurrentUser()

  useEffect(() => {
    api.get('/admin/users')
      .then((users) => setAdvisors(users.filter((u) => u.role === 'advisor')))
      .catch(() => {})
    loadPotentialDuplicates()
  }, [])

  function loadPotentialDuplicates() {
    setLoading(true)
    setError('')
    api.get('/admin/leads/potential-duplicates')
      .then((data) => {
        setGroups(data)
        const defaults = {}
        data.forEach((g, i) => {
          if (g.leads.length > 0) defaults[i] = g.leads[0].id
        })
        setSelectedKeepByGroup(defaults)
      })
      .catch((err) => setError(err.message || 'Could not load duplicates.'))
      .finally(() => setLoading(false))
  }

  function prefillFixForm(lead) {
    setFixLeadId(lead.id)
    setFixLeadName(nameForLead(lead))
    setFixFirstName(lead.first_name || '')
    setFixLastName(lead.last_name || '')
    setFixPhone(lead.phone || '')
    setFixEmail(lead.email || '')
    setFixResult(null)
    setTimeout(() => fixRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80)
  }

  function clearFixForm() {
    setFixLeadId('')
    setFixLeadName('')
    setFixFirstName('')
    setFixLastName('')
    setFixPhone('')
    setFixEmail('')
    setFixResult(null)
  }

  async function mergeGroup(groupIdx) {
    const group = groups[groupIdx]
    const keepId = selectedKeepByGroup[groupIdx]
    if (!keepId) return
    const mergeIds = group.leads.filter((l) => l.id !== keepId).map((l) => l.id)
    if (mergeIds.length === 0) return
    setBusy(true)
    setError('')
    try {
      const result = await api.post('/admin/leads/merge', { keep_lead_id: keepId, merge_lead_ids: mergeIds })
      setMergeResults((prev) => ({ ...(prev || {}), [groupIdx]: result }))
      await loadPotentialDuplicates()
    } catch (err) {
      setError(err.message || 'Merge failed.')
    } finally {
      setBusy(false)
    }
  }

  async function reassignSelected() {
    if (!reassignAdvisorId || selectedForReassign.size === 0) return
    setBusy(true)
    setError('')
    try {
      const result = await api.post('/admin/leads/reassign', {
        lead_ids: Array.from(selectedForReassign),
        advisor_id: reassignAdvisorId,
      })
      setReassignResult(result)
      setSelectedForReassign(new Set())
    } catch (err) {
      setError(err.message || 'Reassign failed.')
    } finally {
      setBusy(false)
    }
  }

  async function fixContactInfo() {
    if (!fixLeadId.trim()) return
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

  const totalLeadsInGroups = useMemo(() => {
    const ids = new Set()
    groups.forEach((g) => g.leads.forEach((l) => ids.add(l.id)))
    return ids.size
  }, [groups])

  return (
    <div className="lead-cleanup-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">Lead Cleanup</h1>
          <p className="page-subtitle">Find and merge duplicate leads, fix contact info, reassign leads.</p>
        </div>
        <button className="btn btn--secondary" onClick={loadPotentialDuplicates} disabled={loading}>
          {loading ? 'Scanning…' : 'Refresh'}
        </button>
      </header>

      {error && <div className="cleanup-error">{error}</div>}

      <div className="cleanup-kpi-row">
        <div className="panel cleanup-kpi-card">
          <span className="cleanup-kpi-label">Duplicate groups found</span>
          <strong className="cleanup-kpi-value" style={{ color: 'var(--signal-amber)' }}>{loading ? '—' : groups.length}</strong>
        </div>
        <div className="panel cleanup-kpi-card">
          <span className="cleanup-kpi-label">Leads affected</span>
          <strong className="cleanup-kpi-value" style={{ color: 'var(--signal-red)' }}>{loading ? '—' : totalLeadsInGroups}</strong>
        </div>
        <div className="panel cleanup-kpi-card">
          <span className="cleanup-kpi-label">Merges this session</span>
          <strong className="cleanup-kpi-value" style={{ color: 'var(--signal-green)' }}>{mergeResults ? Object.keys(mergeResults).length : 0}</strong>
        </div>
      </div>

      <section className="cleanup-main">
        <div className="cleanup-groups">
          <div className="panel">
            <div className="panel-header">
              <h2 className="panel-title">Potential duplicates</h2>
              <span className="panel-count">{groups.length} groups</span>
            </div>

            {loading ? (
              <div className="empty-state">Scanning for duplicates…</div>
            ) : groups.length === 0 ? (
              <div className="empty-state">No duplicate leads found. Your data is clean.</div>
            ) : (
              <div className="cleanup-group-list">
                {groups.map((group, gi) => (
                  <div key={gi} className="cleanup-group">
                    <div className="cleanup-group-header">
                      <span className="cleanup-match-badge">
                        {group.match_type === 'phone' ? '📞 Phone match' : '👤 Name match'}
                      </span>
                      <span className="cleanup-match-key mono">{group.match_key}</span>
                      {mergeResults?.[gi] && (
                        <span className="cleanup-merged-badge">✓ Merged</span>
                      )}
                    </div>
                    <table className="data-table cleanup-group-table">
                      <thead>
                        <tr>
                          <th>Keep</th>
                          <th>Name</th>
                          <th>Phone</th>
                          <th>Email</th>
                          <th>Status</th>
                          <th>Actions</th>
                        </tr>
                      </thead>
                      <tbody>
                        {group.leads.map((lead) => (
                          <tr key={lead.id} className={selectedKeepByGroup[gi] === lead.id ? 'cleanup-row--keep' : ''}>
                            <td>
                              <input
                                type="radio"
                                name={`keep-${gi}`}
                                checked={selectedKeepByGroup[gi] === lead.id}
                                onChange={() => setSelectedKeepByGroup((p) => ({ ...p, [gi]: lead.id }))}
                              />
                            </td>
                            <td>{nameForLead(lead)}</td>
                            <td className="mono">{lead.phone || '—'}</td>
                            <td className="mono">{lead.email || '—'}</td>
                            <td><span className="cleanup-status-pill">{statusLabel(lead.status)}</span></td>
                            <td>
                              <button className="btn btn--secondary cleanup-btn-sm" onClick={() => prefillFixForm(lead)}>
                                Edit
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div className="cleanup-group-actions">
                      <span className="cleanup-hint">Select the lead to keep, others will be merged into it.</span>
                      <button
                        className="btn btn--primary"
                        onClick={() => mergeGroup(gi)}
                        disabled={busy || !selectedKeepByGroup[gi] || !!mergeResults?.[gi]}
                      >
                        {busy ? 'Merging…' : mergeResults?.[gi] ? 'Merged' : 'Merge duplicates'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <aside className="cleanup-sidebar">
          <div className="panel" ref={fixRef}>
            <div className="panel-header">
              <h2 className="panel-title">Fix contact info</h2>
            </div>
            {fixLeadName && (
              <div className="cleanup-editing-badge">
                Editing: <strong>{fixLeadName}</strong>
                <button className="back-link" onClick={clearFixForm}>Clear</button>
              </div>
            )}
            <div className="cleanup-fix-form">
              <label className="cleanup-label">
                Lead ID
                <input
                  className="cleanup-input"
                  value={fixLeadId}
                  onChange={(e) => setFixLeadId(e.target.value)}
                  placeholder="Paste lead UUID or click Edit above"
                />
              </label>
              <label className="cleanup-label">
                First name
                <input className="cleanup-input" value={fixFirstName} onChange={(e) => setFixFirstName(e.target.value)} placeholder="First name" />
              </label>
              <label className="cleanup-label">
                Last name
                <input className="cleanup-input" value={fixLastName} onChange={(e) => setFixLastName(e.target.value)} placeholder="Last name" />
              </label>
              <label className="cleanup-label">
                Phone
                <input className="cleanup-input" value={fixPhone} onChange={(e) => setFixPhone(e.target.value)} placeholder="214-555-0199" />
              </label>
              <label className="cleanup-label">
                Email
                <input className="cleanup-input" value={fixEmail} onChange={(e) => setFixEmail(e.target.value)} placeholder="family@example.com" />
              </label>
              <button
                className="btn btn--primary"
                onClick={fixContactInfo}
                disabled={busy || !fixLeadId.trim() || (!fixPhone.trim() && !fixEmail.trim() && !fixFirstName.trim() && !fixLastName.trim())}
              >
                {busy ? 'Saving…' : 'Save contact info'}
              </button>
              {fixResult && <div className="cleanup-success">Contact info updated.</div>}
            </div>
          </div>

          {user?.role === 'admin' && advisors.length > 0 && (
            <div className="panel">
              <div className="panel-header">
                <h2 className="panel-title">Reassign leads</h2>
              </div>
              <p className="cleanup-hint" style={{ marginBottom: 12 }}>
                Select leads from the duplicate groups above, then reassign to an advisor.
              </p>
              <label className="cleanup-label">
                Assign to
                <select className="cleanup-input" value={reassignAdvisorId} onChange={(e) => setReassignAdvisorId(e.target.value)}>
                  <option value="">Select advisor…</option>
                  {advisors.map((a) => (
                    <option key={a.id} value={a.id}>{a.full_name}</option>
                  ))}
                </select>
              </label>
              <button
                className="btn btn--secondary"
                onClick={reassignSelected}
                disabled={busy || !reassignAdvisorId || selectedForReassign.size === 0}
              >
                Reassign {selectedForReassign.size > 0 ? `${selectedForReassign.size} leads` : ''}
              </button>
              {reassignResult && (
                <div className="cleanup-success">Reassigned {reassignResult.reassigned_count} leads.</div>
              )}
            </div>
          )}

          <div className="panel cleanup-warning-card">
            <strong style={{ color: 'var(--signal-amber)' }}>Merge safety</strong>
            <p className="cleanup-hint" style={{ marginTop: 6 }}>
              Merges run as a single transaction. If anything fails, the entire operation rolls back — no partial state is left behind.
            </p>
          </div>
        </aside>
      </section>
    </div>
  )
}
