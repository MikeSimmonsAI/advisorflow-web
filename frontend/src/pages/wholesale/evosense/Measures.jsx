/* THREE SEPARATE ANSWERS (server: opportunity.measures) and WHY THIS SCORE.
 *
 *   Auction urgency  how soon a VERIFIED sale is scheduled
 *   Deal potential   what the supported numbers say - "unknown" is not "bad"
 *   Data confidence  how much verified evidence the assessment rests on
 *
 * Each shows its own level and reason; none is folded into another. The score
 * explanation uses the engine's own points - nothing is computed here. */
import { Tag } from '../ds/ds'

const URG_TONE = { HIGH: 'danger', VERIFY: 'danger', MEDIUM: 'estimate', UNVERIFIED: 'estimate', LOW: undefined,
  PASSED: 'estimate', POSTPONED: 'estimate', CANCELED: 'info', COMPLETED: 'info', NONE: undefined }
const POT_TONE = { HIGH: 'live', MEDIUM: 'live', LOW: undefined, UNKNOWN: 'estimate', INSUFFICIENT: 'estimate' }
const CONF_TONE = { HIGH: 'live', MEDIUM: 'estimate', LOW: 'danger' }

export function MeasureTags({ m, compact }) {
  if (!m) return null
  const au = m.auction_urgency || {}
  const dp = m.deal_potential || {}
  const dc = m.data_confidence || {}
  return (
    <span className="evo-measures" style={{ display: 'flex', flexDirection: compact ? 'column' : 'row', gap: 4, flexWrap: 'wrap' }}>
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
      {m.next_best_action?.label ? (
        <div className="evo-measure" style={{ border: '1px solid var(--evo-line, #e5e7eb)', borderRadius: 8, padding: 10 }}>
          <div className="evo-small" style={{ textTransform: 'uppercase', letterSpacing: '.06em' }}>Next best action</div>
          <div style={{ margin: '4px 0' }}><b>{m.next_best_action.label}</b></div>
          <div className="evo-small evo-muted">{m.next_best_action.why}</div>
        </div>
      ) : null}
    </div>
  )
}

export function ScoreExplanation({ ex, missing }) {
  if (!ex) return null
  return (
    <div className="evo-small" style={{ marginTop: 12 }} data-testid="score-explanation">
      <p style={{ margin: '0 0 4px' }}><b>Opportunity score: {ex.score ?? 'insufficient evidence'}</b>
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
