/* "Put this on your home screen" - shown on Appointments until the app is
 * installed or the person closes the tip. */
import { useEffect, useState } from 'react'
import { canPromptInstall, isInstalled, isIOS, onInstallable, promptInstall } from './fieldApp'
import './appts.css'
import { ev } from '../engine'

const KEY = 'evo_field_install_tip_closed'

function closed() { try { return localStorage.getItem(KEY) === '1' } catch { return false } }

export default function InstallTip() {
  const [hidden, setHidden] = useState(() => isInstalled() || closed())
  const [can, setCan] = useState(canPromptInstall())
  useEffect(() => onInstallable(setCan), [])
  if (hidden) return null
  const close = () => { try { localStorage.setItem(KEY, '1') } catch { /* private window */ } setHidden(true) }
  return (
    <div className="it-tip" role="note">
      <img src="/icons/field-192.png" alt="" width="44" height="44" />
      <div className="it-text">
        <strong>{ev('Put EvoSys Field on your home screen')}</strong>
        {can ? <span>One tap and it opens straight to your appointments.</span>
          : isIOS() ? <span>iPhone or iPad: tap Share <span aria-hidden="true">⎙</span>, then <b>Add to Home Screen</b>.</span>
            : <span>Android: tap the browser menu ⋮, then <b>Install app</b> or <b>Add to Home screen</b>.</span>}
      </div>
      {can ? <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={async () => { if (await promptInstall()) setHidden(true) }}>Install</button> : null}
      <button type="button" className="it-x" onClick={close} aria-label="Close tip">×</button>
    </div>
  )
}
