/* The transaction-document workspace.
 *
 * WHAT PHASE 4 FIXED HERE. The screen could not tell a document that HAS a
 * stored file from one that is only a remembered filename. Both drew the same
 * way, because the deal room payload never carried `file_id` at all — so a
 * contract that really was in the system displayed as "(reference)", and the
 * View button never appeared for it. An operator could not answer "do we
 * actually have the signed contract" without opening a folder on their own
 * machine. That is the single question this drawer exists to answer.
 *
 * Now every row states which it is, and the actions follow from that:
 *
 *   stored file      View · Download · Replace · Delete
 *   name only        Attach · Delete          (and says it holds no file)
 *   nothing at all   Attach · Delete
 *
 * E-SIGN IS NOT PRETENDED. There is no e-sign provider connected. The signature
 * state is a fact somebody records, the control says so, and the place a
 * provider would report from is named and visibly empty rather than mocked up
 * as though it were working.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { errText, fmtDate, fmtLabel, fmtWhen, Note, Why } from './wsShared'
import { UploadZone, ConfirmDelete, openFile } from './wsFiles'
import { ContractDesk } from './wsContracts'

/* Filing categories, not legal templates. This module ships no state-specific
 * form and generates none — these are labels on a drawer. */
export const DOC_TYPES = [
  'purchase_contract', 'assignment_agreement', 'seller_disclosure', 'addendum',
  'inspection', 'repair_estimate', 'proof_of_funds', 'title_document',
  'closing_statement', 'buyer_doc', 'property_photo', 'other',
]

/* The order a deal actually needs them in, so the drawer reads like a
 * transaction rather than like whatever was uploaded last. */
const ORDER = DOC_TYPES.reduce((acc, t, i) => ({ ...acc, [t]: i }), {})

/* The server sends `status_label` on every row; this map is only for the
 * "move to" options, which are bare keys. Both come from the same table in
 * app/services/wholesale_esign.py. */
const STATUS_LABEL = {
  draft: 'Draft',
  ready_for_review: 'Ready for review',
  approved: 'Approved',
  sent: 'Sent',
  viewed: 'Opened by the other party',
  signed: 'Signed',
  declined: 'Declined',
  voided: 'Voided',
  superseded: 'Superseded',
}

const SIGNATURE_STATES = [
  ['none', 'Not sent'],
  ['out_for_signature', 'Out for signature'],
  ['partially_signed', 'Partially signed'],
  ['signed', 'Signed'],
  ['declined', 'Declined'],
]
const SIGNATURE_LABEL = Object.fromEntries(SIGNATURE_STATES)

function sigTone(state) {
  if (state === 'signed') return 'is-ok'
  if (state === 'declined') return 'is-dnc'
  if (['out_for_signature', 'partially_signed'].includes(state)) return 'is-warn'
  return 'is-muted'
}

/* Phase 5. The tone follows the LIFECYCLE key the server sends, not the raw
 * stored value — `status` still carries the pre-lifecycle vocabulary on older
 * rows, and a screen that pattern-matched on it drew "needed" and "draft" as
 * two different things when they are one. */
function statusTone(key) {
  if (key === 'signed') return 'is-ok'
  if (key === 'declined' || key === 'voided') return 'is-dnc'
  if (key === 'sent' || key === 'viewed') return 'is-warn'
  return 'is-muted'
}

function size(bytes) {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}


/* What is actually held, in one cell. This is the column the whole drawer is
 * for, so it is the one that never hedges. */
function Held({ doc }) {
  const stored = doc.stored_file
  if (stored) {
    return (
      <>
        <div className="ws-doc__file">
          {stored.original_filename || 'file'}
        </div>
        <div className="ws-comp__sub">
          {[fmtLabel((stored.content_type || '').split('/').pop()),
            size(stored.byte_size)].filter(Boolean).join(' · ')}
        </div>
      </>
    )
  }
  if (doc.file_name) {
    return (
      <>
        <div className="ws-doc__file is-reference">{doc.file_name}</div>
        <div className="ws-comp__sub ws-doc__warn">
          Name only — no file is held here
        </div>
      </>
    )
  }
  return <span className="ws-muted">Nothing attached</span>
}


function Row({ doc, busy, act, capability, onAttach, onDelete }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ title: doc.title || '',
                                       doc_type: doc.doc_type })
  const stored = doc.stored_file
  const canUpload = capability && capability.uploads_enabled

  if (editing) {
    return (
      <tr className="ws-doc is-editing">
        <td colSpan={6}>
          <div className="ws-respond">
            <div className="ws-respond__head">
              Editing {doc.title || fmtLabel(doc.doc_type)}
            </div>
            <div className="ws-grid">
              <div className="ws-field">
                <label htmlFor={`dt-${doc.id}`}>Type</label>
                <select id={`dt-${doc.id}`} className="ws-input" value={draft.doc_type}
                        onChange={(e) => setDraft(
                          (s) => ({ ...s, doc_type: e.target.value }))}>
                  {DOC_TYPES.map((x) => (
                    <option key={x} value={x}>{fmtLabel(x)}</option>
                  ))}
                </select>
              </div>
              <div className="ws-field">
                <label htmlFor={`dti-${doc.id}`}>Title</label>
                <input id={`dti-${doc.id}`} className="ws-input" value={draft.title}
                       onChange={(e) => setDraft(
                         (s) => ({ ...s, title: e.target.value }))} />
              </div>
            </div>
            <div className="ws-actions" style={{ marginTop: 10 }}>
              <button className="btn btn--primary btn--sm" disabled={busy}
                      onClick={async () => {
                        if (await act(
                          () => api.patch(`/wholesale/documents/${doc.id}`, {
                            doc_type: draft.doc_type,
                            title: draft.title || null,
                          }), 'Document saved.')) setEditing(false)
                      }}>Save</button>
              <button className="btn btn--secondary btn--sm"
                      onClick={() => setEditing(false)}>Cancel</button>
            </div>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <tr className="ws-doc">
      <td>
        <div className="ws-doc__title">
          {doc.title || <span className="ws-muted">Untitled</span>}
        </div>
        <div className="ws-comp__sub">{fmtLabel(doc.doc_type)}</div>
      </td>
      <td><Held doc={doc} /></td>
      <td>
        <span className={`ws-pill ${statusTone(doc.status_key)}`}>
          {doc.status_label || fmtLabel(doc.status, 'No status')}
        </span>
        {/* Only the moves the server would actually accept are offered. The
            list comes from the server with the row, so this control cannot
            drift from what the lifecycle allows. */}
        {(doc.allowed_next || []).length ? (
          <>
            <label className="ws-vis-hidden" htmlFor={`st-${doc.id}`}>
              Move this document on
            </label>
            <select id={`st-${doc.id}`} value=""
                    className="ws-input ws-input--inline ws-statusedit"
                    disabled={busy}
                    onChange={(e) => {
                      if (!e.target.value) return
                      act(() => api.post(
                        `/wholesale/documents/${doc.id}/status`,
                        { status: e.target.value }), 'Document moved on.')
                    }}>
              <option value="">Move to…</option>
              {doc.allowed_next.map((k) => (
                <option key={k} value={k}>{STATUS_LABEL[k] || fmtLabel(k)}</option>
              ))}
            </select>
          </>
        ) : (
          <div className="ws-comp__sub">Finished — nothing moves from here.</div>
        )}

        {/* The signature state is a SECOND fact, not a second reading of the
            first: a two-party contract can be partially signed while the
            document itself is still out. It sits under the lifecycle rather
            than in a column of its own, so the two are read together and the
            actions column keeps its width. */}
        <div className="ws-doc__sig">
          <label htmlFor={`sig-${doc.id}`}>Signatures</label>
          <select id={`sig-${doc.id}`}
                  className={`ws-input ws-input--inline ws-statusedit ${sigTone(doc.signature_status)}`}
                  value={SIGNATURE_LABEL[doc.signature_status] ? doc.signature_status : 'none'}
                  disabled={busy}
                  onChange={(e) => act(
                    () => api.patch(`/wholesale/documents/${doc.id}`, {
                      doc_type: doc.doc_type, signature_status: e.target.value,
                    }), 'Signature state recorded.')}>
            {SIGNATURE_STATES.map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
          {doc.signature_provider ? (
            <div className="ws-comp__sub">via {doc.signature_provider}</div>
          ) : null}
        </div>
      </td>
      {/* Phase 5 publication boundary, per document and per audience.
          A purchase contract is not a buyer document and an assignment
          agreement is not a seller document; both start unshared and a
          person decides each one. The server reads these same columns. */}
      <td>
        <label className="ws-checkbox ws-doc__share">
          <input type="checkbox" disabled={busy}
                 checked={!!doc.buyer_visible}
                 onChange={(e) => act(
                   () => api.patch(`/wholesale/documents/${doc.id}`, {
                     doc_type: doc.doc_type,
                     buyer_visible: e.target.checked,
                   }), e.target.checked ? 'Shared with investors.'
                                        : 'Hidden from investors.')} />
          Investors
        </label>
        <label className="ws-checkbox ws-doc__share">
          <input type="checkbox" disabled={busy}
                 checked={!!doc.seller_visible}
                 onChange={(e) => act(
                   () => api.patch(`/wholesale/documents/${doc.id}`, {
                     doc_type: doc.doc_type,
                     seller_visible: e.target.checked,
                   }), e.target.checked ? 'Shared with the owner.'
                                        : 'Hidden from the owner.')} />
          Owner
        </label>
        {doc.viewed_at ? (
          <div className="ws-comp__sub">opened {fmtDate(doc.viewed_at)}</div>
        ) : null}
      </td>
      <td>
        {doc.uploaded_at ? fmtDate(doc.uploaded_at) : '—'}
        {doc.executed_at ? (
          <div className="ws-comp__sub">executed {fmtDate(doc.executed_at)}</div>
        ) : null}
      </td>
      <td>
        <span className="ws-actions ws-actions--wrap">
          {stored ? (
            <>
              <button className="btn btn--secondary btn--sm"
                      onClick={() => openFile(stored.url)}>View</button>
              <button className="btn btn--secondary btn--sm"
                      onClick={() => openFile(stored.url, {
                        download: stored.original_filename || 'document' })}>
                Download
              </button>
              {canUpload ? (
                <button className="btn btn--secondary btn--sm"
                        onClick={() => onAttach(doc)}>Replace</button>
              ) : null}
            </>
          ) : canUpload ? (
            <button className="btn btn--secondary btn--sm"
                    onClick={() => onAttach(doc)}>Attach file</button>
          ) : null}
          <button className="btn btn--secondary btn--sm"
                  onClick={() => { setDraft({ title: doc.title || '',
                                              doc_type: doc.doc_type })
                                   setEditing(true) }}>Edit</button>
          <button className="btn btn--secondary btn--sm ws-btn-delete"
                  onClick={() => onDelete(doc)}>Delete</button>
        </span>
      </td>
    </tr>
  )
}


export function DocumentDrawer({ room, act, busy }) {
  const { deal, documents } = room
  const capability = room.file_storage
  const [docType, setDocType] = useState('purchase_contract')
  const [title, setTitle] = useState('')
  const [target, setTarget] = useState(null)      // attach/replace on a slot
  const [confirming, setConfirming] = useState(null)

  const rows = [...(documents || [])].sort(
    (a, b) => (ORDER[a.doc_type] ?? 99) - (ORDER[b.doc_type] ?? 99))
  const held = rows.filter((d) => d.stored_file).length
  const signed = rows.filter((d) => d.signature_status === 'signed').length

  async function upload(files) {
    const fd = new FormData()
    fd.append('file', files[0])
    fd.append('doc_type', target ? target.doc_type : docType)
    if (!target && title) fd.append('title', title)
    if (target) fd.append('document_id', target.id)
    const ok = await act(
      () => api.upload(`/wholesale/deals/${deal.id}/documents/upload`, fd),
      target ? 'File attached.' : 'Document uploaded.')
    if (ok) { setTitle(''); setTarget(null) }
  }

  return (
    <>
    {/* The contract step comes BEFORE the drawer, because picking a form and
        gathering the facts is what happens first; the drawer is where the
        result lands. */}
    <ContractDesk deal={deal} act={act} busy={busy} />

    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Documents ({rows.length})</span>
        <span className="ws-doc__counts">
          {held} with a file · {signed} signed
        </span>
      </div>

      <Note>
        A document can exist before its file does. Every row says which it is.
      </Note>

      {rows.length ? (
        <div className="ws-scroll">
          <table className="ws-table ws-docs">
            <thead>
              <tr>
                <th>Document</th><th>File held</th>
                <th>Status &amp; signatures</th><th>Shared with</th>
                <th>Uploaded</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <Row key={d.id} doc={d} busy={busy} act={act}
                     capability={capability}
                     onAttach={setTarget} onDelete={setConfirming} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Note>Nothing filed on this deal yet.</Note>
      )}

      {confirming ? (
        <ConfirmDelete busy={busy}
                       what={`${confirming.title || fmtLabel(confirming.doc_type)}`
                             + (confirming.stored_file ? ' and the file held with it' : '')}
                       onCancel={() => setConfirming(null)}
                       onConfirm={async () => {
                         if (await act(
                           () => api.delete(`/wholesale/documents/${confirming.id}`),
                           'Document deleted.')) setConfirming(null)
                       }} />
      ) : null}

      <div className="ws-upload-block">
        <div className="ws-doc__addhead">
          {target
            ? `${target.stored_file ? 'Replace the file on' : 'Attach a file to'}: `
              + (target.title || fmtLabel(target.doc_type))
            : 'Add a document'}
        </div>
        {target ? (
          <div className="ws-actions" style={{ marginBottom: 8 }}>
            <button className="btn btn--secondary btn--sm"
                    onClick={() => setTarget(null)}>Cancel</button>
            {target.stored_file ? (
              <span className="ws-comp__sub">
                The file it holds now is replaced; the document row, its type and
                its signature state stay.
              </span>
            ) : null}
          </div>
        ) : (
          <div className="ws-grid" style={{ marginBottom: 8 }}>
            <div className="ws-field">
              <label htmlFor="d-type">Type</label>
              <select id="d-type" className="ws-input" value={docType}
                      onChange={(e) => setDocType(e.target.value)}>
                {DOC_TYPES.map((x) => (
                  <option key={x} value={x}>{fmtLabel(x)}</option>
                ))}
              </select>
            </div>
            <div className="ws-field">
              <label htmlFor="d-title">Title (optional)</label>
              <input id="d-title" className="ws-input" value={title}
                     onChange={(e) => setTitle(e.target.value)} />
            </div>
          </div>
        )}
        <UploadZone capability={capability} busy={busy} onFiles={upload}
                    accept=".pdf,.doc,.docx,.jpg,.jpeg,.png,.webp"
                    label={target ? 'Choose file' : 'Upload document'} />
      </div>

      <SignatureCapability />
    </div>
    </>
  )
}


/* WHETHER A SIGNATURE CAN BE CARRIED FROM HERE, ASKED RATHER THAN ASSUMED.
 *
 * Phase 4 hard-coded "there is no provider" into the copy, which was true of
 * that deployment and would have quietly become a lie the day one was
 * connected. The screen now reads the capability the same way the storage
 * control reads the media backend, and the SEND BUTTON ONLY EXISTS WHEN
 * SOMETHING CAN ACTUALLY SEND. A button that opened nothing would be the
 * fake feature this module exists not to ship. */
function SignatureCapability() {
  const [cap, setCap] = useState(null)

  useEffect(() => {
    let live = true
    api.get('/wholesale/documents/lifecycle')
      .then((d) => { if (live) setCap(d.signature) })
      .catch(() => {})
    return () => { live = false }
  }, [])

  if (!cap) return null
  if (cap.electronic_signature) {
    return (
      <Note>
        A signature provider is connected. Use “Send for signature” on a
        document once it is approved; the document moves to SENT only when the
        provider confirms it went.
      </Note>
    )
  }

  return (
    <Why label="Why there is no “send for signature” button">
      <p>{cap.reason}</p>
      <p>
        The signature state above is a fact a person records after it happened
        somewhere else, and SIGNED is refused until an executed copy is actually
        attached — a document cannot be marked signed with nothing behind it.
      </p>
      <p>
        The document row already carries <code>signature_provider</code> and
        <code> external_ref</code>, which is where a connected provider would
        write its envelope. Until one does, those stay empty rather than being
        filled with something that looks like an integration.
      </p>
    </Why>
  )
}
