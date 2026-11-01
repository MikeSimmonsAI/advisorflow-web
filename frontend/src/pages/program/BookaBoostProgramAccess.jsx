import { useEffect, useRef, useState } from 'react'
import '@fontsource/playfair-display/500.css'
import '@fontsource/playfair-display/600.css'
import './bookaboostProgramAccess.css'
import { programCards } from './programAccessModel'
import { bookaboostProductBrand, OLP } from '../../brand/productBrand'
import { getCurrentUser, logout } from '../../api/client'
import photoHero from '../wholesale/ds/photos/estate.jpg'
import photoWholesale from '../wholesale/ds/photos/aerial.jpg'
import photoBackOffice from '../wholesale/ds/photos/contract.jpg'
import photoExecutive from '../wholesale/ds/photos/skyline.jpg'
import photoLand from '../wholesale/ds/photos/home.jpg'
import photoLandPartners from '../wholesale/ds/photos/land-field.jpg'
import photoFarmRanch from '../wholesale/ds/photos/farm-barn.jpg'
import { olpWorkspaceCards } from '../../brand/olpNav'

/**
 * BookaBoost Program Access, built to the owner-approved 4-panel mockup
 * (panel 1, 2026-10-28): a photographic hero with "Choose Your Business", the
 * welcome and avatar menu top right, and tall photographic cards with a gold
 * hairline border, a round badge, a serif title, one line, and a gold
 * "Open ..." action. An authorized preview wears a green tint and a PREVIEW tag.
 *
 * Shown instead of the other launcher ONLY when the server says this login's
 * brand is BookaBoost (/auth/my-contexts `brand`). The cards are exactly the
 * ones programCards() builds from the server's answer: nothing here adds,
 * hides or authorizes a destination.
 */

const PHOTOS = {
  wholesale: photoWholesale,
  back_office: photoBackOffice,
  executive: photoExecutive,
  preview: photoLand,
  land_partners: photoLandPartners,
  farm_ranch: photoFarmRanch,
}
// OLP exploration: the Wholesale program is presented as OLP - Overlooked
// Properties (its icon and one line). Which programs appear is unchanged.
const IS_OLP = bookaboostProductBrand() === 'olp'
const LINES = {
  wholesale: IS_OLP
    ? 'Overlooked Properties: find, research and match opportunities, then move them to close.'
    : 'Find, analyze, and close more deals with our full acquisition and disposition platform.',
  back_office: 'Manage leads, appointments and your sales pipeline.',
  executive: 'See how the whole business is performing.',
  preview: 'A preview you have been given permission to open.',
  land_partners: 'Land deals and the partners who buy them: land opportunities, land buyers and deals in one place.',
  farm_ranch: 'Farmland, ranches and acreage: research the evidence and match the right land buyers.',
}

export function bookaboostTitle(brandName) {
  return (brandName || 'BookaBoost') + ' · Choose Your Business'
}

function initials(user) {
  const n = String(user?.full_name || user?.name || user?.email || '').trim()
  if (!n) return ''
  const parts = n.replace(/@.*/, '').split(/[\s._-]+/).filter(Boolean)
  return ((parts[0] || '')[0] + ((parts[1] || '')[0] || '')).toUpperCase()
}

const ICONS = {
  wholesale: <path d="M3 11.5 12 4l9 7.5M6 10v9h12v-9M10 19v-5h4v5" />,
  back_office: <path d="M5 20V13M10 20V9M15 20v-5M20 20V5" />,
  executive: <path d="M4 20h16M6 16l4-5 3 3 5-7" />,
  preview: <path d="M5 19c0-8 5-13 14-14-1 9-6 14-14 14zm0 0 7-7" />,
  land_partners: <path d="M3 18l6-9 4 6 3-4 5 7H3zM7 6a2 2 0 1 0 0-.01" />,
  farm_ranch: <path d="M4 20V10l6-4 6 4v10M4 20h16M10 20v-5h4v5M16 12h4v8M18 8V5" />,
  business: <path d="M4 20V8l8-4 8 4v12M9 20v-5h6v5" />,
}

function Badge({ kind }) {
  if (kind === 'wholesale' && IS_OLP) {
    return (
      <span className="bbpa-badge bbpa-badge--olp" aria-hidden="true">
        <img src={OLP.icon} alt="" width="40" height="32" />
      </span>
    )
  }
  return (
    <span className={'bbpa-badge' + (kind === 'preview' ? ' bbpa-badge--green' : '')} aria-hidden="true">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9"
           strokeLinecap="round" strokeLinejoin="round" focusable="false">{ICONS[kind] || ICONS.business}</svg>
    </span>
  )
}

function OpenArrow() {
  return (
    <span className="bbpa-go" aria-hidden="true">
      <svg width="14" height="14" viewBox="0 0 14 14" focusable="false">
        <path d="M5 3l4 4-4 4" stroke="currentColor" strokeWidth="1.9" fill="none" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </span>
  )
}

/** The BookaBoost logo as on the live BookaBoost website header. */
export function BookaBoostLogo() {
  return (
    <span className="bbpa-logo">
      <img src="/brands/bookaboost-mark.svg" width="38" height="38" alt="" />
      <span className="bbpa-wordmark" aria-hidden="true">Booka<span>Boost</span></span>
      <span className="bbpa-sr">BookaBoost</span>
    </span>
  )
}

function PhotoCard({ item, onOpen }) {
  const kind = item.type === 'preview' ? 'preview' : (item.program || 'business')
  const photo = PHOTOS[kind] || photoExecutive
  const title = item.display
  const line = LINES[kind] || item.summary || ''
  const action = 'Open ' + (item.type === 'preview' ? (title || 'preview') : title)
  const body = (
    <>
      <span className="bbpa-photo" style={{ backgroundImage: 'url(' + photo + ')' }} aria-hidden="true" />
      <span className="bbpa-scrim" aria-hidden="true" />
      {kind === 'preview' ? <span className="bbpa-tag">Preview</span> : null}
      <span className="bbpa-card-body">
        <Badge kind={kind} />
        <span className="bbpa-title">{title}</span>
        {item.orgLabel ? <span className="bbpa-org">{item.orgLabel}</span> : null}
        <span className="bbpa-line">{line}</span>
        <span className="bbpa-open"><OpenArrow />{action}</span>
      </span>
    </>
  )
  // A preview is a real link to a signed-in page of this app - never
  // window.open, which browsers block as a pop-up.
  const href = item.type === 'preview' ? (item.href || item.url) : null
  return href
    ? <a className="bbpa-card" data-program={kind} href={href} aria-label={action}>{body}</a>
    : <button type="button" className="bbpa-card" data-program={kind} aria-label={action} onClick={() => onOpen(item)}>{body}</button>
}

function AccountMenu({ user, onSignOut }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const close = e => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    const esc = e => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', esc) }
  }, [open])
  const name = user?.full_name || user?.name || user?.email || ''
  const email = user?.full_name && user?.email ? user.email : ''
  return (
    <div className="bbpa-account" ref={ref}>
      {name ? (
        <span className="bbpa-welcome"><small>Welcome back,</small><b>{name}</b></span>
      ) : null}
      <button type="button" className="bbpa-avatar" aria-haspopup="menu" aria-expanded={open}
              aria-label="Account menu" onClick={() => setOpen(o => !o)}>
        <span className="bbpa-initials">{initials(user) || '•'}</span>
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" focusable="false">
          <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" />
        </svg>
      </button>
      {open ? (
        <div className="bbpa-menu" role="menu">
          {name ? <div className="bbpa-menu-who"><b>{name}</b>{email ? <small>{email}</small> : null}</div> : null}
          <button type="button" role="menuitem" className="bbpa-menu-item" onClick={onSignOut}>Sign out</button>
        </div>
      ) : null}
    </div>
  )
}

function Hero({ user, onSignOut, brandName, children }) {
  return (
    <header className="bbpa-hero" style={{ backgroundImage: 'url(' + photoHero + ')' }}>
      <span className="bbpa-hero-scrim" aria-hidden="true" />
      <div className="bbpa-wrap bbpa-top">
        <BookaBoostLogo />
        {onSignOut ? <AccountMenu user={user} onSignOut={onSignOut} /> : null}
      </div>
      <div className="bbpa-wrap bbpa-hero-text">
        <h1 id="bbpa-title">Choose Your <em>Business</em></h1>
        <p>Access your {brandName} workspaces</p>
        {children}
      </div>
    </header>
  )
}

function useTitle(title) {
  useEffect(() => {
    if (typeof document === 'undefined') return undefined
    const previous = document.title
    document.title = title
    return () => { document.title = previous }
  }, [title])
}

function defaultSignOut() {
  return async () => { await logout(); window.location.href = '/login' }
}

/** Loading and error states, in the BookaBoost look, before the answer arrives. */
export function BookaBoostProgramAccessState({ state, onRetry, brandName, user: userProp, onSignOut }) {
  const name = brandName || 'BookaBoost'
  useTitle(bookaboostTitle(name))
  const user = userProp !== undefined ? userProp : getCurrentUser()
  return (
    <div className="bbpa">
      <Hero user={user} onSignOut={onSignOut || defaultSignOut()} brandName={name} />
      <main className="bbpa-wrap bbpa-main">
        {state === 'error' ? (
          <div className="bbpa-message" role="alert">
            <b>We couldn't load your workspaces.</b>
            <span>Check your connection and try again.</span>
            {onRetry ? <button type="button" className="bbpa-button" onClick={onRetry}>Try again</button> : null}
          </div>
        ) : (
          <div role="status" aria-live="polite">
            <span className="bbpa-sr">Loading your workspaces…</span>
            <div className="bbpa-grid" aria-hidden="true">
              <span className="bbpa-card bbpa-skeleton" />
              <span className="bbpa-card bbpa-skeleton" />
              <span className="bbpa-card bbpa-skeleton" />
            </div>
          </div>
        )}
      </main>
    </div>
  )
}

export default function BookaBoostProgramAccess({ contexts, onChoose, user: userProp, onSignOut }) {
  const brandName = contexts?.brand?.name || 'BookaBoost'
  useTitle(bookaboostTitle(brandName))
  const { visible, others, previews } = programCards(contexts)
  const user = userProp !== undefined ? userProp : getCurrentUser()
  const signOut = onSignOut || defaultSignOut()
  // Operational destinations first (Wholesale, Back Office, any other
  // workspace), then authorized previews - one row, as in the mockup.
  // OLP: Land Partners and Farm & Ranch sit right after the Wholesale card they
  // belong to, and only when the server gave this login that Wholesale card.
  const olpExtras = IS_OLP ? olpWorkspaceCards(visible) : []
  const at = visible.findIndex(c => c.type === 'product' && c.key === 'wholesale')
  const cards = [
    ...(at >= 0 ? [...visible.slice(0, at + 1), ...olpExtras, ...visible.slice(at + 1)] : visible),
    ...others.map(w => ({ ...w, program: 'business', display: w.organization_name })),
    ...previews,
  ]

  return (
    <div className="bbpa">
      <Hero user={user} onSignOut={signOut} brandName={brandName} />
      <main className="bbpa-wrap bbpa-main" aria-labelledby="bbpa-title">
        {cards.length > 0 ? (
          <ul className={'bbpa-grid bbpa-grid--n' + Math.min(cards.length, 4)} aria-label="Your workspaces">
            {cards.map(item => (
              <li key={item.type + ':' + item.organization_id + ':' + (item.key || '') + ':' + (item.view || '')}>
                <PhotoCard item={item} onOpen={onChoose} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="bbpa-message">
            <b>No workspaces are assigned to this login yet.</b>
            <span>Ask your administrator to give you access.</span>
          </div>
        )}
      </main>
    </div>
  )
}
