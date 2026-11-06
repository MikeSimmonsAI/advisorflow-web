export const VOLT_PUBLIC_ORIGIN = 'https://voltmarketing.live'
export const VOLT_API_ORIGIN = 'https://api.voltmarketing.live'
export const VOLT_SUPPORT_EMAIL = 'support@voltmarketing.live'

export function isVoltHost(hostname = window.location.hostname) {
  return hostname === 'voltmarketing.live' || hostname === 'www.voltmarketing.live'
}
