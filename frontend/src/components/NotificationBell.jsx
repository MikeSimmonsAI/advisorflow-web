import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from './SignalPulse'
import './NotificationBell.css'

// THE SINGLE LARGEST SOURCE OF TRAFFIC IN THE PRODUCT.
//
// At 30s this component alone accounted for over half of every HTTP request the
// backend served - more than every dashboard, lead list and report combined -
// and it kept doing it in background tabs nobody was looking at, all night, on
// a 512 MB instance.
//
// Two changes, neither of which costs the user anything:
//
//   1. 60s instead of 30s. A hot-reply alert that lands within a minute is
//      still an alert; nobody is watching the bell for sub-minute latency, and
//      the click-through path is the Replies inbox anyway.
//   2. Nothing polls while the tab is hidden. The interval is cleared on
//      `visibilitychange` and a single immediate refresh runs when the tab
//      comes back, so a returning user sees current state at once rather than
//      waiting out the remainder of a tick.
//
// Effect: ~1,440 requests/day/client -> ~720 at the ceiling, and ~0 for the
// hours a dashboard sits open behind other windows.
const POLL_INTERVAL_MS = 60000

// The endpoint returns { items, unread_count, has_more }. It used to return a
// bare array. Reading both means the frontend and the backend can deploy in
// either order without the bell throwing on the shape it doesn't expect.
function readPayload(res) {
  if (Array.isArray(res)) return { items: res, unreadCount: res.length }
  const items = Array.isArray(res?.items) ? res.items : []
  const unreadCount = Number.isFinite(res?.unread_count) ? res.unread_count : items.length
  return { items, unreadCount }
}

export default function NotificationBell() {
  const [notifications, setNotifications] = useState([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const wrapRef = useRef(null)
  const inFlight = useRef(false)

  function load() {
    // A slow response must not stack a second request behind it. Without this
    // guard a backend under load turns one poller into a queue of them.
    if (inFlight.current) return
    inFlight.current = true
    api.get('/notifications/')
      .then((res) => {
        const { items, unreadCount: n } = readPayload(res)
        setNotifications(items)
        setUnreadCount(n)
      })
      .catch(() => {})
      .finally(() => { inFlight.current = false })
  }

  useEffect(() => {
    let interval = null
    const start = () => {
      if (interval === null) interval = setInterval(load, POLL_INTERVAL_MS)
    }
    const stop = () => {
      if (interval !== null) { clearInterval(interval); interval = null }
    }
    function onVisibility() {
      if (document.hidden) {
        stop()
      } else {
        load()   // catch up immediately rather than after a full tick
        start()
      }
    }
    if (!document.hidden) { load(); start() }
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [])

  useEffect(() => {
    function handleClickOutside(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  async function handleNotificationClick(n) {
    try {
      await api.post(`/notifications/${n.id}/read`, {})
    } catch {}
    setNotifications((prev) => prev.filter((x) => x.id !== n.id))
    setUnreadCount((c) => (c > 0 ? c - 1 : 0))
    setOpen(false)
    // A link from the server wins (a Wholesale inquiry opens its deal); only an
    // in-app path is followed, never an absolute URL.
    if (n.link && n.link.startsWith('/') && !n.link.startsWith('//')) navigate(n.link)
    else if (n.lead_id) navigate(`/leads/${n.lead_id}`)
  }

  // The badge reads the server's count, not the length of the capped page.
  const count = unreadCount

  return (
    <div className="notif-bell-wrap" ref={wrapRef}>
      <button className="notif-bell-btn" onClick={() => setOpen((o) => !o)} aria-label="Notifications">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {count > 0 && <span className="notif-bell-badge">{count > 9 ? '9+' : count}</span>}
      </button>

      {open && (
        <div className="notif-dropdown">
          <div className="notif-dropdown-header">
            <span>Notifications</span>
            {count > 0 && <span className="notif-dropdown-count">{count} unread</span>}
          </div>
          {notifications.length === 0 ? (
            <div className="notif-empty">You're all caught up.</div>
          ) : (
            <ul className="notif-list">
              {notifications.map((n) => (
                <li key={n.id} className="notif-item" onClick={() => handleNotificationClick(n)}>
                  {(n.type === 'hot_reply' || n.type === 'wholesale_inquiry') && <SignalPulse color={n.type === 'hot_reply' ? 'red' : 'green'} size={6} />}
                  <div className="notif-item-body">
                    <p className="notif-item-text">{n.message}</p>
                    <span className="notif-item-time">{new Date(n.created_at).toLocaleString()}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
