/* THE FIELD APP ON THE HOME SCREEN (build list #26).
 *
 * The site already has one installable app (the advisor mobile workspace,
 * /manifest.webmanifest, opens at /m). On the Wholesale side the page points
 * at a second one instead - EvoSys Wholesale, which opens straight to
 * Appointments - so a rep who adds it to their phone or tablet gets the
 * right app with the right icon. Leaving Wholesale puts the original back. */

import { engine } from '../engine'

const FIELD = { manifest: '/manifest-field.webmanifest', icon: '/icons/field-apple-180.png', title: 'EvoSys Field' }
let saved = null

function link(rel) {
  let el = document.querySelector(`link[rel="${rel}"]`)
  if (!el) { el = document.createElement('link'); el.rel = rel; document.head.appendChild(el) }
  return el
}

function meta(name) {
  let el = document.querySelector(`meta[name="${name}"]`)
  if (!el) { el = document.createElement('meta'); el.name = name; document.head.appendChild(el) }
  return el
}

export function applyFieldAppHead(on) {
  if (typeof document === 'undefined') return
  const m = link('manifest'); const i = link('apple-touch-icon'); const t = meta('apple-mobile-web-app-title')
  if (on) {
    if (!saved) saved = { manifest: m.getAttribute('href'), icon: i.getAttribute('href'), title: t.getAttribute('content') }
    if (m.getAttribute('href') !== FIELD.manifest) m.setAttribute('href', FIELD.manifest)
    if (i.getAttribute('href') !== FIELD.icon) i.setAttribute('href', FIELD.icon)
    // A second Wholesale tenant's field app is never titled with another
    // brand's name (#272 P4); EvoSys keeps "EvoSys Field".
    t.setAttribute('content', engine() ? FIELD.title : 'Field')
  } else if (saved) {
    if (saved.manifest) m.setAttribute('href', saved.manifest)
    if (saved.icon) i.setAttribute('href', saved.icon)
    if (saved.title) t.setAttribute('content', saved.title); else t.removeAttribute('content')
    saved = null
  }
}

/* Android / Chrome hands us an install prompt we can show on a button. */
let deferred = null
const listeners = new Set()
if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); deferred = e; listeners.forEach((f) => f(true)) })
  window.addEventListener('appinstalled', () => { deferred = null; listeners.forEach((f) => f(false)) })
}
export function canPromptInstall() { return !!deferred }
export function onInstallable(f) { listeners.add(f); return () => listeners.delete(f) }
export async function promptInstall() {
  if (!deferred) return false
  deferred.prompt()
  const r = await deferred.userChoice.catch(() => null)
  deferred = null
  return r && r.outcome === 'accepted'
}

export function isInstalled() {
  try {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true
  } catch { return false }
}

export function isIOS() {
  const ua = (typeof navigator !== 'undefined' && navigator.userAgent) || ''
  return /iPad|iPhone|iPod/.test(ua) || (ua.includes('Macintosh') && typeof document !== 'undefined' && 'ontouchend' in document)
}
