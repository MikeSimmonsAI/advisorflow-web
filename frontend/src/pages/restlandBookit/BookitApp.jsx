// Restland BookIT — the application shell (owner directive v2.0, Nov 25 2026).
// Light theme only. Every number and record comes from /restland-bookit/*,
// which decides on the server who may see what; nothing here is a control.
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import './bookit.css'
import { Icon, Toast } from './ui'
import * as S from './screens'

export const Bk = createContext(null)
export const useBk = () => useContext(Bk)

const ADVISOR_NAV = [
  ['', 'My Day', 'day'], ['appointments', 'Appointments', 'calendar'], ['follow-ups', 'Follow-Ups', 'repeat'],
  ['evo6', 'Evo 6 Block', 'network'], ['add-leads', 'ADD Leads', 'heart'], ['families', 'My Families', 'family'],
  ['service', 'Service Requests', 'wrench'], ['sales', 'Sales & Revenue', 'dollar'], ['performance', 'Performance', 'chart'],
  ['notifications', 'Notifications', 'bell'],
]
const MANAGER_NAV = [
  ['command', 'Command Center', 'shield'], ['team', 'Advisor Management', 'users'], ['audit', 'Activity Audit', 'audit'],
  ['settings', 'Settings', 'gear'],
]
const BOTTOM = [['', 'My Day', 'day'], ['appointments', 'Appointments', 'calendar'], ['follow-ups', 'Follow-Ups', 'repeat'],
  ['evo6', 'Evo 6 Block', 'network']]
const BASE = '/program/restland-bookit'
const POLL_MS = 30000

export default function BookitApp() {
  const nav = useNavigate()
  const loc = useLocation()
  const [me, setMe] = useState(null)
  const [err, setErr] = useState(null)
  const [open, setOpen] = useState(() => { try { return localStorage.getItem('bk_rail') !== '0' } catch { return true } })
  const [toast, setToast] = useState(null)
  const [more, setMore] = useState(false)
  const [copilot, setCopilot] = useState(false)
  const [unread, setUnread] = useState(0)
  const [fresh, setFresh] = useState({ at: null, ok: true })
  const [tick, setTick] = useState(0)                         // bump to refresh every screen
  const toastT = useRef(null)

  useEffect(() => { document.title = 'Restland BookIT' }, [])
  useEffect(() => {
    api.get('/restland-bookit/me').then(setMe).catch(e => setErr(e.message || 'BookIT could not be opened.'))
  }, [])
  // polling fallback: refresh visible data every 30s; mark stale if it fails
  useEffect(() => {
    const t = setInterval(() => setTick(x => x + 1), POLL_MS)
    return () => clearInterval(t)
  }, [])
  useEffect(() => {
    if (!me) return
    api.get('/restland-bookit/notifications?unread=true').then(r => {
      setUnread((r.notifications || []).length); setFresh({ at: new Date(), ok: true })
    }).catch(() => setFresh(f => ({ ...f, ok: false })))
  }, [me, tick])

  const say = useCallback((text, isErr) => {
    clearTimeout(toastT.current)
    setToast({ text, err: isErr })
    toastT.current = setTimeout(() => setToast(null), 3600)
  }, [])
  const refresh = useCallback(() => setTick(x => x + 1), [])
  const go = useCallback((p) => { setMore(false); nav(BASE + (p ? '/' + p : '')) }, [nav])
  const toggle = () => setOpen(o => { try { localStorage.setItem('bk_rail', o ? '0' : '1') } catch { /* ignore */ } return !o })

  const sub = loc.pathname.slice(BASE.length).replace(/^\//, '').split('/')[0]
  const all = me?.me?.manager ? [...ADVISOR_NAV, ...MANAGER_NAV] : ADVISOR_NAV
  const title = (all.find(([p]) => p === sub) || ADVISOR_NAV[0])[1]
  const ctx = useMemo(() => ({ me, say, refresh, tick, go, base: BASE }), [me, say, refresh, tick, go])

  if (err) {
    return (
      <div className="bk" style={{ display: 'block' }}><div className="bk-page" style={{ maxWidth: 560, margin: '10vh auto' }}>
        <div className="bk-card"><h2><span className="bk-mark">B</span> Restland BookIT</h2>
          <p className="muted">{err}</p><button className="bk-btn" onClick={() => nav('/workspaces')}>Back to Program Access</button>
        </div></div></div>
    )
  }
  return (
    <Bk.Provider value={ctx}>
      <div className="bk">
        <aside className={'bk-rail' + (open ? ' open' : '')} aria-label="BookIT navigation">
          <div className="bk-brand">
            <button className="bk-mark" style={{ border: 0 }} onClick={toggle} aria-label={open ? 'Collapse navigation' : 'Expand navigation'}>B</button>
            {open && <div className="bk-word"><b>Restland</b> <i>BookIT</i><small>Book It. Close It. Track It.</small></div>}
          </div>
          {ADVISOR_NAV.map(([p, l, ic]) => (
            <button key={p} className={'bk-nav' + (sub === p ? ' on' : '')} data-tip={l} onClick={() => go(p)} aria-current={sub === p ? 'page' : undefined}>
              <Icon name={ic} />{open && <span>{l}</span>}{open && p === 'notifications' && unread > 0 && <span className="badge err">{unread}</span>}
            </button>
          ))}
          {me?.me?.manager && <div className="sep" />}
          {me?.me?.manager && MANAGER_NAV.map(([p, l, ic]) => (
            <button key={p} className={'bk-nav' + (sub === p ? ' on' : '')} data-tip={l} onClick={() => go(p)}><Icon name={ic} />{open && <span>{l}</span>}</button>
          ))}
          <div className="grow" />
          <button className="bk-nav" data-tip="Program Access" onClick={() => nav('/workspaces')}><Icon name="out" />{open && <span>Program Access</span>}</button>
          {open && <div className="faint" style={{ padding: '6px 10px' }}>Powered by EvoSys Pro</div>}
        </aside>
        <main className="bk-main">
          <header className="bk-head">
            <div>
              <div className="crumb">Restland BookIT</div>
              <h1>{title}</h1>
            </div>
            <S.Search />
            <span className={'bk-status hide-m' + (fresh.ok ? '' : ' stale')} title="When BookIT last checked for updates">
              <i />{fresh.ok ? (fresh.at ? 'Updated ' + fresh.at.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'Loading') : 'Reconnecting…'}
            </span>
            <button className="bk-iconbtn" onClick={() => go('notifications')} aria-label="Notifications"><Icon name="bell" />{unread > 0 && <span className="dot" />}</button>
            <div className="bk-avatar" title={me?.me?.name || ''}>{(me?.me?.name || '?').split(' ').map(x => x[0]).slice(0, 2).join('')}</div>
          </header>
          <div className="bk-page">
            {!me ? <div className="bk-card"><div className="bk-skel" /><div className="bk-skel" style={{ width: '70%' }} /></div> : (
              <Routes>
                <Route index element={<S.MyDay />} />
                <Route path="appointments" element={<S.Appointments />} />
                <Route path="follow-ups" element={<S.FollowUps />} />
                <Route path="evo6" element={<S.Evo6 kind="evo6" />} />
                <Route path="add-leads" element={<S.AddLeads />} />
                <Route path="families" element={<S.Families />} />
                <Route path="service" element={<S.Service />} />
                <Route path="sales" element={<S.Sales />} />
                <Route path="performance" element={<S.Performance />} />
                <Route path="notifications" element={<S.Notifications onRead={() => setUnread(0)} />} />
                <Route path="profile" element={<S.Profile />} />
                {me.me.manager && <Route path="command" element={<S.Command />} />}
                {me.me.manager && <Route path="team" element={<S.Team />} />}
                {me.me.manager && <Route path="audit" element={<S.Audit />} />}
                {me.me.manager && <Route path="settings" element={<S.Settings />} />}
                <Route path="*" element={<Navigate to={BASE} replace />} />
              </Routes>
            )}
          </div>
        </main>
        <nav className="bk-bottom" aria-label="BookIT">
          {BOTTOM.map(([p, l, ic]) => <button key={p} className={sub === p ? 'on' : ''} onClick={() => go(p)}><Icon name={ic} />{l}</button>)}
          <button onClick={() => setMore(true)}><Icon name="more" />More</button>
        </nav>
        {more && <>
          <div className="bk-scrim" onClick={() => setMore(false)} />
          <div className="bk-more" role="dialog" aria-label="More">
            {[['add-leads', 'ADD Leads', 'heart'], ['families', 'My Families', 'family'], ['service', 'Service Requests', 'wrench'],
              ['sales', 'Sales & Revenue', 'dollar'], ['performance', 'Performance', 'chart'], ['notifications', 'Notifications', 'bell'],
              ...(me?.me?.manager ? [['command', 'Command Center', 'shield'], ['team', 'Advisor Management', 'users']] : []),
              ['profile', 'My Profile', 'user']].map(([p, l, ic]) =>
              <button key={p} onClick={() => go(p)}><Icon name={ic} />{l}</button>)}
            <button onClick={() => { setMore(false); setCopilot(true) }}><Icon name="spark" />Copilot</button>
            <button onClick={() => nav('/workspaces')}><Icon name="out" />Program Access</button>
          </div>
        </>}
        {!copilot && <button className="bk-btn pri bk-fab" onClick={() => setCopilot(true)}><Icon name="spark" />Copilot</button>}
        {copilot && <S.Copilot onClose={() => setCopilot(false)} />}
        <Toast toast={toast} />
      </div>
    </Bk.Provider>
  )
}
