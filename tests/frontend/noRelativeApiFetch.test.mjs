// The frontend and the API are different hosts in production, and the
// frontend rewrites every path to index.html. A fetch('/god/ops/...') or
// fetch('/api/...') therefore reads HTML and carries no token. Every API call
// goes through api/client.js (or prefixes API_BASE). This guard keeps it so.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
const root = new URL('../../frontend/src/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const bad = []
function walk(d) {
  for (const n of readdirSync(d)) {
    const p = join(d, n)
    if (statSync(p).isDirectory()) walk(p)
    else if (/\.(jsx?|mjs)$/.test(n)) {
      const s = readFileSync(p, 'utf8')
      const re = /\bfetch\(\s*[`'"]\/(?!\/)/g
      let m
      while ((m = re.exec(s))) bad.push(p.slice(root.length) + ':' + (s.slice(0, m.index).split('\n').length))
    }
  }
}
walk(root)
if (bad.length) { console.error('relative fetch() to an API path:\n  ' + bad.join('\n  ')); process.exit(1) }
console.log('noRelativeApiFetch: 0 relative API fetches')
