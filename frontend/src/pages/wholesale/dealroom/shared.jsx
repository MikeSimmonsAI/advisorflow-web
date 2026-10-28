/* Deal Room (owner side) - pieces shared by the Buyers command center and the
 * Deal Room management screens. Every control calls a real endpoint under
 * /wholesale/deal-room; nothing here is decorative. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Alert, Drawer } from '../ds/ds'
import { errText } from '../wsShared'

export const DRS = '/wholesale/deal-room'
export const usd = (v) => (v === null || v === undefined || v === '' ? '—' : '$' + Math.round(Number(v)).toLocaleString('en-US'))
export const words = (s) => (s || '').toString().replace(/_/g, ' ')
export const cap = (s) => { const w = words(s); return w ? w[0].toUpperCase() + w.slice(1) : '' }
export const when = (iso) => (iso ? new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '—')
export const day = (iso) => (iso ? new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—')

export function useNow(active = true) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!active) return undefined
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active])
  return now
}

export function TimeLeft({ endsAt, short }) {
  const now = useNow(!!endsAt)
  if (!endsAt) return null
  const s = Math.max(0, Math.floor((new Date(endsAt).getTime() - now) / 1000))
  const d = Math.floor(s / 86400); const h = Math.floor((s % 86400) / 3600); const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const txt = s ? `${d ? d + 'd ' : ''}${h}h ${String(m).padStart(2, '0')}m${short ? '' : ` ${String(sec).padStart(2, '0')}s`}` : 'Ending now'
  return <span className="drm-timer" role="timer" aria-label={`Ends ${new Date(endsAt).toLocaleString()}`}>{txt}</span>
}

/** Invite one investor. Practice invitations (.invalid address) are never emailed. */
export function InviteDrawer({ open, onClose, onDone, roomActive, buyer }) {
  const [f, setF] = useState({ name: '', email: '', company: '', is_test: !roomActive })
  const [link, setLink] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (open) {
      setLink(null); setErr(null); setCopied(false)
      setF({ name: buyer ? (buyer.contact_name || buyer.name || '') : '', email: buyer ? (buyer.email || '') : '',
             company: buyer ? (buyer.company_name || '') : '', is_test: !roomActive })
    }
  }, [open, roomActive, buyer])
  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr(null)
    try {
      const r = await api.post(`${DRS}/invites`, { ...f, buyer_id: buyer ? buyer.id : undefined })
      setLink(r); onDone && onDone(`Invitation created for ${r.name}.`)
    } catch (x) { setErr(errText(x)) } finally { setBusy(false) }
  }
  const copy = async () => {
    try { await navigator.clipboard.writeText(link.link); setCopied(true) } catch { setCopied(false) }
  }
  return (
    <Drawer open={open} onClose={onClose} title="Invite investor"
            sub="They get a private, single-use link to create their Deal Room login.">
      {link ? (
        <div className="drm-linkbox" role="status">
          <p className="drm-linkbox__t"><b>{link.name}</b> · {link.delivery && link.delivery.startsWith('not_sent')
            ? 'Not emailed - ' + link.delivery.replace(/^not_sent:\s*/, '') : 'Emailed to ' + link.email}</p>
          <label className="drm-field"><span>Secure invitation link</span>
            <input readOnly value={link.link} onFocus={(e) => e.target.select()} /></label>
          <div className="drm-row">
            <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" onClick={copy}>{copied ? 'Copied' : 'Copy link'}</button>
            <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setLink(null)}>Invite another</button>
          </div>
          <p className="evo-small evo-muted">Shown once. It works one time and expires {when(link.expires_at)}.</p>
        </div>
      ) : (
        <form onSubmit={submit} className="drm-form">
          <label className="drm-field"><span>Investor name</span>
            <input required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} autoComplete="off" /></label>
          <label className="drm-field"><span>Email</span>
            <input required type="email" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} autoComplete="off" /></label>
          <label className="drm-field"><span>Company (optional)</span>
            <input value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></label>
          <label className="drm-check"><input type="checkbox" checked={f.is_test}
                 onChange={(e) => setF({ ...f, is_test: e.target.checked })} />
            Practice invitation (use an address ending in .invalid - never emailed)</label>
          {!roomActive && !f.is_test ? (
            <Alert>Your Deal Room is not active yet, so real invitations are refused. Finish setup and activate it,
              or send a practice invitation to try the investor experience.</Alert>
          ) : null}
          <Alert>{err}</Alert>
          <button className="evo-btn evo-btn--primary" disabled={busy}>{busy ? 'Creating…' : 'Create invitation'}</button>
        </form>
      )}
    </Drawer>
  )
}

/** The Deal Room status the Buyers page and the Deal Room page both read. */
export function useRoomStatus(bump) {
  const [s, setS] = useState(null)
  useEffect(() => {
    let alive = true
    api.get(`${DRS}/overview?include_test=true`).then((x) => { if (alive) setS(x) }).catch(() => { if (alive) setS({ status: 'unknown' }) })
    return () => { alive = false }
  }, [bump])
  return s
}
