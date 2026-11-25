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

// Order and names follow the owner's mockup (restland-advisor-portal.html, Nov 25):
// My Day, Appointments, Follow-Ups, My Families, 6 Block, Calendar, Performance,
// Notifications, My Profile, Management. The directive's extra screens follow.
const ADVISOR_NAV = [
  ['', 'My Day'], ['appointments', 'Appointments'], ['follow-ups', 'Follow-Ups'], ['families', 'My Families'],
  ['evo6', 'Evo 6 Block'], ['calendar', 'Calendar'], ['performance', 'Performance'], ['notifications', 'Notifications'],
  ['profile', 'My Profile'],
]
const EXTRA_NAV = [['add-leads', 'ADD Leads'], ['service', 'Service Requests'], ['sales', 'Sales & Revenue']]
// Management is one menu item; its four screens are tabs inside it.
const MANAGEMENT = [['command', 'Command Center'], ['team', 'Advisor Management'], ['audit', 'Activity Audit'], ['settings', 'Settings']]
const MANAGEMENT_KEYS = MANAGEMENT.map(([p]) => p)
// Page headings: [advisor title, manager title, line under it]
const HEADINGS = {
  appointments: ['My Appointments', 'Appointments', 'Assigned records only', 'Every advisor'],
  'follow-ups': ['My Follow-Ups', 'Follow-Ups', 'Assigned records only', 'Every advisor'],
  families: ['My Families', 'Families', 'Assigned families only', 'Every advisor'],
  evo6: ['My Evo 6 Block', 'Evo 6 Block', 'Referral intake', 'Referral intake · every advisor'],
  calendar: ['My Calendar', 'Calendar', 'Central time', 'Central time · every advisor'],
  performance: ['My Performance', 'Performance', 'Personal metrics only', 'Team and advisor metrics'],
  notifications: ['Notifications', 'Notifications', 'Reminders and alerts', 'Reminders and alerts'],
  profile: ['My Profile', 'My Profile', 'Individual account', 'Individual account'],
  'add-leads': ['ADD Leads', 'ADD Leads', 'At-death families', 'At-death families · every advisor'],
  service: ['Service Requests', 'Service Requests', 'Assigned requests only', 'Every advisor'],
  sales: ['Sales & Revenue', 'Sales & Revenue', 'Your recorded sales', 'Every advisor'],
  command: ['Restland Command Center', 'Restland Command Center', 'Authorized managers only', 'Authorized managers only'],
  team: ['Restland Command Center', 'Restland Command Center', 'Authorized managers only', 'Authorized managers only'],
  audit: ['Restland Command Center', 'Restland Command Center', 'Authorized managers only', 'Authorized managers only'],
  settings: ['Restland Command Center', 'Restland Command Center', 'Authorized managers only', 'Authorized managers only'],
}
const BOTTOM = [['', 'My Day', 'day'], ['appointments', 'Appointments', 'calendar'], ['follow-ups', 'Follow-Ups', 'repeat'],
  ['evo6', 'Evo 6 Block', 'network']]
const BASE = '/program/restland-bookit'
const POLL_MS = 30000

export default function BookitApp() {
  const nav = useNavigate()
  const loc = useLocation()
  const [me, setMe] = useState(null)
  const [err, setErr] = useState(null)
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

  const sub = loc.pathname.slice(BASE.length).replace(/^\//, '').split('/')[0]
  const manager = !!me?.me?.manager
  const head = HEADINGS[sub]
  const inManagement = MANAGEMENT_KEYS.includes(sub)
  const ctx = useMemo(() => ({ me, say, refresh, tick, go, base: BASE }), [me, say, refresh, tick, go])

  if (err) {
    return (
      <div className="bk" style={{ display: 'block' }}><div className="bk-page" style={{ maxWidth: 560, margin: '10vh auto' }}>
        <div className="bk-card"><h2>Restland BookIT</h2>
          <p className="muted">{err}</p><button className="bk-btn" onClick={() => nav('/workspaces')}>Back to Program Access</button>
        </div></div></div>
    )
  }
  return (
    <Bk.Provider value={ctx}>
      <div className="bk">
        <header className="bk-top">
          <div className="bk-top-brand"><b>RESTLAND</b><small>FAMILY SERVICE ADVISOR PORTAL</small></div>
          <S.Search />
          <span className={'bk-status hide-m' + (fresh.ok ? '' : ' stale')} title="When BookIT last checked for updates">
            <i />{fresh.ok ? (fresh.at ? 'Updated ' + fresh.at.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }) : 'Loading') : 'Reconnecting…'}
          </span>
          <button className="bk-iconbtn" onClick={() => go('notifications')} aria-label="Notifications"><Icon name="bell" />{unread > 0 && <span className="dot" />}</button>
          <div className="bk-top-who" title={me?.me?.name || ''}>{me?.me?.name || ''}</div>
        </header>
        <aside className="bk-rail" aria-label="BookIT navigation">
          {ADVISOR_NAV.map(([p, l]) => (
            <button key={p} className={'bk-nav' + (sub === p ? ' on' : '')} onClick={() => go(p)} aria-current={sub === p ? 'page' : undefined}>
              <span>{l}</span>{p === 'notifications' && unread > 0 && <span className="badge err">{unread}</span>}
            </button>
          ))}
          {manager && <button className={'bk-nav' + (inManagement ? ' on' : '')} onClick={() => go('command')} aria-current={inManagement ? 'page' : undefined}><span>Management</span></button>}
          <div className="sep" />
          {EXTRA_NAV.map(([p, l]) => (
            <button key={p} className={'bk-nav' + (sub === p ? ' on' : '')} onClick={() => go(p)} aria-current={sub === p ? 'page' : undefined}><span>{l}</span></button>
          ))}
          <div className="grow" />
          <button className="bk-nav" onClick={() => nav('/workspaces')}><span>Program Access</span></button>
          <div className="bk-powered">Restland BookIT · Powered by EvoSys Pro</div>
        </aside>
        <main className="bk-main">
          <div className="bk-page">
            {me && head && <div className="bk-title"><h1>{manager ? head[1] : head[0]}</h1><p><small>{manager ? head[3] : head[2]}</small></p></div>}
            {me && manager && inManagement && <div className="bk-subtabs" role="tablist">{MANAGEMENT.map(([p, l]) =>
              <button key={p} role="tab" aria-selected={sub === p} className={sub === p ? 'on' : ''} onClick={() => go(p)}>{l}</button>)}</div>}
            {!me ? <div className="bk-card"><div className="bk-skel" /><div className="bk-skel" style={{ width: '70%' }} /></div> : (
              <Routes>
                <Route index element={<S.MyDay />} />
                <Route path="appointments" element={<S.Appointments />} />
                <Route path="calendar" element={<S.Appointments initialView="cal" />} />
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
            {[['families', 'My Families', 'family'], ['calendar', 'Calendar', 'calendar'], ['performance', 'Performance', 'chart'],
              ['notifications', 'Notifications', 'bell'], ['profile', 'My Profile', 'user'],
              ...(me?.me?.manager ? [['command', 'Management', 'shield']] : []),
              ['add-leads', 'ADD Leads', 'heart'], ['service', 'Service Requests', 'wrench'], ['sales', 'Sales & Revenue', 'dollar']].map(([p, l, ic]) =>
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
