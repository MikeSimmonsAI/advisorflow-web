/* THREE SEPARATE ANSWERS (server: opportunity.measures) and WHY THIS SCORE.
 *
 *   Auction urgency  how soon a VERIFIED sale is scheduled
 *   Deal potential   what the supported numbers say - "unknown" is not "bad"
 *   Data confidence  how much verified evidence the assessment rests on
 *
 * Each shows its own level and reason; none is folded into another. The score
 * explanation uses the engine's own points - nothing is computed here. */
import { Tag } from '../ds/ds'

const URG_TONE = { CRITICAL: 'danger', URGENT_VERIFY: 'danger', UPCOMING: 'estimate', VERIFY: 'estimate',
  OUTCOME_UNVERIFIED: 'estimate', POSTPONED: 'estimate', CANCELED: 'info', COMPLETED: 'info', NONE: undefined }
export const WORK_TONE = { CRITICAL: 'danger', REVIEW_URGENT: 'danger', HIGH: 'estimate', NORMAL: undefined,
  REVIEW: 'estimate', CLOSED: 'info' }
export const WORK_OPTIONS = [['CRITICAL', 'Critical — act now'], ['REVIEW_URGENT', 'Review required — urgent'],
  ['HIGH', 'High — work next'], ['NORMAL', 'Normal'], ['REVIEW', 'Review required'], ['CLOSED', 'Closed / inactive']]

const fmtDate = (d) => {
  if (!d) return null
  const [y, m, day] = d.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, day)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).toUpperCase()
}

/* THE WORK PRIORITY (server: work_priority.py) - the same object on every screen:
 * AUCTION date (and how sure we are), URGENCY, WORK PRIORITY, NEXT ACTION. */
export function WorkCell({ w, compact }) {
  if (!w) return <span className="evo-muted evo-small">—</span>
  return (
    <span className="evo-work" data-testid="work-priority" style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <Tag kind={WORK_TONE[w.work_priority]}>{w.work_priority_label || w.work_priority}</Tag>
      {w.urgency_label && w.urgency !== 'NONE' ? <span className="evo-small"><b>{w.urgency_label}</b></span> : null}
      {w.auction_date ? (
        <span className="evo-small evo-muted">{w.auction_kind || 'Auction'} {fmtDate(w.auction_date)}
          {w.auction_verified === false ? ' · not verified' : w.auction_verified ? ' · verified' : ''}</span>
      ) : null}
      {!compact && w.next_action ? <span className="evo-small">Next: {w.next_action}</span> : null}
    </span>
  )
}
const POT_TONE = { HIGH: 'live', MEDIUM: 'live', LOW: undefined, UNKNOWN: 'estimate', INSUFFICIENT: 'estimate' }
const CONF_TONE = { HIGH: 'live', MEDIUM: 'estimate', LOW: 'danger' }

export function MeasureTags({ m, compact }) {
  if (!m) return null
  const au = m.auction_urgency || {}
  const dp = m.deal_potential || {}
  const dc = m.data_confidence || {}
  return (
    <span className="evo-measures" style={{ display: 'flex', flexDirection: compact ? 'column' : 'row', gap: 4, flexWrap: 'wrap' }}>
      {m.work_priority ? <span title={m.work_priority.reason || ''}><span className="evo-small evo-muted">Work </span>
        <Tag kind={WORK_TONE[m.work_priority.level]}>{m.work_priority.label}</Tag></span> : null}
      <span title={au.reason || ''}><span className="evo-small evo-muted">Urgency </span>
        <Tag kind={URG_TONE[au.level]}>{au.label || '—'}</Tag></span>
      <span title={dp.reason || ''}><span className="evo-small evo-muted">Potential </span>
        <Tag kind={POT_TONE[dp.level]}>{dp.label || '—'}</Tag></span>
      <span title={dc.reason || ''}><span className="evo-small evo-muted">Confidence </span>
        <Tag kind={CONF_TONE[dc.level]}>{dc.label || '—'}</Tag></span>
    </span>
  )
}

export function MeasurePanel({ m }) {
  if (!m) return null
  const rows = [
    ['Work priority', m.work_priority && { ...m.work_priority, label: m.work_priority.label, level: m.work_priority.level }, WORK_TONE],
    ['Auction urgency', m.auction_urgency, URG_TONE],
    ['Deal potential', m.deal_potential, POT_TONE],
    ['Data confidence', m.data_confidence, CONF_TONE],
  ]
  const ind = m.deal_potential?.indicators
  return (
    <div className="evo-measure-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10, marginTop: 10 }}>
      {rows.map(([title, x, tone]) => (
        <div key={title} className="evo-measure" style={{ border: '1px solid var(--evo-line, #e5e7eb)', borderRadius: 8, padding: 10 }}>
          <div className="evo-small" style={{ textTransform: 'uppercase', letterSpacing: '.06em' }}>{title}</div>
          <div style={{ margin: '4px 0' }}><Tag kind={tone[x?.level]}>{x?.label || '—'}</Tag></div>
          <div className="evo-small evo-muted">{x?.reason || ''}</div>
          {title === 'Deal potential' && ind?.reading ? (
            <div className="evo-small" style={{ marginTop: 4 }}>Indicators: <b>{ind.reading}</b> (score {ind.score}) — not a profit estimate</div>
          ) : null}
        </div>
      ))}
      {m.next_best_action?.label || m.next_best_action?.work ? (
        <div className="evo-measure" style={{ border: '1px solid var(--evo-line, #e5e7eb)', borderRadius: 8, padding: 10 }}>
          <div className="evo-small" style={{ textTransform: 'uppercase', letterSpacing: '.06em' }}>Next action</div>
          <div style={{ margin: '4px 0' }}><b>{m.next_best_action.work || m.next_best_action.label}</b></div>
          <div className="evo-small evo-muted">{m.next_best_action.work ? m.next_best_action.label : m.next_best_action.why}</div>
        </div>
      ) : null}
    </div>
  )
}

export function ScoreExplanation({ ex, missing }) {
  if (!ex) return null
  return (
    <div className="evo-small" style={{ marginTop: 12 }} data-testid="score-explanation">
      <p style={{ margin: '0 0 4px' }}><b>Investment evidence score: {ex.score ?? 'insufficient evidence'}</b>
        {ex.label ? ` · ${ex.label}` : ''}</p>
      <p style={{ margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: '.06em' }}>Why this score?</p>
      <table className="evo-table evo-small">
        <caption className="evo-sr">Score parts</caption>
        <tbody>
          {(ex.lines || []).map((l) => (
            <tr key={l.group}>
              <th scope="row" style={{ textAlign: 'left', fontWeight: 600 }}>{l.group}</th>
              <td className="is-num"><b>{l.points > 0 ? `+${l.points}` : l.points}</b></td>
              <td className="evo-muted">{l.items.map((i) => i.label).join(' · ')}</td>
            </tr>
          ))}
          <tr><th scope="row" style={{ textAlign: 'left' }}>Total</th><td className="is-num"><b>{ex.score ?? '—'}</b></td>
            <td className="evo-muted">{ex.note || ''}</td></tr>
        </tbody>
      </table>
      {ex.not_scored?.length ? (
        <p className="evo-muted" style={{ margin: '6px 0 0' }}>Shown, not scored: {ex.not_scored.join(' · ')}</p>
      ) : null}
      {missing?.length ? (
        <>
          <p style={{ margin: '8px 0 2px' }}><b>Missing inputs</b> (unknown - not counted for or against)</p>
          <ul style={{ margin: 0, paddingLeft: 18 }}>{missing.map((x) => <li key={x}>{x}</li>)}</ul>
        </>
      ) : null}
      <p className="evo-muted" style={{ margin: '6px 0 0' }}>{ex.scale}</p>
    </div>
  )
}
