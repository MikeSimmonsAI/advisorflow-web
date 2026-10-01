// A PROJECT HELPER USED WITHOUT BEING IMPORTED.
//
// Vite builds a file that calls an undefined name without complaint; the page
// then crashes the moment that code runs. On 2026-10-01 three pages called the
// shared asUtc() helper with no import (Leads workspace, Pipeline conversation
// tabs, Cleaning overview) and the Leads page crashed in production for hours.
//
// This walks every module under frontend/src with Babel's scope analysis and
// fails when an identifier is referenced, has no binding in its file (no
// import, no declaration), and IS exported by some module in frontend/src -
// i.e. exactly "forgot to import our own helper". Browser globals and
// third-party names are not project exports, so they never trip it.
//
// Babel ships with the frontend toolchain (@vitejs/plugin-react). If it is not
// installed here the check is skipped, loudly.
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const src = join(root, 'frontend', 'src')
const candidates = [join(root, 'frontend', 'package.json'), join(root, 'package.json')]
let parser, traverse
for (const pkg of candidates) {
  if (!existsSync(pkg)) continue
  try {
    const req = createRequire(pkg)
    parser = req('@babel/parser')
    const t = req('@babel/traverse')
    traverse = t.default || t
    break
  } catch { /* try the next location */ }
}
if (!parser || !traverse) {
  console.log('missingImports: SKIPPED - @babel/parser/@babel/traverse not installed (run npm ci in frontend/)')
  process.exit(0)
}

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(jsx?|mjs)$/.test(name) && !/\.test\./.test(name)) out.push(p)
  }
  return out
}

const parse = (code) => parser.parse(code, { sourceType: 'module', plugins: ['jsx'], errorRecovery: true })
const files = walk(src)
const asts = new Map()
const exported = new Set()
for (const f of files) {
  let ast
  try { ast = parse(readFileSync(f, 'utf8')) } catch { continue }
  asts.set(f, ast)
  for (const node of ast.program.body) {
    if (node.type !== 'ExportNamedDeclaration') continue
    const d = node.declaration
    if (d && (d.type === 'FunctionDeclaration' || d.type === 'ClassDeclaration') && d.id) exported.add(d.id.name)
    if (d && d.type === 'VariableDeclaration') for (const v of d.declarations) if (v.id.type === 'Identifier') exported.add(v.id.name)
    for (const s of node.specifiers || []) if (s.exported && s.exported.name) exported.add(s.exported.name)
  }
}

const problems = []
for (const [f, ast] of asts) {
  const seen = new Set()
  traverse(ast, {
    ReferencedIdentifier(path) {
      const name = path.node.name
      if (!exported.has(name) || seen.has(name)) return
      if (path.parentPath.isJSXMemberExpression() || path.isJSXIdentifier() && /^[a-z]/.test(name)) return
      if (path.scope.hasBinding(name, true)) return
      seen.add(name)
      problems.push(`${relative(root, f)}:${path.node.loc?.start.line} uses ${name}() but never imports or declares it`)
    },
  })
}

if (problems.length) {
  console.error('FAILED - project helpers used without an import:')
  problems.forEach(p => console.error(' - ' + p))
  process.exit(1)
}
console.log(`missingImports: ${asts.size} modules checked, ${exported.size} project exports, no missing imports`)
