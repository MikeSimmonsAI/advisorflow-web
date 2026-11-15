/**
 * WHERE "BACK" GOES. Only screens inside this app that can launch a builder,
 * matched exactly; anything else (another site, //host, javascript:, a typo)
 * falls back to Program Access. The query string is ignored.
 */
const RETURN_TO = [
  [/^\/workspaces$/, 'Program Access'],
  [/^\/sales$/, 'Back Office'],
  [/^\/sales\/(my-day|pipeline)$/, 'Back Office'],
  [/^\/sales\/compensation$/, 'Team Compensation'],
  [/^\/sales\/my-compensation$/, 'My Compensation'],
  [/^\/sales\/pricing$/, 'Pricing & Plans'],
  [/^\/sales\/(opportunities|leads)\/[A-Za-z0-9-]{8,64}$/, 'the deal'],
]
export function safeReturn(raw) {
  const fallback = { to: '/workspaces', label: 'Program Access' }
  if (typeof raw !== 'string' || !raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return fallback
  const path = raw.split(/[?#]/)[0]
  for (const [re, label] of RETURN_TO) if (re.test(path)) return { to: path, label }
  return fallback
}

