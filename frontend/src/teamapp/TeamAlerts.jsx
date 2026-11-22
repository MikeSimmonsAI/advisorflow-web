/* PHONE ALERTS for the EvoSys Pro team app. One switch per phone: new Team
 * Chat messages buzz it even when the app is closed. Uses the platform's web
 * push (src/mobile/push.js + /push/*). The lock screen shows who wrote and
 * where ("New message from Michael") - never the message itself.
 *
 * `banner` = the slim prompt on the Chat list, shown only while alerts are off
 * and could be turned on. Without it = the full card on the Account screen. */
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { PUSH_STATUS, pushApi, readPushStatus, subscribePush, unsubscribePush } from '../mobile/push'
import { registerServiceWorker } from '../mobile/pwa'
import { isInstalled, isIOS } from '../pages/wholesale/appts/fieldApp'

export function useTeamAlerts() {
  const papi = pushApi(api)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const refresh = () => readPushStatus(papi.getConfig).then(setStatus).catch(() => setStatus(PUSH_STATUS.UNSUPPORTED))
  useEffect(() => { registerServiceWorker().then(refresh) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  async function turnOn() {
    setBusy(true); setMsg(null)
    try {
      const r = await subscribePush({ getConfig: papi.getConfig, postSubscription: papi.postSubscription })
      if (r.ok) {
        setMsg('Alerts are on for this phone. A test alert is on its way.')
        try { await api.post('/push/test', { url: '/app/account' }) } catch { /* the switch still worked */ }
      } else if (r.status === PUSH_STATUS.DENIED) {
        setMsg('Alerts are blocked for EvoSys Pro. Turn them on in your phone’s Settings → Notifications, then come back.')
      }
      await refresh()
    } catch (e) { setMsg((e && e.message) || 'Could not turn alerts on.') } finally { setBusy(false) }
  }
  async function turnOff() {
    setBusy(true); setMsg(null)
    try { await unsubscribePush({ deleteByHash: papi.deleteByHash }); setMsg('Alerts are off for this phone.'); await refresh() }
    catch (e) { setMsg((e && e.message) || 'Could not turn alerts off.') } finally { setBusy(false) }
  }
  async function test() {
    setBusy(true); setMsg(null)
    try { await api.post('/push/test', { url: '/app/account' }); setMsg('Test alert sent - it should pop up in a few seconds.') }
    catch (e) { setMsg((e && e.message) || 'Could not send a test alert.') } finally { setBusy(false) }
  }
  return { status, busy, msg, turnOn, turnOff, test }
}

export default function TeamAlerts({ banner = false }) {
  const { status, busy, msg, turnOn, turnOff, test } = useTeamAlerts()
  if (status === null || status === PUSH_STATUS.UNCONFIGURED) return null
  const needsInstall = isIOS() && !isInstalled()

  if (banner) {
    if (status !== PUSH_STATUS.AVAILABLE && !msg) return null
    return (
      <div className="ta-note ta-alerts-banner" role="note">
        <span aria-hidden="true">🔔</span>
        <span className="ta-alerts-text">{msg || (needsInstall
          ? 'Add EvoSys Pro to your Home Screen, open it from the icon, then turn on alerts.'
          : 'Get a buzz when someone messages you, even with the app closed.')}</span>
        {!needsInstall && status === PUSH_STATUS.AVAILABLE &&
          <button type="button" className="ta-btn ta-btn--primary ta-btn--sm" disabled={busy} onClick={turnOn}>{busy ? 'Turning on…' : 'Turn on'}</button>}
      </div>
    )
  }

  let body
  if (needsInstall) body = 'On iPhone, add EvoSys Pro to your Home Screen first (Share → Add to Home Screen), open it from the icon, then turn alerts on here.'
  else if (status === PUSH_STATUS.UNSUPPORTED) body = 'This browser can’t show alerts. Open EvoSys Pro from your Home Screen icon.'
  else if (status === PUSH_STATUS.DENIED) body = 'Alerts are blocked for EvoSys Pro. Turn them on in your phone’s Settings → Notifications, then come back.'
  else if (status === PUSH_STATUS.SUBSCRIBED) body = 'On. New chat messages and announcements buzz this phone.'
  else body = 'Off. Turn on to get a buzz for new chat messages and announcements, even with the app closed.'
  return (
    <section className="ta-card ta-alerts">
      <h2 className="ta-h3">🔔 Phone alerts</h2>
      <p className="ta-muted">{body}</p>
      {msg && <p className="ta-note" role="status">{msg}</p>}
      <div className="ta-alerts-actions">
        {!needsInstall && status === PUSH_STATUS.AVAILABLE &&
          <button type="button" className="ta-btn ta-btn--primary" disabled={busy} onClick={turnOn}>{busy ? 'Turning on…' : 'Turn on alerts'}</button>}
        {status === PUSH_STATUS.SUBSCRIBED && <>
          <button type="button" className="ta-btn ta-btn--primary" disabled={busy} onClick={test}>Send a test alert</button>
          <button type="button" className="ta-btn ta-btn--ghost" disabled={busy} onClick={turnOff}>Turn off</button>
        </>}
      </div>
    </section>
  )
}
