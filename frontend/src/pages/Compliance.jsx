import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
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
  const [entries, setEntries] = useState([])
  const [stats, setStats] = useState(emptyStats)
  const [phone, setPhone] = useState('')
  const [reason, setReason] = useState('')
  const [dncPhone, setDncPhone] = useState('')
  const [dncReason, setDncReason] = useState('Permanent DNC')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

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

  return (
    <div className="compliance-page">
      <section className="compliance-hero glass-panel">
        <div>
          <p className="eyebrow">AdvisorFlow Control</p>
          <h1>Compliance Center</h1>
          <p className="hero-copy">
            Manage permanent suppression, manual DNC requests, and reply-based STOP protections.
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
          <small>Automatic keyword detection</small>
        </article>
      </section>

      <section className="compliance-grid">
        <form className="glass-panel compliance-form" onSubmit={addPermanentDnc}>
          <div>
            <p className="eyebrow red">Permanent DNC</p>
            <h2>Add Permanent DNC</h2>
            <p>Adds the phone to suppression and marks the matching lead as DNC inside the same organization.</p>
          </div>
          <label>
            Phone number
            <input value={dncPhone} onChange={(e) => setDncPhone(e.target.value)} placeholder="214-555-0101" required />
          </label>
          <label>
            Reason
            <textarea value={dncReason} onChange={(e) => setDncReason(e.target.value)} rows="3" placeholder="Permanent DNC" />
          </label>
          <button className="danger-button" disabled={busy}>Add Permanent DNC</button>
        </form>

        <form className="glass-panel compliance-form" onSubmit={addSuppressionEntry}>
          <div>
            <p className="eyebrow blue">Manual Suppression</p>
            <h2>Add Suppression Entry</h2>
            <p>Blocks outreach to this number without changing the lead status unless using Permanent DNC.</p>
          </div>
          <label>
            Phone number
            <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="972-555-0144" required />
          </label>
          <label>
            Reason
            <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows="3" placeholder="Requested no further outreach" required />
          </label>
          <button className="primary-button" disabled={busy}>Add to Suppression List</button>
        </form>
      </section>

      <section className="glass-panel suppression-panel">
        <div className="section-header">
          <div>
            <p className="eyebrow green">Protected Outreach</p>
            <h2>Suppression List</h2>
          </div>
          <button className="ghost-button" onClick={loadSuppressionList} disabled={loading || busy}>Refresh</button>
        </div>

        <div className="table-wrap">
          <table className="suppression-table">
            <thead>
              <tr>
                <th>Phone</th>
                <th>Reason</th>
                <th>Source</th>
                <th>Added</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="5" className="empty-cell">Loading compliance data...</td></tr>
              ) : entries.length === 0 ? (
                <tr><td colSpan="5" className="empty-cell">No suppressed numbers yet.</td></tr>
              ) : (
                entries.map((entry) => (
                  <tr key={entry.id}>
                    <td className="phone-cell">{entry.phone}</td>
                    <td>{entry.reason}</td>
                    <td><span className={`source-pill ${entry.source}`}>{entry.source === 'reply_stop' ? 'Reply STOP' : 'Manual'}</span></td>
                    <td>{formatDate(entry.added_at)}</td>
                    <td className="actions-cell">
                      <button className="remove-button" onClick={() => removeEntry(entry.id)} disabled={busy} type="button">Remove</button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
