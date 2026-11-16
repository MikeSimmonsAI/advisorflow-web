/**
 * PROGRAM ACCESS — the app's home screen.
 *
 * Exactly the programs desktop Program Access lets this login open: the same
 * /auth/my-contexts answer, flattened by programInventory() (desktop's cards,
 * sales tools, "Other authorized workspaces" and Previews) with no cap and no
 * mobile-only filter. Search narrows that full list; clearing it restores it.
 * A tap opens the entry with the shared openProgramItem() rule, remembering
 * that it came from the app so the workspace's Back / Program Access returns
 * to /app/programs.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fetchMyContexts } from '../api/client'
import { programInventory, programKey } from '../pages/program/programAccessModel'
import { openProgramItem } from '../pages/program/programDestinations'
import { clearLauncherOrigin, MOBILE_PROGRAMS } from '../pages/program/launcherOrigin'
import { LABELS, matchesProgram, programStyle } from './teamHelpers'
import { ErrorNote, Icon, Loading, ScreenTitle } from './TeamApp'

export function ProgramRow({ item, onOpen }) {
  const look = programStyle(item)
  return (
    <button type="button" className="ta-prow" style={{ '--acc': look.accent }} onClick={onOpen}
            data-program-key={programKey(item)}>
      <span className="ta-prow-tile" aria-hidden><Icon name={look.icon} size={22} /></span>
      <span className="ta-prow-main">
        <span className="ta-prow-name">{item.display}</span>
        {item.orgLabel && <span className="ta-prow-sub">{item.orgLabel}</span>}
        {item.type === 'preview' && <span className="ta-prow-sub">Preview</span>}
      </span>
      <Icon name="chev" size={20} />
    </button>
  )
}

export default function ProgramsScreen() {
  const navigate = useNavigate()
  const [ctx, setCtx] = useState(null)
  const [deal, setDeal] = useState(null)
  const [error, setError] = useState(null)
  const [n, setN] = useState(0)
  const [query, setQuery] = useState('')
  // Back on Programs: the round trip is over, so the web launcher is the web
  // launcher again for anything this tab does next.
  useEffect(() => { clearLauncherOrigin() }, [])
  useEffect(() => {
    let live = true
    setError(null)
    fetchMyContexts({ force: n > 0 }).then(c => {
      if (!live) return
      setCtx(c || {})
      // Same fallback as desktop: an older backend without `deal_builder`
      // is asked directly.
      if (c && !c.deal_builder) {
        api.get('/deal-builder/access', { noOrgContext: true, skipRedirect: true })
          .then(d => { if (live) setDeal(d) }).catch(() => {})
      }
    }).catch(e => { if (live) setError(e) })
    return () => { live = false }
  }, [n])

  const all = useMemo(() => ctx ? programInventory(ctx, ctx.deal_builder || deal, MOBILE_PROGRAMS) : [], [ctx, deal])
  const rows = all.filter(item => matchesProgram(item, query))

  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.programAccess} />
      <label className="ta-searchbox">
        <Icon name="search" size={20} />
        <span className="ta-sr">Search programs</span>
        <input type="search" placeholder="Search programs…" value={query} onChange={e => setQuery(e.target.value)}
               enterKeyHint="search" autoComplete="off" />
        {query && <button type="button" className="ta-icon-btn ta-icon-btn--sm" onClick={() => setQuery('')} aria-label="Clear search"><Icon name="x" size={16} /></button>}
      </label>
      {error && <ErrorNote error={error} onRetry={() => setN(x => x + 1)} />}
      {!ctx && !error && <Loading />}
      {ctx && all.length === 0 && <div className="ta-state">No programs are assigned to your account yet.</div>}
      {ctx && all.length > 0 && rows.length === 0 && <div className="ta-state">No program matches “{query}”.</div>}
      {rows.length > 0 && (
        <ul className="ta-plist" aria-label="Programs" data-count={rows.length} data-total={all.length}>
          {rows.map(item => (
            <li key={programKey(item)}>
              <ProgramRow item={item} onOpen={() => openProgramItem(item, navigate, { origin: MOBILE_PROGRAMS })} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
