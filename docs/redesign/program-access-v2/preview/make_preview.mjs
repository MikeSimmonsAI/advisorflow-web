// Turn a normal frontend build into a TEST-DATA design preview of Program Access V2.
//
//   cd frontend && VITE_API_BASE_URL=http://api.test npm run build
//   node ../docs/redesign/program-access-v2/preview/make_preview.mjs dist
//
// It writes dist/pa2-preview.js and loads it first from dist/index.html. That
// script answers every request to the fake API host (http://api.test) from
// ../qa/fixtures.json, signs in a fake test user, opens /workspaces and shows a
// "Design preview - test data" ribbon. Nothing reaches a real server, account
// or customer record. Production builds never run this script.
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const dist = path.resolve(process.argv[2] || 'dist')
const fixtures = JSON.parse(fs.readFileSync(path.join(here, '../qa/fixtures.json'), 'utf8'))
delete fixtures._note

const script = `/* Program Access V2 design preview - TEST DATA ONLY (no real API). */
(function () {
  var FIX = ${JSON.stringify(fixtures)};
  var who = (new URLSearchParams(location.search).get('as')) || sessionStorage.getItem('pa2.as') || 'owner';
  if (!FIX[who]) who = 'owner';
  sessionStorage.setItem('pa2.as', who);
  var f = FIX[who];
  var pins = (f.pins || []).slice();
  function b64(o) { return btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\\+/g, '-').replace(/\\//g, '_'); }
  var token = b64({ alg: 'HS256', typ: 'JWT' }) + '.' + b64({ sub: f.user.id, exp: Math.floor(Date.now() / 1000) + 86400 }) + '.preview';
  try {
    if (localStorage.getItem('pa2.who') !== who) { localStorage.clear(); localStorage.setItem('pa2.who', who); }
    localStorage.setItem('af_token', token);
    localStorage.setItem('af_user', JSON.stringify(f.user));
  } catch (e) {}
  if (location.pathname === '/' || location.pathname === '/index.html' || location.pathname === '/login') {
    history.replaceState(null, '', '/workspaces');
  }
  var realFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    if (url.indexOf('http://api.test') !== 0) return realFetch(input, init);
    var p = url.slice('http://api.test'.length).split('?')[0];
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    var body = {};
    if (p === '/auth/my-contexts') body = f.contexts;
    else if (p === '/launcher/pins') {
      if (method === 'PUT') { try { pins = JSON.parse(init.body).keys || []; } catch (e) {} }
      body = { keys: pins };
    }
    else if (p === '/launcher/attention') body = { items: f.attention || [] };
    else if (p === '/health/ready') body = { status: 'ok' };
    else if (p === '/settings/profile') body = f.user;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } }));
  };
  document.addEventListener('DOMContentLoaded', function () {
    var r = document.createElement('div');
    r.setAttribute('role', 'note');
    r.style.cssText = 'position:fixed;left:50%;bottom:10px;transform:translateX(-50%);z-index:99999;background:#111827;color:#fff;font:600 12px/1.2 Inter,system-ui,sans-serif;padding:8px 12px;border-radius:999px;box-shadow:0 6px 18px rgba(0,0,0,.35);opacity:.92';
    r.innerHTML = 'Design preview \\u00b7 test data only \\u00b7 view as: '
      + ['owner', 'salesperson', 'operator'].map(function (k) {
          return k === who ? '<b style="color:#93c5fd">' + k + '</b>'
            : '<a style="color:#fff" href="/?as=' + k + '">' + k + '</a>';
        }).join(' | ');
    document.body.appendChild(r);
  });
})();
`
fs.writeFileSync(path.join(dist, 'pa2-preview.js'), script)
const indexPath = path.join(dist, 'index.html')
let html = fs.readFileSync(indexPath, 'utf8')
if (!html.includes('pa2-preview.js')) {
  html = html.replace(/<head>/i, '<head>\n    <script src="/pa2-preview.js"></script>\n    <meta name="robots" content="noindex">')
  fs.writeFileSync(indexPath, html)
}
console.log('Program Access V2 preview prepared in', dist)
