/**
 * PROGRAMS — the app's home. The same Program Access answer as the web
 * launcher: /auth/my-contexts -> programCards() + salesToolCards(), opened
 * with the shared openProgramItem(). A workspace the server did not list is
 * never a row here, and opening a row re-checks on the server.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fetchMyContexts } from '../api/client'
import { programCards } from '../pages/program/programAccessModel'
import { openProgramItem, salesToolCards } from '../pages/program/programDestinations'
import { badge, LABELS } from './teamHelpers'
import { ErrorNote, Icon, Loading, ScreenTitle, useTeam } from './TeamApp'

function iconFor(item) {
  if (item.type === 'deal_builder' || item.type === 'back_office' || item.program === 'back_office') return 'briefcase'
  return 'layers'
}

export default function ProgramsScreen() {
  const navigate = useNavigate()
  const { me, unread } = useTeam()
  const [ctx, setCtx] = useState(null)
  const [error, setError] = useState(null)
  const [n, setN] = useState(0)
  useEffect(() => {
    let live = true
    setError(null)
    fetchMyContexts({ force: n > 0 }).then(c => { if (live) setCtx(c || {}) }).catch(e => { if (live) setError(e) })
    return () => { live = false }
  }, [n])

  const access = (me && me.access) || {}
  let rows = []
  if (ctx) {
    const { visible, others } = programCards(ctx)
    rows = [...visible, ...others, ...salesToolCards(ctx, ctx.deal_builder, '/app/programs')]
  }

  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.programAccess} />
      {access.chat && (
        <ul className="ta-list" aria-label="Team">
          <li><button type="button" className="ta-row" onClick={() => navigate('/app/chat')}>
            <span className="ta-row-icon"><Icon name="chat" /></span>
            <span className="ta-row-main"><span className="ta-row-name">{LABELS.teamChat}</span></span>
            {badge(unread) && <span className="ta-pill">{badge(unread)}</span>}
            <Icon name="chev" size={20} />
          </button></li>
        </ul>
      )}
      {error && <ErrorNote error={error} onRetry={() => setN(x => x + 1)} />}
      {!ctx && !error && <Loading />}
      {ctx && (
        rows.length === 0
          ? <div className="ta-state">No programs are assigned to your account yet.</div>
          : <ul className="ta-list" aria-label="Programs">
            {rows.map(item => (
              <li key={(item.type || 'w') + ':' + (item.key || item.organization_id || item.display)}>
                <button type="button" className="ta-row" onClick={() => openProgramItem(item, navigate)}>
                  <span className="ta-row-icon"><Icon name={iconFor(item)} /></span>
                  <span className="ta-row-main">
                    <span className="ta-row-name">{item.display}</span>
                    {(item.orgLabel || item.kind) && <span className="ta-row-sub">{item.orgLabel || item.kind}</span>}
                  </span>
                  <Icon name="chev" size={20} />
                </button>
              </li>
            ))}
          </ul>
      )}
    </div>
  )
}
