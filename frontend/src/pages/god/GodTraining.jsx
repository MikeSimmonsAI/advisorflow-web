/**
 * GOD MODE — TRAINING & READINESS.
 *
 * The question this screen answers is "can somebody other than me run a demo",
 * and it answers it by naming the person and the step they have stopped on
 * rather than by averaging a percentage. "Everybody stalls on
 * what-not-to-promise" is a finding; "the team averages 62%" is not.
 *
 * Assigning is done from the person's own Manage Access screen, where the rest
 * of their access is — a second place to grant something is a second place for
 * it to be wrong. This screen links there rather than duplicating it.
 *
 * Data:
 *   GET /god/training/readiness
 *   GET /god/training/paths
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'
import { StatusBadge, SectionLabel, NoSource } from './StatusBadge'

const TONE = { complete: 'ok', in_progress: 'pend', not_started: 'off' }

function when (iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString()
}

export default function GodTraining () {
  const navigate = useNavigate()
  const [report, setReport] = useState(null)
  const [paths, setPaths] = useState([])
  const [filter, setFilter] = useState('')
  const [openPath, setOpenPath] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [a, b] = await Promise.all([
        api.get('/god/training/readiness' + (filter ? '?path_key=' + filter : '')),
        api.get('/god/training/paths'),
      ])
      setReport(a); setPaths(b.paths || [])
    } catch (e) { setErr(e?.message || 'Could not load readiness.') }
    finally { setLoading(false) }
  }, [filter])
  useEffect(() => { load() }, [load])

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ padding: '8px 2px 20px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
            ← COMMAND CENTER
          </button>
          <h1 style={{ margin: 0, color: 'var(--gm-head)', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
            Training &amp; Readiness
          </h1>
          <p style={{ margin: '9px 0 0', color: 'var(--gm-blue)', fontSize: 12, maxWidth: 760 }}>
            Who has been asked to learn what, and where they stopped. Assign a
            path from a person's Manage Access screen — the same place their
            brand, workspace and Demo Suite access is granted.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ borderColor: 'var(--gm-pill-red-bd)', marginBottom: 16 }}>
            <div style={{ color: T.red, fontSize: 12 }}>{err}</div>
          </div>
        )}

        <div className="gm-stats" style={{ marginBottom: 18 }}>
          <div className="gm-stat">
            <div className="gm-k">ASSIGNED</div>
            <div className="gm-v">{report?.assigned ?? '—'}</div>
            <div className="gm-s">people with a path</div>
          </div>
          <div className="gm-stat">
            <div className="gm-k">COMPLETE</div>
            <div className="gm-v">{report?.complete ?? '—'}</div>
            <div className="gm-s">finished the whole path</div>
          </div>
          <div className="gm-stat">
            <div className="gm-k">OVERDUE</div>
            <div className="gm-v">{report?.overdue ?? '—'}</div>
            <div className="gm-s">past a due date</div>
          </div>
        </div>

        <div className="gm-filters">
          <select className="gm-input" value={filter} onChange={e => setFilter(e.target.value)}>
            <option value="">Every path</option>
            {paths.map(p => <option key={p.key} value={p.key}>{p.name}</option>)}
          </select>
          <button className="gm-btn" onClick={load} disabled={loading}>
            {loading ? '…' : 'REFRESH'}
          </button>
        </div>

        {report?.stuck_on?.length > 0 && (
          <>
            <SectionLabel note="a finding, not an average">WHERE PEOPLE STOP</SectionLabel>
            <div className="gm-card" style={{ marginBottom: 18 }}>
              {report.stuck_on.map((s, i) => (
                <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'center',
                                      padding: '6px 0' }}>
                  <span className="gm-pill amber">{s.people}</span>
                  <span style={{ fontSize: 12.5 }}>{s.step}</span>
                </div>
              ))}
            </div>
          </>
        )}

        <SectionLabel note="one row per person per path">READINESS</SectionLabel>
        <div className="gm-card" style={{ padding: 0, marginBottom: 18 }}>
          <div className="gm-tablewrap">
            <table className="gm-table">
              <thead><tr>
                <th>PERSON</th><th>PATH</th><th>PROGRESS</th><th>STATUS</th>
                <th>NEXT STEP</th><th>DUE</th><th></th>
              </tr></thead>
              <tbody>
                {loading && <tr><td className="gm-empty" colSpan={7}>Loading…</td></tr>}
                {!loading && (report?.people || []).length === 0 &&
                  <tr><td className="gm-empty" colSpan={7}>
                    Nobody has been assigned training yet. Open somebody in
                    Manage Access to assign a path.
                  </td></tr>}
                {(report?.people || []).map((p, i) => (
                  <tr key={i}>
                    <td>
                      <div className="gm-orgname">{p.name || '—'}</div>
                      <div className="gm-orgsub">{p.email}</div>
                    </td>
                    <td style={{ fontSize: 11.5 }}>{p.path_name}</td>
                    <td>{p.completed_steps} / {p.total_steps}</td>
                    <td><StatusBadge tone={TONE[p.status] || 'off'}>
                      {p.status.replace('_', ' ').toUpperCase()}</StatusBadge></td>
                    <td style={{ fontSize: 11.5, color: T.dim, maxWidth: 300 }}>
                      {p.next_step || <NoSource>finished</NoSource>}
                    </td>
                    <td style={{ color: p.overdue ? T.amber : undefined, fontSize: 11.5 }}>
                      {when(p.due_at) || <NoSource>no date</NoSource>}
                    </td>
                    <td>
                      <div className="gm-acts">
                        <button className="gm-act"
                                onClick={() => navigate('/god/access/' + p.user_id)}>
                          MANAGE ACCESS
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <SectionLabel note="what each path teaches, and what it is grounded in">
          THE PATHS
        </SectionLabel>
        {paths.map(p => (
          <div className="gm-card" key={p.key} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span className="gm-pill blue">{p.audience_label}</span>
              <strong style={{ color: 'var(--gm-head)', fontSize: 14 }}>{p.name}</strong>
              <span style={{ color: T.dim, fontSize: 11 }}>
                {p.total_steps} steps · about {p.minutes} min
              </span>
              {p.requires_demo &&
                <span className="gm-pill amber">needs Demo Suite access</span>}
              <span style={{ flex: 1 }} />
              <button className="gm-btn"
                      onClick={() => setOpenPath(openPath === p.key ? null : p.key)}>
                {openPath === p.key ? 'HIDE STEPS' : 'SHOW STEPS'}
              </button>
            </div>
            <p style={{ margin: '10px 0 0', color: T.text, fontSize: 12.5 }}>{p.summary}</p>
            <p style={{ margin: '6px 0 0', color: T.dim, fontSize: 11.5 }}>{p.why}</p>
            {openPath === p.key && (
              <div style={{ marginTop: 14, borderTop: '1px solid ' + T.line, paddingTop: 12 }}>
                {(p.steps || []).map((s, i) => (
                  <div key={s.key} style={{ marginBottom: 12 }}>
                    <div style={{ color: T.blue, fontSize: 11 }}>
                      STEP {i + 1}
                      {s.practice_scenario &&
                        ' · practised in the Demo Suite (' + s.practice_scenario + ')'}
                    </div>
                    <div style={{ color: 'var(--gm-head)', fontSize: 12.5, marginTop: 2 }}>{s.title}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
