import { useEffect, useMemo, useState } from 'react'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './Compliance.css'

/**
 * ORIGIN NOTE: this page's layout and interaction design were drafted
 * by ChatGPT in a separate task. The original used raw fetch() calls to
 * "/api/compliance/..." with no auth header attached at all - that
 * would have failed immediately against this app's real JWT-based auth
 * (every other page uses the shared api client in src/api/client.js,
 * which attaches the Authorization header automatically). Ported here
 * to use that real client, and fixed the route paths since this
 * backend has no /api prefix.
 */

const emptyStats = { total: 0, manual: 0, reply_stop: 0 }

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

export default function Compliance() {
  const currentUser = getCurrentUser()
  const isAdmin = ['org_admin', 'super_admin', 'god_admin'].includes(currentUser?.role)

  const [entries, setEntries] = useState([])
  const [stats, setStats] = useState(emptyStats)
  const [phone, setPhone] = useState('')
  const [reason, setReason] = useState('')
  const [dncPhone, setDncPhone] = useState('')
  const [dncReason, setDncReason] = useState('Permanent DNC')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')

  const manualPercent = useMemo(() => {
    if (!stats.total) return 0
    return Math.round((stats.manual / stats.total) * 100)
  }, [stats])

  async function loadSuppressionList() {
    setError('')
    setLoading(true)
    try {
      const data = await api.get('/compliance/suppression-list')
      setEntries(data.entries || [])
      setStats(data.stats || emptyStats)
    } catch (err) {
      setError(err.message || 'Something went wrong loading compliance data.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadSuppressionList() }, [])

  async function addSuppressionEntry(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await api.post('/compliance/suppression-list', { phone, reason, source: 'manual' })
      setPhone('')
      setReason('')
      await loadSuppressionList()
    } catch (err) {
      setError(err.message || 'Could not add suppression entry.')
    } finally {
      setBusy(false)
    }
  }

  async function addPermanentDnc(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await api.post('/compliance/permanent-dnc', { phone: dncPhone, reason: dncReason || 'Permanent DNC' })
      setDncPhone('')
      setDncReason('Permanent DNC')
      await loadSuppressionList()
    } catch (err) {
      setError(err.message || 'Could not add permanent DNC.')
    } finally {
      setBusy(false)
    }
  }

  async function removeEntry(entryId) {
    setError('')
    setBusy(true)
    try {
      await api.delete(`/compliance/suppression-list/${entryId}`)
      await loadSuppressionList()
    } catch (err) {
      setError(err.message || 'Could not remove suppression entry.')
    } finally {
      setBusy(false)
    }
  }

  const filtered = entries.filter(e =>
    !search || e.phone.includes(search) || (e.reason || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="compliance-page">
      <section className="compliance-hero glass-panel">
        <div>
          <h1>DNC / Suppression List</h1>
          <p className="hero-copy">
            Numbers on this list will never receive outreach from this organization.
            {isAdmin
              ? ' As an admin, you can add or remove entries.'
              : ' You can view and add numbers. Only admins can remove entries.'}
          </p>
        </div>
        <div className="signal-orb" aria-hidden="true" />
      </section>

      {error ? <div className="compliance-alert">{error}</div> : null}

      <section className="compliance-stats">
        <article className="stat-card glass-panel">
          <span>Total Suppressed</span>
          <strong>{stats.total}</strong>
          <small>All protected numbers</small>
        </article>
        <article className="stat-card glass-panel blue">
          <span>Manual Adds</span>
          <strong>{stats.manual}</strong>
          <small>{manualPercent}% of suppression list</small>
        </article>
        <article className="stat-card glass-panel amber">
          <span>Reply STOP</span>
          <strong>{stats.reply_stop}</strong>
          <small>Auto-detected opt-outs</small>
        </article>
      </section>

      <section className="compliance-grid">
        {/* Add to suppression — available to ALL users */}
        <form className="glass-panel compliance-form" onSubmit={addSuppressionEntry}>
          <div>
            <p className="eyebrow blue">Add to DNC List</p>
            <h2>Suppress a Number</h2>
            <p>Blocks all outreach to this number. Use for verbal opt-outs, STOP requests, or any do-not-contact situation.</p>
          </div>
          <label>
            Phone number
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="214-555-0101" required />
          </label>
          <label>
            Reason <span style={{ fontWeight: 400, opacity: 0.6 }}>(required)</span>
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows="3" placeholder="Verbally requested no further contact" required />
          </label>
          <button className="primary-button" disabled={busy}>Add to Suppression List</button>
        </form>

        {/* Permanent DNC — org_admin+ only */}
        {isAdmin ? (
          <form className="glass-panel compliance-form" onSubmit={addPermanentDnc}>
            <div>
              <p className="eyebrow red">Admin Only</p>
              <h2>Permanent DNC</h2>
              <p>Adds to suppression AND marks the matching lead's status as DNC. This cannot be undone by non-admins.</p>
            </div>
            <label>
              Phone number
              <input value={dncPhone} onChange={(e) => setDncPhone(e.target.value)} placeholder="972-555-0144" required />
            </label>
            <label>
              Reason
              <textarea value={dncReason} onChange={(e) => setDncReason(e.target.value)} rows="3" placeholder="Permanent DNC" />
            </label>
            <button className="danger-button" disabled={busy}>Add Permanent DNC</button>
          </form>
        ) : (
          <div className="glass-panel compliance-form compliance-locked">
            <span className="lock-icon">🔒</span>
            <h2>Permanent DNC</h2>
            <p>Only organization admins can mark a lead as Permanent DNC. Contact your admin to permanently suppress a number and update the lead status.</p>
          </div>
        )}
      </section>

      <section className="glass-panel suppression-panel">
        <div className="section-header">
          <div>
            <p className="eyebrow green">Protected Numbers</p>
            <h2>Suppression List</h2>
          </div>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <input
              className="suppression-search"
              placeholder="Search phone or reason…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            <button className="ghost-button" onClick={loadSuppressionList} disabled={loading || busy}>Refresh</button>
          </div>
        </div>

        <div className="table-wrap">
          <table className="suppression-table">
            <thead>
              <tr>
                <th>Phone</th>
                <th>Reason</th>
                <th>Source</th>
                <th>Added</th>
                {isAdmin && <th />}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={isAdmin ? 5 : 4} className="empty-cell">Loading suppression list...</td></tr>
              ) : filtered.length === 0 ? (
                <tr><td colSpan={isAdmin ? 5 : 4} className="empty-cell">
                  {search ? 'No matches for that search.' : 'No suppressed numbers yet. Numbers opted out via STOP will appear here automatically.'}
                </td></tr>
              ) : (
                filtered.map((entry) => (
                  <tr key={entry.id}>
                    <td className="phone-cell">{entry.phone}</td>
                    <td>{entry.reason}</td>
                    <td>
                      <span className={`source-pill ${entry.source}`}>
                        {entry.source === 'REPLY_STOP' || entry.source === 'reply_stop' ? '🛑 Reply STOP' : '✋ Manual'}
                      </span>
                    </td>
                    <td>{formatDate(entry.added_at)}</td>
                    {isAdmin && (
                      <td className="actions-cell">
                        <button className="remove-button" onClick={() => removeEntry(entry.id)} disabled={busy} type="button">
                          Remove
                        </button>
                      </td>
                    )}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {!isAdmin && entries.length > 0 && (
          <p className="compliance-readonly-note">
            🔒 Only organization admins can remove entries from this list.
          </p>
        )}
      </section>
    </div>
  )
}
