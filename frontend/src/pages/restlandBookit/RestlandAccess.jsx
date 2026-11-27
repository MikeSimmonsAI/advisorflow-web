import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser, logout, setWorkspaceContext } from '../../api/client'
import './restland-access.css'

// Restland staff Program Access (owner's mockup, Nov 26): the first screen after
// a Restland staff sign-in. Restland's look only - no EvoSys or AdvisorFlow
// branding. The memorial park picture is an illustration, not a photograph of
// the property. "Open BookIT" goes to My Day; BookIT's Program Access returns here.

export function useRestlandBrand(title) {
  useEffect(() => {
    const prevTitle = document.title
    document.title = title
    const links = [...document.querySelectorAll("link[rel~='icon']")]
    const prev = links.map(l => l.href)
    links.forEach(l => { l.href = '/restland/favicon.svg' })
    if (!links.length) {
      const l = document.createElement('link'); l.rel = 'icon'; l.href = '/restland/favicon.svg'; document.head.appendChild(l)
    }
    return () => { document.title = prevTitle; links.forEach((l, i) => { l.href = prev[i] }) }
  }, [title])
}

const KIND = { onsite: 'Office visit', call: 'Phone / video call', offsite: 'Home visit', unknown: 'Type unspecified' }

function fmtDay(iso) {
  if (!iso) return ''
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' })
}
function fmtTime(t) {
  if (!t) return ''
  const [h, m] = t.split(':').map(Number)
  return (h % 12 || 12) + ':' + String(m || 0).padStart(2, '0') + (h >= 12 ? ' PM' : ' AM')
}

export default function RestlandAccess({ ctx }) {
  const navigate = useNavigate()
  const user = getCurrentUser() || {}
  const card = (ctx.bookit_contexts || [])[0]
  const [q, setQ] = useState(null)
  const [err, setErr] = useState(false)
  useRestlandBrand('Program Access | Restland')
  // This device signs in on Restland's own page from now on (and after sign-out).
  useEffect(() => { try { localStorage.setItem('af_login_brand', 'restland') } catch { /* ignore */ } }, [])

  useEffect(() => {
    if (!card) return
    setWorkspaceContext(card.organization_id)
    api.get('/restland-bookit/quick-view').then(setQ).catch(() => setErr(true))
  }, [card?.organization_id])

  const open = (path) => { if (card) setWorkspaceContext(card.organization_id); navigate(path) }
  const name = (user.full_name || user.name || '').trim()
  const first = name.split(' ')[0]
  const initials = name.split(' ').filter(Boolean).slice(0, 2).map(s => s[0].toUpperCase()).join('') || 'R'

  return (
    <div className="rla">
      <aside className="rla-rail">
        <div className="rla-brand"><strong>Restland</strong><span>Cemetery &amp; Funeral Home</span></div>
        <nav aria-label="Account navigation">
          <a aria-current="page" href="/workspaces" onClick={e => e.preventDefault()}>Program Access</a>
          <a href="/change-password" onClick={e => { e.preventDefault(); navigate('/change-password') }}>Change Password</a>
        </nav>
        <div className="rla-account">
          <div className="rla-avatar" aria-hidden="true">{initials}</div>
          <div><strong>{name || 'Restland staff'}</strong><span>Advisor</span></div>
          <a href="/restland" onClick={async e => { e.preventDefault(); await logout(); navigate('/restland') }}>Sign out</a>
        </div>
      </aside>
      <main className="rla-main">
        <div className="rla-welcome"><div><h1>Welcome back{first ? ', ' + first : ''}</h1><p>Choose your Restland workspace.</p></div></div>
        <section className="rla-hero" aria-labelledby="rla-featured">
          <h2 id="rla-featured">Restland BookIT</h2>
          <p>Manage appointments, family file reviews, and follow-ups.</p>
          <button className="rla-button" onClick={() => open('/program/restland-bookit')} disabled={!card}>Open BookIT →</button>
        </section>
        <section className="rla-workspaces" aria-labelledby="rla-mine">
          <h2 id="rla-mine">My Workspaces</h2>
          {!card ? <p className="rla-muted">BookIT isn't open for your login yet. Please check with your manager.</p> : (
            <article className="rla-layout">
              <div className="rla-feature">
                <h3>Restland BookIT</h3><p>Family Service Advisor workspace</p>
                <a href="/program/restland-bookit" onClick={e => { e.preventDefault(); open('/program/restland-bookit') }}>Open workspace →</a>
              </div>
              <section className="rla-quick" aria-labelledby="rla-personal">
                <div className="rla-qtitle"><div><h3 id="rla-personal">My quick view</h3>
                  <p>My booked appointments · Next 7 days{q?.name && user.full_name !== q.name ? ' · ' + q.name : ''}</p></div></div>
                {err ? <p className="rla-muted">Your quick view couldn't load. Open BookIT to see your schedule.</p>
                  : !q ? <div className="rla-stats"><div className="rla-stat"><strong>…</strong><span>Total booked</span></div></div>
                  : !q.has_seat ? <p className="rla-muted">Your login isn't on the advisor team, so there's no personal schedule to show.</p>
                  : <>
                    <div className="rla-stats">
                      <div className="rla-stat"><strong>{q.total}</strong><span>Total booked</span></div>
                      <div className="rla-stat"><strong>{q.onsite}</strong><span>Office visits</span></div>
                      <div className="rla-stat"><strong>{q.call}</strong><span>Phone / video calls</span></div>
                      <div className="rla-stat"><strong>{q.offsite}</strong><span>Home visits</span></div>
                    </div>
                    {q.unspecified > 0 && <p className="rla-muted rla-small">{q.unspecified} more with the visit type not set.</p>}
                    <div className="rla-next">
                      <p><strong>Next appointment</strong>
                        <span>{q.next ? fmtDay(q.next.date) + ' · ' + fmtTime(q.next.time) + ' CT · ' + KIND[q.next.visit || 'unknown']
                          : 'Nothing booked in the next 7 days.'}</span></p>
                      <a href="/program/restland-bookit/calendar" onClick={e => { e.preventDefault(); open('/program/restland-bookit/calendar') }}>View my schedule →</a>
                    </div>
                  </>}
              </section>
            </article>)}
        </section>
      </main>
    </div>
  )
}
