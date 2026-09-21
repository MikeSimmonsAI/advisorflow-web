/**
 * WHAT KIND OF FAILURE THIS WAS — one answer for every request the app makes.
 *
 * THE DEFECT. Four different situations reached the page as one of two
 * sentences: the server's `detail` when it happened to send a string, and
 * "Request failed" for everything else — a 403 with no body, a 404, a 500, and
 * a 422 whose `detail` is FastAPI's LIST of field errors, which is not a
 * string and so was thrown away. A validation error on a form read exactly
 * like a server crash. And a request that got no answer at all read "Unable
 * to reach the server. Please check your connection", which, in the production
 * incident that prompted this file, sent people to look at their wifi while
 * the backend was restarting.
 *
 * The rule this module holds: NOTHING HERE EVER SAYS "UNREACHABLE". That word
 * is reserved for a request that got no response (api/client.js, the fetch
 * rejection branch). Anything with a status code was answered, and says so.
 *
 * `kind` is for code; the message is for a person.
 *
 * Imports nothing, so it runs under plain node:
 *     node tests/frontend/httpErrors.test.mjs
 */

export function httpErrorKind(status) {
  if (status === 401 || status === 403) return 'auth'
  if (status === 404) return 'not_found'
  if (status === 400 || status === 409 || status === 422) return 'validation'
  if (status === 402) return 'entitlement'
  if (status === 429) return 'rate_limited'
  if (status >= 500) return 'server'
  return 'http'
}

export function fallbackHttpMessage(status, detail) {
  // FastAPI's validation shape: [{loc: ['body', 'email'], msg: '...', type: '...'}].
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] || {}
    const where = Array.isArray(first.loc)
      ? first.loc.filter(p => p !== 'body' && p !== 'query' && p !== 'path').join('.')
      : ''
    const msg = first.msg || 'is invalid'
    return (where ? where + ': ' : '') + msg
      + (detail.length > 1 ? ' (and ' + (detail.length - 1) + ' more)' : '')
  }
  switch (httpErrorKind(status)) {
    case 'auth':         return "You don't have access to this."
    case 'not_found':    return 'That could not be found.'
    case 'validation':   return 'The request was not accepted (HTTP ' + status + ').'
    case 'entitlement':  return 'This feature is not enabled for this account.'
    case 'rate_limited': return 'Too many requests — wait a moment and try again.'
    case 'server':       return 'The server hit an error (HTTP ' + status + '). '
                              + 'It has been logged — try again in a moment.'
    default:             return 'Request failed (HTTP ' + status + ').'
  }
}
