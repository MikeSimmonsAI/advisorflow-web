/** Pure percentage display helpers: never turn an absent denominator into 0%. */
export const percentage = (numerator, denominator) => {
  if (numerator == null || denominator == null || Number(denominator) <= 0) return null
  const n = Number(numerator), d = Number(denominator)
  return Number.isFinite(n) && Number.isFinite(d) && n >= 0 ? Math.round(n / d * 1000) / 10 : null
}
export const percentText = value => value == null || !Number.isFinite(Number(value))
  ? '—' : `${Number(value).toFixed(1).replace(/\\.0$/, '')}%`
export const barWidth = value => value == null || !Number.isFinite(Number(value))
  ? '0%' : `${Math.min(100, Math.max(0, Number(value)))}%`
export const eventMix = (replies, optOuts) => {
  if (replies == null || optOuts == null) return null
  const genuine = Number(replies), stopped = Number(optOuts)
  if (!Number.isInteger(genuine) || !Number.isInteger(stopped) || genuine < 0 || stopped < 0) return null
  const total = genuine + stopped
  return { replies: genuine, optOuts: stopped, total,
           replyPct: percentage(genuine, total), optOutPct: percentage(stopped, total) }
}
