/* Upload, gallery and confirm-delete — written once, used by every tab.
 *
 * "Do not create separate upload implementations for every tab." So there is
 * one here. The property photo grid, the document drawer and a buyer's proof
 * of funds all mount these.
 *
 * TWO RULES THIS FILE EXISTS TO KEEP:
 *
 * 1. An upload control is NEVER shown when the deployment cannot store the
 *    file. `capability` comes from the server and says so in words that name
 *    the variable to set. A button that looks live and 503s is how somebody
 *    concludes the product is broken rather than unconfigured.
 *
 * 2. Delete always asks first, and says what it is deleting. "Remove" is not
 *    used anywhere in this module any more, because it cannot be told apart
 *    from "exclude", "unlink" or "archive".
 */
import { useEffect, useRef, useState } from 'react'
import { api, API_BASE, fetchObjectUrl } from '../../api/client'
import { errText } from './wsShared'

/* An <img src> cannot send an Authorization header — the browser issues a
 * plain, unauthenticated GET. A private purchase contract or a photo of
 * somebody's house is not a public object, so the bytes are fetched with the
 * session like every other request and rendered from an object URL.
 *
 * The alternative is making the files public, which is the thing the security
 * section of the brief specifically forbids. */
export function AuthImage({ path, alt, className }) {
  // A data:, blob: or absolute URL is already loadable and is not one of our
  // authenticated endpoints. Passing it through keeps this component usable
  // anywhere a src might come from something other than the file store.
  const direct = !path || /^(data:|blob:|https?:)/.test(path)
  const [src, setSrc] = useState(direct ? path : null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (direct) { setSrc(path); setFailed(false); return undefined }
    let url = null
    let cancelled = false
    setSrc(null); setFailed(false)
    fetchObjectUrl(path)
      .then((objectUrl) => {
        url = objectUrl
        if (cancelled) { URL.revokeObjectURL(objectUrl); return }
        setSrc(objectUrl)
      })
      .catch(() => { if (!cancelled) setFailed(true) })
    // Revoked on unmount, or the tab leaks one blob per photo per visit.
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url) }
  }, [path, direct])

  if (failed) return <span className="ws-img-failed">Could not load</span>
  if (!src) return <span className="ws-img-loading" />
  return <img src={src} alt={alt || ''} className={className} />
}


/* For a link the browser navigates to (download / open in a tab) the same
 * problem does not arise the same way, but the token still must not go in the
 * URL — so these open through the app, which fetches and hands over a blob. */
export async function openFile(path, options) {
  const url = await fetchObjectUrl(path)
  const download = options && options.download
  if (download) {
    // Save, rather than open. The name comes from the record, never from the
    // object key — which is a uuid and would land in Downloads as one.
    const a = document.createElement('a')
    a.href = url
    a.download = download
    document.body.appendChild(a)
    a.click()
    a.remove()
  } else {
    window.open(url, '_blank', 'noopener')
  }
  // Given to the new tab or the download; revoking immediately would blank it.
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}


export function StorageNotice({ capability }) {
  if (!capability || capability.uploads_enabled) return null
  return (
    <div className="ws-warn">
      <strong>File storage is not configured.</strong>{' '}
      {capability.reason}
    </div>
  )
}


/* A drop zone and a file picker over the same handler. Drag-and-drop without a
 * picker excludes anybody using a keyboard; a picker without drag-and-drop is
 * the thing people complain about. Both, one code path. */
const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.heic'])

export function UploadZone({ capability, accept, label, busy, onFiles,
                            multiple = false }) {
  const input = useRef(null)
  const [over, setOver] = useState(false)

  if (!capability || !capability.uploads_enabled) {
    return <StorageNotice capability={capability} />
  }

  // The server's list is what the DEPLOYMENT can store; this zone may accept
  // less than that. A photo drop zone offering .pdf and .docx is the control
  // telling a person something the endpoint will then refuse.
  const imagesOnly = String(accept || '').startsWith('image/')
  const extensions = (capability.allowed_extensions || [])
    .filter((e) => !imagesOnly || IMAGE_EXTENSIONS.has(String(e).toLowerCase()))

  const take = (list) => {
    const files = Array.from(list || [])
    if (files.length) onFiles(multiple ? files : [files[0]])
  }

  return (
    <div className={`ws-drop ${over ? 'is-over' : ''} ${busy ? 'is-busy' : ''}`}
         onDragOver={(e) => { e.preventDefault(); setOver(true) }}
         onDragLeave={() => setOver(false)}
         onDrop={(e) => {
           e.preventDefault(); setOver(false)
           if (!busy) take(e.dataTransfer.files)
         }}>
      {/* A20. The picker is hidden and driven by the button beside it, so it
          is never seen — but it is still in the accessibility tree, and it was
          announced as an unlabelled file field. `label` is the button's own
          text, which is exactly what this control does. */}
      <input ref={input} type="file" accept={accept} multiple={multiple}
             aria-label={label} style={{ display: 'none' }}
             onChange={(e) => { take(e.target.files); e.target.value = '' }} />
      <button type="button" className="btn btn--secondary btn--sm" disabled={busy}
              onClick={() => input.current && input.current.click()}>
        {busy ? 'Uploading…' : label}
      </button>
      <span className="ws-drop__hint">
        or drop {multiple ? 'files' : 'a file'} here ·{' '}
        {extensions.join(' ')} ·{' '}
        up to {Math.round((capability.max_bytes || 0) / 1048576)} MB
        {/* Where the bytes actually go. Said in one clause, secondary to the
            control, and never claimed to be more than it is. */}
        {!capability.durable
          ? ' · uploads are not configured'
          : capability.replicated
            ? ''
            : ` · stored on ${capability.where || 'this server'}, not replicated`}
      </span>
    </div>
  )
}


/* Delete, with the name of the thing in the question.
 *
 * "Are you sure?" is not a confirmation, it is a reflex test. This says what
 * will be deleted and that it cannot be undone, and the confirming button
 * carries the verb rather than the word "OK". */
export function ConfirmDelete({ what, busy, onCancel, onConfirm }) {
  return (
    <div className="ws-confirm">
      <span className="ws-confirm__text">
        Delete <strong>{what}</strong>? This cannot be undone.
      </span>
      <span className="ws-actions">
        <button className="btn btn--secondary btn--sm" disabled={busy}
                onClick={onCancel}>Cancel</button>
        <button className="btn btn--danger btn--sm" disabled={busy}
                onClick={onConfirm}>{busy ? 'Deleting…' : 'Delete'}</button>
      </span>
    </div>
  )
}


/* The property photo gallery: upload, cover, caption, delete. */
/* THE PROPERTY GALLERY.
 *
 * A wholesale property is not one picture. It is the front, the back, the
 * kitchen, the roof, the foundation crack and the thing in the garage — and an
 * investor is sent a SET, in an order somebody chose, of the ones somebody
 * decided to share. Four things this grid has to carry that a plain uploader
 * does not:
 *
 *   CATEGORY      so "the third one is the roof" is data rather than a caption
 *   ORDER         so the front elevation is not the last thing they scroll to
 *   COVER         which is the one that leads everywhere else
 *   BUYER-VISIBLE which is a PUBLICATION decision, off by default, per photo
 *
 * That last one is the important one. A photo of the inside of somebody's
 * house does not become investor-facing marketing because it was uploaded. The
 * badge on the tile says which ones are shared, and the server reads the same
 * column when it builds the investor room.
 *
 * Reordering is two buttons rather than drag-and-drop on purpose: dragging is
 * lovely with a mouse and unusable with a keyboard or a thumb, and the order
 * here is a real piece of data rather than a flourish.
 */
export const PHOTO_CATEGORIES = [
  ['exterior_front', 'Front'], ['exterior_rear', 'Rear'],
  ['exterior_side', 'Side'], ['kitchen', 'Kitchen'],
  ['living_room', 'Living room'], ['bedroom', 'Bedroom'],
  ['bathroom', 'Bathroom'], ['garage', 'Garage'], ['roof', 'Roof'],
  ['hvac', 'HVAC'], ['electrical', 'Electrical'], ['plumbing', 'Plumbing'],
  ['foundation', 'Foundation'], ['damage', 'Damage'],
  ['repair_area', 'Repair area'], ['yard', 'Yard'],
  ['neighborhood', 'Neighborhood'], ['other', 'Other'],
]

const CATEGORY_LABEL = Object.fromEntries(PHOTO_CATEGORIES)


export function PhotoGrid({ photos, capability, onUpload, onPatch, onDelete,
                           onReorder, busy }) {
  const [confirming, setConfirming] = useState(null)
  const [editing, setEditing] = useState(null)
  const [draft, setDraft] = useState('')

  const shared = photos.filter((p) => p.buyer_visible).length

  function move(index, delta) {
    const next = photos.slice()
    const target = index + delta
    if (target < 0 || target >= next.length) return
    const [row] = next.splice(index, 1)
    next.splice(target, 0, row)
    onReorder(next.map((p) => p.id))
  }

  return (
    <>
      <UploadZone capability={capability} accept="image/*" multiple
                  label="Upload photos" busy={busy} onFiles={onUpload} />

      {!photos.length ? (
        <p className="ws-panel-note">
          No photos yet. The first one you upload becomes the cover.
        </p>
      ) : (
        <>
          <div className="ws-gallery-bar">
            <span>
              {photos.length} photo{photos.length === 1 ? '' : 's'}
            </span>
            <span className={shared ? 'ws-shared-count' : 'ws-muted'}>
              {shared
                ? `${shared} shared with investors`
                : 'None shared with investors yet'}
            </span>
          </div>
          <div className="ws-photos">
            {photos.map((p, index) => (
              <figure className={`ws-photo ${p.is_primary ? 'is-cover' : ''} ${p.buyer_visible ? 'is-shared' : ''}`}
                      key={p.id}>
                <button type="button" className="ws-photo__open"
                        onClick={() => openFile(p.url)}
                        title="Open full size">
                  <AuthImage path={p.url}
                             alt={p.caption || p.original_filename || 'Property photo'} />
                </button>
                <span className="ws-photo__badges">
                  {p.is_primary ? <span className="ws-photo__badge">Cover</span> : null}
                  {p.buyer_visible
                    ? <span className="ws-photo__badge is-shared">Investors</span>
                    : null}
                  {p.seller_visible
                    ? <span className="ws-photo__badge is-shared">Owner</span>
                    : null}
                </span>
                <figcaption>
                  {editing === p.id ? (
                    <span className="ws-actions">
                      <input className="ws-input ws-input--inline" value={draft}
                             placeholder="Caption"
                             onChange={(e) => setDraft(e.target.value)} />
                      <button className="btn btn--primary btn--sm" disabled={busy}
                              onClick={async () => {
                                await onPatch(p, { caption: draft })
                                setEditing(null)
                              }}>Save</button>
                      <button className="btn btn--secondary btn--sm"
                              onClick={() => setEditing(null)}>Cancel</button>
                    </span>
                  ) : (
                    <>
                      <span className="ws-photo__caption">
                        {p.caption || <span className="ws-muted">No caption</span>}
                      </span>

                      <label className="ws-photo__cat">
                        <span className="ws-vis-hidden">
                          What this photo is of
                        </span>
                        <select className="ws-input ws-input--inline"
                                value={p.category || ''} disabled={busy}
                                onChange={(e) => onPatch(p, { category: e.target.value })}>
                          <option value="">Not categorised</option>
                          {PHOTO_CATEGORIES.map(([key, text]) => (
                            <option key={key} value={key}>{text}</option>
                          ))}
                        </select>
                      </label>

                      {/* The publication decisions, per photo, per audience,
                          both off by default. TWO checkboxes rather than one
                          "publish" switch because they are two different
                          judgements: a damage close-up usually belongs in an
                          investor gallery and almost never on the page the
                          person who lives there is reading. */}
                      <label className="ws-checkbox ws-photo__share">
                        <input type="checkbox" disabled={busy}
                               checked={!!p.buyer_visible}
                               onChange={(e) => onPatch(
                                 p, { buyer_visible: e.target.checked })} />
                        Show to investors
                      </label>
                      <label className="ws-checkbox ws-photo__share">
                        <input type="checkbox" disabled={busy}
                               checked={!!p.seller_visible}
                               onChange={(e) => onPatch(
                                 p, { seller_visible: e.target.checked })} />
                        Show to the owner
                      </label>

                      <span className="ws-actions">
                        <button className="btn btn--secondary btn--sm"
                                disabled={busy || index === 0}
                                title="Move earlier"
                                onClick={() => move(index, -1)}>&larr;</button>
                        <button className="btn btn--secondary btn--sm"
                                disabled={busy || index === photos.length - 1}
                                title="Move later"
                                onClick={() => move(index, 1)}>&rarr;</button>
                        {!p.is_primary ? (
                          <button className="btn btn--secondary btn--sm" disabled={busy}
                                  onClick={() => onPatch(p, { is_primary: true })}>
                            Make cover
                          </button>
                        ) : null}
                        <button className="btn btn--secondary btn--sm"
                                onClick={() => { setEditing(p.id); setDraft(p.caption || '') }}>
                          Caption
                        </button>
                        <button className="btn btn--danger btn--sm"
                                onClick={() => setConfirming(p)}>Delete</button>
                      </span>
                    </>
                  )}
                </figcaption>
              </figure>
            ))}
          </div>
        </>
      )}

      {confirming ? (
        <ConfirmDelete busy={busy}
                       what={confirming.caption || confirming.original_filename || 'this photo'}
                       onCancel={() => setConfirming(null)}
                       onConfirm={async () => {
                         await onDelete(confirming); setConfirming(null)
                       }} />
      ) : null}
    </>
  )
}
