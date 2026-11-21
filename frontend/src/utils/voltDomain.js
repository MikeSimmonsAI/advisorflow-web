export const VOLT_PUBLIC_ORIGIN = 'https://voltmarketing.live'
export const VOLT_API_ORIGIN = 'https://api.voltmarketing.live'
export const VOLT_SUPPORT_EMAIL = 'support@voltmarketing.live'

export const COVERAGE_HOST = 'coverage.voltmarketing.live'
export const COVERAGE_PUBLIC_URL = `https://${COVERAGE_HOST}`

// The customer coverage website's own short address. Its home page IS the
// coverage site; every other path still works as on the main domain.
export function isCoverageHost(hostname = window.location.hostname) {
  return hostname === COVERAGE_HOST
}

export function isVoltHost(hostname = window.location.hostname) {
  return hostname === 'voltmarketing.live' || hostname === 'www.voltmarketing.live' || hostname === COVERAGE_HOST
}
