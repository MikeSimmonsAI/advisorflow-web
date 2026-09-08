/**
 * PLATFORM HEALTH — six real conditions, one server source, written for the
 * person who owns the business.
 *
 * Data: GET /god/platform-health. The server computes every section in grouped
 * queries and hands back its own severity and prose, so this file has no
 * opinion about whether messaging is healthy and cannot invent one.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT THIS PASS FIXED
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * The grid used to answer in our vocabulary. A section with no telemetry
 * rendered the words "no source" in grey italics, followed by a line reading
 * "needs: invoices + payments tables". Both were true. Neither told Mike
 * anything about his business — "no source" does not say whether the gap is in
 * his setup or in our code, and a table name is a ticket in our repo, not a
 * decision he can make.
 *
 * The server now sends owner-facing prose and a `severity` from the
 * platform-wide vocabulary. This file renders that vocabulary and nothing else:
 *
 *   HEALTHY          working, and we can see that it is working
 *   NEEDS ATTENTION  working, but something needs a decision soon
 *   ACTION REQUIRED  not working, or costing money right now
 *   CAN'T CHECK      we cannot see this — NOT a report that it is fine
 *   NOTHING YET      nothing has happened here, so there is nothing to measure
 *
 * The last two are the ones a three-state model keeps collapsing into green.
 * They are distinct here, they are distinct in the tokens, and neither of them
 * renders as a healthy tile.
 *
 * `technical` — an actual exception, when a check itself fell over — is kept
 * out of the sentence an owner reads and shown in small print underneath, so
 * the diagnostic is not lost and is not the headline.
 *
 * Clicking a section that carries a `to` goes to the affected resource.
 */

/* Legacy `status` is still sent for older readers; `severity` is the one this
 * renders. Falling back through normalize-equivalents here rather than
 * assuming, so a deploy where the API is briefly older does not render blanks. */
const LEGACY_TO_SEVERITY = {
  ok: 'healthy',
  warn: 'attention',
  bad: 'action_required',
  off: 'no_data',
  no_source: 'unavailable',
}

/* The dot keeps its own scale because it is a GLANCE affordance: an owner
 * scanning six tiles reads colour before words. "Can't check" and "nothing
 * yet" both render unlit — neither has earned a colour. */
const DOT = {
  healthy: 'ok',
  attention: 'warn',
  action_required: 'bad',
  unavailable: 'off',
  no_data: 'off',
}

function severityOf(section) {
  return section.severity || LEGACY_TO_SEVERITY[section.status] || 'unavailable'
}

function SeverityPill({ severity, label }) {
  return (
    <span className={'gm-sev sv-' + severity}>
      {label || severity.replace('_', ' ')}
    </span>
  )
}

export default function PlatformHealth({ data, loading, error, onGo }) {
  if (loading) {
    return <div className="gm-card gm-empty">Reading platform conditions…</div>
  }
  if (error) {
    return (
      <div className="gm-card gm-empty" style={{ color: '#ff8299' }}>
        Platform health is unavailable: {error}
      </div>
    )
  }
  const sections = (data && data.sections) || []
  if (!sections.length) {
    return <div className="gm-card gm-empty">No health sections were returned.</div>
  }

  return (
    <div className="gm-healths">
      {sections.map(s => {
        const sv = severityOf(s)
        const clickable = !!s.to && typeof onGo === 'function'
        const Tag = clickable ? 'button' : 'div'
        return (
          <Tag
            key={s.key}
            type={clickable ? 'button' : undefined}
            className={`gm-health sv-${sv} ${clickable ? 'gm-click' : ''}`}
            onClick={clickable ? () => onGo(s.to) : undefined}
            title={clickable ? 'Open the affected resource' : undefined}
          >
            <span className="gm-health-top">
              <b>{s.label}</b>
              <i className={`gm-dot ${DOT[sv] || 'off'}`} />
            </span>
            <SeverityPill severity={sv} label={s.severity_label} />
            <p className="gm-health-head">{s.headline}</p>
            <p>{s.detail}</p>
            {/* WHAT WOULD HAVE TO CHANGE — an outcome, in his words, not a
                table name. Only rendered when the server sends one. */}
            {s.needs ? (
              <p className="gm-health-needs">
                <span>To report this we would need</span> {s.needs}.
              </p>
            ) : null}
            {/* The raw diagnostic, deliberately last and deliberately quiet. */}
            {s.technical ? (
              <p className="gm-health-tech">{s.technical}</p>
            ) : null}
          </Tag>
        )
      })}
    </div>
  )
}
