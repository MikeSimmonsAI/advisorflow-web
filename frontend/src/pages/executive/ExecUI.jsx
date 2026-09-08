/**
 * ExecUI - the pieces every Executive screen is built from.
 *
 * WHY THESE ARE SHARED RATHER THAN REPEATED
 * =========================================
 * The Command Center, Portfolio and the organization drill-down all render the
 * same three things: a headline figure, a health verdict, and an exception.
 * When those were written three times they drifted three ways - one screen
 * rendered a missing figure as "-", another as 0, a third as blank. An
 * executive comparing two screens then had to work out whether the difference
 * was in the business or in the code.
 *
 * THE MOST IMPORTANT RULE IN THIS FILE IS `Figure`. A value the server could
 * not compute arrives as null, and null renders as WORDS, never as a number.
 * "$0 MRR" and "we cannot price this customer" are opposite statements about a
 * business, and a screen that renders them identically is lying about one of
 * them. Everything here refuses to make that mistake on a page's behalf.
 */

/* -- formatting ----------------------------------------------------------- */

export function num(n) {
  if (n === null || n === undefined) return null
  return Number(n).toLocaleString()
}

export function money(n) {
  if (n === null || n === undefined) return null
  return '$' + Number(n).toLocaleString(undefined,
    { minimumFractionDigits: 0, maximumFractionDigits: 0 })
}

export function pct(n) {
  if (n === null || n === undefined) return null
  return Number(n).toFixed(Number.isInteger(n) ? 0 : 1) + '%'
}

export function day(v) {
  if (!v) return null
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? null
    : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/** "3 days ago" / "today". Reads like a person talking about a business. */
export function since(v) {
  if (!v) return null
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return null
  const days = Math.floor((Date.now() - d.getTime()) / 86400000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return days + ' days ago'
  if (days < 365) return Math.round(days / 30) + ' months ago'
  return Math.round(days / 365) + ' years ago'
}

export function plural(n, one, many) { return n === 1 ? one : many }

/* -- the honest-value primitive ------------------------------------------- */

/**
 * A figure, or the reason there isn't one.
 *
 * `value` is the already-formatted string, or null/undefined when the server
 * could not produce it. `absent` is what to say instead - and it is always
 * words, at a smaller size, in the muted ink, so it can never be mistaken for
 * a quantity.
 */
export function Figure({ value, absent = 'Not tracked' }) {
  if (value === null || value === undefined || value === '') {
    return <span className="ex-v ex-none">{absent}</span>
  }
  return <span className="ex-v">{value}</span>
}

/** Inline version, for table cells and metadata rows. */
export function Val({ value, absent = 'not tracked' }) {
  if (value === null || value === undefined || value === '') {
    return <span className="ex-none-inline">{absent}</span>
  }
  return <>{value}</>
}

/* -- headline tile -------------------------------------------------------- */

export function Kpi({ label, value, absent, sub, lead, alarm, onClick, title }) {
  const cls = ['ex-kpi']
  if (lead) cls.push('ex-lead')
  if (alarm) cls.push('ex-alarm')
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag className={cls.join(' ')} onClick={onClick}
         type={onClick ? 'button' : undefined} title={title}>
      <span className="ex-k">{label}</span>
      <Figure value={value} absent={absent || 'Not tracked'} />
      {sub ? <span className="ex-s">{sub}</span> : null}
    </Tag>
  )
}

/* -- the health verdict --------------------------------------------------- */

export function Health({ health, label }) {
  return <span className={'ex-pill h-' + (health || 'inactive')}>{label || health}</span>
}

/* -- comparison against the rest of the portfolio -------------------------
   Not a league table. An executive wants to know whether this organization is
   ahead of or behind the others on the two figures that matter, and by how
   much. When there is nothing to compare against, nothing is shown - a
   comparison of one is not a comparison. */

export function Delta({ value, median, format = pct, unit = '' }) {
  if (value === null || value === undefined ||
      median === null || median === undefined) return null
  const diff = value - median
  const dir = Math.abs(diff) < 0.05 ? 'flat' : diff > 0 ? 'up' : 'down'
  const arrow = dir === 'flat' ? '=' : dir === 'up' ? '▲' : '▼'
  const shown = format === pct
    ? Math.abs(diff).toFixed(Number.isInteger(diff) ? 0 : 1) + '%'
    : Math.abs(Math.round(diff)).toLocaleString() + unit
  return (
    <span className={'ex-delta ' + dir}>
      {arrow} {dir === 'flat' ? 'in line with' : shown + (dir === 'up' ? ' above' : ' below')}
      {dir === 'flat' ? '' : ''} portfolio median
    </span>
  )
}
