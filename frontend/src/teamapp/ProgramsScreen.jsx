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
 * SAME LOOK AS DESKTOP PROGRAM ACCESS (V2): each card wears the program photo,
 * one-line description and category desktop uses (v2/commandCenterModel.js,
 * v2/photos.js - presentation only, nothing there grants access), and the
 * category chips filter All Programs the way desktop's do. Before a person
 * picks phone pins, Pinned shows their desktop pins (or desktop's featured
 * workspace) instead of an empty box.
 *
 * A TAP OPENS THE PROGRAM'S PHONE WORKSPACE (/app/p/<pid>: Dashboard,
 * Replies, More - ProgramWorkspace.jsx), whose More -> Open Full Workspace uses
 * the shared openProgramItem() rule. A deal's demo and the Deal Builder tools
 * still open directly, as before. A program with a reply queue shows its
 * unanswered count on the card; tapping the count opens Replies.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, fetchMyContexts } from '../api/client'
import { programInventory, programKey } from '../pages/program/programAccessModel'
import { openProgramItem } from '../pages/program/programDestinations'
import { clearLauncherOrigin, MOBILE_PROGRAMS } from '../pages/program/launcherOrigin'
import { LABELS, MAX_PINS, livePinKeys, matchesProgram, pinnedOrDesktop, programStyle, togglePinKey } from './teamHelpers'
import { availableCategories, categoryOf, photoKeyFor, shortLine } from '../pages/program/v2/commandCenterModel'
import { cardPhoto } from '../pages/program/v2/photos'
import { ErrorNote, Icon, Loading, ScreenTitle } from './TeamApp'
import { useReplyCount } from './ProgramWorkspace'
import { mobileProgramPath, opensDirect } from './programMobile'

const OPTS = { noOrgContext: true, skipRedirect: true }

function looks(item) {
  const look = programStyle(item)
  return { look, style: { '--acc': look.accent, '--acc2': look.second || look.accent,
                          '--photo': 'url("' + cardPhoto(photoKeyFor(item)) + '")' } }
}

/** The line under the name: desktop's own short line (business or focus). */
function subLabel(item) {
  if (item.type === 'preview' || item.program === 'preview') return 'Preview'
  return shortLine(item) || ''
}

/** The unanswered-replies count on a card: its own tap target, opening Replies. */
function CountBadge({ item, onReplies }) {
  const label = useReplyCount(item)
  if (!label) return null
  return (
    <button type="button" className="ta-count" onClick={onReplies}
            aria-label={label + ' conversation' + (label === '1' ? '' : 's') + ' need a reply - open Replies'}>
      {label}</button>
  )
}

/** Large pinned shortcut: coloured card with a soft skyline. */
function PinCard({ item, onOpen, onReplies }) {
  const { look, style } = looks(item)
  return (
    <div className="ta-cardwrap">
      <button type="button" className={'ta-pin ta-pin--photo' + (look.second ? ' has-second' : '')} style={style} onClick={onOpen}
              data-program-key={programKey(item)}>
        <span className="ta-pin-icon" aria-hidden><Icon name={look.icon} size={22} /></span>
        <span className="ta-pin-name">{item.display}</span>
        {subLabel(item) && <span className="ta-pin-sub">{subLabel(item)}</span>}
      </button>
      <CountBadge item={item} onReplies={onReplies} />
    </div>
  )
}

/** Compact grid tile: colour icon tile, name underneath (wraps). */
export function ProgramTile({ item, onOpen, onReplies }) {
  const { look, style } = looks(item)
  return (
    <div className="ta-cardwrap">
      <button type="button" className="ta-tile ta-tile--photo" style={style} onClick={onOpen} data-program-key={programKey(item)}>
        <span className="ta-tile-photo" aria-hidden />
        <span className={'ta-tile-icon' + (look.second ? ' has-second' : '')} aria-hidden><Icon name={look.icon} size={20} /></span>
        <span className="ta-tile-name">{item.display}</span>
        {subLabel(item) && <span className="ta-tile-sub">{subLabel(item)}</span>}
      </button>
      {onReplies && <CountBadge item={item} onReplies={onReplies} />}
    </div>
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
  const [deskPins, setDeskPins] = useState([])   // desktop Program Access pins (same keys)
  const [cat, setCat] = useState('all')
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
    // Desktop's pins only fill an empty Pinned row; any failure = no fallback.
    api.get('/launcher/pins', OPTS)
      .then(r => { if (live) setDeskPins(Array.isArray(r && r.keys) ? r.keys : []) })
      .catch(() => {})
    return () => { live = false }
  }, [])

  const all = useMemo(() => ctx ? programInventory(ctx, ctx.deal_builder || deal, MOBILE_PROGRAMS) : [], [ctx, deal])
  const { items: pinned, fromDesktop } = useMemo(() => pinnedOrDesktop(all, pins || [], deskPins, programKey), [all, pins, deskPins])
  const cats = useMemo(() => availableCategories(all), [all])
  const activeCat = cats.some(c => c.key === cat) ? cat : 'all'
  const searching = query.trim().length > 0
  const rows = all.filter(item => matchesProgram(item, query) && (activeCat === 'all' || categoryOf(item) === activeCat))
  const open = item => opensDirect(item)
    ? openProgramItem(item, navigate, { origin: MOBILE_PROGRAMS })
    : navigate(mobileProgramPath(item))
  const replies = item => navigate(mobileProgramPath(item, 'replies'))

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
          {!pinErr && fromDesktop && pinned.length > 0 && <p className="ta-muted ta-small ta-pin-note">From your desktop Program Access. Tap Edit to choose your own.</p>}
          {pinned.length > 0
            ? <div className={'ta-pins' + (pinned.length === 1 ? ' is-one' : '')}>
                {pinned.map(item => <PinCard key={programKey(item)} item={item} onOpen={() => open(item)} onReplies={() => replies(item)} />)}
              </div>
            : <button type="button" className="ta-pin-empty" onClick={() => setEditing(true)}>
                <Icon name="pin" size={22} /> Pin up to {MAX_PINS} programs you open most</button>}
        </section>
      )}

      {ctx && all.length > 0 && (
        <section className="ta-section" aria-labelledby="ta-all-h">
          <h2 id="ta-all-h" className="ta-h2">{searching ? 'Results' : 'All Programs'}</h2>
          {cats.length > 2 && (
            <div className="ta-chips" role="group" aria-label="Filter programs">
              {cats.map(c => (
                <button key={c.key} type="button" className={'ta-chip' + (activeCat === c.key ? ' is-on' : '')}
                        aria-pressed={activeCat === c.key} onClick={() => setCat(c.key)}>
                  {c.key === 'all' ? 'All (' + all.length + ')' : c.label}</button>))}
            </div>)}
          {rows.length === 0
            ? <div className="ta-state">{searching ? 'No program matches “' + query + '”.' : 'Nothing in this group.'}</div>
            : <ul className="ta-grid" aria-label={searching ? 'Matching programs' : 'All programs'} data-count={rows.length} data-total={all.length}>
                {rows.map(item => <li key={programKey(item)}><ProgramTile item={item} onOpen={() => open(item)} onReplies={() => replies(item)} /></li>)}
              </ul>}
        </section>
      )}

      {editing && <EditPins all={all} keys={livePinKeys(all, pins || [], programKey)}
                            onClose={() => setEditing(false)} onSave={savePins} />}
    </div>
  )
}
