import { useEffect } from 'react'
import './bookaboostProgramAccess.css'
import { programCards } from './programAccessModel'
import { getCurrentUser, logout } from '../../api/client'
import photoEstate from '../wholesale/ds/photos/estate.jpg'
import photoNetwork from '../wholesale/ds/photos/network.jpg'
import photoSkyline from '../wholesale/ds/photos/skyline.jpg'
import photoLand from '../wholesale/ds/photos/home.jpg'

/**
 * BookaBoost Program Access - black, charcoal and gold, photographic cards.
 *
 * Shown instead of the EvoSys launcher ONLY when the server says this login's
 * brand is BookaBoost (/auth/my-contexts `brand`). The cards are exactly the
 * ones programCards() builds from the server's answer: nothing here adds,
 * hides or authorizes a destination. The EvoSys launcher is untouched.
 */

// The photo behind each kind of destination. Presentation only.
const PHOTOS = {
  wholesale: photoEstate,
  back_office: photoNetwork,
  executive: photoSkyline,
  preview: photoLand,
}
// One line under each title - short, plain, fast to read.
const LINES = {
  wholesale: 'Properties, sellers, deals and buyers in one place.',
  back_office: 'Your sales pipeline, appointments and customers.',
  executive: 'How the whole brand is performing.',
  preview: 'A demo you have been given permission to view.',
}
const KINDS = {
  wholesale: 'Wholesale',
  back_office: 'Back Office',
  executive: 'Executive Suite',
  preview: 'Authorized preview',
}

export function bookaboostTitle(brandName) {
  return (brandName || 'BookaBoost') + ' · Program Access'
}

function firstName(user) {
  const n = String(user?.full_name || user?.name || '').trim()
  return n ? n.split(/\s+/)[0] : ''
}

function Arrow() {
  return (
    <svg className="bbpa-arrow" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true" focusable="false">
      <path d="M3 9h11M10 4.5L14.5 9 10 13.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** The BookaBoost logo, built exactly as on the BookaBoost marketing page:
 *  the mark (public/brands/bookaboost-mark.svg) and the BOOKA/BOOST wordmark. */
export function BookaBoostLogo({ sub }) {
  return (
    <span className="bbpa-logo">
      <img src="/brands/bookaboost-mark.svg" width="36" height="36" alt="" />
      <span className="bbpa-wordmark" aria-hidden="true">BOOKA<span>BOOST</span></span>
      <span className="bbpa-sr">BookaBoost</span>
      {sub ? <span className="bbpa-logo-sub">{sub}</span> : null}
    </span>
  )
}

function PhotoCard({ item, onOpen }) {
  const program = item.type === 'preview' ? 'preview' : (item.program || 'business')
  const photo = PHOTOS[program] || photoSkyline
  const title = item.display
  const line = LINES[program] || item.summary || ''
  const kind = item.type === 'preview' ? (item.label || KINDS.preview) : (KINDS[program] || item.kind)
  const action = item.type === 'preview' ? 'Open preview' : 'Open'
  const label = action + ': ' + title + (item.orgLabel ? ' (' + item.orgLabel + ')' : '')
  const body = (
    <>
      <span className="bbpa-photo" style={{ backgroundImage: 'url(' + photo + ')' }} aria-hidden="true" />
      <span className="bbpa-scrim" aria-hidden="true" />
      <span className="bbpa-card-body">
        <span className="bbpa-kind">{kind}</span>
        <span className="bbpa-title">{title}</span>
        {item.orgLabel ? <span className="bbpa-org">{item.orgLabel}</span> : null}
        <span className="bbpa-line">{line}</span>
        <span className="bbpa-open">{action}<Arrow /></span>
      </span>
    </>
  )
  // A preview is a real link (a signed-in page of this app, or the deal's
  // own demo link) - never window.open, which browsers block as a pop-up.
  const href = item.type === 'preview' ? (item.href || item.url) : null
  return href
    ? <a className="bbpa-card" data-program={program} href={href} aria-label={label}>{body}</a>
    : <button type="button" className="bbpa-card" data-program={program} aria-label={label} onClick={() => onOpen(item)}>{body}</button>
}

function Header({ user, onSignOut }) {
  const name = user?.full_name || user?.name || user?.email || ''
  const email = user?.full_name && user?.email ? user.email : ''
  return (
    <header className="bbpa-top">
      <div className="bbpa-wrap bbpa-top-inner">
        <BookaBoostLogo sub="Program Access" />
        <div className="bbpa-top-right">
          {name ? (
            <span className="bbpa-user">
              <b>{name}</b>{email ? <small>{email}</small> : null}
            </span>
          ) : null}
          {onSignOut ? <button type="button" className="bbpa-signout" onClick={onSignOut}>Sign out</button> : null}
        </div>
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
  useTitle(bookaboostTitle(brandName))
  const user = userProp !== undefined ? userProp : getCurrentUser()
  return (
    <div className="bbpa">
      <Header user={user} onSignOut={onSignOut || defaultSignOut()} />
      <main className="bbpa-wrap bbpa-main">
        {state === 'error' ? (
          <div className="bbpa-message" role="alert">
            <b>We couldn't load your businesses.</b>
            <span>Check your connection and try again.</span>
            {onRetry ? <button type="button" className="bbpa-signout bbpa-retry" onClick={onRetry}>Try again</button> : null}
          </div>
        ) : (
          <div role="status" aria-live="polite">
            <span className="bbpa-sr">Loading your businesses…</span>
            <div className="bbpa-grid" aria-hidden="true">
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
  const first = firstName(user)
  const main = [...visible, ...others.map(w => ({ ...w, program: 'business', display: w.organization_name }))]
  const count = main.length

  return (
    <div className="bbpa">
      <Header user={user} onSignOut={signOut} />
      <main className="bbpa-wrap bbpa-main">
        <section className="bbpa-hero" aria-labelledby="bbpa-title">
          <p className="bbpa-eyebrow">{first ? 'Welcome back, ' + first : 'Welcome back'}</p>
          <h1 id="bbpa-title">Choose your business.</h1>
          <p className="bbpa-lead">Open a business to work inside it. Each one keeps its own data, team and permissions.</p>
        </section>

        <section aria-labelledby="bbpa-work-title">
          <div className="bbpa-section-head">
            <h2 id="bbpa-work-title">Your businesses</h2>
            <span>{count === 0 ? 'None assigned yet' : count + (count === 1 ? ' business' : ' businesses')}</span>
          </div>
          {count > 0 ? (
            <ul className="bbpa-grid" aria-label="Your businesses">
              {main.map(item => (
                <li key={item.type + ':' + item.organization_id + ':' + (item.key || '')}>
                  <PhotoCard item={item} onOpen={onChoose} />
                </li>
              ))}
            </ul>
          ) : (
            <div className="bbpa-message">
              <b>No businesses are assigned to this login yet.</b>
              <span>Ask your administrator to give you access.</span>
            </div>
          )}
        </section>

        {previews.length > 0 && (
          <section className="bbpa-previews" aria-labelledby="bbpa-previews-title">
            <div className="bbpa-section-head">
              <h2 id="bbpa-previews-title">Authorized preview</h2>
              <span>Demos you may view. Not a live business.</span>
            </div>
            <ul className="bbpa-grid" aria-label="Authorized previews">
              {previews.map(item => (
                <li key={item.organization_id}>
                  <PhotoCard item={item} onOpen={onChoose} />
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>
      <footer className="bbpa-wrap bbpa-foot">Each business keeps its own data, team and permissions.</footer>
    </div>
  )
}
