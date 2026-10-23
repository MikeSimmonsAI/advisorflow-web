/* PHONE ALERTS for the field app (build list #27). One switch: new visits,
 * moves, offer answers and hot leads buzz this phone even with the app
 * closed. Uses the platform's web push (src/mobile/push.js + /push/*). The
 * lock screen only ever shows a generic line like "Appointment update"; the
 * details open in the app after the person is signed in. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { PUSH_STATUS, pushApi, readPushStatus, subscribePush, unsubscribePush } from '../../../mobile/push'
import { registerServiceWorker } from '../../../mobile/pwa'
import { isInstalled, isIOS } from './fieldApp'
import './appts.css'
import { ev } from '../engine'

export default function PhoneAlerts({ compact = false }) {
  const papi = pushApi(api)
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const refresh = () => readPushStatus(papi.getConfig).then(setStatus).catch(() => setStatus(PUSH_STATUS.UNSUPPORTED))
  useEffect(() => { registerServiceWorker().then(refresh) }, [])

  async function turnOn() {
    setBusy(true); setMsg(null)
    try {
      const r = await subscribePush({ getConfig: papi.getConfig, postSubscription: papi.postSubscription })
      if (r.ok) { setMsg('Alerts are on for this phone.'); try { await papi.test() } catch { /* the switch still worked */ } }
      else if (r.status === PUSH_STATUS.DENIED) setMsg('Alerts are blocked for this app in your phone settings.')
      await refresh()
    } catch (e) { setMsg(e.message || 'Could not turn alerts on.') } finally { setBusy(false) }
  }
  async function turnOff() {
    setBusy(true); setMsg(null)
    try { await unsubscribePush({ deleteByHash: papi.deleteByHash }); setMsg('Alerts are off for this phone.'); await refresh() }
    catch (e) { setMsg(e.message || 'Could not turn alerts off.') } finally { setBusy(false) }
  }

  if (status === null || status === PUSH_STATUS.UNCONFIGURED) return null
  const needsInstall = isIOS() && !isInstalled()
  let body
  if (needsInstall) body = <span>{ev('On iPhone and iPad, add EvoSys Field to your home screen first, then open it from the icon and turn alerts on here.')}</span>
  else if (status === PUSH_STATUS.UNSUPPORTED) body = <span>{ev('This browser can\'t show alerts. Open EvoSys Field from your home screen.')}</span>
  else if (status === PUSH_STATUS.DENIED) body = <span>Alerts are blocked for this app. Turn them on in your phone's settings, then come back.</span>
  else if (status === PUSH_STATUS.SUBSCRIBED) body = <span>On. New visits, changes, offer answers and hot leads buzz this phone.</span>
  else body = <span>Get a buzz on this phone for new visits, changes, offer answers and hot leads - even with the app closed.</span>
  if (compact && status === PUSH_STATUS.SUBSCRIBED && !msg) return null
  return (
    <div className="it-tip" role="note">
      <span className="pa-bell" aria-hidden="true">🔔</span>
      <div className="it-text"><strong>Alerts on this phone</strong>{body}{msg ? <span className="evo-small">{msg}</span> : null}</div>
      {!needsInstall && status === PUSH_STATUS.AVAILABLE ? (
        <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy} onClick={turnOn}>{busy ? 'Turning on…' : 'Turn on'}</button>
      ) : null}
      {status === PUSH_STATUS.SUBSCRIBED ? (
        <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={turnOff}>Turn off</button>
      ) : null}
    </div>
  )
}
