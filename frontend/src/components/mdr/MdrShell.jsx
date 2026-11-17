/* MY DEAL ROOM SHELL PIECES (redesign, Nov 2026).
 *
 * Layout decides WHEN these apply (the My Deal Room shell: EvoSys Pro brand,
 * inside /wholesale, not OLP, not a vertical) and computes the rail with the
 * SAME items and the SAME visible() filter as before. These components only
 * draw what Layout hands them, so none of them can show a tool a role, plan
 * or feature flag has closed.
 *
 *   MdrBrand        open-door mark + "My Deal Room" wordmark + engine line
 *   MdrNavContext   the visible rail, for the group landing pages and Go to
 *   MdrQuickJump    "Go to…" — any permitted tool in two keystrokes
 *   MdrAccountMenu  identity, permitted platform navigation, website, sign out
 */
import { createContext, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import './mdr-workspace.css'

export const MDR_MARK = '/brands/my-deal-room-door.svg'
export const MDR_NAME = 'My Deal Room'

/** True when this shell is the My Deal Room product (not BookaBoost / OLP). */
export function isMdrShell({ inWholesale, olpShell, theme, evosysTheme }) {
  return !!(inWholesale && !olpShell && theme === evosysTheme)
}

export function MdrBrand({ engine, collapsed }) {
  return (
    <Link to="/wholesale/evosense" className="mdr-brand" aria-label={`${MDR_NAME} — Acquisition Command`}>
      <img className="mdr-brand__mark" src={MDR_MARK} alt="" width="32" height="32" />
      {collapsed ? null : (
        <span className="mdr-brand__text">
          <span className="mdr-brand__word">{MDR_NAME}</span>
          {engine ? <span className="mdr-brand__engine">Acquisition engine · <b>{engine}</b></span> : null}
        </span>
      )}
    </Link>
  )
}

/** { pinned, groups:[{key,label,items}] } — exactly what the rail shows. */
export const MdrNavContext = createContext(null)
export const useMdrNav = () => useContext(MdrNavContext)

function useOutside(ref, open, close) {
  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) close() }
    const onKey = (e) => { if (e.key === 'Escape') close() }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [ref, open, close])
}

/** "Go to…": every tool this person may open, filtered as they type. */
export function MdrQuickJump({ nav, Icon }) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [hi, setHi] = useState(0)
  const box = useRef(null)
  const input = useRef(null)
  const navigate = useNavigate()
  useOutside(box, open, () => setOpen(false))
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target
      const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)
      if (!typing && (e.key === 'g' || e.key === 'G') && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); setOpen(true) }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])
  useEffect(() => { if (open) { setQ(''); setHi(0); setTimeout(() => input.current && input.current.focus(), 0) } }, [open])
  const rows = useMemo(() => {
    const out = []
    if (!nav) return out
    if (nav.pinned) out.push({ group: null, item: nav.pinned })
    for (const g of nav.groups) for (const item of g.items) out.push({ group: g.label, item })
    const t = q.trim().toLowerCase()
    return t ? out.filter((r) => (r.item.label + ' ' + (r.group || '')).toLowerCase().includes(t)) : out
  }, [nav, q])
  const go = (to) => { setOpen(false); navigate(to) }
  let lastGroup
  return (
    <div className="mdr-jump" ref={box}>
      <button type="button" className="mdr-jump__btn" aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}
              title="Go to any tool (press G)">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>
        <span className="mdr-jump__label">Go to</span><kbd aria-hidden="true">G</kbd>
      </button>
      {open ? (
        <div className="mdr-pop" role="dialog" aria-label="Go to a tool">
          <input ref={input} className="mdr-pop__search" value={q} placeholder="Type a tool name…" aria-label="Filter tools"
                 onChange={(e) => { setQ(e.target.value); setHi(0) }}
                 onKeyDown={(e) => {
                   if (e.key === 'ArrowDown') { e.preventDefault(); setHi((h) => Math.min(h + 1, rows.length - 1)) }
                   if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
                   if (e.key === 'Enter' && rows[hi]) { e.preventDefault(); go(rows[hi].item.to) }
                 }} />
          <div role="listbox" aria-label="Tools">
            {rows.length ? rows.map((r, i) => {
              const head = r.group !== lastGroup ? (lastGroup = r.group, r.group) : null
              return (
                <div key={r.item.to}>
                  {head ? <div className="mdr-pop__group">{head}</div> : null}
                  <button type="button" role="option" aria-selected={i === hi} className={`mdr-pop__item${i === hi ? ' is-on' : ''}`}
                          onMouseEnter={() => setHi(i)} onClick={() => go(r.item.to)}>
                    {Icon ? <Icon name={r.item.icon} /> : null}{r.item.label}
                  </button>
                </div>
              )
            }) : <p className="mdr-pop__empty">No tool by that name in your access.</p>}
          </div>
        </div>
      ) : null}
    </div>
  )
}

/**
 * The account menu: who and where you are, the platform navigation this
 * person is permitted (Back Office / other workspaces, drawn by the existing
 * ContextSwitcher, which renders nothing for someone without them), the
 * workspace's website and Sign out.
 */
export function MdrAccountMenu({ person, role, org, photo, website, onSignOut, platformNav }) {
  const [open, setOpen] = useState(false)
  const box = useRef(null)
  useOutside(box, open, () => setOpen(false))
  const initial = (person || '?')[0]
  return (
    <div className="mdr-acct" ref={box}>
      <button type="button" className="mdr-acct__btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}
              title={[person, role, org].filter(Boolean).join(' · ')}>
        <span className="mdr-acct__avatar" aria-hidden="true">{photo ? <img src={photo} alt="" /> : initial}</span>
        <span className="mdr-acct__name">{person}</span>
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open ? (
        <div className="mdr-pop" role="menu" aria-label="Account">
          <dl className="mdr-pop__id">
            {org ? <><dt>Workspace</dt><dd data-identity="organization">{org}</dd></> : null}
            <dt>Signed in as</dt><dd data-identity="person">{person}</dd>
            {role ? <><dt>Role</dt><dd data-identity="role">{role}</dd></> : null}
          </dl>
          {platformNav ? <><div className="mdr-pop__sep" />{platformNav}</> : null}
          <div className="mdr-pop__sep" />
          <Link role="menuitem" className="mdr-pop__item" to="/settings" onClick={() => setOpen(false)}>My settings</Link>
          {website ? (
            <a role="menuitem" className="mdr-pop__item" href={website} target="_blank" rel="noopener noreferrer">↗ Back to website</a>
          ) : null}
          <button type="button" role="menuitem" className="mdr-pop__item" onClick={onSignOut}>Sign out</button>
        </div>
      ) : null}
    </div>
  )
}
