/**
 * PLATFORM HEALTH — six real conditions, one server source.
 *
 * Data: GET /god/platform-health. The server computes every section in grouped
 * queries and hands back its own status and prose, so this file has no opinion
 * about whether messaging is healthy and cannot invent one.
 *
 * A section the backend marks `no_source` renders grey and says what would have
 * to be built. It never renders green for silence — a subsystem with no
 * telemetry is not a subsystem that is fine.
 *
 * Clicking a section that carries a `to` goes to the affected resource.
 */
import { NoSource } from './StatusBadge'

const DOT = { ok: 'ok', warn: 'warn', bad: 'bad', off: 'off', no_source: 'off' }

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
        const clickable = !!s.to && typeof onGo === 'function'
        const Tag = clickable ? 'button' : 'div'
        return (
          <Tag
            key={s.key}
            type={clickable ? 'button' : undefined}
            className={`gm-health ${clickable ? 'gm-click' : ''}`}
            onClick={clickable ? () => onGo(s.to) : undefined}
            title={clickable ? 'Open the affected resource' : undefined}
          >
            <span className="gm-health-top">
              <b>{s.label}</b>
              <i className={`gm-dot ${DOT[s.status] || 'off'}`} />
            </span>
            <p style={{ marginBottom: 5, color: '#a9c0d6', fontSize: 9.5, fontWeight: 600 }}>
              {s.status === 'no_source' ? <NoSource>{s.headline}</NoSource> : s.headline}
            </p>
            <p>{s.detail}</p>
            {s.needs ? (
              <p style={{ marginTop: 6, color: '#4a637f' }}>needs: {s.needs}</p>
            ) : null}
          </Tag>
        )
      })}
    </div>
  )
}
