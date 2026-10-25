import { useState } from 'react'
import '../../design/evTokens.css'
import './ProgramAccessPortal.css'
import { programCards } from './programAccessModel'
import WorkspaceCard from '../../design/WorkspaceCard'
import PlatformArt from '../../design/art/PlatformArt'
import { getCurrentUser, logout } from '../../api/client'

/**
 * Multi-program identity = one authorized chooser, not a customer CRM.
 * Caller supplies the server's /auth/my-contexts payload; this surface
 * never computes permissions or changes credentials. Every card comes from
 * programCards(contexts) — nothing here can add a workspace the server did
 * not list for this login.
 *
 * UI-REDESIGN lane, issue #275 — see docs/redesign/PROGRAM_ACCESS_SPEC.md.
 */

const VIEW_KEY = 'ev.programAccess.view'
function readView() {
  try { return localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid' } catch { return 'grid' }
}
function saveView(v) {
  try { localStorage.setItem(VIEW_KEY, v) } catch { /* storage unavailable: keep in memory only */ }
}

function initials(name) {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

const POINTS = [
  { k: 'login', t: 'One secure login', d: 'A single sign-in for every business you are authorized to run.',
    icon: <path d="M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z" /> },
  { k: 'env', t: 'Separate business environments', d: 'Each program opens in its own dedicated workspace.',
    icon: <path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" /> },
  { k: 'data', t: 'Organized data and permissions', d: 'Records, teams and access stay inside their business.',
    icon: <path d="M12 3l8 3v6c0 4.5-3.3 7.6-8 9-4.7-1.4-8-4.5-8-9V6zM8.5 12l2.5 2.5 4.5-5" /> },
]

export default function ProgramAccessPortal({ contexts, onChoose, user: userProp, onSignOut }) {
  const { visible, others, previews } = programCards(contexts)
  const [view, setView] = useState(readView)
  const user = userProp !== undefined ? userProp : getCurrentUser()
  const personName = user?.full_name || user?.name || user?.email || ''
  const personEmail = user?.full_name && user?.email ? user.email : ''
  const count = visible.length + others.length

  const choose = (v) => { setView(v); saveView(v) }
  const signOut = onSignOut || (async () => { await logout(); window.location.href = '/login' })

  return (
    <div className="ev-ds pa-portal">
      <header className="pa-top">
        <div className="ev-container pa-top-inner">
          <div className="pa-brand">
            <span className="pa-brand-mark" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="20" height="20"><path d="M5 6h14M5 12h10M5 18h14" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" /></svg>
            </span>
            <span className="pa-brand-text"><span>EvoSys <b>Pro</b></span><small>Program Access</small></span>
          </div>
          <div className="pa-top-right">
            <span className="ev-pill pa-private">
              <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M7 11V8a5 5 0 0 1 10 0v3M6 11h12v9H6z" stroke="currentColor" strokeWidth="2" fill="none" strokeLinejoin="round" /></svg>
              Authorized workspaces only
            </span>
            {personName && (
              <div className="pa-user">
                <span className="pa-avatar" aria-hidden="true">{initials(personName)}</span>
                <span className="pa-user-text"><b>{personName}</b>{personEmail && <small>{personEmail}</small>}</span>
              </div>
            )}
            <button type="button" className="ev-btn ev-btn--ghost pa-signout" onClick={signOut}>Sign out</button>
          </div>
        </div>
      </header>

      <main>
        <section className="pa-hero" aria-labelledby="pa-title">
          <div className="ev-container pa-hero-inner">
            <div className="pa-hero-copy">
              <p className="ev-eyebrow">Program access{personName ? ' · ' + personName.split(/\s+/)[0] : ''}</p>
              <h1 id="pa-title">Choose your <span>business.</span></h1>
              <p className="pa-lead">One login. Separate operations. Open a program to work inside its own dedicated environment.</p>
              <ul className="pa-points">
                {POINTS.map(p => (
                  <li key={p.k}>
                    <span className="pa-point-icon" aria-hidden="true">
                      <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">{p.icon}</svg>
                    </span>
                    <span><b>{p.t}</b><small>{p.d}</small></span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="pa-hero-art"><PlatformArt /></div>
          </div>
        </section>

        <section className="ev-container pa-work" aria-labelledby="pa-work-title">
          <div className="pa-toolbar">
            <div>
              <h2 id="pa-work-title">Your workspaces</h2>
              <p>{count === 0 ? 'No workspaces assigned yet' : count + (count === 1 ? ' business' : ' businesses') + ' available to this login'}</p>
            </div>
            {visible.length > 1 && (
              <div className="pa-viewtoggle" role="group" aria-label="Layout">
                <button type="button" aria-pressed={view === 'grid'} onClick={() => choose('grid')}>
                  <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M2 2h5v5H2zM9 2h5v5H9zM2 9h5v5H2zM9 9h5v5H9z" fill="currentColor" /></svg>
                  Grid
                </button>
                <button type="button" aria-pressed={view === 'list'} onClick={() => choose('list')}>
                  <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M2 3h12v2.5H2zM2 6.75h12v2.5H2zM2 10.5h12V13H2z" fill="currentColor" /></svg>
                  List
                </button>
              </div>
            )}
          </div>

          {visible.length > 0 && (
            <div className={'pa-grid pa-grid--' + view + ' pa-grid--n' + Math.min(visible.length, 5)} role="list" aria-label="Your programs">
              {visible.map(item => (
                <div role="listitem" key={item.type + ':' + item.organization_id + ':' + (item.key || '')}>
                  <WorkspaceCard item={item} onOpen={onChoose} layout={view} />
                </div>
              ))}
            </div>
          )}

          {others.length > 0 && (
            <section className="pa-others" aria-labelledby="pa-others-title">
              <h2 id="pa-others-title">Other authorized workspaces</h2>
              <div className="pa-others-list">
                {others.map(w => (
                  <button type="button" key={w.organization_id} onClick={() => onChoose(w)}>
                    <span className="pa-others-mark" aria-hidden="true">{initials(w.organization_name)}</span>
                    <span className="pa-others-name">{w.organization_name}</span>
                    <span aria-hidden="true">→</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {previews.length > 0 && (
            <section className="pa-previews" aria-labelledby="pa-previews-title">
              <div className="pa-toolbar">
                <div>
                  <h2 id="pa-previews-title">Previews</h2>
                  <p>Mockups for prospects who are not live customers yet. Each opens its demo link in a new tab.</p>
                </div>
              </div>
              <div className={'pa-grid pa-grid--' + view + ' pa-grid--n' + Math.min(previews.length, 5)} role="list" aria-label="Previews">
                {previews.map(item => (
                  <div role="listitem" key={item.organization_id}>
                    <WorkspaceCard item={item} onOpen={onChoose} layout={view} actionLabel="Open preview" />
                  </div>
                ))}
              </div>
            </section>
          )}

          {!visible.length && !others.length && !previews.length && (
            <div className="pa-empty">
              <b>No workspaces have been assigned to this account.</b>
              <span>Ask your administrator to grant access to a program.</span>
            </div>
          )}
        </section>
      </main>

      <footer className="ev-container pa-foot">Each program keeps its own data, workflow and permissions.</footer>
    </div>
  )
}
