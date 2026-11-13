import { useEffect, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { API_BASE, clearToken } from '../api/client'

/**
 * TEAM PRICING DEAL BUILDERS — two entry points, access decided by the server.
 *
 *   /team-prices/sales-deal-builder   any signed-in user; the page itself is
 *                                     customer prices only, and the
 *                                     salesperson's own commission comes from
 *                                     the server for sales-team members.
 *   /team-prices/executive-deal-builder  executive entry. The old public URL
 *                                     /team-prices/deal-builder.html is now a
 *                                     static stub that forwards here (it must
 *                                     overwrite the old file: the static host
 *                                     keeps serving deleted files). The
 *                                     executive page is NOT in this
 *                                     bundle or on the static site: it is
 *                                     downloaded from the backend, which serves
 *                                     it only to the owner or an explicit
 *                                     `deal_builder_executive` grantee.
 *
 * Nothing in this file decides who may see compensation. It only routes.
 */

const SALES_PATH = '/team-prices/sales-deal-builder'
const EXEC_PATH = '/team-prices/executive-deal-builder'
const frameStyle = { position: 'fixed', inset: 0, display: 'block', width: '100%', height: '100dvh', border: 0, background: '#f3f6f8' }
const boxStyle = { maxWidth: 520, margin: '12vh auto', padding: 28, background: '#fff', border: '1px solid #dce5ea', borderRadius: 15, font: '15px/1.5 system-ui, sans-serif', color: '#102b40' }

function token() {
  try { return localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token') } catch { return null }
}
function loginUrl(next) { return '/login?next=' + encodeURIComponent(next) }

function shareApiBase() {
  // The builder pages run in a same-origin iframe and read this to find the API.
  try { window.__EVOSYS_API_BASE__ = API_BASE } catch { /* ignore */ }
}

function Notice({ title, children }) {
  return (
    <div style={{ minHeight: '100dvh', background: '#f3f6f8', padding: 16 }}>
      <div style={boxStyle} role="status">
        <div style={{ fontWeight: 800, fontSize: 22, marginBottom: 6 }}>EvoSys <span style={{ color: '#087f83' }}>Pro</span></div>
        <h1 style={{ fontSize: 20, margin: '10px 0' }}>{title}</h1>
        {children}
      </div>
    </div>
  )
}

export function SalesDealBuilder() {
  shareApiBase()
  if (!token()) return <Navigate to={loginUrl(SALES_PATH)} replace />
  return <iframe title="EvoSys Pro Sales Deal Builder" src="/team-prices/sales-builder-frame.html" style={frameStyle} />
}

export function ExecutiveDealBuilder() {
  const [view, setView] = useState({ kind: 'loading' })
  shareApiBase()
  const signedIn = !!token()

  useEffect(() => {
    if (!signedIn) return
    let gone = false
    const headers = { Authorization: 'Bearer ' + token() }
    ;(async () => {
      try {
        const r = await fetch(API_BASE + '/deal-builder/executive-page', { headers, cache: 'no-store' })
        if (gone) return
        if (r.status === 401) { clearToken(); setView({ kind: 'expired' }); return }
        if (r.ok) { setView({ kind: 'page', html: await r.text() }); return }
        if (r.status === 403) {
          const a = await fetch(API_BASE + '/deal-builder/access', { headers, cache: 'no-store' })
          const access = a.ok ? await a.json() : {}
          if (!gone) setView({ kind: access.sales ? 'sales' : 'denied' })
          return
        }
        setView({ kind: 'error' })
      } catch {
        if (!gone) setView({ kind: 'error' })
      }
    })()
    return () => { gone = true }
  }, [signedIn])

  if (!signedIn || view.kind === 'expired') return <Navigate to={loginUrl(EXEC_PATH)} replace />
  if (view.kind === 'sales') return <Navigate to={SALES_PATH} replace />
  if (view.kind === 'page') return <iframe title="EvoSys Pro Executive Deal Builder" srcDoc={view.html} style={frameStyle} />
  if (view.kind === 'denied') {
    return (
      <Notice title="This page needs executive access">
        <p>Your account can't open the executive Deal Builder. If you sell for EvoSys Pro, ask the owner to add you to the sales team so you can use the Sales Deal Builder.</p>
        <p><Link to="/">Back to EvoSys Pro</Link></p>
      </Notice>
    )
  }
  if (view.kind === 'error') {
    return (
      <Notice title="Couldn't load the Deal Builder">
        <p>The server didn't answer. Please refresh in a moment.</p>
      </Notice>
    )
  }
  return <Notice title="Loading the Deal Builder…"><p>Checking your access.</p></Notice>
}
