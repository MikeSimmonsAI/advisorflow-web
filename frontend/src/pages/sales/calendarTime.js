/**
 * Time and geometry for the scheduling screens.
 *
 * ONE MODULE, SO THE TWO SCREENS CANNOT DISAGREE. Team Calendar places events
 * in the BRAND's timezone (one shared grid, seven day columns); Team
 * Availability places them in EACH PERSON'S OWN timezone (one column per
 * human, whose working day is their own). Those are genuinely different
 * questions, and the reason they live in one file is that the rounding, the
 * pixel maths and the day-boundary handling underneath them must be identical
 * — a meeting that renders at 9:00 on one screen and 9:15 on the other
 * destroys confidence in both.
 *
 * THE TIME CONTRACT WITH THE API
 * ------------------------------
 * The backend stores and sends NAIVE datetimes with no timezone suffix, and
 * they come in two flavours that must never be confused:
 *
 *   `starts_at`        — a naive UTC INSTANT ("2026-09-11T14:45:00")
 *   `starts_at_local`  — the SAME instant already resolved to the brand's wall
 *                        clock by the server ("2026-09-11T09:45:00")
 *
 * `new Date("2026-09-11T14:45:00")` parses that as the BROWSER's local time.
 * For an instant that is simply wrong; for a resolved wall clock it is right
 * only by luck when the viewer happens to sit in the brand's zone. That is the
 * bug that once rendered a 9am Chicago meeting at 2pm, so:
 *
 *   · a resolved `*_local` string goes through `parseWall` and is formatted
 *     with NO timezone, because the conversion already happened server-side;
 *   · a naive UTC instant goes through `asUtc` (which appends the Z the server
 *     omits) and is only ever formatted WITH an explicit `timeZone`.
 *
 * Never mix the two. Every function below says which it takes.
 */

// ── parsing ─────────────────────────────────────────────────────────────────

/** A naive UTC instant -> a real Date. Appends the Z the API omits. */
export function asUtc(iso) {
  if (!iso) return null
  const s = String(iso)
  const d = new Date(/[Zz]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z')
  return isNaN(d) ? null : d
}

/**
 * An already-resolved wall clock -> a Date used purely as a formatting vehicle.
 *
 * Rebuilt component by component rather than parsed, so the numbers the server
 * resolved are the numbers rendered, whatever zone the viewer is in.
 */
export function parseWall(iso) {
  if (!iso) return null
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  if (!m) {
    const d = new Date(iso)
    return isNaN(d) ? null : d
  }
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]),
                  Number(m[4]), Number(m[5]))
}

// ── local calendar dates, without the toISOString trap ──────────────────────

/**
 * Local YYYY-MM-DD.
 *
 * `toISOString().slice(0,10)` is the classic wrong answer here: it converts to
 * UTC first, so for anyone west of Greenwich an evening date silently becomes
 * tomorrow. This team is in Central, so that is every evening.
 */
export function ymd(d) {
  const p = n => String(n).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

export function addDays(d, n) {
  const c = new Date(d.getTime())
  c.setDate(c.getDate() + n)
  return c
}

/** Midday, deliberately. Stepping a date by days from midnight can land on a
 *  DST transition and give the same or a skipped day; from noon it never can. */
export function dayFromYmd(s) {
  const m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return new Date()
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12, 0, 0)
}

export function startOfWeek(d, weekStartsOn = 0) {
  const c = new Date(d.getFullYear(), d.getMonth(), d.getDate(), 12, 0, 0)
  const diff = (c.getDay() - weekStartsOn + 7) % 7
  return addDays(c, -diff)
}

export function startOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1, 12, 0, 0)
}

export function endOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0, 12, 0, 0)
}

/** The six-week block a month view actually draws, including the spill days. */
export function monthGridRange(d, weekStartsOn = 0) {
  const first = startOfWeek(startOfMonth(d), weekStartsOn)
  const lastDay = endOfMonth(d)
  let last = startOfWeek(lastDay, weekStartsOn)
  last = addDays(last, 6)
  return { first, last }
}

export function sameDay(a, b) {
  return !!a && !!b && ymd(a) === ymd(b)
}

// ── formatting ──────────────────────────────────────────────────────────────

/** "9:45 AM" from a resolved wall clock. No timeZone — see the module note. */
export function wallTime(iso) {
  const d = parseWall(iso)
  return d ? d.toLocaleTimeString(undefined,
    { hour: 'numeric', minute: '2-digit' }) : ''
}

/** "9:45 AM" from a naive UTC instant, rendered in an explicit zone. */
export function zoneTime(iso, tz) {
  const d = asUtc(iso)
  if (!d) return ''
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: tz || undefined, hour: 'numeric', minute: '2-digit',
    }).format(d)
  } catch {
    // An unknown IANA name must not blank out a whole column.
    return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  }
}

export function hourLabel(h) {
  const h12 = h % 12 === 0 ? 12 : h % 12
  return h12 + (h < 12 || h === 24 ? ' AM' : ' PM')
}

export function dayName(d, long = false) {
  return d.toLocaleDateString(undefined, { weekday: long ? 'long' : 'short' })
}

export function monthName(d) {
  return d.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
}

/**
 * "Sep 6 – 12, 2026" / "Sep 28 – Oct 4, 2026" / one long date for a single day.
 *
 * The year is appended as plain text rather than asked of `toLocaleDateString`.
 * A partial option set is not a format: `{day:'numeric', year:'numeric'}` is a
 * combination no locale has a pattern for, and en-US answers it with
 * "2026 (day: 12)" — which is how the header of this calendar read
 * "Sep 6 – 2026 (day: 12)" in the first audit pass. Ask a locale only for
 * combinations it actually formats, and join the rest yourself.
 */
export function rangeLabel(from, to) {
  if (ymd(from) === ymd(to)) {
    return from.toLocaleDateString(undefined,
      { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
  }
  const sameMonth = from.getMonth() === to.getMonth()
    && from.getFullYear() === to.getFullYear()
  const sameYear = from.getFullYear() === to.getFullYear()
  const a = from.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  const b = sameMonth
    ? String(to.getDate())
    : to.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  const year = sameYear ? to.getFullYear()
    : from.getFullYear() + '–' + to.getFullYear()
  return a + ' – ' + b + ', ' + year
}

/**
 * Does a [start, end] span touch this local date, in the given zone?
 *
 * FOUND IN THE VISUAL AUDIT. Both grids previously asked "does the span START
 * or END on this day", which silently dropped the MIDDLE of anything spanning
 * more than two days — so a Wednesday-to-Friday block of PTO rendered on
 * Wednesday and Friday and left Thursday looking bookable. An overlap test is
 * the only correct question, and the caller clamps the span to the day so a
 * boundary does not render as a zero-height sliver.
 */
export function spanTouchesDay(startIso, endIso, tz, dayKey) {
  const s = zoneYmd(startIso, tz)
  const e = zoneYmd(endIso, tz)
  if (!s || !e) return false
  // An end at exactly midnight belongs to the previous day, not to the day it
  // names — otherwise a block "until Friday 00:00" paints an empty Friday.
  const endsAtMidnight = zoneMinutes(endIso, tz) === 0
  const lastDay = endsAtMidnight ? prevYmd(e) : e
  return s <= dayKey && dayKey <= lastDay
}

function prevYmd(key) {
  const m = String(key).match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!m) return key
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]) - 1, 12)
  return ymd(d)
}

/**
 * Where a span sits on ONE day's grid, clamped to that day.
 *
 * A span that began yesterday starts at the top of the column; one that ends
 * tomorrow runs to the bottom. Without the clamp, `zoneMinutes` of a start that
 * is not on this day returns that OTHER day's wall clock and the block lands at
 * a meaningless position.
 */
export function clampToDay(startIso, endIso, tz, dayKey, endHour) {
  const startsToday = zoneYmd(startIso, tz) === dayKey
  const endsToday = zoneYmd(endIso, tz) === dayKey
  return {
    startMin: startsToday ? zoneMinutes(startIso, tz) : 0,
    endMin: endsToday ? zoneMinutes(endIso, tz) : endHour * 60,
  }
}

// ── grid geometry ───────────────────────────────────────────────────────────

/** Minutes past midnight, from a resolved wall clock. */
export function wallMinutes(iso) {
  const d = parseWall(iso)
  return d ? d.getHours() * 60 + d.getMinutes() : null
}

/**
 * Minutes past midnight IN A GIVEN ZONE, from a naive UTC instant.
 *
 * Uses Intl rather than arithmetic on the offset, because the offset is not a
 * constant: across a DST boundary the same clock time has two different
 * offsets, and anything that subtracts a fixed number of hours renders the
 * whole of one day an hour out twice a year.
 */
export function zoneMinutes(iso, tz) {
  const d = asUtc(iso)
  if (!d) return null
  let h = d.getHours(), m = d.getMinutes()
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz || undefined, hour: 'numeric', minute: 'numeric', hour12: false,
    }).formatToParts(d)
    h = Number(parts.find(p => p.type === 'hour')?.value ?? h)
    m = Number(parts.find(p => p.type === 'minute')?.value ?? m)
  } catch { /* fall through to the browser's own zone */ }
  // Intl renders midnight as hour 24 in some locales.
  if (h === 24) h = 0
  return h * 60 + m
}

/** Which local date a naive UTC instant falls on, in a given zone. */
export function zoneYmd(iso, tz) {
  const d = asUtc(iso)
  if (!d) return null
  try {
    const parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: tz || undefined, year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(d)
    const g = t => parts.find(p => p.type === t)?.value
    return g('year') + '-' + g('month') + '-' + g('day')
  } catch {
    return ymd(d)
  }
}

/**
 * Place a [start, end] span on a vertical grid, clipped to the visible hours.
 *
 * Returns null when the span falls entirely outside the window — the caller
 * renders nothing rather than a zero-height sliver, which is what produced
 * the hairlines along the top of the old grid.
 *
 * `minHeight` exists because a 15-minute meeting is 11px tall at a 46px hour,
 * and 11px cannot hold a label. Growing it to a readable minimum makes the
 * block slightly taller than its true duration; the alternative is a block
 * nobody can read, which is worse on a screen whose only job is to be read.
 */
export function placeSpan(startMin, endMin, opts) {
  const { startHour, endHour, pxPerHour, minHeight = 19 } = opts
  if (startMin == null || endMin == null) return null
  const top0 = (startMin / 60 - startHour) * pxPerHour
  const bot0 = (endMin / 60 - startHour) * pxPerHour
  const full = (endHour - startHour) * pxPerHour
  if (bot0 <= 0 || top0 >= full) return null
  const top = Math.max(0, top0)
  const height = Math.max(minHeight, Math.min(full, bot0) - top)
  return { top, height }
}

/**
 * Lay overlapping blocks side by side within one column.
 *
 * Two meetings at the same time must not hide one another — a rep whose 2pm is
 * double-booked needs to SEE that, and stacking them would render the calendar
 * a liar at exactly the moment it matters most.
 *
 * Simple sweep: a block joins the first lane whose last occupant has ended.
 * Not a perfect interval-graph colouring, and does not need to be — three
 * concurrent meetings is already a problem the screen is reporting rather than
 * a layout to optimise.
 */
export function assignLanes(items) {
  const sorted = [...items].sort((a, b) =>
    (a.startMin - b.startMin) || (b.endMin - a.endMin))
  const laneEnds = []
  sorted.forEach(it => {
    let lane = laneEnds.findIndex(end => end <= it.startMin)
    if (lane === -1) {
      lane = laneEnds.length
      laneEnds.push(it.endMin)
    } else {
      laneEnds[lane] = it.endMin
    }
    it.lane = lane
  })
  const lanes = Math.max(1, laneEnds.length)
  sorted.forEach(it => { it.lanes = lanes })
  return sorted
}

// ── event classification ────────────────────────────────────────────────────

/**
 * Which legend colour an appointment gets.
 *
 * Driven by the SERVER's meeting-type key and internal flag, never by the
 * title. A title is free text; a legend that colours by keyword would repaint
 * itself the first time a rep typed "call" into a demo's name, and a legend
 * that cannot be trusted is worse than no legend.
 */
export function apptKind(a) {
  if (!a) return 'blocked'
  if (a.status === 'cancelled') return 'blocked'
  const key = (a.meeting_type_key || '').toLowerCase()
  if (key.includes('demo')) return 'demo'
  if (key.includes('follow')) return 'followup'
  if (key.includes('call') || a.meeting_provider === 'phone') return 'call'
  // `is_internal` is the server's own flag on the meeting type.
  if (a.meeting_type_internal) return 'internal'
  if (!a.opportunity_id && !a.prospect?.name && !a.prospect?.company) return 'internal'
  return 'customer'
}

/** The legend entries, in the order they read. */
export const CAL_LEGEND = [
  { kind: 'customer', label: 'Customer Meeting' },
  { kind: 'demo',     label: 'Demo / Proposal' },
  { kind: 'call',     label: 'Call' },
  { kind: 'followup', label: 'Follow Up' },
  { kind: 'internal', label: 'Internal Meeting' },
  { kind: 'blocked',  label: 'Blocked Time' },
  { kind: 'pto',      label: 'PTO / Out of Office' },
  { kind: 'external', label: 'External Calendar (Busy)' },
]

export const AV_LEGEND = [
  { kind: 'free',     label: 'Available' },
  { kind: 'customer', label: 'Customer Meeting' },
  { kind: 'internal', label: 'Internal Meeting' },
  { kind: 'blocked',  label: 'Lunch / Blocked' },
  { kind: 'pto',      label: 'PTO / Time Off' },
  { kind: 'external', label: 'External Calendar (Busy)' },
]
