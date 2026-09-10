/**
 * God Mode — Customer Launches. A review surface, not a dump of the table.
 *
 * ===========================================================================
 * WHAT WAS WRONG WITH THE VERSION THIS REPLACES
 * ===========================================================================
 *
 * Two things, and the second was the serious one.
 *
 * 1. IT SHOWED ONE PROGRESS NUMBER AND CALLED IT "Progress". A customer who
 *    had answered every question and whose build had not started read as
 *    "100%" in one column and, on the Implementation screen, "0%" — with
 *    nothing anywhere saying those measure different things. It looked like
 *    the platform contradicting itself. Both figures are now here, both
 *    labelled, side by side, so the normal case reads as normal.
 *
 * 2. IT RENDERED THE DATABASE. The detail panel was:
 *
 *        {key}: {String(value)}
 *
 *    which put `sigAffirm: true`, `cpSandbox: not_requested` and
 *    `hostProvider: SiteGround` in front of staff as though they were three
 *    comparable facts. The labels existed the whole time — GET /launch/config
 *    carries every field's label, kind and option set. This screen now asks
 *    for them. See ../launch/present.js; nothing below formats a value itself.
 *
 * SECRETS ARE STRUCTURALLY ABSENT. The backend's `read_step` returns
 * `secrets_set` — a list of KEYS — and no value, ever. There is no branch here
 * that could print one, because there is nothing to print.
 */
import { useEffect, useMemo, useState } from 'react'
import { api, API_BASE } from '../../api/client'
import {
  PROGRESS, buildSchemaIndex, intakeState, presentAllSteps, SECRET_NOTE,
} from '../launch/present'

/** Fetch a protected file with the session's token and save it. */
async function downloadFile(orgId, f) {
  const token = localStorage.getItem('af_token')
    || localStorage.getItem('bookaboost_token')
  const res = await fetch(
    API_BASE + '/god/launch/' + orgId + '/files/' + f.id + '/download',
    { headers: token ? { Authorization: 'Bearer ' + token } : {} })
  if (!res.ok) { window.alert('Could not download that file.'); return }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = f.filename || 'download'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

const TONE = {
  neutral:  { bg: '#f9fafb', text: '#6b7280', border: '#e5e7eb' },
  warning:  { bg: '#fffbeb', text: '#92400e', border: '#fcd34d' },
  info:     { bg: '#eff6ff', text: '#1d4ed8', border: '#93c5fd' },
  positive: { bg: '#f0fdf4', text: '#166534', border: '#86efac' },
  danger:   { bg: '#fef2f2', text: '#b91c1c', border: '#fca5a5' },
}

function Chip({ label, tone = 'neutral', title }) {
  const s = TONE[tone] || TONE.neutral
  return (
    <span title={title} style={{
      background: s.bg, color: s.text, border: '1px solid ' + s.border,
      borderRadius: 999, padding: '3px 10px', fontSize: 11, fontWeight: 700,
      whiteSpace: 'nowrap', display: 'inline-block',
    }}>{label}</span>
  )
}

/**
 * A labelled progress bar.
 *
 * `concept` is not decoration — it is the fix. Every bar on this screen says
 * which of the two things it measures, in the words from present.js, so the
 * same phrase appears here, on the Implementation screen and in the customer's
 * own wizard.
 */
function Progress({ concept, done, total, pct }) {
  const c = PROGRESS[concept]
  const full = pct >= 100
  return (
    <div style={{ minWidth: 168 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'baseline', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.06em',
                       textTransform: 'uppercase',
                       color: 'var(--god-muted, #6b7280)' }}>{c.label}</span>
        <b style={{ fontSize: 12 }}>
          {total ? `${done}/${total}` : '—'} · {pct}%
        </b>
      </div>
      <div style={{ height: 6, borderRadius: 999,
                    background: 'var(--god-border, #e5e7eb)', overflow: 'hidden' }}>
        <i style={{ display: 'block', height: '100%', width: Math.min(pct, 100) + '%',
                    background: full ? '#22c55e'
                      : concept === 'intake' ? '#3b82f6' : '#a855f7' }} />
      </div>
    </div>
  )
}

const FILTERS = [
  { key: 'needs_review', label: 'Needs review' },
  { key: 'in_progress', label: 'In progress' },
  { key: 'not_started', label: 'Not started' },
  { key: 'reviewed', label: 'Reviewed' },
  { key: 'attention', label: 'Needs attention' },
  { key: 'all', label: 'All' },
]

export default function GodLaunches() {
  const [rows, setRows] = useState([])
  const [schema, setSchema] = useState(null)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('needs_review')
  const [brand, setBrand] = useState('all')
  const [implStatus, setImplStatus] = useState('all')
  const [busy, setBusy] = useState(false)

  const load = () => {
    setLoading(true)
    api.get('/god/launch')
      .then(d => { setRows(d.launches || []); setError(null) })
      .catch(e => setError(e?.detail || 'Could not load launches'))
      .finally(() => setLoading(false))
  }

  // The schema is fetched ONCE and is what turns stored keys into labels.
  useEffect(() => {
    api.get('/launch/config')
      .then(d => setSchema(buildSchemaIndex(d)))
      .catch(() => setSchema(buildSchemaIndex({ steps: [] })))
  }, [])
  useEffect(load, [])

  const open = orgId => {
    setDetail({ loading: true, orgId })
    api.get('/god/launch/' + orgId)
      .then(d => setDetail({ ...d, orgId }))
      .catch(e => setDetail({ orgId, error: e?.detail || 'Could not load' }))
  }

  const markReviewed = async orgId => {
    setBusy(true)
    try {
      await api.post('/god/launch/' + orgId + '/review', {})
      open(orgId)
      load()
    } finally { setBusy(false) }
  }

  const brands = useMemo(() => {
    const set = new Map()
    for (const r of rows) if (r.brand_name) set.set(r.brand_name, true)
    return ['all', ...Array.from(set.keys()).sort()]
  }, [rows])

  const statuses = useMemo(() => {
    const set = new Map()
    for (const r of rows) if (r.implementation_status) set.set(r.implementation_status, true)
    return ['all', ...Array.from(set.keys()).sort()]
  }, [rows])

  const shown = useMemo(() => rows.filter(r => {
    if (brand !== 'all' && r.brand_name !== brand) return false
    if (implStatus !== 'all' && r.implementation_status !== implStatus) return false
    if (filter === 'all') return true
    if (filter === 'needs_review') return r.intake_state === 'submitted'
    if (filter === 'attention') return (r.blockers?.length || 0) + (r.warnings?.length || 0) > 0
    return r.intake_state === filter
  }), [rows, filter, brand, implStatus])

  const counts = useMemo(() => ({
    needs_review: rows.filter(r => r.intake_state === 'submitted').length,
    in_progress: rows.filter(r => r.intake_state === 'in_progress').length,
    not_started: rows.filter(r => r.intake_state === 'not_started').length,
    reviewed: rows.filter(r => r.intake_state === 'reviewed').length,
    attention: rows.filter(r => (r.blockers?.length || 0) + (r.warnings?.length || 0) > 0).length,
    all: rows.length,
  }), [rows])

  const card = {
    background: 'var(--god-card, #fff)',
    border: '1px solid var(--god-border, #e5e7eb)',
    borderRadius: 12, padding: '18px 20px', marginBottom: 14,
  }

  const select = {
    fontSize: 12, padding: '6px 10px', borderRadius: 8,
    border: '1px solid var(--god-border, #e5e7eb)',
    background: 'var(--god-card, #fff)', color: 'var(--god-text, #1f2937)',
  }

  return (
    <div style={{ padding: '28px 32px', color: 'var(--god-text, #1f2937)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', marginBottom: 18, gap: 16,
                    flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>Customer Launches</h1>
          <p style={{ margin: '6px 0 0', color: 'var(--god-muted, #6b7280)', fontSize: 14 }}>
            Onboarding intake and build progress for every customer, including
            the ones nobody has opened
          </p>
        </div>
        <button onClick={load} disabled={loading} style={{
          fontSize: 12, padding: '6px 14px', borderRadius: 8,
          border: '1px solid var(--god-border, #e5e7eb)',
          background: 'var(--god-card, #fff)', cursor: 'pointer',
          color: 'var(--god-text, #1f2937)' }}>↻ Refresh</button>
      </div>

      {/* THE TWO CONCEPTS, EXPLAINED ONCE, ABOVE EVERYTHING THAT USES THEM. */}
      <div style={{ ...card, display: 'flex', gap: 24, flexWrap: 'wrap',
                    background: 'var(--god-card, #fbfdff)' }}>
        <div style={{ flex: '1 1 280px' }}>
          <b style={{ fontSize: 12 }}>{PROGRESS.intake.label}</b>
          <p style={{ margin: '4px 0 0', fontSize: 12,
                      color: 'var(--god-muted, #6b7280)' }}>{PROGRESS.intake.meaning}</p>
        </div>
        <div style={{ flex: '1 1 280px' }}>
          <b style={{ fontSize: 12 }}>{PROGRESS.implementation.label}</b>
          <p style={{ margin: '4px 0 0', fontSize: 12,
                      color: 'var(--god-muted, #6b7280)' }}>
            {PROGRESS.implementation.meaning}
          </p>
        </div>
      </div>

      {error && (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5',
                      borderRadius: 8, padding: 16, color: '#dc2626',
                      marginBottom: 16 }}>{error}</div>
      )}

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center',
                    marginBottom: 16 }}>
        {FILTERS.map(f => (
          <button key={f.key} onClick={() => setFilter(f.key)} style={{
            fontSize: 12, fontWeight: 600, padding: '6px 12px', borderRadius: 999,
            cursor: 'pointer',
            border: '1px solid ' + (filter === f.key ? '#3b82f6' : 'var(--god-border, #e5e7eb)'),
            background: filter === f.key ? '#3b82f6' : 'var(--god-card, #fff)',
            color: filter === f.key ? '#fff' : 'var(--god-text, #1f2937)',
          }}>{f.label} {counts[f.key] ?? 0}</button>
        ))}
        <span style={{ flex: 1 }} />
        <select value={brand} onChange={e => setBrand(e.target.value)} style={select}>
          {brands.map(b => <option key={b} value={b}>{b === 'all' ? 'All brands' : b}</option>)}
        </select>
        <select value={implStatus} onChange={e => setImplStatus(e.target.value)} style={select}>
          {statuses.map(s => (
            <option key={s} value={s}>
              {s === 'all' ? 'All implementation statuses' : s.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
      </div>

      {loading ? (
        <div style={card}><p style={{ color: '#9ca3af', fontSize: 13 }}>Loading…</p></div>
      ) : shown.length === 0 ? (
        <div style={card}>
          <p style={{ color: '#9ca3af', fontSize: 13, margin: 0 }}>
            {rows.length === 0
              ? 'No implementations yet. A launch appears here as soon as a deal becomes a customer.'
              : 'Nothing matches these filters. Everything else is under another filter, not missing.'}
          </p>
        </div>
      ) : shown.map(r => {
        const st = intakeState(r.intake_state)
        const isOpen = detail && detail.orgId === r.organization_id
        return (
          <div key={r.implementation_id} style={{
            ...card,
            borderLeft: r.blockers?.length ? '3px solid #ef4444'
              : r.intake_state === 'submitted' ? '3px solid #3b82f6' : card.border,
          }}>
            <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start',
                          flexWrap: 'wrap' }}>
              <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8,
                              flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 15 }}>
                    {r.organization_name || r.organization_id}
                  </b>
                  <Chip label={st.label} tone={st.tone} />
                </div>
                <div style={{ marginTop: 5, fontSize: 12,
                              color: 'var(--god-muted, #6b7280)' }}>
                  {r.brand_name || 'No brand set'}
                  {' · '}Build status: {(r.implementation_status || 'unknown').replace(/_/g, ' ')}
                  {r.target_launch_date
                    ? ' · Target ' + new Date(r.target_launch_date).toLocaleDateString()
                    : ' · No target date'}
                </div>
                <div style={{ marginTop: 6, fontSize: 12,
                              color: 'var(--god-muted, #6b7280)' }}>
                  {r.file_count} file{r.file_count === 1 ? '' : 's'}
                  {r.submitted_at
                    ? ' · Submitted ' + new Date(r.submitted_at).toLocaleDateString()
                    : ''}
                  {r.reviewed_at
                    ? ' · Reviewed ' + new Date(r.reviewed_at).toLocaleDateString()
                    : ''}
                </div>
              </div>

              <Progress concept="intake" done={r.intake_complete_steps}
                        total={r.intake_total_steps} pct={r.intake_pct} />
              <Progress concept="implementation" done={r.implementation_settled}
                        total={r.implementation_total} pct={r.implementation_pct} />

              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button onClick={() => (isOpen ? setDetail(null) : open(r.organization_id))}
                        style={{
                          fontSize: 12, padding: '7px 14px', borderRadius: 8,
                          border: '1px solid var(--god-border, #e5e7eb)',
                          background: 'var(--god-card, #fff)', cursor: 'pointer',
                          color: 'var(--god-text, #1f2937)', fontWeight: 600 }}>
                  {isOpen ? 'Close' : 'Review'}
                </button>
              </div>
            </div>

            {(r.blockers?.length || r.warnings?.length) ? (
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 12 }}>
                {(r.blockers || []).map((b, i) => (
                  <Chip key={'b' + i} label={b} tone="danger" />
                ))}
                {(r.warnings || []).map((w, i) => (
                  <Chip key={'w' + i} label={w} tone="warning" />
                ))}
              </div>
            ) : null}

            {isOpen ? (
              <LaunchReview detail={detail} schema={schema} busy={busy}
                            onReviewed={() => markReviewed(r.organization_id)} />
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

/**
 * One customer's submission, read in the order they filled it in.
 *
 * Driven by the SCHEMA, not by `Object.entries(answers)`: object key order is
 * insertion order, which is the order somebody happened to type, and a
 * reviewer should read Company before Signature every time.
 */
function LaunchReview({ detail, schema, busy, onReviewed }) {
  if (detail.loading) return <p style={{ color: '#9ca3af', fontSize: 13 }}>Loading…</p>
  if (detail.error) return <p style={{ color: '#dc2626', fontSize: 13 }}>{detail.error}</p>
  if (!schema) return <p style={{ color: '#9ca3af', fontSize: 13 }}>Loading the form…</p>

  const sections = presentAllSteps(schema, detail.answers)
  const sub = detail.submission
  const hasSecret = sections.some(s => s.rows.some(r => r.secure))

  return (
    <div style={{ marginTop: 16, paddingTop: 16,
                  borderTop: '1px solid var(--god-border, #e5e7eb)' }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap',
                    alignItems: 'center', marginBottom: 14 }}>
        {sub ? (
          <span style={{ fontSize: 12, color: 'var(--god-muted, #6b7280)' }}>
            Signed by <b>{sub.signed_name || 'somebody'}</b>
            {sub.submitted_at
              ? ' on ' + new Date(sub.submitted_at).toLocaleString() : ''}
          </span>
        ) : (
          <span style={{ fontSize: 12, color: 'var(--god-muted, #6b7280)' }}>
            Not submitted yet — this is a draft in progress.
          </span>
        )}
        <span style={{ flex: 1 }} />
        {sub && !sub.reviewed_at ? (
          <button onClick={onReviewed} disabled={busy} style={{
            fontSize: 12, padding: '7px 14px', borderRadius: 8,
            border: '1px solid #86efac', background: '#f0fdf4',
            color: '#166534', cursor: busy ? 'default' : 'pointer',
            fontWeight: 700, opacity: busy ? 0.6 : 1 }}>
            Mark reviewed — reopens editing for the customer
          </button>
        ) : null}
        <a href={'/god/implementations'} style={{ fontSize: 12 }}>View implementation</a>
        <a href={'/god/customers/' + detail.orgId} style={{ fontSize: 12 }}>View customer</a>
      </div>

      {detail.blockers?.length ? (
        <div style={{ background: '#fffbeb', border: '1px solid #fcd34d',
                      borderRadius: 8, padding: 12, marginBottom: 14,
                      fontSize: 12, color: '#92400e' }}>
          <b>Still outstanding in the customer's intake</b>
          <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
            {detail.blockers.map((b, i) => (
              <li key={i}>{b.step_label} — {b.label}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {sections.map(s => (
        <div key={s.key} style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8,
                        marginBottom: 8 }}>
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                           letterSpacing: '.06em',
                           color: 'var(--god-muted, #6b7280)' }}>{s.label}</span>
            <span style={{ fontSize: 11, color: '#9ca3af' }}>{s.pct}%</span>
          </div>
          {s.rows.length === 0 ? (
            <p style={{ fontSize: 12, color: '#9ca3af', margin: 0 }}>
              Nothing answered in this section yet.
            </p>
          ) : (
            <div style={{ display: 'grid',
                          gridTemplateColumns: 'repeat(auto-fill,minmax(260px,1fr))',
                          gap: 10 }}>
              {s.rows.map(row => (
                <div key={row.key} style={{ gridColumn: row.long ? '1 / -1' : undefined }}>
                  <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em',
                                textTransform: 'uppercase', color: '#9ca3af' }}>
                    {row.label}
                  </div>
                  <div style={{ fontSize: 13, marginTop: 2,
                                whiteSpace: row.long ? 'pre-wrap' : 'normal',
                                color: row.secure ? '#6b7280' : 'inherit',
                                fontStyle: row.secure ? 'italic' : 'normal' }}>
                    {row.value}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      {hasSecret ? (
        <p style={{ fontSize: 11, color: '#9ca3af', margin: '0 0 14px' }}>
          {SECRET_NOTE}
        </p>
      ) : null}

      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
                    letterSpacing: '.06em', color: 'var(--god-muted, #6b7280)',
                    margin: '16px 0 6px' }}>
        Files ({detail.files?.length || 0})
      </div>
      {(detail.files || []).length === 0
        ? <p style={{ fontSize: 12, color: '#9ca3af' }}>Nothing uploaded yet.</p>
        : (detail.files || []).map(f => (
          <div key={f.id} style={{ fontSize: 12, marginBottom: 4 }}>
            {/* A plain href cannot work: the file lives on the API origin and
                the route needs an Authorization header, which a browser
                navigation will not send. */}
            <a href="#" onClick={ev => { ev.preventDefault(); downloadFile(detail.orgId, f) }}>
              {f.filename}
            </a>
            <span style={{ color: '#9ca3af' }}>
              {' '}· {Math.round(f.file_size / 1024)} KB
              {f.label ? ' · ' + f.label : ''}
            </span>
          </div>
        ))}
    </div>
  )
}
