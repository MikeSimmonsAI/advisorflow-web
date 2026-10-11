/* THE CONTRACT STEP, WITHOUT THE PRODUCT WRITING A CONTRACT.
 *
 * This is the screen for the moment a deal needs paper. It does two things and
 * refuses a third:
 *
 *   IT KEEPS YOUR FORMS.  The contract your attorney wrote is uploaded once,
 *   named, and available on every deal after that. The platform stores the
 *   file; it never reads inside it, fills it in, or checks it against any
 *   state's law.
 *
 *   IT HANDS YOU THE FACTS.  Every value a purchase or assignment document
 *   normally needs, already in the system, laid out for transcription — with
 *   the blanks left blank and labelled with where that fact actually comes
 *   from. A legal description is not guessed from an address.
 *
 *   IT OFFERS A STARTER SET.  A plain-English Texas purchase agreement,
 *   assignment and buyer disclosure, filled from the deal (ReadyContracts),
 *   always labelled as a starter to have an attorney approve, and sendable
 *   for e-signature when a provider is connected.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { errText, fmtLabel, Note, Why } from './wsShared'
import { openFile } from './wsFiles'

export function ContractDesk({ deal, act, busy }) {
  const [sheet, setSheet] = useState(null)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(false)

  const load = useCallback(async () => {
    try {
      setSheet(await api.get(`/wholesale/deals/${deal.id}/fill-sheet`))
    } catch (e) {
      setError(errText(e))
    }
  }, [deal.id])

  useEffect(() => { load() }, [load])

  if (error) return <div className="ws-error">{error}</div>
  if (!sheet) return null

  const sum = sheet.summary || { total: 0, complete: 0, needs_review: 0, missing: 0 }

  /* Plain text, because the destination is somebody else's word processor.
   * A blank stays blank and keeps its note, so what is missing travels with
   * what is known. */
  function asText() {
    return sheet.groups.map((g) => (
      g.group + '\n' + g.fields.map(
        (f) => `  ${f.label}: ${f.value || '—'}`
              + (f.note && !f.value ? `   (${f.note})` : '')
      ).join('\n')
    )).join('\n\n')
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(asText())
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      setError('This browser would not let the page write to the clipboard. '
               + 'Select the sheet and copy it instead.')
    }
  }

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Papering this deal</span>
        <span className="ws-actions">
          <span className={`ws-pill ${sum.missing ? 'is-warn' : 'is-ok'}`}>
            {sum.complete} complete
          </span>
          {sum.needs_review ? (
            <span className="ws-pill is-warn">{sum.needs_review} to check</span>
          ) : null}
          {sum.missing ? (
            <span className="ws-pill is-dnc">{sum.missing} missing</span>
          ) : null}
        </span>
      </div>

      <ReadyContracts deal={deal} onSent={load} />

      <Note>
        Using your own form instead? Carry these facts into it.
      </Note>

      <Templates templates={sheet.templates} />

      <div className="ws-fill">
        <div className="ws-fill__head">
          <span>This deal, ready to transcribe</span>
          <span className="ws-actions">
            <button className="btn btn--secondary btn--sm" onClick={copy}
                    disabled={busy}>
              {copied ? 'Copied' : 'Copy the sheet'}
            </button>
          </span>
        </div>
        {sheet.groups.map((g) => (
          <div className="ws-fill__group" key={g.group}>
            <h4>
              {g.group}
              {/* The group's own state, so somebody scanning for what is left
                  does not have to read every row. */}
              <span className={`ws-fill__flag ${g.ready ? 'is-ok' : 'is-open'}`}>
                {g.ready ? 'complete'
                  : [g.counts?.missing ? `${g.counts.missing} missing` : null,
                     g.counts?.needs_review ? `${g.counts.needs_review} to check` : null]
                    .filter(Boolean).join(' · ')}
              </span>
            </h4>
            <dl>
              {g.fields.map((f) => (
                <div key={f.label} className={`is-${f.status || 'complete'}`}>
                  <dt>{f.label}</dt>
                  <dd>
                    {f.value || <span className="ws-muted">not on file</span>}
                    {f.status === 'needs_review' ? (
                      <span className="ws-fill__tag">Check this</span>
                    ) : null}
                    {f.note && f.status !== 'complete' ? (
                      <span className="ws-fill__note">{f.note}</span>
                    ) : null}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>

      <Why label="About the ready-made contracts">
        <p>
          A wholesale assignment is a binding real-property agreement. The
          ready-made set is a plain-English starting point for Texas, not legal
          advice: have a Texas real estate attorney approve it once (or upload
          the set they give you above) before you rely on it.
        </p>
        <p>
          Every value in it comes from this deal or from what you type on this
          screen. A document cannot be sent while a required blank is open, and
          it only shows as Signed once the signing service confirms every party
          signed.
        </p>
      </Why>
    </div>
  )
}


/* READY-MADE CONTRACTS: the starter purchase agreement, assignment and buyer
 * disclosure, filled from this deal. Preview, print, or send for e-signature.
 * Fields the deal does not hold yet can be typed here; a document cannot be
 * sent while a required blank or a signer's email is missing. */
const EDITABLE = {
  purchase_agreement: ['seller_name', 'seller_email', 'price', 'earnest_money', 'option_days',
    'title_company', 'closing_date', 'additional_terms'],
  assignment_agreement: ['assignee_name', 'assignee_signer', 'assignee_email', 'contract_date',
    'assignment_fee', 'assignee_deposit', 'title_company', 'closing_date', 'additional_terms'],
  assignee_disclosure: ['assignee_name', 'assignee_signer', 'assignee_email'],
}

function ReadyContracts({ deal, onSent }) {
  const [kits, setKits] = useState(null)
  const [kind, setKind] = useState(null)
  const [vals, setVals] = useState({})
  const [pv, setPv] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    api.get('/wholesale/contract-kits').then(setKits).catch((e) => setError(errText(e)))
  }, [])

  const preview = useCallback(async (k, v) => {
    setBusy(true); setError(null)
    try { setPv(await api.post(`/wholesale/deals/${deal.id}/contracts/${k}/preview`, { values: v })) }
    catch (e) { setError(errText(e)) }
    finally { setBusy(false) }
  }, [deal.id])

  function open(k) { setKind(k); setVals({}); setNotice(null); preview(k, {}) }

  function printIt() {
    const w = window.open('', '_blank')
    if (!w) { setError('The browser blocked the print window. Allow pop-ups for this site.'); return }
    w.document.write(pv.html); w.document.close(); w.focus(); setTimeout(() => w.print(), 300)
  }

  async function send() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api.post(`/wholesale/deals/${deal.id}/contracts/${kind}/send`, { values: vals })
      if (r.sent) { setNotice(`${r.message} Track it under Documents.`); onSent && onSent() }
      else setError(r.message)
    } catch (e) { setError(errText(e)) }
    finally { setBusy(false) }
  }

  if (!kits) return error ? <div className="ws-error">{error}</div> : null
  const sig = kits.signature || {}
  const labels = kits.fields || {}
  const blocked = pv ? [...(pv.missing || []), ...(pv.signer_problems || [])] : []

  return (
    <div className="ws-fill" style={{ marginBottom: 14 }}>
      <div className="ws-fill__head">
        <span>Ready-made contracts</span>
        <span className={`ws-pill ${sig.electronic ? 'is-ok' : 'is-muted'}`}>
          {sig.electronic ? `E-signing on (${sig.label})` : 'E-signing not connected'}
        </span>
      </div>
      <Note>{kits.notice}</Note>
      <div className="ws-actions" style={{ flexWrap: 'wrap', margin: '8px 0' }}>
        {kits.kits.map((k) => (
          <button key={k.kind} className={`btn btn--sm ${kind === k.kind ? 'btn--primary' : 'btn--secondary'}`}
                  onClick={() => open(k.kind)} title={k.when}>{k.short}</button>
        ))}
      </div>
      {error ? <div className="ws-error">{error}</div> : null}
      {notice ? <div className="ws-good">{notice}</div> : null}
      {kind && pv ? (
        <>
          <div className="ws-grid">
            {(EDITABLE[kind] || []).map((key) => (
              <div className="ws-field" key={key}>
                <label htmlFor={`rc-${key}`}>{labels[key] || key}</label>
                {key === 'additional_terms' ? (
                  <textarea id={`rc-${key}`} rows={2} value={vals[key] ?? ''}
                            placeholder={pv.values[key] || ''}
                            onChange={(e) => setVals((v) => ({ ...v, [key]: e.target.value }))} />
                ) : (
                  <input id={`rc-${key}`} className="ws-input" value={vals[key] ?? ''}
                         type={key === 'closing_date' || key === 'contract_date' ? 'date' : 'text'}
                         placeholder={pv.values[key] || 'not on file'}
                         onChange={(e) => setVals((v) => ({ ...v, [key]: e.target.value }))} />
                )}
              </div>
            ))}
          </div>
          <div className="ws-actions" style={{ margin: '8px 0' }}>
            <button className="btn btn--secondary btn--sm" disabled={busy} onClick={() => preview(kind, vals)}>
              Update preview</button>
            <button className="btn btn--secondary btn--sm" disabled={busy} onClick={printIt}>Print / save PDF</button>
            <button className="btn btn--primary btn--sm" disabled={busy || !sig.electronic || blocked.length > 0}
                    onClick={send}
                    title={!sig.electronic ? (sig.reason || 'E-signing is not available')
                      : blocked.length ? `Missing: ${blocked.join(', ')}`
                      : 'Each signer gets a private link by email, confirms a code, and signs - in order'}>
              Send for e-signature</button>
          </div>
          {blocked.length ? <div className="ws-hint">Still needed before sending: {blocked.join(' · ')}</div> : null}
          {!sig.electronic ? (
            <div className="ws-hint">E-signing is not available right now. Print it and upload the signed copy under Documents.</div>
          ) : null}
          <iframe title="Contract preview" srcDoc={pv.html} sandbox=""
                  style={{ width: '100%', height: 560, border: '1px solid #ddd', borderRadius: 8, background: '#fff', marginTop: 8 }} />
        </>
      ) : null}
    </div>
  )
}


function Templates({ templates }) {
  if (!templates || !templates.length) {
    return (
      <div className="ws-templates is-empty">
        No forms are stored yet. Add the contract your attorney gave you in
        Wholesale settings and it will be here on every deal.
      </div>
    )
  }
  return (
    <div className="ws-templates">
      {templates.map((t) => (
        <div className="ws-template" key={t.id}>
          <div className="ws-template__name">{t.name}</div>
          <div className="ws-comp__sub">
            {[fmtLabel(t.doc_type), t.jurisdiction, t.source_note]
              .filter(Boolean).join(' · ')}
          </div>
          {t.guidance ? (
            <div className="ws-template__guidance">{t.guidance}</div>
          ) : null}
          {t.file_url ? (
            <div className="ws-actions">
              <button className="btn btn--secondary btn--sm"
                      onClick={() => openFile(t.file_url)}>Open</button>
              <button className="btn btn--secondary btn--sm"
                      onClick={() => openFile(t.file_url,
                                              { download: t.file_name || 'form' })}>
                Download
              </button>
            </div>
          ) : null}
        </div>
      ))}
    </div>
  )
}


/* THE TEMPLATE LIBRARY, in settings rather than on a deal: it belongs to the
 * organization, not to one transaction. */
export function TemplateLibrary() {
  const [rows, setRows] = useState(null)
  const [notice, setNotice] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [showArchived, setShowArchived] = useState(false)
  const [form, setForm] = useState({ name: '', doc_type: 'purchase_contract',
                                     jurisdiction: '', source_note: '',
                                     guidance: '' })
  const [file, setFile] = useState(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const params = showArchived ? '?include_archived=true' : ''
      setRows(await api.get('/wholesale/contract-templates' + params))
    } catch (e) {
      setError(errText(e))
    }
  }, [showArchived])

  useEffect(() => { load() }, [load])

  async function upload(e) {
    e.preventDefault()
    if (!file || !form.name.trim()) return
    setBusy(true); setError(null); setNotice(null)
    try {
      const fd = new FormData()
      fd.append('file', file)
      Object.entries(form).forEach(([k, v]) => { if (v) fd.append(k, v) })
      await api.upload('/wholesale/contract-templates', fd)
      setNotice('Form stored.')
      setForm({ name: '', doc_type: 'purchase_contract', jurisdiction: '',
                source_note: '', guidance: '' })
      setFile(null)
      load()
    } catch (e2) {
      setError(errText(e2))
    } finally {
      setBusy(false)
    }
  }

  async function archive(row, restore) {
    setBusy(true); setError(null)
    try {
      await api.post(`/wholesale/contract-templates/${row.id}/archive`
                     + (restore ? '?restore=true' : ''), {})
      setNotice(restore ? 'Form restored.' : 'Form retired.')
      load()
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  if (!rows) return null

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Your contract forms</span>
        <label className="ws-checkbox">
          <input type="checkbox" checked={showArchived}
                 onChange={(e) => setShowArchived(e.target.checked)} />
          Show retired
        </label>
      </div>

      <Note>{rows.notice}</Note>
      {error ? <div className="ws-error">{error}</div> : null}
      {notice ? <div className="ws-good">{notice}</div> : null}

      {rows.templates.length ? (
        <div className="ws-templates">
          {rows.templates.map((t) => (
            <div className={`ws-template ${t.is_active ? '' : 'is-retired'}`}
                 key={t.id}>
              <div className="ws-template__name">
                {t.name}
                {t.is_active ? null : <span className="ws-pill is-muted">retired</span>}
              </div>
              <div className="ws-comp__sub">
                {[fmtLabel(t.doc_type), t.jurisdiction, t.source_note]
                  .filter(Boolean).join(' · ')}
              </div>
              {t.guidance ? (
                <div className="ws-template__guidance">{t.guidance}</div>
              ) : null}
              <div className="ws-actions">
                {t.file_url ? (
                  <button className="btn btn--secondary btn--sm"
                          onClick={() => openFile(t.file_url)}>Open</button>
                ) : null}
                <button className="btn btn--secondary btn--sm" disabled={busy}
                        onClick={() => archive(t, !t.is_active)}>
                  {t.is_active ? 'Retire' : 'Restore'}
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Note>Nothing stored yet.</Note>
      )}

      <form className="ws-form ws-template-add" onSubmit={upload}>
        <div className="ws-doc__addhead">Add a form</div>
        <div className="ws-grid">
          <div className="ws-field">
            <label htmlFor="tpl-name">What you call it</label>
            <input id="tpl-name" className="ws-input" value={form.name}
                   onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                   placeholder="TREC 20-18 plus our rider" />
          </div>
          <div className="ws-field">
            <label htmlFor="tpl-type">Type</label>
            <select id="tpl-type" className="ws-input" value={form.doc_type}
                    onChange={(e) => setForm(
                      (f) => ({ ...f, doc_type: e.target.value }))}>
              {(rows.doc_types || []).map((x) => (
                <option key={x} value={x}>{fmtLabel(x)}</option>
              ))}
            </select>
          </div>
          <div className="ws-field">
            <label htmlFor="tpl-juris">Written for</label>
            <input id="tpl-juris" className="ws-input" value={form.jurisdiction}
                   onChange={(e) => setForm(
                     (f) => ({ ...f, jurisdiction: e.target.value }))}
                   placeholder="Texas" />
            <span className="ws-hint">
              Recorded as you type it. Nothing here checks a form against any
              state's law.
            </span>
          </div>
          <div className="ws-field">
            <label htmlFor="tpl-source">Who produced it</label>
            <input id="tpl-source" className="ws-input" value={form.source_note}
                   onChange={(e) => setForm(
                     (f) => ({ ...f, source_note: e.target.value }))}
                   placeholder="Our attorney, March 2026" />
          </div>
        </div>
        <div className="ws-field">
          <label htmlFor="tpl-guidance">Note for whoever uses it next</label>
          <textarea id="tpl-guidance" rows={2} value={form.guidance}
                    onChange={(e) => setForm(
                      (f) => ({ ...f, guidance: e.target.value }))} />
        </div>
        <div className="ws-field">
          <label htmlFor="tpl-file">The file</label>
          <input id="tpl-file" type="file" accept=".pdf,.doc,.docx"
                 onChange={(e) => setFile(e.target.files[0] || null)} />
        </div>
        <div className="ws-actions">
          <button className="btn btn--primary btn--sm"
                  disabled={busy || !file || !form.name.trim()}>
            {busy ? 'Storing…' : 'Store this form'}
          </button>
        </div>
      </form>
    </div>
  )
}
