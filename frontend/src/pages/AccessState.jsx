/**
 * What the app shows when a route is refused or does not exist.
 *
 * Both used to be `<Navigate to="/" replace />`. That is the bug this file
 * exists to fix: clicking a nav item and silently arriving back on Overview
 * looks like the click did nothing, or like the app is broken. The user is
 * given no way to tell "you are not allowed here" apart from "that page is
 * gone" apart from "the button is dead" — and support tickets that start
 * "the menu doesn't work" all three look identical.
 *
 * Refusing is still refusing. Nothing here grants access; it only explains.
 * Both states keep the surrounding Layout, so the nav is still there and the
 * screen is never a dead end.
 */

import { Link } from 'react-router-dom'

const WRAP = {
  maxWidth: 560,
  margin: '80px auto',
  padding: '32px',
  textAlign: 'center',
}

const TITLE = {
  fontSize: 20,
  fontWeight: 700,
  margin: '0 0 10px',
  color: 'var(--text-primary)',
}

const BODY = {
  fontSize: 14,
  lineHeight: 1.6,
  color: 'var(--text-secondary)',
  margin: '0 0 8px',
}

const META = {
  fontSize: 12,
  color: 'var(--text-tertiary)',
  margin: '18px 0 22px',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
  wordBreak: 'break-all',
}

const ROLE_LABEL = {
  org_admin: 'an organization admin',
  super_admin: 'a super admin',
  god_admin: 'a platform admin',
}

export function Unauthorized({ required, role, path }) {
  const need = ROLE_LABEL[required] || 'a higher access level'
  return (
    <div style={WRAP}>
      <div style={{ fontSize: 32, marginBottom: 12 }} aria-hidden="true">🔒</div>
      <h1 style={TITLE}>You don't have access to this page</h1>
      <p style={BODY}>
        This screen is limited to {need}. Your account is signed in as{' '}
        <strong>{role || 'an advisor'}</strong>.
      </p>
      <p style={BODY}>
        If you need it, ask an administrator in your organization to grant
        access — nothing is broken, and you don't need to sign in again.
      </p>
      {path && <div style={META}>{path}</div>}
      <Link className="btn btn--ghost" to="/">Back to Overview</Link>
    </div>
  )
}

export function NotFound({ path }) {
  return (
    <div style={WRAP}>
      <div style={{ fontSize: 32, marginBottom: 12 }} aria-hidden="true">🧭</div>
      <h1 style={TITLE}>That page doesn't exist</h1>
      <p style={BODY}>
        The address didn't match any screen in the workspace. It may have been
        renamed, or the link that brought you here may be out of date.
      </p>
      {path && <div style={META}>{path}</div>}
      <Link className="btn btn--ghost" to="/">Back to Overview</Link>
    </div>
  )
}
