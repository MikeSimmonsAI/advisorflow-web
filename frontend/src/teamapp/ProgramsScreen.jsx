/**
 * PROGRAMS — the app's home screen (approved board "Programs, Option B").
 *
 * Search at the top, a Pinned row of two large shortcuts, and a compact grid of
 * All Programs. The grid is exactly what desktop Program Access lets this login
 * open: the same /auth/my-contexts answer, flattened by programInventory()
 * (desktop's cards, sales tools, "Other authorized workspaces" and Previews)
 * with no cap and no mobile-only filter. Search narrows that full list;
 * clearing it restores it.
 *
 * PINS are this account's own choice, kept on the server (/team-app/pins, at
 * most two). A pin only shows while the program is still in the inventory, so
 * a revoked program's pin disappears on its own. Pinned programs stay in All
 * Programs too.
 *
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
import { LABELS, MAX_PINS, livePinKeys, matchesProgram, pinnedItems, programStyle, togglePinKey } from './teamHelpers'
import { ErrorNote, Icon, Loading, ScreenTitle } from './TeamApp'

const OPTS = { noOrgContext: true, skipRedirect: true }

function looks(item) {
  const look = programStyle(item)
  return { look, style: { '--acc': look.accent, '--acc2': look.second || look.accent } }
}

function subLabel(item) {
  if (item.type === 'preview') return 'Preview'
  return item.orgLabel || ''
}

/** Large pinned shortcut: coloured card with a soft skyline. */
function PinCard({ item, onOpen }) {
  const { look, style } = looks(item)
  return (
    <button type="button" className={'ta-pin' + (look.second ? ' has-second' : '')} style={style} onClick={onOpen}
            data-program-key={programKey(item)}>
      <svg className="ta-pin-art" viewBox="0 0 120 90" aria-hidden focusable="false">
        <path d="M60 90V38l22-10v62M86 90V22l20 8v60M40 90V56l16-6v40" fill="none" stroke="currentColor" strokeWidth="1.4" />
        <path d="M64 46h14M64 54h14M64 62h14M64 70h14M90 34h12M90 42h12M90 50h12M90 58h12M90 66h12" stroke="currentColor" strokeWidth="1" />
      </svg>
      <span className="ta-pin-icon" aria-hidden><Icon name={look.icon} size={30} /></span>
      <span className="ta-pin-name">{item.display}</span>
      {subLabel(item) && <span className="ta-pin-sub">{subLabel(item)}</span>}
    </button>
  )
}

/** Compact grid tile: colour icon tile, name underneath (wraps). */
export function ProgramTile({ item, onOpen }) {
  const { look, style } = looks(item)
  return (
    <button type="button" className="ta-tile" style={style} onClick={onOpen} data-program-key={programKey(item)}>
      <span className={'ta-tile-icon' + (look.second ? ' has-second' : '')} aria-hidden><Icon name={look.icon} size={26} /></span>
      <span className="ta-tile-name">{item.display}</span>
      {subLabel(item) && <span className="ta-tile-sub">{subLabel(item)}</span>}
    </button>
  )
}

function EditPins({ all, keys, onClose, onSave }) {
  const [draft, setDraft] = useState(keys)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const full = draft.length >= MAX_PINS
  async function done() {
    setBusy(true); setErr(null)
    try { await onSave(draft); onClose() } catch (e) { setErr((e && e.message) || 'Could not save your pins. Try again.') } finally { setBusy(false) }
  }
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="ta-sheet" role="presentation" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ta-sheet-card" role="dialog" aria-modal="true" aria-labelledby="ta-pins-title">
        <div className="ta-sheet-grip" aria-hidden />
        <h2 id="ta-pins-title" className="ta-sheet-title">Edit pins</h2>
        <p className="ta-muted ta-small">Choose up to {MAX_PINS}. {full ? 'Uncheck one to pick another.' : ''}</p>
        <ul className="ta-pinlist" aria-label="Your programs">
          {all.map(item => {
            const k = programKey(item)
            const on = draft.includes(k)
            const { look, style } = looks(item)
            return (
              <li key={k}>
                <label className={'ta-pinrow' + (!on && full ? ' is-off' : '')} style={style}>
                  <span className="ta-pinrow-tile" aria-hidden><Icon name={look.icon} size={20} /></span>
                  <span className="ta-pinrow-name">{item.display}{subLabel(item) && <small>{subLabel(item)}</small>}</span>
                  <input type="checkbox" checked={on} disabled={!on && full}
                         onChange={() => setDraft(d => togglePinKey(d, k))} aria-label={'Pin ' + item.display} />
                </label>
              </li>
            )
          })}
        </ul>
        {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
        <button type="button" className="ta-btn ta-btn--primary ta-btn--block" onClick={done} disabled={busy}>{busy ? 'Saving…' : 'Done'}</button>
      </div>
    </div>
  )
}

export default function ProgramsScreen() {
  const navigate = useNavigate()
  const [ctx, setCtx] = useState(null)
  const [deal, setDeal] = useState(null)
  const [error, setError] = useState(null)
  const [n, setN] = useState(0)
  const [query, setQuery] = useState('')
  const [pins, setPins] = useState(null)        // null = not loaded yet
  const [pinErr, setPinErr] = useState(false)
  const [editing, setEditing] = useState(false)
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
        api.get('/deal-builder/access', OPTS).then(d => { if (live) setDeal(d) }).catch(() => {})
      }
    }).catch(e => { if (live) setError(e) })
    return () => { live = false }
  }, [n])
  useEffect(() => {
    let live = true
    api.get('/team-app/pins', OPTS)
      .then(r => { if (live) { setPins(Array.isArray(r && r.keys) ? r.keys : []); setPinErr(false) } })
      .catch(() => { if (live) { setPins([]); setPinErr(true) } })
    return () => { live = false }
  }, [])

  const all = useMemo(() => ctx ? programInventory(ctx, ctx.deal_builder || deal, MOBILE_PROGRAMS) : [], [ctx, deal])
  const pinned = useMemo(() => pinnedItems(all, pins || [], programKey), [all, pins])
  const searching = query.trim().length > 0
  const rows = all.filter(item => matchesProgram(item, query))
  const open = item => openProgramItem(item, navigate, { origin: MOBILE_PROGRAMS })

  async function savePins(keys) {
    const r = await api.put('/team-app/pins', { keys })
    setPins(Array.isArray(r && r.keys) ? r.keys : keys)
    setPinErr(false)
  }

  return (
    <div className="ta-screen">
      <ScreenTitle title={LABELS.programs} />
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

      {ctx && all.length > 0 && !searching && pins !== null && (
        <section className="ta-section" aria-labelledby="ta-pinned-h">
          <div className="ta-section-head">
            <h2 id="ta-pinned-h" className="ta-h2">Pinned</h2>
            <button type="button" className="ta-linkbtn" onClick={() => setEditing(true)}>Edit</button>
          </div>
          {pinErr && <p className="ta-muted ta-small">Your pins could not be loaded. You can still pick them with Edit.</p>}
          {pinned.length > 0
            ? <div className={'ta-pins' + (pinned.length === 1 ? ' is-one' : '')}>
                {pinned.map(item => <PinCard key={programKey(item)} item={item} onOpen={() => open(item)} />)}
              </div>
            : <button type="button" className="ta-pin-empty" onClick={() => setEditing(true)}>
                <Icon name="pin" size={22} /> Pin up to {MAX_PINS} programs you open most</button>}
        </section>
      )}

      {ctx && all.length > 0 && (
        <section className="ta-section" aria-labelledby="ta-all-h">
          <h2 id="ta-all-h" className="ta-h2">{searching ? 'Results' : 'All Programs'}</h2>
          {rows.length === 0
            ? <div className="ta-state">No program matches “{query}”.</div>
            : <ul className="ta-grid" aria-label={searching ? 'Matching programs' : 'All programs'} data-count={rows.length} data-total={all.length}>
                {rows.map(item => <li key={programKey(item)}><ProgramTile item={item} onOpen={() => open(item)} /></li>)}
              </ul>}
        </section>
      )}

      {editing && <EditPins all={all} keys={livePinKeys(all, pins || [], programKey)}
                            onClose={() => setEditing(false)} onSave={savePins} />}
    </div>
  )
}
