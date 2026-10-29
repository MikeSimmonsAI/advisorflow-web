/* THE BRAND THIS BACK OFFICE SELLS (#272), for the few sentences on these
 * screens that name it ("changed outside EvoSys Pro").
 *
 * SalesShell records it from /sales/me (`brand_display_name`,
 * `brand_short_name`) and every screen reads it through useSalesBrand(). Until
 * the server has answered - and for EvoSys Pro always - it is exactly the text
 * these screens carried before: "EvoSys Pro" / "EvoSys". Only the words change;
 * nothing about the layout does. */
import { useEffect, useState } from 'react'

const KEY = 'af_sales_brand'
const DEFAULT = { name: 'EvoSys Pro', short: 'EvoSys' }
let current = null
const listeners = new Set()

export function brandFromMe(me) {
  return { name: (me && me.brand_display_name) || DEFAULT.name,
           short: (me && me.brand_short_name) || DEFAULT.short }
}

export function setSalesBrand(me) {
  current = brandFromMe(me)
  try { sessionStorage.setItem(KEY, JSON.stringify(current)) } catch { /* storage blocked */ }
  listeners.forEach((f) => f(current))
}

export function salesBrand() {
  if (current) return current
  try {
    const raw = sessionStorage.getItem(KEY)
    if (raw) return { ...DEFAULT, ...JSON.parse(raw) }
  } catch { /* storage blocked */ }
  return DEFAULT
}

export function useSalesBrand() {
  const [b, setB] = useState(salesBrand)
  useEffect(() => { listeners.add(setB); return () => { listeners.delete(setB) } }, [])
  return b
}
