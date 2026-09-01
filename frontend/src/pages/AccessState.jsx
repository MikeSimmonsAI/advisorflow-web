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

/**
 * VerificationUnavailable — "we could not ask", which is not "you may not".
 *
 * The third state the workspace guard was missing. A 500, a dropped
 * connection, a timeout or a 404 from a backend older than this bundle all
 * used to land on Unauthorized above, which told a properly authorized advisor
 * that he lacked access to his own workspace. That is a lie the app told with
 * complete confidence, and the person who reads it has no way to know it is
 * one.
 *
 * This screen refuses to enter the workspace - it is not fail-open, and the
 * server has still granted nothing - while refusing to accuse the reader of
 * anything. It also gives them the only useful action: try again.
 */
export function VerificationUnavailable({ status, message, path, onRetry }) {
  return (
    <div style={WRAP}>
      <div style={{ fontSize: 32, marginBottom: 12 }} aria-hidden="true">⚠️</div>
      <h1 style={TITLE}>We couldn't check your access just now</h1>
      <p style={BODY}>
        The server didn't answer the access check{status ? ` (HTTP ${status})` : ''},
        so this workspace hasn't been opened yet. <strong>This is not a refusal.</strong>{' '}
        Nothing about your account has changed and nothing here says you lack access.
      </p>
      <p style={BODY}>
        Try again in a moment. If it keeps happening, tell your administrator
        {status ? ` that the access check returned ${status}.` : ' that the access check could not be reached.'}
      </p>
      {message && <div style={META}>{message}</div>}
      {path && <div style={META}>{path}</div>}
      <button className="btn btn--ghost" type="button" onClick={onRetry}>
        Try again
      </button>
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
