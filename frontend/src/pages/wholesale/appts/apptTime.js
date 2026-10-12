/* Appointment times are shown and typed in Central time (Dallas-Fort Worth),
 * whatever the device's clock says, and sent to the server with the offset. */

export const TZ = 'America/Chicago'
export const LENGTHS = [[15, '15 minutes'], [30, '30 minutes'], [45, '45 minutes'], [60, '1 hour'], [90, '1½ hours'], [120, '2 hours']]

/** "YYYY-MM-DD" for a Date as seen in Central time. */
export function centralDate(d = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(d)
  const get = (t) => parts.find((p) => p.type === t)?.value
  return `${get('year')}-${get('month')}-${get('day')}`
}

export function todayCentral() { return centralDate(new Date()) }
export function tomorrowCentral() { return centralDate(new Date(Date.now() + 86400000)) }

/** Minutes Central is behind UTC at a given instant (300 in summer, 360 in winter). */
export function centralOffsetMinutes(at) {
  const s = new Intl.DateTimeFormat('en-US', { timeZone: TZ, timeZoneName: 'shortOffset' }).format(at)
  const m = s.match(/GMT([+-]\d+)(?::(\d+))?/)
  if (!m) return 360
  const h = Number(m[1])
  return -(h * 60 + Math.sign(h) * Number(m[2] || 0))
}

/** "2026-10-12" + "14:30" in Central -> ISO string with the right offset. */
export function centralToIso(day, time) {
  if (!day || !time) return null
  const guess = new Date(`${day}T${time}:00Z`)
  const off = centralOffsetMinutes(new Date(guess.getTime() + 6 * 3600000))
  const sign = off >= 0 ? '-' : '+'
  const a = Math.abs(off)
  return `${day}T${time}:00${sign}${String(Math.floor(a / 60)).padStart(2, '0')}:${String(a % 60).padStart(2, '0')}`
}

/** An ISO instant -> { day, time } as Central. */
export function isoToCentral(iso) {
  if (!iso) return { day: '', time: '' }
  const d = new Date(iso)
  const t = new Intl.DateTimeFormat('en-GB', { timeZone: TZ, hour: '2-digit', minute: '2-digit', hour12: false }).format(d)
  return { day: centralDate(d), time: t }
}

/** "Mon, Oct 12" for a Central "YYYY-MM-DD". */
export function dayLabel(day) {
  if (!day) return ''
  const d = new Date(`${day}T12:00:00Z`)
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' }).format(d)
}
