// CSV exports: quoted, and formulas neutralised (OWASP CSV injection).
import assert from 'node:assert/strict'
import { csvCell, csvRow } from '../../frontend/src/utils/csvCell.js'
let n = 0
const eq = (a, b) => { assert.equal(a, b); n++ }
eq(csvCell('Acme, Inc.'), '"Acme, Inc."')
eq(csvCell('say "hi"'), '"say ""hi"""')
eq(csvCell('=HYPERLINK("http://x","click")'), '"\'=HYPERLINK(""http://x"",""click"")"')
eq(csvCell('@SUM(A1)'), '"\'@SUM(A1)"')
eq(csvCell('+cmd|calc'), '"\'+cmd|calc"')
eq(csvCell('-2+3+cmd'), '"\'-2+3+cmd"')
eq(csvCell('\t=1'), '"\'\t=1"')
eq(csvCell('+1 (214) 555-0100'), '"+1 (214) 555-0100"')
eq(csvCell('-12.5'), '"-12.5"')
eq(csvCell(null), '""')
eq(csvCell(0), '"0"')
eq(csvRow(['a', '=b', 3]), '"a","\'=b","3"')
console.log(`csvCell: ${n} passed`)
