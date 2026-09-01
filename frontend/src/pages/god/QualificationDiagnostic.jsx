/**
 * LEAD QUALIFICATION DIAGNOSTIC — God Mode only.
 *
 * Access Diagnostic answers WHAT MAY THIS PERSON REACH. This answers the next
 * question: of the population they may already reach, WHO MAY ACTUALLY BE
 * CONTACTED on a channel, and why not for the rest.
 *
 * EVERY NUMBER AND EVERY REASON COMES FROM THE SERVER. This file contains no
 * list of reason codes, no thresholds and no bucket logic - it renders what the
 * engine returned and nothing else. A hardcoded reason here would eventually
 * disagree with the engine, and the screen would be confidently wrong about
 * why somebody was excluded.
 *
 * READ-ONLY. There is no send control on this page, no assignment, no campaign
 * creation, and the endpoint behind it writes nothing. The operator is
 * inspecting another user's authorized population; the diagnostic runs the
 * engine AS that user and cannot widen their scope.
 *
 * A FAILED REQUEST IS NEVER RENDERED AS ZERO. That is the same rule the
 * dashboard now follows, and it matters more here: a diagnostic that shows
 * "0 ready" because the server did not answer would send somebody looking for
 * a data problem that does not exist.
 */
import { useState, useEffect } from 'react'
import { api } from '../../api/client'

const MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

// The channels offered here come from the server's own vocabulary, fetched on
// mount. The fallback below is used only if that call fails, and it offers the
// one channel the engine is authoritative for rather than pretending to more.
const FALLBACK_CHANNELS = ['email']

function Row({ label, value, mono }) {
  return (
    <div style={{ display: 'flex', gap: 16, padding: '5px 0',
                  borderBottom: '1px solid rgba(128,128,128,0.14)' }}>
      <div style={{ minWidth: 210, opacity: 0.62, fontSize: 12.5 }}>{label}</div>
      <div style={{ fontSize: 13.5, fontFamily: mono ? MONO : 'inherit',
                    wordBreak: 'break-all' }}>
        {value === null || value === undefined || value === ''
          ? <span style={{ opacity: 0.4 }}>—</span>
          : String(value)}
      </div>
    </div>
  )
}

function Panel({ title, children }) {
  return (
    <section style={{ marginTop: 26 }}>
      <h3 style={{ fontSize: 11.5, letterSpacing: '0.09em',
                   textTransform: 'uppercase', opacity: 0.55, margin: '0 0 8px' }}>
        {title}
      </h3>
      {children}
    </section>
  )
}

/**
 * A headline count.
 *
 * `value` is rendered exactly as given. `null` and `undefined` render an
 * em-dash, never 0 - "we did not get a number" and "the number is zero" are
 * different facts and a diagnostic that conflates them is worse than useless.
 */
function Card({ label, value, tone, hint }) {
  const colors = {
    neutral: ['rgba(128,128,128,0.10)', 'inherit'],
    good:    ['rgba(30,200,130,0.12)', '#1a9c6b'],
    warn:    ['rgba(235,170,40,0.14)', '#b6830f'],
    bad:     ['rgba(240,80,80,0.12)', '#d8434a'],
  }[tone || 'neutral']
  const known = value !== null && value !== undefined
  return (
    <div style={{ flex: '1 1 150px', minWidth: 150, padding: '14px 16px',
                  borderRadius: 10, background: colors[0],
                  border: '1px solid rgba(128,128,128,0.20)' }}>
      <div style={{ fontSize: 10.5, letterSpacing: '0.08em',
                    textTransform: 'uppercase', opacity: 0.6 }}>{label}</div>
      <div style={{ fontSize: 30, fontWeight: 700, marginTop: 4, color: colors[1],
                    fontVariantNumeric: 'tabular-nums' }}>
        {known ? Number(value).toLocaleString('en-US')
               : <span style={{ opacity: 0.35, fontSize: 24 }}>—</span>}
      </div>
      {hint && <div style={{ fontSize: 11.5, opacity: 0.55, marginTop: 2 }}>{hint}</div>}
    </div>
  )
}

/**
 * A reason breakdown, rendered straight from the engine.
 *
 * Renders NOTHING invented. If the engine returns an empty list the panel says
 * so plainly rather than showing a set of plausible-looking zeros.
 */
function Reasons({ title, rows, tone }) {
  const bar = { good: '#1a9c6b', warn: '#b6830f', bad: '#d8434a' }[tone] || '#888'
  const total = (rows || []).reduce((n, r) => n + (r.count || 0), 0)
  return (
    <Panel title={title}>
      {!rows || rows.length === 0 ? (
        <div style={{ fontSize: 13.5, opacity: 0.6 }}>
          The engine reported none in this category.
        </div>
      ) : rows.map(r => (
        <div key={r.code} style={{ display: 'flex', alignItems: 'center', gap: 12,
                                   padding: '7px 0',
                                   borderBottom: '1px solid rgba(128,128,128,0.14)' }}>
          <div style={{ minWidth: 54, textAlign: 'right', fontWeight: 700,
                        fontSize: 14, color: bar,
                        fontVariantNumeric: 'tabular-nums' }}>
            {Number(r.count).toLocaleString('en-US')}
          </div>
          <div style={{ flex: 1, fontSize: 13.5 }}>{r.label}</div>
          <div style={{ fontSize: 11, opacity: 0.42, fontFamily: MONO }}>{r.code}</div>
          <div style={{ width: 90, height: 6, borderRadius: 3,
                        background: 'rgba(128,128,128,0.16)', overflow: 'hidden' }}>
            <div style={{ width: total ? `${(r.count / total) * 100}%` : 0,
                          height: '100%', background: bar }} />
          </div>
        </div>
      ))}
    </Panel>
  )
}


/**
 * THE RECONCILIATION BANNER.
 *
 * `counts_agree` compares the number of leads the subject is AUTHORIZED to see
 * against the number the engine actually evaluated. They should be identical:
 * qualification starts from the authorized query and only ever narrows what it
 * decides, never what it looks at.
 *
 * If they diverge, the qualified population is not the authorized population,
 * and nothing on the screen below should be treated as a send list - which is
 * why this says so in those words rather than showing a small grey warning.
 */
function Reconciliation({ run }) {
  if (!run || run.counts_agree === undefined || run.counts_agree === null) return null
  const ok = run.counts_agree === true
  return (
    <div style={{
      marginTop: 18, padding: '14px 18px', borderRadius: 10,
      display: 'flex', alignItems: 'flex-start', gap: 12,
      background: ok ? 'rgba(30,200,130,0.12)' : 'rgba(240,80,80,0.14)',
      border: `1px solid ${ok ? 'rgba(30,200,130,0.4)' : 'rgba(240,80,80,0.55)'}`,
    }} role={ok ? undefined : 'alert'}>
      <div style={{ fontSize: 20, lineHeight: 1 }} aria-hidden="true">
        {ok ? '✓' : '⚠'}
      </div>
      <div>
        <div style={{ fontWeight: 700, fontSize: ok ? 14 : 16,
                      color: ok ? '#1a9c6b' : '#d8434a',
                      letterSpacing: ok ? 0 : '0.01em' }}>
          {ok ? 'AUTHORIZED POPULATION RECONCILED'
              : 'QUALIFICATION POPULATION DOES NOT MATCH AUTHORIZED SCOPE'}
        </div>
        <div style={{ fontSize: 13, opacity: 0.75, marginTop: 3 }}>
          {ok
            ? `All ${Number(run.total_authorized).toLocaleString('en-US')} authorized leads were evaluated.`
            : `${Number(run.total_authorized).toLocaleString('en-US')} authorized, `
              + `${Number(run.total_selected).toLocaleString('en-US')} evaluated. `
              + 'DO NOT treat this as send-ready - the difference must be explained first.'}
        </div>
      </div>
    </div>
  )
}

/**
 * CAN THE PRIORITY ACTUALLY TELL THESE LEADS APART.
 *
 * The counts above cannot answer that. "94 HIGH" looks like a finding and can
 * equally mean the band is a constant - which is exactly what the first
 * production run turned out to be. Spread is the number that tells them apart,
 * so it is shown next to the counts rather than buried in the raw report, and
 * the server states the verdict in a sentence rather than leaving it to be
 * inferred from a histogram nobody reads.
 */
function PriorityAudit({ audit }) {
  const degenerate = audit.distinct_scores !== null
    && audit.distinct_scores !== undefined && audit.distinct_scores <= 1
  const thin = !degenerate && audit.spread !== null
    && audit.spread !== undefined && audit.spread < 10
  const tone = degenerate ? 'bad' : thin ? 'warn' : 'good'
  const inputs = audit.inputs || {}
  const total = inputs.total_leads || 0

  return (
    <Panel title="Priority quality — does the band distinguish anything">
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Card label="Distinct scores" value={audit.distinct_scores} tone={tone} />
        <Card label="Spread" value={audit.spread} tone={tone}
              hint="highest minus lowest" />
        <Card label="Lowest" value={audit.min_score} />
        <Card label="Median" value={audit.median_score} />
        <Card label="Highest" value={audit.max_score} />
      </div>

      {audit.verdict && (
        <div role={degenerate ? 'alert' : undefined}
             style={{ marginTop: 14, padding: '12px 16px', borderRadius: 10,
                      fontSize: 13.5, fontWeight: degenerate ? 600 : 400,
                      background: degenerate ? 'rgba(240,80,80,0.14)'
                        : thin ? 'rgba(235,170,40,0.14)' : 'rgba(30,200,130,0.12)',
                      color: degenerate ? '#d8434a' : thin ? '#b6830f' : '#1a9c6b',
                      border: `1px solid ${degenerate ? 'rgba(240,80,80,0.5)'
                        : 'rgba(128,128,128,0.25)'}` }}>
          {audit.verdict}
        </div>
      )}

      {audit.score_histogram && (
        <div style={{ marginTop: 14 }}>
          {Object.entries(audit.score_histogram).map(([band, n]) => (
            <div key={band} style={{ display: 'flex', alignItems: 'center',
                                     gap: 12, padding: '4px 0' }}>
              <span style={{ minWidth: 70, fontFamily: MONO, fontSize: 12,
                             opacity: 0.7 }}>{band}</span>
              <span style={{ minWidth: 50, textAlign: 'right', fontWeight: 700,
                             fontSize: 13,
                             fontVariantNumeric: 'tabular-nums' }}>{n}</span>
              <span style={{ flex: 1, height: 8, borderRadius: 4,
                             background: 'rgba(128,128,128,0.16)' }}>
                <span style={{ display: 'block', height: '100%', borderRadius: 4,
                               width: audit.scored_leads
                                 ? `${(n / audit.scored_leads) * 100}%` : 0,
                               background: '#7a8ba0' }} />
              </span>
            </div>
          ))}
        </div>
      )}

      {/* THE RAW INPUTS. A factor true for everybody is traced to the field
          that made it true - which is how a value stamped on a whole import
          file gets caught being read as a fact about a person. */}
      <div style={{ marginTop: 18 }}>
        <div style={{ fontSize: 11.5, letterSpacing: '0.06em',
                      textTransform: 'uppercase', opacity: 0.5,
                      marginBottom: 6 }}>
          What the factors are computed from
        </div>
        {inputs.relationship_type_distribution && (
          <Row label="relationship_type"
               value={Object.entries(inputs.relationship_type_distribution)
                 .map(([k, v]) => `${k}: ${v}`).join('   ')
                 + (Object.keys(inputs.relationship_type_distribution).length === 1
                    && total > 1
                      ? '   ← one value for the whole book: an import setting, not a per-lead fact'
                      : '')} />
        )}
        <Row label="Imported last contact date"
             value={`${inputs.with_imported_last_contact_date} of ${total}`} />
        <Row label="Imported last action"
             value={`${inputs.with_imported_last_action} of ${total}`} />
        <Row label="Imported status reason"
             value={`${inputs.with_imported_status_reason} of ${total}`} />
        <Row label="Messaged from this platform"
             value={`${inputs.with_platform_last_messaged_at} of ${total}`} />
        <Row label="Has both names"
             value={`${inputs.with_both_names} of ${total}`} />
        <Row label="Has ZIP" value={`${inputs.with_zip_code} of ${total}`} />
        <Row label="Has street address"
             value={`${inputs.with_street_address} of ${total}`} />
        {inputs.source_year_distribution && (
          <Row label="Source year"
               value={Object.entries(inputs.source_year_distribution)
                 .map(([k, v]) => `${k}: ${v}`).join('   ')} />
        )}
      </div>
    </Panel>
  )
}

/**
 * One run: a workspace scenario, its counts and its reasons.
 *
 * A run that ERRORED renders the error. It does not render zeros, and it does
 * not render nothing - a scenario that failed is itself a finding.
 */
function Run({ run, subject, channel }) {
  if (run.error) {
    return (
      <section style={{ marginTop: 26, padding: '14px 18px', borderRadius: 10,
                        background: 'rgba(240,80,80,0.10)',
                        border: '1px solid rgba(240,80,80,0.35)' }}>
        <div style={{ fontWeight: 600, fontSize: 13.5, color: '#d8434a' }}>
          {run.scenario} — this scenario could not be evaluated
        </div>
        <div style={{ fontSize: 12.5, opacity: 0.8, marginTop: 4, fontFamily: MONO }}>
          {run.error}
        </div>
        <div style={{ fontSize: 12.5, opacity: 0.7, marginTop: 6 }}>
          No counts are shown for it, because there are none — not zero.
        </div>
      </section>
    )
  }

  const pr = run.priority || {}
  return (
    <section style={{ marginTop: 30, paddingTop: 8,
                      borderTop: '1px solid rgba(128,128,128,0.22)' }}>
      <h3 style={{ fontSize: 13, margin: '10px 0 2px' }}>{run.scenario}</h3>

      <Panel title="Subject and context">
        <Row label="User" value={subject?.full_name} />
        <Row label="Email" value={subject?.email} />
        <Row label="Workspace" value={run.resolved_workspace_name} />
        <Row label="Workspace id" value={run.resolved_workspace_id} mono />
        <Row label="Workspace role" value={run.resolved_role} />
        <Row label="Channel" value={channel} />
        <Row label="Generated at" value={new Date().toLocaleString()} />
      </Panel>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 20 }}>
        <Card label="Authorized leads" value={run.total_authorized}
              hint="what lead_scope allows this person" />
        <Card label="Selected" value={run.total_selected}
              hint="what the engine evaluated" />
        <Card label="Ready" value={run.ready} tone="good"
              hint="may be sent on this channel" />
        <Card label="Review required" value={run.review} tone="warn"
              hint="a person should look first" />
        <Card label="Excluded" value={run.excluded} tone="bad"
              hint="must not be contacted here" />
      </div>

      <Reconciliation run={run} />

      <Panel title="Ready priority breakdown">
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Card label="High" value={pr.HIGH} tone="good" />
          <Card label="Medium" value={pr.MEDIUM} />
          <Card label="Low" value={pr.LOW} />
        </div>
        <div style={{ fontSize: 12, opacity: 0.55, marginTop: 8 }}>
          Bands are the sum of the named factors below — no opaque score.
        </div>
      </Panel>

      {run.priority_audit && <PriorityAudit audit={run.priority_audit} />}

      <Reasons title="Why leads qualified — priority factors"
               rows={run.priority_factors} tone="good" />
      <Reasons title="Why human review is required"
               rows={run.review_reasons} tone="warn" />
      <Reasons title="Why leads were excluded"
               rows={run.exclusion_reasons} tone="bad" />

      {Array.isArray(run.sample) && run.sample.length > 0 && (
        <Panel title={`Lead detail — first ${run.sample.length} of this person's own book`}>
          <div style={{ fontSize: 12, opacity: 0.55, marginBottom: 8 }}>
            Ids and decisions only. No names, addresses or phone numbers, and
            nothing outside the subject's own authorized scope.
          </div>
          {run.sample.map(s => (
            <div key={s.lead_id}
                 style={{ display: 'flex', alignItems: 'center', gap: 12,
                          padding: '6px 0',
                          borderBottom: '1px solid rgba(128,128,128,0.14)' }}>
              <span style={{ fontFamily: MONO, fontSize: 11.5, opacity: 0.6,
                             minWidth: 250 }}>{s.lead_id}</span>
              <span style={{ fontSize: 12, fontWeight: 700, minWidth: 130 }}>
                {s.bucket}
              </span>
              <span style={{ fontSize: 12, opacity: 0.7, minWidth: 70 }}>
                {s.priority || '—'}
              </span>
              <span style={{ fontSize: 12, opacity: 0.7, minWidth: 50,
                             fontVariantNumeric: 'tabular-nums' }}>
                {s.score === null || s.score === undefined ? '—' : s.score}
              </span>
              <span style={{ fontSize: 11.5, opacity: 0.55, fontFamily: MONO }}>
                {(s.reasons || []).join(', ')}
              </span>
            </div>
          ))}
        </Panel>
      )}
    </section>
  )
}


export default function QualificationDiagnostic() {
  const [ident, setIdent] = useState('')
  const [channel, setChannel] = useState('email')
  const [channels, setChannels] = useState(null)   // null = not yet loaded
  const [authoritative, setAuthoritative] = useState(FALLBACK_CHANNELS)
  const [withDetail, setWithDetail] = useState(false)
  const [report, setReport] = useState(null)
  const [state, setState] = useState('idle')       // idle|loading|ok|error
  const [problem, setProblem] = useState(null)

  // THE CHANNEL LIST COMES FROM THE SERVER, so this page cannot offer a channel
  // the engine does not know about. In an effect, not in the render body: a
  // fetch during render sets state during render, and React re-renders on that,
  // which is an infinite request loop pointed at production.
  useEffect(() => {
    let live = true
    api.get('/qualification/vocabulary')
      .then(v => {
        if (!live) return
        setChannels(v.channels || FALLBACK_CHANNELS)
        setAuthoritative(v.authoritative_channels || FALLBACK_CHANNELS)
      })
      .catch(() => {
        // Offer only what is known to be authoritative rather than guessing
        // wider. A failure here must not invent a channel.
        if (!live) return
        setChannels(FALLBACK_CHANNELS)
        setAuthoritative(FALLBACK_CHANNELS)
      })
    return () => { live = false }
  }, [])

  /**
   * EVERY FAILURE GETS ITS OWN HONEST STATE.
   *
   * The old shape of this mistake is one `.catch` that sets an error string,
   * which turns a 403, a missing user and a dropped connection into the same
   * sentence. They need different actions from the person reading them, so
   * they are told apart here by status and named.
   */
  function describe(err) {
    const status = err?.status
    if (status === 401) return {
      title: 'Your session has expired',
      body: 'Sign in again — nothing about the diagnostic failed.',
    }
    if (status === 403) return {
      title: 'Permission denied',
      body: 'This diagnostic is restricted to the platform owner. The server '
          + 'refused the request; the page did not.',
    }
    if (status === 404) return {
      title: 'No user matches that exactly',
      body: 'Lookup is exact by design — an email or a user id, never a partial '
          + 'match. Two people sharing a name is how a diagnostic ends up '
          + 'confidently describing the wrong person.',
    }
    if (status === 400) return {
      title: 'The request was refused',
      body: err?.message || 'Check the channel and try again.',
    }
    if (status >= 500) return {
      title: 'The server could not complete the diagnostic',
      body: `HTTP ${status}. This is not a result — no counts are shown, because `
          + 'there are none. Try again in a moment.',
    }
    return {
      title: 'The server did not answer',
      body: (err?.message || 'The request failed before a response arrived.')
          + ' No counts are shown: a failed request is not a population of zero.',
    }
  }

  async function run(e) {
    e && e.preventDefault()
    setState('loading'); setProblem(null); setReport(null)
    try {
      const q = ident.includes('@')
        ? `email=${encodeURIComponent(ident.trim())}`
        : `user_id=${encodeURIComponent(ident.trim())}`
      const detail = withDetail ? '&sample=50' : ''
      const data = await api.get(
        `/god/ops/diagnostics/qualification?${q}&channel=${encodeURIComponent(channel)}${detail}`)
      setReport(data)
      setState('ok')
    } catch (err) {
      setProblem(describe(err))
      setState('error')
    }
  }

  const r = report
  const runs = r?.runs || []
  const offer = channels || FALLBACK_CHANNELS

  return (
    <div style={{ maxWidth: 940, padding: '4px 0 60px' }}>
      <h2 style={{ margin: '0 0 4px', fontSize: 20 }}>Lead Qualification Diagnostic</h2>
      <p style={{ margin: '0 0 18px', opacity: 0.62, fontSize: 13.5, maxWidth: 700 }}>
        Read-only. Runs the platform qualification engine as one named person,
        over their own authorized leads, for one channel. It writes nothing,
        sends nothing, and cannot widen that person's scope.
      </p>

      <form onSubmit={run} style={{ display: 'flex', gap: 10, maxWidth: 760,
                                    flexWrap: 'wrap', alignItems: 'center' }}>
        <input
          value={ident}
          onChange={e => setIdent(e.target.value)}
          placeholder="exact email, or user id"
          style={{ flex: '1 1 280px', padding: '10px 12px', borderRadius: 8,
                   fontSize: 14, border: '1px solid rgba(128,128,128,0.34)',
                   background: 'transparent', color: 'inherit' }}
        />
        <select value={channel} onChange={e => setChannel(e.target.value)}
                style={{ padding: '10px 12px', borderRadius: 8, fontSize: 14,
                         border: '1px solid rgba(128,128,128,0.34)',
                         background: 'transparent', color: 'inherit' }}>
          {offer.map(ch => (
            <option key={ch} value={ch}>
              {ch.toUpperCase()}
              {authoritative.includes(ch) ? '' : ' — not yet authoritative'}
            </option>
          ))}
        </select>
        <button type="submit" disabled={state === 'loading' || !ident.trim()}
                style={{ padding: '10px 18px', borderRadius: 8, fontSize: 14,
                         fontWeight: 600,
                         cursor: state === 'loading' ? 'default' : 'pointer',
                         border: '1px solid rgba(128,128,128,0.34)',
                         background: 'rgba(128,128,128,0.12)', color: 'inherit' }}>
          {state === 'loading' ? 'Running…' : 'Run Qualification'}
        </button>
        <label style={{ fontSize: 12.5, opacity: 0.7, display: 'flex',
                        alignItems: 'center', gap: 6 }}>
          <input type="checkbox" checked={withDetail}
                 onChange={e => setWithDetail(e.target.checked)} />
          include lead detail
        </label>
      </form>

      {!authoritative.includes(channel) && (
        <div style={{ marginTop: 12, padding: '9px 14px', borderRadius: 8,
                      fontSize: 13, background: 'rgba(235,170,40,0.14)',
                      color: '#b6830f' }}>
          The engine is not yet authoritative for {channel.toUpperCase()}. These
          counts are a preview; that channel's existing guards remain the
          enforcement path until it is migrated and independently tested.
        </div>
      )}

      {state === 'loading' && (
        <div style={{ marginTop: 18, fontSize: 13.5, opacity: 0.7 }}>
          Running the engine over this person's authorized leads…
        </div>
      )}

      {state === 'error' && problem && (
        <div role="alert" style={{ marginTop: 18, padding: '12px 16px',
                      borderRadius: 8, background: 'rgba(240,80,80,0.12)',
                      border: '1px solid rgba(240,80,80,0.4)' }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: '#d8434a' }}>
            {problem.title}
          </div>
          <div style={{ fontSize: 13, opacity: 0.85, marginTop: 3 }}>
            {problem.body}
          </div>
        </div>
      )}

      {state === 'ok' && r?.error && (
        <div style={{ marginTop: 18, padding: '12px 16px', borderRadius: 8,
                      background: 'rgba(235,170,40,0.14)', color: '#b6830f',
                      fontSize: 13.5 }}>
          {r.error}
        </div>
      )}

      {state === 'ok' && !r?.error && runs.length === 0 && (
        <div style={{ marginTop: 18, padding: '12px 16px', borderRadius: 8,
                      background: 'rgba(128,128,128,0.10)', fontSize: 13.5 }}>
          <strong>No scenario could be evaluated for this person.</strong>
          <div style={{ opacity: 0.75, marginTop: 3 }}>
            They hold no active customer workspace membership, so there is no
            workspace to qualify leads in. Run the Access Diagnostic to see why.
          </div>
        </div>
      )}

      {state === 'ok' && runs.map((run, i) => (
        <Run key={i} run={run} subject={r.subject} channel={r.channel} />
      ))}

      {state === 'ok' && r && (
        <details style={{ marginTop: 30 }}>
          <summary style={{ cursor: 'pointer', fontSize: 12.5, opacity: 0.6 }}>
            Raw report
          </summary>
          <pre style={{ marginTop: 10, padding: 14, borderRadius: 8, fontSize: 11.5,
                        overflowX: 'auto', background: 'rgba(128,128,128,0.10)' }}>
            {JSON.stringify(r, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}
