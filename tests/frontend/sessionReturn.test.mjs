// Session expiry: say why, and return the person to where they were.
import assert from 'node:assert/strict'
import { sessionExpiredUrl, safeNextPath } from '../../frontend/src/utils/sessionReturn.js'

let n = 0
const eq = (a, b) => { assert.deepEqual(a, b); n++ }

eq(sessionExpiredUrl({ pathname: '/leads/abc', search: '?tab=sms' }), '/login?expired=1&next=%2Fleads%2Fabc%3Ftab%3Dsms')
eq(sessionExpiredUrl({ pathname: '/m/conversations/x', search: '' }), '/m/login?expired=1&next=%2Fm%2Fconversations%2Fx')
eq(sessionExpiredUrl({ pathname: '/m', search: '' }), '/m/login?expired=1&next=%2Fm')
eq(sessionExpiredUrl({ pathname: '/login', search: '?expired=1' }), '/login')
eq(sessionExpiredUrl({ pathname: '/m/login', search: '' }), '/m/login')

eq(safeNextPath('/leads/abc?tab=sms'), '/leads/abc?tab=sms')
eq(safeNextPath('/m/prospects/1'), '/m/prospects/1')
eq(safeNextPath('https://evil.example/'), null)
eq(safeNextPath('//evil.example/'), null)
eq(safeNextPath('/\\evil.example'), null)
eq(safeNextPath('javascript:alert(1)'), null)
eq(safeNextPath('/login?next=/x'), null)
eq(safeNextPath('/m/login'), null)
eq(safeNextPath('/loginhelp'), '/loginhelp')
eq(safeNextPath(null), null)
eq(safeNextPath(''), null)

console.log(`sessionReturn: ${n} passed`)
