/**
 * Where a 401 sends someone: the right sign-in screen (phone or desktop), told
 * WHY ("expired=1"), and told where they were ("next") so signing back in
 * returns them there instead of to the home screen. Only an in-app path is
 * ever carried.
 */
export function sessionExpiredUrl(loc) {
  const path = (loc && loc.pathname) || '/'
  const here = path + ((loc && loc.search) || '')
  const mobile = path === '/m' || path.startsWith('/m/')
  const login = mobile ? '/m/login' : '/login'
  if (path === '/login' || path === '/m/login') return login
  return login + '?expired=1&next=' + encodeURIComponent(here)
}

/** A `next` value is followed only when it is a path inside this app. */
export function safeNextPath(raw) {
  const p = String(raw || '')
  if (!p.startsWith('/') || p.startsWith('//') || p.startsWith('/\\')) return null
  if (/^\/(m\/)?login(\b|\/|\?|$)/.test(p)) return null
  return p
}
