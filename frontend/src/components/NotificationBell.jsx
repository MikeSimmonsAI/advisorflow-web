import { useEffect, useState, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SignalPulse from './SignalPulse'
import './NotificationBell.css'

const POLL_INTERVAL_MS = 30000 // check for new notifications every 30s

export default function NotificationBell() {
  const [notifications, setNotifications] = useState([])
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const wrapRef = useRef(null)

  function load() {
    api.get('/notifications/').then(setNotifications).catch(() => {})
  }

  useEffect(() => {
    load()
    const interval = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(interval)
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
    setOpen(false)
    if (n.lead_id) navigate(`/leads/${n.lead_id}`)
  }

  const count = notifications.length

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
                  {n.type === 'hot_reply' && <SignalPulse color="red" size={6} />}
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
