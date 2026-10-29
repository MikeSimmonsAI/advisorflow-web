// THE GUIDED IMPORTER.
//
//   1 Upload  2 Map Fields  3 Analyze & Clean  4 Classify
//   5 Review Problems  6 Approve Import  7 Import Results
//
// Nothing reaches the CRM before step 6, and step 6 defaults to "Stage
// everything — do not activate". Every number on screen is counted by the
// server from the staged rows the commit will read, so what you are shown is
// what will happen.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './intake.css'
import { EMAIL_LABEL, OrgBanner, Pill, RowTable, SMS_LABEL, STATUS_LABEL, STATUS_TONE, Tile,
         errorText, fmt } from './intakeShared'
import { lineCheckText, approveEnabled, commitGate, isAnalyzed, reachableStep, resolveStep, stepAfterCommit, orgNameMatches, approveBlockedReason } from './importSteps'

const STEPS = ['Upload', 'Map Fields', 'Analyze & Clean', 'Classify', 'Review Problems',
               'Approve Import', 'Import Results']
// SCI (target="sci"): the same engine for steps 1-5, no Classify step (an SCI
// file's own status is SCI source data), and SCI's own loader at the end.
// The numbers are the server's step numbers; the labels are what is shown.
const SCI_STEPS = [[1, 'Upload'], [2, 'Map Fields'], [3, 'Analyze & Clean'], [5, 'Review Problems'],
                   [6, 'Check & Load into SCI'], [7, 'Results']]
const PIPELINE = [['parsing', 'Parsing'], ['normalizing', 'Normalizing'],
                  ['deduplicating', 'Deduplicating'], ['matching', 'Matching'],
                  ['classifying', 'Classifying'], ['analyzing', 'Analyzing'],
                  ['ready_for_review', 'Ready for Review']]
const SCI_LEADS = '/program?tab=leads'
// Staged or loaded into SCI: the rows can no longer change here (the server refuses too).
const SCI_FROZEN = ['program_stage', 'program_load']
// The server's answer when the CRM Import Center asks for an SCI batch.
const SCI_REFUSAL = 'SCI files are loaded through SCI'

/**
 * target   'crm' (default): the Import Center at /imports - unchanged.
 *          'sci': an SCI workspace's lead import (/program/import). Steps 1-5
 *          run on /program/intake; the last step is SCI's own check and load.
 * basePath where this wizard's own pages live ('/imports' or '/program/import').
 * onDone   optional: called with the batch after an SCI stage or load.
 */
export default function ImportWizard({ target = 'crm', basePath = '/imports', onDone } = {}) {
  const sci = target === 'sci'
  const apiBase = sci ? '/program/intake' : '/intake'
  const { batchId } = useParams()
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const [ctx, setCtx] = useState(null)
  const [ctxErr, setCtxErr] = useState('')
  const [fieldsMeta, setFieldsMeta] = useState(null)
  const [batch, setBatch] = useState(null)
  const [err, setErr] = useState('')
  const urlStep = Number(params.get('step'))
  // Never show a step the batch cannot be on: a stale ?step=6 on a batch that
  // has since been re-mapped falls back to where the server says it is.
  // Step gating lives in ./importSteps (pure, unit-tested).
  const resolved = resolveStep(batch, urlStep, !!batchId)
  // SCI has no Classify step: anything that would land on it opens Review.
  const step = sci && resolved === 4 ? 5 : resolved
  // While the server is (re)analyzing, whichever of steps 3-5 asked for it
  // shows progress, and the same step renders the result when it finishes.
  const working = batch && ['processing', 'interrupted'].includes(batch.status) && step >= 3 && step <= 5
  const goStep = n => setParams(p => { const q = new URLSearchParams(p); q.set('step', n); return q })

  useEffect(() => {
    api.get(`${apiBase}/context`).then(setCtx).catch(e => setCtxErr(errorText(e)))
    api.get(`${apiBase}/fields`).then(setFieldsMeta).catch(() => {})
  }, [apiBase])

  const load = useCallback(async () => {
    if (!batchId) return null
    try {
      const b = await api.get(`${apiBase}/batches/${batchId}`)
      setBatch(b)
      return b
    } catch (e) { setErr(errorText(e)); return null }
  }, [batchId, apiBase])
  useEffect(() => { load() }, [load])

  // Poll while the server is working; the tab may be closed at any time.
  useEffect(() => {
    if (!batch || !['processing', 'committing'].includes(batch.status)) return
    const t = setInterval(load, 1500)
    return () => clearInterval(t)
  }, [batch, load])

  if (ctxErr) {
    return (
      <div className="ic-page">
        <h1 className="page-title">{sci ? 'Import Leads' : 'Import Center'}</h1>
        <div className="ic-blocker">
          <div className="ic-blocker-title">{sci ? 'This import is not available here' : 'Select an organization first'}</div>
          <p>{ctxErr}</p>
          {sci ? (
            <p className="ic-muted">Only a manager of this SCI workspace can import leads into it.</p>
          ) : (
            <p className="ic-muted">Imports always belong to exactly one organization. Platform
              owners must enter a customer before importing; nothing is ever imported at the
              platform level.</p>
          )}
          {sci && <button className="btn btn--secondary" onClick={() => navigate(SCI_LEADS)}>Back to SCI Leads</button>}
        </div>
      </div>
    )
  }

  // A batch made by the SCI importer is loaded by SCI, never by the CRM commit.
  if (!sci && (batch?.destination === 'sci' || err === SCI_REFUSAL)) {
    return (
      <div className="ic-page">
        <h1 className="page-title">Import Center</h1>
        <div className="ic-blocker">
          <div className="ic-blocker-title">This file belongs to SCI</div>
          <p>{batch?.batch_code || 'This batch'} was uploaded with SCI's Import Leads. It is loaded through SCI, not into the CRM.</p>
          <button className="btn btn--primary" onClick={() => navigate(`/program/import/${batch?.id || batchId}`)}>Open it in SCI Import Leads</button>
        </div>
      </div>
    )
  }

  return (
    <div className="ic-page">
      <header className="page-header">
        <div>
          <h1 className="page-title">{sci ? 'Import Leads into SCI' : 'Import Center'}</h1>
          <p className="page-subtitle">{sci
            ? <>Upload the list, check every column and every problem, then decide. Nothing is texted or emailed and nobody is put in a campaign by an import.</>
            : <>Upload a file, see exactly what it contains, and decide what enters the CRM.
                Uploading a contact does not make it a lead.</>}</p>
        </div>
        <div className="ic-header-actions">
          {sci
            ? <button className="btn btn--secondary" onClick={() => navigate(SCI_LEADS)}>Back to SCI Leads</button>
            : <button className="btn btn--secondary" onClick={() => navigate(basePath)}>Import Ledger</button>}
        </div>
      </header>
      <OrgBanner ctx={ctx} />
      {batch && (
        <div className="ic-batchbar">
          <span className="mono ic-strong">{batch.batch_code}</span>
          <span className="ic-muted">{batch.filename}</span>
          <span className="ic-muted">{fmt(batch.rows_submitted)} rows</span>
          <span className="ic-muted">{batch.source}{batch.source_detail ? ` · ${batch.source_detail}` : ''}</span>
          <Pill tone={STATUS_TONE[batch.status]}>{sci ? (SCI_STATUS[batch.status] || STATUS_LABEL[batch.status] || batch.status) : (STATUS_LABEL[batch.status] || batch.status)}</Pill>
        </div>
      )}
      <Stepper step={step} batch={batch} onGo={n => batch && goStep(n)} steps={sci ? SCI_STEPS : null} />
      {err && <div className="ic-error">{err}</div>}
      {batch?.error && <div className="ic-error">{batch.error}</div>}

      {step === 1 && <UploadStep ctx={ctx} sci={sci} apiBase={apiBase} onCreated={b => navigate(`${basePath}/${b.id}?step=2`)} />}
      {step === 2 && batch && <MapStep batch={batch} meta={fieldsMeta} reload={load} sci={sci} apiBase={apiBase}
                                       onAnalyzed={() => { load(); goStep(3) }} />}
      {working && <AnalyzeStep batch={batch} reload={load} catalog={fieldsMeta?.classifications || []} apiBase={apiBase}
                               onNext={() => {}} onRemap={() => goStep(2)} />}
      {!working && step === 3 && batch && <AnalyzeStep batch={batch} reload={load} catalog={fieldsMeta?.classifications || []} apiBase={apiBase} sci={sci}
                                           nextLabel={sci ? 'Review problems →' : undefined}
                                           onNext={() => goStep(sci ? 5 : 4)} onRemap={() => goStep(2)} />}
      {!sci && !working && step === 4 && batch && <ClassifyStep batch={batch} reload={load} apiBase={apiBase}
                                            onDone={() => { load(); goStep(5) }} onNext={() => goStep(5)} />}
      {!working && step === 5 && batch && <ReviewStep batch={batch} reload={load} catalog={fieldsMeta?.classifications || []}
                                          initialTab={params.get('tab')} apiBase={apiBase} sci={sci} title={sci ? 'Step 4 — Review Problems' : undefined}
                                          nextLabel={sci ? 'Check what SCI will do →' : undefined}
                                          onNext={() => goStep(6)} />}
      {!sci && step === 6 && batch && <ApproveStep batch={batch} ctx={ctx} reload={load} apiBase={apiBase}
                                           onReview={tab => setParams(p => { const q = new URLSearchParams(p); q.set('step', 5); q.set('tab', tab); return q })}
                                           onCommitted={mode => { load(); goStep(stepAfterCommit(mode)) }}
                                           onCancelled={() => navigate(basePath)} />}
      {!sci && step === 7 && batch && <ResultsStep batch={batch} onLedger={() => navigate(`${basePath}?batch=${batch.id}`)}
                                           onApprove={() => goStep(6)} />}
      {sci && step === 6 && batch && <SciLoadStep batch={batch}
                                          onReview={() => goStep(5)}
                                          onDone={async () => { const b = await load(); goStep(7); onDone && onDone(b) }} />}
      {sci && step === 7 && batch && <SciResultsStep batch={batch} onLoad={() => goStep(6)}
                                          onLeads={() => navigate(SCI_LEADS)} />}
    </div>
  )
}

const SCI_STATUS = { staged: 'Staged in SCI', committed: 'Loaded into SCI', ready_for_review: 'Ready to check',
                     mapping: 'Mapping columns', processing: 'Analyzing', committing: 'Loading…' }

function Stepper({ step, batch, onGo, steps = null }) {
  const analyzed = isAnalyzed(batch)
  const reach = reachableStep(batch)
  const list = steps || STEPS.map((label, i) => [i + 1, label])
  return (
    <ol className="ic-stepper">
      {list.map(([n, label], i) => {
        const reachable = batch && (n <= 2 || (analyzed && n <= reach))
        return (
          <li key={label} className={`ic-step ${n === step ? 'ic-step--on' : ''} ${n < step ? 'ic-step--done' : ''}`}>
            <button type="button" disabled={!reachable} onClick={() => onGo(n)}>
              <span className="ic-step-n">{i + 1}</span><span className="ic-step-l">{label}</span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}

// ── 1. Upload ───────────────────────────────────────────────────────────────

function UploadStep({ ctx, onCreated, sci = false, apiBase = '/intake' }) {
  const fileRef = useRef(null)
  const [file, setFile] = useState(null)
  const [form, setForm] = useState({ source: 'csv', source_detail: '', source_year: '',
                                     list_name: '', campaign_purpose: '', offer_hook: '', tags: '' })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  async function fromGoogle() {
    setBusy(true); setErr('')
    try { onCreated(await api.post('/intake/batches/google-contacts', { list_name: form.list_name.trim() || null })) }
    catch (e) { setErr(errorText(e)) }
    finally { setBusy(false) }
  }

  async function submit() {
    if (!file) { setErr('Choose a .csv or .xlsx file.'); return }
    setBusy(true); setErr('')
    const fd = new FormData()
    fd.append('file', file)
    // SCI: the server marks the batch as an SCI file; only the list name travels.
    const sent = sci ? { list_name: form.list_name } : form
    Object.entries(sent).forEach(([k, v]) => { if (String(v).trim()) fd.append(k, String(v).trim()) })
    try { onCreated(await api.upload(`${apiBase}/batches`, fd)) }
    catch (e) { setErr(errorText(e)) }
    finally { setBusy(false) }
  }

  if (sci) {
    return (
      <section className="panel ic-panel">
        <div className="panel-header"><h2 className="panel-title">Step 1 — Upload the lead list</h2></div>
        <div className={`ic-drop ${file ? 'ic-drop--has' : ''}`} onClick={() => fileRef.current?.click()}
             onDragOver={e => e.preventDefault()}
             onDrop={e => { e.preventDefault(); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]) }}>
          <input ref={fileRef} type="file" accept=".csv,.xlsx" hidden
                 onChange={e => setFile(e.target.files[0] || null)} />
          {file ? (
            <><div className="ic-strong">{file.name}</div>
              <div className="ic-muted">{(file.size / 1024 / 1024).toFixed(2)} MB · click to change</div></>
          ) : (
            <><div className="ic-strong">Drop the Excel or CSV list here</div>
              <div className="ic-muted">or click to choose · nothing is loaded yet</div></>
          )}
        </div>
        <div className="ic-form-grid">
          <label className="ic-span2">Name for this list (optional)
            <input className="settings-input" placeholder="e.g. October mailing responses"
                   value={form.list_name} onChange={e => set('list_name', e.target.value)} />
          </label>
        </div>
        <p className="ic-note">Usual columns: <b>Lead ID</b>, First Name, Last Name, Phone, Email,
          <b> Location Friendly Name</b>, Primary Campaign, Lead Status, Last Activity Date. Other column names are fine —
          you match them up on the next step.</p>
        {err && <div className="ic-error">{err}</div>}
        <div className="ic-actions">
          <button className="btn btn--primary btn--lg" disabled={busy || !file} onClick={submit}>
            {busy ? 'Reading file…' : 'Upload & read columns →'}
          </button>
        </div>
      </section>
    )
  }

  return (
    <section className="panel ic-panel">
      <div className="panel-header"><h2 className="panel-title">Step 1 — Upload</h2></div>
      <div className={`ic-drop ${file ? 'ic-drop--has' : ''}`} onClick={() => fileRef.current?.click()}
           onDragOver={e => e.preventDefault()}
           onDrop={e => { e.preventDefault(); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]) }}>
        <input ref={fileRef} type="file" accept=".csv,.xlsx,.txt,.tsv" hidden
               onChange={e => setFile(e.target.files[0] || null)} />
        {file ? (
          <><div className="ic-strong">{file.name}</div>
            <div className="ic-muted">{(file.size / 1024 / 1024).toFixed(2)} MB · click to change</div></>
        ) : (
          <><div className="ic-strong">Drop a CSV or Excel file here</div>
            <div className="ic-muted">or click to choose · up to 50 MB · nothing is imported yet</div></>
        )}
      </div>
      <div className="ic-form-grid">
        <label>Source
          <select className="filter-select" value={form.source} onChange={e => set('source', e.target.value)}>
            {(ctx?.sources || [{ key: 'csv', label: 'CSV Import' }]).map(s =>
              <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </label>
        <label>Source detail
          <input className="settings-input" placeholder="e.g. All Contacts Export 2026-09-24"
                 value={form.source_detail} onChange={e => set('source_detail', e.target.value)} />
        </label>
        <label>Source year
          <input className="settings-input" type="number" placeholder="optional"
                 value={form.source_year} onChange={e => set('source_year', e.target.value)} />
        </label>
        <label>List name
          <input className="settings-input" placeholder="optional"
                 value={form.list_name} onChange={e => set('list_name', e.target.value)} />
        </label>
        <label>Campaign purpose
          <input className="settings-input" placeholder="optional — stored on the batch"
                 value={form.campaign_purpose} onChange={e => set('campaign_purpose', e.target.value)} />
        </label>
        <label>Offer hook
          <input className="settings-input" placeholder="optional — stored on the batch"
                 value={form.offer_hook} onChange={e => set('offer_hook', e.target.value)} />
        </label>
        <label className="ic-span2">Tags for every record in this file
          <input className="settings-input" placeholder="comma separated, optional"
                 value={form.tags} onChange={e => set('tags', e.target.value)} />
        </label>
      </div>
      <p className="ic-note">Campaign, list and tags describe the <b>batch</b>. They do not overwrite
        anything already on an existing customer record.</p>
      {err && <div className="ic-error">{err}</div>}
      <div className="ic-actions">
        <button className="btn btn--primary btn--lg" disabled={busy || !file} onClick={submit}>
          {busy ? 'Reading file…' : 'Upload & read columns →'}
        </button>
        <button className="btn btn--secondary btn--lg" disabled={busy} onClick={fromGoogle}
                title="Reads your connected Google Contacts into this same review. Nothing is imported until you commit.">
          📇 From Google Contacts
        </button>
      </div>
    </section>
  )
}

// ── 2. Map ──────────────────────────────────────────────────────────────────

const KIND_LABEL = { standard: 'Platform field', custom: 'Custom field', vertical: 'Vertical field',
                     source: 'Keep as source data', ignore: 'Ignore' }
const SCI_KIND_LABEL = { ...KIND_LABEL, source: 'Keep as SCI data' }
// The SCI columns the importer keeps on purpose (see intake/fields.py
// _SCI_HEADER_MAP and programs/intake_bridge.py): mapped, not "unmapped".
const SCI_KEPT = { location_friendly_name: 'Location Friendly Name', primary_campaign: 'Primary Campaign',
                   lead_status: 'Lead Status', last_activity: 'Last Activity',
                   campaign_channel: 'Campaign Channel', lead_owner_manager: 'Lead Owner: Manager' }

function MapStep({ batch, meta, reload, onAnalyzed, sci = false, apiBase = '/intake' }) {
  const [preview, setPreview] = useState(null)
  const [mapping, setMapping] = useState({})
  const [policy, setPolicy] = useState(batch.update_policy || { mode: 'fill_blanks', fields: [] })
  const [dateOrder, setDateOrder] = useState(batch.classification?.date_order || 'auto')
  const [onlyUnmapped, setOnlyUnmapped] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get(`${apiBase}/batches/${batch.id}/preview`).then(p => {
      setPreview(p)
      const m = {}
      p.columns.forEach(c => { m[c.header] = { kind: c.mapping.kind, target: c.mapping.target } })
      setMapping(m)
    }).catch(e => setErr(errorText(e)))
  }, [batch.id, apiBase])

  const standard = meta?.standard || []
  const groups = useMemo(() => {
    const g = {}
    standard.forEach(f => { (g[f.group] = g[f.group] || []).push(f) })
    return g
  }, [standard])
  const taken = useMemo(() => {
    const t = {}
    Object.entries(mapping).forEach(([h, m]) => { if (m.kind === 'standard' && m.target) (t[m.target] = t[m.target] || []).push(h) })
    return t
  }, [mapping])
  const sciKept = m => sci && ['source', 'custom', 'vertical'].includes(m?.kind) && SCI_KEPT[m?.target]
  const unmapped = Object.values(mapping).filter(m => m.kind === 'source' && !sciKept(m)).length
  const kindLabels = sci ? SCI_KIND_LABEL : KIND_LABEL
  const set = (h, patch) => setMapping(m => ({ ...m, [h]: { ...m[h], ...patch } }))
  const slug = h => h.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '').slice(0, 60) || 'field'

  async function saveAndAnalyze() {
    setBusy(true); setErr('')
    try {
      await api.put(`${apiBase}/batches/${batch.id}/mapping`, {
        mapping, update_policy: policy, classification: { date_order: dateOrder } })
      await api.post(`${apiBase}/batches/${batch.id}/analyze`, {})
      onAnalyzed()
    } catch (e) { setErr(errorText(e)); reload() }
    finally { setBusy(false) }
  }

  if (!preview) return <section className="panel ic-panel"><div className="empty-state">{err || 'Reading columns…'}</div></section>
  const cols = preview.columns.filter(c => !onlyUnmapped || (mapping[c.header]?.kind === 'source' && !sciKept(mapping[c.header])))
  return (
    <section className="panel ic-panel">
      <div className="panel-header">
        <h2 className="panel-title">Step 2 — Map Fields</h2>
        <span className="panel-count">{preview.headers.length} columns · {fmt(preview.row_count)} rows</span>
      </div>
      {sci && unmapped > 0 && (
        <div className="ic-note">
          <b>{unmapped} other column{unmapped === 1 ? '' : 's'}</b> will be kept with each family's SCI record exactly as in the file.
          Map one only if it belongs in a field.
          <label className="ic-inline"><input type="checkbox" checked={onlyUnmapped}
                 onChange={e => setOnlyUnmapped(e.target.checked)} /> Show only these</label>
        </div>
      )}
      {!sci && unmapped > 0 && (
        <div className="ic-warn">
          <b>{unmapped} unmapped column{unmapped === 1 ? '' : 's'}.</b> They are not lost — each is kept on the record as
          source data. Map, mark as a custom field, or ignore each one deliberately.
          <label className="ic-inline"><input type="checkbox" checked={onlyUnmapped}
                 onChange={e => setOnlyUnmapped(e.target.checked)} /> Show only unmapped</label>
        </div>
      )}
      <div className="ic-table-wrap">
        <table className="ic-table ic-maptable">
          <thead><tr><th>Column in file</th><th>Sample values</th><th>Filled</th><th>Maps to</th><th></th></tr></thead>
          <tbody>
            {cols.map(c => {
              const m = mapping[c.header] || { kind: 'source' }
              const dupe = m.kind === 'standard' && taken[m.target]?.length > 1
              return (
                <tr key={c.header} className={dupe ? 'ic-row--warn' : ''}>
                  <td className="ic-strong">{c.header}
                    {!sciKept(m) && (c.mapping.confidence === 'low' || c.mapping.confidence === 'none') ?
                      <div className="ic-muted ic-small">no confident match</div> : null}</td>
                  <td className="ic-small ic-samples">{c.samples.length ? c.samples.join(' · ') : <span className="ic-muted">empty</span>}</td>
                  <td className="mono ic-small">{Math.round(100 * c.filled / Math.max(preview.row_count, 1))}%</td>
                  <td>
                    <select className="filter-select ic-select-sm" value={m.kind}
                            onChange={e => set(c.header, { kind: e.target.value,
                              target: e.target.value === 'standard' ? '' : (e.target.value === 'ignore' ? null : slug(c.header)) })}>
                      {Object.entries(kindLabels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  </td>
                  <td>
                    {m.kind === 'standard' && (
                      <select className="filter-select ic-select-sm" value={m.target || ''}
                              onChange={e => set(c.header, { target: e.target.value })}>
                        <option value="">— choose —</option>
                        {Object.entries(groups).map(([g, fs]) => (
                          <optgroup key={g} label={g}>
                            {fs.map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
                          </optgroup>
                        ))}
                      </select>
                    )}
                    {sciKept(m) && (
                      <div className="ic-sci-kept" title={m.target}>Kept as SCI data — {SCI_KEPT[m.target]}</div>
                    )}
                    {!sciKept(m) && ['custom', 'vertical', 'source'].includes(m.kind) && (
                      <input className="settings-input ic-input-sm mono" value={m.target || ''}
                             onChange={e => set(c.header, { target: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') })} />
                    )}
                    {dupe && <div className="ic-error-inline">Also mapped from: {taken[m.target].filter(h => h !== c.header).join(', ')}</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {sci && (
        <div className="ic-note" data-testid="sci-map-note">
          SCI needs <b>Lead ID</b> (Platform field “Source record ID”) and the location
          (Location Friendly Name, kept as SCI data). Primary Campaign and Lead Status are kept as SCI data too.
          Every original value is kept exactly as it is in the file.
        </div>
      )}
      <div className="ic-subpanel">
        {!sci && <>
        <h3>When a row matches a record already in the CRM</h3>
        <label className="ic-radio"><input type="radio" checked={policy.mode === 'fill_blanks'}
               onChange={() => setPolicy({ mode: 'fill_blanks', fields: [] })} />
          <span><b>Only fill empty fields</b> (recommended) — never replace a value that is already there.</span></label>
        <label className="ic-radio"><input type="radio" checked={policy.mode === 'overwrite'}
               onChange={() => setPolicy({ mode: 'overwrite', fields: policy.fields || [] })} />
          <span><b>Allow these fields to be updated</b> from this file. Fields a person corrected by hand are never overwritten.</span></label>
        {policy.mode === 'overwrite' && (
          <div className="ic-checks">
            {(meta?.updatable_fields || []).map(f => (
              <label key={f} className="ic-inline"><input type="checkbox" checked={policy.fields.includes(f)}
                     onChange={e => setPolicy(p => ({ ...p, fields: e.target.checked ? [...p.fields, f] : p.fields.filter(x => x !== f) }))} /> {f}</label>
            ))}
          </div>
        )}
        </>}
        <h3>Dates written like 03/04/2021</h3>
        <select className="filter-select" value={dateOrder} onChange={e => setDateOrder(e.target.value)}>
          <option value="auto">Decide per column — flag anything ambiguous for review</option>
          <option value="mdy">Month / day / year (US)</option>
          <option value="dmy">Day / month / year</option>
        </select>
      </div>
      {err && <div className="ic-error">{err}</div>}
      <div className="ic-actions">
        <button className="btn btn--primary btn--lg" disabled={busy} onClick={saveAndAnalyze}>
          {busy ? 'Starting analysis…' : 'Save mapping & analyze →'}
        </button>
      </div>
    </section>
  )
}

// ── 3. Analyze ──────────────────────────────────────────────────────────────

function AnalyzeStep({ batch, reload, catalog, onNext, onRemap, apiBase = '/intake', nextLabel, sci = false }) {
  const [cat, setCat] = useState(null)
  const a = batch.analysis || {}
  if (batch.status === 'processing' || batch.status === 'interrupted') {
    const idx = PIPELINE.findIndex(([k]) => k === batch.stage)
    return (
      <section className="panel ic-panel">
        <div className="panel-header"><h2 className="panel-title">Step 3 — Analyze & Clean</h2>
          <span className="panel-count">{batch.progress_pct || 0}%</span></div>
        <div className="ic-progress"><div style={{ width: `${batch.progress_pct || 0}%` }} /></div>
        <ol className="ic-pipeline">
          <li className="ic-pl--done">Uploading</li>
          {PIPELINE.map(([k, l], i) => (
            <li key={k} className={i < idx ? 'ic-pl--done' : i === idx ? 'ic-pl--on' : ''}>{l}</li>
          ))}
        </ol>
        {batch.status === 'interrupted' ? (
          <div className="ic-warn">The analysis stopped responding (the server may have restarted).
            It is safe to run it again.
            <button className="btn btn--secondary btn--sm" onClick={async () => {
              await api.post(`${apiBase}/batches/${batch.id}/analyze`, {}); reload() }}>Run analysis again</button></div>
        ) : <p className="ic-muted">Running on the server. You can close this tab — the result will be waiting in the Import Ledger.</p>}
      </section>
    )
  }
  if (!a.status) {
    return (
      <section className="panel ic-panel">
        <div className="empty-state">This batch has not been analyzed yet.</div>
        <div className="ic-actions"><button className="btn btn--primary" onClick={onRemap}>Go to mapping</button></div>
      </section>
    )
  }
  const st = a.status || {}
  const m = a.match || {}
  const ph = a.phones || {}
  const em = a.emails || {}
  const cov = a.coverage || {}
  const T = (label, value, category, tone, sub) =>
    <Tile key={label} label={label} value={value} tone={tone} sub={sub}
          onClick={category ? () => setCat(category === cat?.c ? null : { c: category, t: label }) : undefined}
          active={cat?.c === category} />
  return (
    <section className="panel ic-panel">
      <div className="panel-header">
        <h2 className="panel-title">Step 3 — Analyze & Clean</h2>
        <span className="panel-count">{a.timing_seconds ? `${a.timing_seconds}s` : ''}</span>
      </div>
      <p className="ic-muted">Every number is counted from the staged rows. Click any number to see the rows behind it.{' '}
        {sci ? 'Nothing has been loaded into SCI yet.' : 'Nothing has been written to the CRM.'}</p>

      <h3 className="ic-group">The file</h3>
      <div className="ic-tiles">
        {T('Total rows', a.total_rows, 'all', 'blue')}
        {T('Unique records', a.unique_records, null, 'blue')}
        {T('Duplicates in file', m.duplicates_in_file, 'match:duplicates_in_file', 'purple', sci ? 'linked to the same person' : 'merged into the first occurrence')}
        {T('Possible duplicates', m.possible_duplicates_in_file, 'match:possible_duplicates_in_file', 'amber', 'need a decision')}
        {T(sci ? 'Already in SCI' : 'Existing CRM matches', m.existing_exact, 'match:existing_exact', 'blue', sci ? 'same Lead ID — updated, never duplicated' : 'will update, not duplicate')}
        {T(sci ? 'Possibly already in SCI' : 'Possible CRM matches', m.existing_possible, 'match:existing_possible', 'amber')}
        {T('New records', m.new_records, 'match:new', 'green')}
      </div>

      <h3 className="ic-group">Phones</h3>
      <div className="ic-tiles">
        {T('Valid-format phones', ph.valid_format, 'phones:valid', 'blue', 'format only — not SMS permission')}
        {T('Missing phone', ph.missing, 'phones:missing', 'muted')}
        {T('Invalid phone', ph.invalid, 'sms:invalid', 'red')}
        {T('Mobile', ph.line_type?.mobile, 'line:mobile', 'green', 'from a mobile column or verification')}
        {T('Landline', ph.line_type?.landline, 'line:landline', 'muted')}
        {T('VoIP', ph.line_type?.voip, 'line:voip', 'muted')}
        {T('Unknown line type', ph.line_type?.unknown, 'line:unknown', 'amber')}
      </div>

      <h3 className="ic-group">Emails</h3>
      <div className="ic-tiles">
        {T('Usable emails', em.usable, 'emails:usable', 'green')}
        {T('Invalid', em.invalid, 'email:invalid', 'red')}
        {T('Hard bounced', em.hard_bounce, 'email:hard_bounce', 'red')}
        {T('Unsubscribed', em.unsubscribed, 'email:unsubscribed', 'red')}
        {T('Suppressed', em.suppressed, 'email:suppressed', 'red')}
        {T('Needs a look', em.review, 'email:review', 'amber', 'role address or unverified')}
      </div>

      <h3 className="ic-group">Direct contact coverage</h3>
      <div className="ic-tiles">
        {T('Phone + email', cov.phone_and_email, 'coverage:phone_and_email', 'green')}
        {T('Phone only', cov.phone_only, 'coverage:phone_only', 'blue')}
        {T('Email only', cov.email_only, 'coverage:email_only', 'blue')}
        {T('No direct contact', cov.no_direct_contact, 'coverage:no_direct_contact', 'muted', 'kept as Needs Enrichment')}
      </div>

      <h3 className="ic-group">Outcome if you continue</h3>
      <div className="ic-tiles">
        {T('Ready', st.ready, 'status:ready', 'green')}
        {T('Needs review', st.needs_review, 'status:needs_review', 'amber')}
        {T('Needs enrichment', a.needs_enrichment, 'flag:needs_enrichment', 'muted')}
        {T('Blocked', st.blocked, 'status:blocked', 'red', 'existing record is DNC')}
        {T('Invalid', st.invalid, 'status:invalid', 'red')}
        {T('Historical customers', a.historical_customers, 'flag:historical_customer', 'purple')}
        {T('Exact customer matches', m.exact_customer_matches, 'match:customer_exact', 'purple')}
        {!sci && T('Would become leads', a.would_activate_as_leads_if_ready_committed, 'flag:creates_lead', 'green', 'Ready rows classified as opportunities')}
        {T('SMS ready', a.sms_ready, 'sms:ready', 'green', 'mobile + consent + clean')}
        {T('Email ready', a.email_ready, 'email:ready', 'green')}
      </div>

      {a.read?.malformed_count > 0 && (
        <div className="ic-warn">{a.read.malformed_count} line(s) in the file were malformed (extra cells or broken quoting).
          Extra cells are kept on the row as source data.</div>
      )}

      {cat && <RowTable batchId={batch.id} category={cat.c === 'all' ? null : cat.c} title={cat.t} catalog={catalog} apiBase={apiBase}
                        sci={sci} editable={sci && !SCI_FROZEN.includes(batch.commit_mode)} onChanged={sci ? () => reload() : undefined} />}

      <div className="ic-actions">
        <button className="btn btn--secondary" onClick={onRemap}>← Change mapping</button>
        <button className="btn btn--primary btn--lg" onClick={onNext}>{nextLabel || 'Classify records →'}</button>
      </div>
    </section>
  )
}

// ── 4. Classify ─────────────────────────────────────────────────────────────

function ClassifyStep({ batch, reload, onDone, onNext, apiBase = '/intake' }) {
  const [data, setData] = useState(null)
  const [vmap, setVmap] = useState({})
  const [fallback, setFallback] = useState('contact')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [adding, setAdding] = useState(false)
  const [newCls, setNewCls] = useState({ key: '', label: '', record_class: 'contact', creates_lead: false })

  const loadVals = useCallback(() => {
    api.get(`${apiBase}/batches/${batch.id}/classification-values`).then(d => {
      setData(d)
      setFallback(d.fallback || 'contact')
      const m = {}
      d.values.forEach(v => { m[v.value] = v.classification || '' })
      setVmap(m)
    }).catch(e => setErr(errorText(e)))
  }, [batch.id, apiBase])
  useEffect(() => { loadVals() }, [loadVals])

  async function save() {
    setBusy(true); setErr('')
    try {
      await api.put(`${apiBase}/batches/${batch.id}/mapping`,
                    { classification: { value_map: vmap, fallback } })
      await api.post(`${apiBase}/batches/${batch.id}/analyze`, {})
      onDone()
    } catch (e) { setErr(errorText(e)) }
    finally { setBusy(false) }
  }
  async function addClass() {
    try {
      await api.post('/intake/classifications', newCls)
      setAdding(false); setNewCls({ key: '', label: '', record_class: 'contact', creates_lead: false })
      loadVals()
    } catch (e) { setErr(errorText(e)) }
  }

  if (!data) return <section className="panel ic-panel"><div className="empty-state">{err || 'Loading values…'}</div></section>
  const catalog = data.catalog || []
  const cdef = k => catalog.find(c => c.key === k)
  const becomes = k => { const c = cdef(k); return !c ? '' : c.creates_lead ? 'Contact + Lead (opportunity)' : `Contact only · ${c.record_class.replace('_', ' ')}` }
  const unmappedCount = data.values.filter(v => !vmap[v.value]).reduce((s, v) => s + v.count, 0)
  return (
    <section className="panel ic-panel">
      <div className="panel-header"><h2 className="panel-title">Step 4 — Classify</h2></div>
      <p className="ic-muted">Classification decides what a record <b>is</b>. Only classifications marked “Contact + Lead”
        create sales opportunities, and only for rows with a usable phone or email.</p>
      {data.column ? (
        <>
          <div className="ic-note">Row-level classification from column <b>“{data.column}”</b>.
            {unmappedCount > 0 && <span className="ic-error-inline"> {fmt(unmappedCount)} rows have a value with no classification — they will go to review.</span>}</div>
          <div className="ic-table-wrap">
            <table className="ic-table">
              <thead><tr><th>Value in file</th><th>Rows</th><th>Classification</th><th>Becomes</th></tr></thead>
              <tbody>
                {data.values.map(v => (
                  <tr key={v.value}>
                    <td className="ic-strong">{v.value}</td>
                    <td className="mono">{fmt(v.count)}</td>
                    <td>
                      <select className="filter-select ic-select-sm" value={vmap[v.value] || ''}
                              onChange={e => setVmap(m => ({ ...m, [v.value]: e.target.value }))}>
                        <option value="">— Needs classification —</option>
                        {catalog.filter(c => c.key !== 'needs_classification').map(c =>
                          <option key={c.key} value={c.key}>{c.label}</option>)}
                      </select>
                      {v.suggested && !vmap[v.value] && <div className="ic-muted ic-small">suggested: {cdef(v.suggested)?.label}</div>}
                    </td>
                    <td className="ic-small">{becomes(vmap[v.value])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <div className="ic-warn">No column is mapped to <b>Record classification</b>, so every row uses the default below.
          If the file has a segment / type / status column, map it on the Map Fields step.</div>
      )}
      <div className="ic-subpanel">
        <h3>Default record classification</h3>
        <p className="ic-muted ic-small">Used for rows with a blank classification{data.column ? ` (${fmt(data.blank_count)} rows)` : ' (every row)'}.
          An outside database is not a list of new inquiries — the safe default is Contact.</p>
        <select className="filter-select" value={fallback} onChange={e => setFallback(e.target.value)}>
          {catalog.filter(c => c.key !== 'needs_classification').map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <span className="ic-small ic-muted" style={{ marginLeft: 10 }}>{becomes(fallback)}</span>
        {['new_inquiry', 'cold_prospect', 'warm_prospect', 'purchased_lead'].includes(fallback) && (
          <div className="ic-error">Every unclassified row with a phone or email will become an active lead. Be sure this file is a lead list.</div>
        )}
      </div>
      <div className="ic-subpanel">
        {!adding ? <button className="btn btn--secondary btn--sm" onClick={() => setAdding(true)}>+ Add a classification for this organization</button> : (
          <div className="ic-form-grid">
            <label>Key<input className="settings-input" value={newCls.key} onChange={e => setNewCls(c => ({ ...c, key: e.target.value }))} /></label>
            <label>Label<input className="settings-input" value={newCls.label} onChange={e => setNewCls(c => ({ ...c, label: e.target.value }))} /></label>
            <label>Record class
              <select className="filter-select" value={newCls.record_class} onChange={e => setNewCls(c => ({ ...c, record_class: e.target.value }))}>
                {['contact', 'lead', 'customer', 'previous_customer', 'renewal', 'partner', 'vendor', 'employee', 'other'].map(r => <option key={r} value={r}>{r}</option>)}
              </select></label>
            <label className="ic-inline"><input type="checkbox" checked={newCls.creates_lead} onChange={e => setNewCls(c => ({ ...c, creates_lead: e.target.checked }))} /> Creates a lead</label>
            <div className="ic-actions"><button className="btn btn--secondary btn--sm" onClick={() => setAdding(false)}>Cancel</button>
              <button className="btn btn--primary btn--sm" onClick={addClass}>Save classification</button></div>
          </div>
        )}
      </div>
      {err && <div className="ic-error">{err}</div>}
      <div className="ic-actions">
        <button className="btn btn--secondary" disabled={busy} onClick={onNext}>Skip — keep current →</button>
        <button className="btn btn--primary btn--lg" disabled={busy} onClick={save}>{busy ? 'Re-analyzing…' : 'Apply classification & re-analyze'}</button>
      </div>
    </section>
  )
}

// ── 5. Review ───────────────────────────────────────────────────────────────

const REVIEW_TABS = [
  ['status:needs_review', 'Needs review', 'review'],
  ['status:duplicate', 'Duplicates in file', 'duplicate'],
  ['status:existing_match', 'Existing records', 'existing_matches'],
  ['status:blocked', 'Blocked', 'blocked'],
  ['status:needs_enrichment', 'Needs enrichment', 'enrichment'],
  ['status:invalid', 'Invalid', 'invalid'],
  ['status:approved', 'Approved', null],
  ['status:skipped', 'Skipped', null],
]

const TAB_ALIASES = { duplicates: 'status:duplicate', blocked: 'status:blocked',
                      enrichment: 'status:needs_enrichment' }

function ReviewStep({ batch, reload, catalog, onNext, initialTab, apiBase = '/intake', title, nextLabel, sci = false }) {
  const tabLabel = (k, l) => (sci && k === 'status:existing_match' ? 'Already in SCI' : l)
  const [tab, setTab] = useState(TAB_ALIASES[initialTab] || REVIEW_TABS[0][0])
  const [tick, setTick] = useState(0)
  const [msg, setMsg] = useState('')
  const counts = batch.analysis?.status || {}
  const cnt = key => counts[key.split(':')[1]] ?? 0
  async function bulk(body, label) {
    if (!window.confirm(`${label} — apply to every row in “${REVIEW_TABS.find(t => t[0] === tab)[1]}”?`)) return
    try {
      const r = await api.post(`${apiBase}/batches/${batch.id}/rows/bulk`, { category: tab, ...body })
      setMsg(`${fmt(r.updated)} rows updated${r.refused ? `, ${fmt(r.refused)} left as they were` : ''}.`)
      setTick(t => t + 1); reload()
    } catch (e) { setMsg(errorText(e)) }
  }
  return (
    <section className="panel ic-panel">
      <div className="panel-header"><h2 className="panel-title">{title || 'Step 5 — Review Problems'}</h2></div>
      <p className="ic-muted">Uncertain matches are never merged automatically. Decide here, or leave them staged —
        they will not be imported until someone does.</p>
      <div className="ic-tabs">
        {REVIEW_TABS.map(([k, l]) => (
          <button key={k} className={`ic-tab ${tab === k ? 'ic-tab--on' : ''}`} onClick={() => setTab(k)}>
            {tabLabel(k, l)} <span className="ic-tab-n">{fmt(cnt(k))}</span></button>
        ))}
      </div>
      <div className="ic-bulk">
        {tab === 'status:needs_review' && <>
          <button className="btn btn--secondary btn--sm" onClick={() => bulk({ duplicate_resolution: 'keep_separate' }, 'Keep possible duplicates as separate records')}>Keep all separate</button>
          <button className="btn btn--secondary btn--sm" onClick={() => bulk({ approve: true }, 'Approve')}>Approve all</button>
          <button className="btn btn--secondary btn--sm" onClick={() => bulk({ skip: true }, 'Skip')}>Skip all</button></>}
        {tab === 'status:duplicate' && <>
          <button className="btn btn--secondary btn--sm" onClick={() => bulk({ duplicate_resolution: 'merge' }, sci ? 'Link to the same person' : 'Merge into the first occurrence')}>{sci ? 'Link all to the same person' : 'Merge all into first occurrence'}</button>
          <button className="btn btn--secondary btn--sm" onClick={() => bulk({ duplicate_resolution: 'skip_incoming' }, 'Skip duplicates entirely')}>Skip all</button></>}
        {tab === 'status:existing_match' && <>
          <button className="btn btn--secondary btn--sm" onClick={() => bulk({ duplicate_resolution: 'update_existing' }, 'Update existing records')}>Update all existing</button>
          <button className="btn btn--secondary btn--sm" onClick={() => bulk({ duplicate_resolution: 'skip_incoming' }, 'Leave existing records untouched')}>Leave all untouched</button></>}
        {tab === 'status:blocked' && <span className="ic-muted ic-small">A blocked record matches a Do-Not-Contact record. Approving updates the existing record only — it is never reactivated.</span>}
        {tab === 'status:needs_enrichment' && <span className="ic-muted ic-small">These have no usable phone or email. They can be preserved as contacts (never leads) on the Approve step.</span>}
      </div>
      {msg && <div className="ic-note">{msg}</div>}
      <RowTable key={tab + tick} batchId={batch.id} category={tab} title={tabLabel(tab, REVIEW_TABS.find(t => t[0] === tab)[1])}
                catalog={catalog} editable={!sci || !SCI_FROZEN.includes(batch.commit_mode)} onChanged={() => reload()}
                apiBase={apiBase} sci={sci} />
      <div className="ic-actions"><button className="btn btn--primary btn--lg" onClick={onNext}>{nextLabel || 'Continue to approval →'}</button></div>
    </section>
  )
}

// ── 6. Approve ──────────────────────────────────────────────────────────────

const MODES = [
  ['stage_only', 'Stage everything — do not activate', 'Nothing enters the CRM. The batch stays staged and reviewable; import later.', 'Safest'],
  ['ready_only', 'Import Ready only', 'Ready rows, approved rows and exact existing-record updates. Review rows stay staged.', ''],
  ['ready_and_review', 'Import Ready + Review', 'Also imports undecided review rows — possible duplicates are kept SEPARATE and never become leads.', ''],
]

function ApproveStep({ batch, ctx, reload, onReview, onCommitted, onCancelled, apiBase = '/intake' }) {
  const [mode, setMode] = useState('stage_only')
  const [enrich, setEnrich] = useState(true)
  const [pv, setPv] = useState(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.get(`${apiBase}/batches/${batch.id}/commit-preview?mode=${mode}&include_enrichment=${enrich}`)
      .then(setPv).catch(e => setErr(errorText(e)))
  }, [batch.id, mode, enrich, apiBase])
  const orgName = ctx?.organization_name || pv?.organization_name || ''
  const gate = commitGate(batch)
  const canPress = approveEnabled(batch, mode, typed, orgName, busy)

  async function go() {
    setBusy(true); setErr('')
    try {
      await api.post(`${apiBase}/batches/${batch.id}/commit`, { mode, include_enrichment: enrich,
        confirm_organization_name: mode === 'stage_only' ? null : typed })
      // Every successful decision - including "Keep staged" - moves on to
      // Step 7, which reports what happened (or that everything is staged).
      onCommitted(mode)
    } catch (e) { setErr(errorText(e)) }
    finally { setBusy(false) }
  }
  async function cancel() {
    if (!window.confirm('Cancel this batch? Nothing has been imported; the staged rows are kept for the record.')) return
    await api.post(`${apiBase}/batches/${batch.id}/cancel`, {})
    onCancelled()
  }
  return (
    <section className="panel ic-panel">
      <div className="panel-header"><h2 className="panel-title">Step 6 — Approve Import</h2></div>
      {batch.status === 'staged' && <div className="ic-note">This batch is <b>staged</b>. Nothing from it is in the CRM.</div>}
      <div className="ic-modes">
        {MODES.map(([k, t, d, badge]) => (
          <label key={k} className={`ic-mode ${mode === k ? 'ic-mode--on' : ''}`}>
            <input type="radio" name="mode" checked={mode === k} onChange={() => setMode(k)} />
            <div><div className="ic-strong">{t} {badge && <Pill tone="green">{badge}</Pill>}</div>
              <div className="ic-muted ic-small">{d}</div></div>
          </label>
        ))}
      </div>
      <label className="ic-inline ic-enrich"><input type="checkbox" checked={enrich} onChange={e => setEnrich(e.target.checked)} />
        Preserve <b>Needs Enrichment</b> records as contacts (searchable and matchable; never leads, never in outreach)</label>
      <div className="ic-reviewlinks">
        <button className="btn btn--secondary btn--sm" onClick={() => onReview('duplicates')}>Review duplicates</button>
        <button className="btn btn--secondary btn--sm" onClick={() => onReview('blocked')}>Review blocked</button>
        <button className="btn btn--secondary btn--sm" onClick={() => onReview('enrichment')}>Review enrichment</button>
      </div>
      {pv && (
        <div className="ic-tiles">
          <Tile label="Contacts created" value={pv.create_contacts} tone="blue" />
          <Tile label="…of which Needs Enrichment" value={pv.enrichment_contacts} tone="muted" />
          <Tile label="Existing records updated" value={pv.update_existing} tone="blue" />
          <Tile label="Duplicates merged" value={pv.merge_file_duplicates} tone="purple" />
          <Tile label="Leads activated" value={pv.activate_leads} tone="green" sub="opportunities only" />
          <Tile label="Skipped" value={pv.skip} tone="muted" />
          <Tile label="Stays staged" value={Object.values(pv.not_selected || {}).reduce((a, b) => a + b, 0)} tone="amber" />
        </div>
      )}
      {pv?.lead_capacity?.limit != null && pv.activate_leads > (pv.lead_capacity.available ?? 0) && (
        <div className="ic-warn">The plan has room for {fmt(pv.lead_capacity.available)} more leads. Contacts are still
          imported; leads beyond the limit are held and reported.</div>
      )}
      {mode !== 'stage_only' && (
        <div className="ic-confirm">
          <div>This writes into <b>{orgName}</b>. Type the organization name to confirm:</div>
          {/* The placeholder used to BE the org name, in the same font as typed
              text, so the box looked filled while it was empty and the Import
              button stayed disabled with no explanation. */}
          <input className="settings-input" value={typed} onChange={e => setTyped(e.target.value)}
                 placeholder="Type the organization name here" aria-label={`Type ${orgName} to confirm`} autoComplete="off" />
          <div className={`ic-small ${orgNameMatches(typed, orgName) ? 'ic-ok' : 'ic-muted'}`}>
            {orgNameMatches(typed, orgName) ? '✓ Matches — you can import now.' : <>Required: <b>{orgName}</b> (capitals and spacing don't matter)</>}
          </div>
        </div>
      )}
      {!gate.ok && <div className="ic-warn" data-testid="commit-blocked">{gate.reason}</div>}
      {gate.ok && !canPress && approveBlockedReason(batch, mode, typed, orgName, busy) && (
        <div className="ic-note" data-testid="approve-hint">{approveBlockedReason(batch, mode, typed, orgName, busy)}</div>
      )}
      {err && <div className="ic-error">{err}</div>}
      <div className="ic-actions">
        <button className="btn btn--danger" onClick={cancel} disabled={busy}>Cancel batch</button>
        <button className={`btn ${mode === 'stage_only' ? 'btn--secondary' : 'btn--primary'} btn--lg`}
                disabled={!canPress} onClick={go}>
          {busy ? 'Working…' : mode === 'stage_only' ? 'Keep staged (no import)' : `Import into ${orgName}`}
        </button>
      </div>
    </section>
  )
}

// ── 7. Results ──────────────────────────────────────────────────────────────

function ResultsStep({ batch, onLedger, onApprove }) {
  const r = batch.commit_report || {}
  if (batch.status === 'committing') {
    return (
      <section className="panel ic-panel">
        <div className="panel-header"><h2 className="panel-title">Step 7 — Importing…</h2>
          <span className="panel-count">{batch.progress_pct || 0}%</span></div>
        <div className="ic-progress"><div style={{ width: `${batch.progress_pct || 0}%` }} /></div>
        <p className="ic-muted">Running on the server. You can close this tab.</p>
      </section>
    )
  }
  return (
    <section className="panel ic-panel">
      <div className="panel-header"><h2 className="panel-title">Step 7 — Import Results</h2>
        <Pill tone={STATUS_TONE[batch.status]}>{STATUS_LABEL[batch.status] || batch.status}</Pill></div>
      <div className="ic-tiles">
        <Tile label="Contacts created" value={r.contacts_created} tone="blue" />
        <Tile label="Existing updated" value={r.contacts_updated} tone="blue" />
        <Tile label="Duplicates merged" value={r.file_duplicates_merged} tone="purple" />
        <Tile label="Leads created" value={r.leads_created} tone="green" />
        <Tile label="Needs enrichment preserved" value={r.needs_enrichment_preserved} tone="muted" />
        <Tile label="Skipped" value={r.skipped} tone="muted" />
        <Tile label="Held — plan capacity" value={r.held_for_capacity} tone="amber" />
        <Tile label="Failed" value={r.failed} tone="red" />
        <Tile label="Still staged" value={r.rows_still_staged ?? (batch.status === 'staged' ? batch.rows_staged : undefined)} tone="amber" />
      </div>
      {r.lead_not_activated && Object.keys(r.lead_not_activated).length > 0 && (
        <div className="ic-note">Opportunity rows not activated as leads:{' '}
          {Object.entries(r.lead_not_activated).map(([k, v]) => `${k.replace(/_/g, ' ')} (${fmt(v)})`).join(', ')}</div>
      )}
      {(r.errors || []).length > 0 && (
        <div className="ic-error">{r.errors.map(e => <div key={e.row}>Row {e.row}: {e.error}</div>)}</div>
      )}
      {batch.status === 'staged' && (
        <div className="ic-note">Everything in this batch is <b>staged</b> — nothing is in the CRM yet. Import it
          whenever you are ready from Approve Import.</div>
      )}
      <div className="ic-actions">
        {['staged', 'partially_committed'].includes(batch.status) && (
          <button className="btn btn--secondary" onClick={onApprove}>
            {batch.status === 'staged' ? '← Import staged rows' : '← Import remaining staged rows'}</button>
        )}
        <button className="btn btn--primary" onClick={onLedger}>Open in Import Ledger</button>
      </div>
    </section>
  )
}

// ── SCI: check & load (replaces Approve / Results for target="sci") ─────────
//
// The CRM commit never runs for an SCI file. This step asks SCI's own loader
// what it would do with the reviewed rows (a check that changes nothing), then
// either STAGES them (safe default: SCI keeps the rows, no family goes live)
// or LOADS them (SCI holds anyone who needs a look and adds the clean families
// to Leads). Neither texts, emails or enrolls anyone.

const NOTHING_SENT = 'No texts or emails are sent and nobody is put in a campaign. Families are only contacted after someone picks them on Choose & Send.'

function SciLoadStep({ batch, onReview, onDone }) {
  const [pv, setPv] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [campaign, setCampaign] = useState('')   // #385: the campaign THIS file belongs to
  useEffect(() => {
    let live = true
    setPv(null); setErr('')
    api.get(`/program/leads/import/batches/${batch.id}/sci-preview`)
      .then(d => { if (live) setPv(d) })
      .catch(e => { if (live) setErr(errorText(e)) })
    return () => { live = false }
  }, [batch.id, batch.status, tick])

  async function run(mode) {
    if (mode === 'load' && !window.confirm(
      `Load ${fmt(pv?.rows_to_sci || 0)} rows into SCI families?\n\n` +
      'Clean families are added to SCI Leads. Anyone with an unknown location, a possible duplicate or a do-not-contact status is held for review (a past "not interested" is shown, not held).\n\n' +
      NOTHING_SENT)) return
    setBusy(mode); setErr('')
    try {
      await api.post(`/program/leads/import/batches/${batch.id}/load`, { mode, campaign_family: campaign || null })
      onDone()
    } catch (e) { setErr(errorText(e)); setTick(t => t + 1) }
    finally { setBusy('') }
  }

  if (!pv) return <section className="panel ic-panel"><div className="empty-state">{err || 'Asking SCI what this file would do…'}</div></section>
  const st = pv.stage || {}
  const ex = pv.existing || {}
  const ids = pv.lead_id_problems || {}
  const staged = batch.commit_mode === 'program_stage'
  return (
    <section className="panel ic-panel" data-testid="sci-load-step">
      <div className="panel-header"><h2 className="panel-title">Step 5 — Check & Load into SCI</h2></div>
      <p className="ic-muted">This is SCI's own check of the reviewed rows. It has not changed anything.</p>
      {staged && <div className="ic-note">These rows are <b>staged</b> in SCI (kept, not live). Load them into SCI families when you are ready.</div>}

      <h3 className="ic-group">The file</h3>
      <div className="ic-tiles">
        <Tile label="Rows in the file" value={pv.rows_in_file} tone="blue" />
        <Tile label="Going to SCI" value={pv.rows_to_sci} tone="green" />
        <Tile label="Left out on Review" value={pv.excluded?.total} tone="muted" />
        <Tile label="People" value={st.contact_masters} tone="blue" sub="rows for the same person are linked" />
      </div>

      <h3 className="ic-group">Families</h3>
      <div className="ic-tiles">
        <Tile label="New Lead IDs" value={ex.new} tone="green" />
        <Tile label="Already in SCI" value={ex.already_in_sci} tone="blue" sub="updated, never duplicated" />
        <Tile label="Already in SCI Leads" value={ex.already_live} tone="blue" />
        <Tile label="Already held for review" value={ex.on_hold} tone="amber" />
      </div>

      <h3 className="ic-group">Locations and checks</h3>
      <div className="ic-tiles">
        <Tile label="Matched to a location" value={st.location_mapped} tone="green" />
        <Tile label="Location not recognized" value={st.location_review} tone="amber" sub="held for review" />
        <Tile label="Possible duplicates" value={st.duplicate_review_rows} tone="amber" sub="held for review" />
        <Tile label="Notes in name fields" value={st.data_note_rows} tone="amber" sub="held for review" />
        <Tile label="Phones that won't work" value={st.bad_phone_rows} tone="red" sub="can still be emailed" />
        <Tile label="No phone (email only)" value={st.no_phone_rows} tone="muted" />
      </div>

      {(pv.excluded?.by_reason || []).length > 0 && (
        <div className="ic-note">Left out because of what was decided on Review:{' '}
          {pv.excluded.by_reason.map(x => `${x.label} (${fmt(x.rows)})`).join(', ')}.
          <button className="btn btn--secondary btn--sm" style={{ marginLeft: 8 }} onClick={onReview}>Change on Review</button></div>
      )}
      {(ids.blank > 0 || (ids.repeated || []).length > 0) && (
        <div className="ic-note" data-testid="sci-lead-id-problems">
          {ids.blank > 0 && <div>{fmt(ids.blank)} row(s) have no Lead ID - SCI gives each one its own generated ID (marked as generated, never shown as a CRM ID).</div>}
          {(ids.repeated || []).length > 0 && <div>{fmt(ids.repeated.length)} Lead ID(s) appear more than once - every row is kept and the repeats are held for review.</div>}
        </div>
      )}
      <h3 className="ic-group">Campaign for this file</h3>
      <div className="ic-note" data-testid="sci-campaign-pick">
        <select value={campaign} onChange={e => setCampaign(e.target.value)} aria-label="Campaign for this file">
          <option value="">Use each row's own campaign column (no guessing)</option>
          {(pv.campaigns || []).map(c => <option key={c.key} value={c.key}>{c.name}{c.active ? '' : ' (off)'}</option>)}
        </select>
        <div className="ic-muted">Every row in this file is put in the campaign you pick. A row with no campaign is never sent anything.</div>
      </div>
      {(st.bad_phones || []).length > 0 && (
        <div className="ic-note">Phone numbers that won't work: {st.bad_phones.slice(0, 10).map(b => `row ${b.row} (${b.phone})`).join(', ')}
          {st.bad_phones.length > 10 ? '…' : ''}</div>
      )}
      {(pv.blockers || []).length > 0 && (
        <div className="ic-error" data-testid="sci-blockers">{pv.blockers.map(b => <div key={b}>{b}</div>)}</div>
      )}
      <div className="ic-note"><b>Nothing is sent.</b> {NOTHING_SENT}</div>
      {err && <div className="ic-error">{err}</div>}
      <div className="ic-actions">
        <button className="btn btn--secondary" onClick={onReview} disabled={!!busy}>← Back to Review</button>
        {!staged && (
          <button className="btn btn--primary btn--lg" disabled={!pv.can_load || !!busy} onClick={() => run('stage')}
                  title="SCI keeps the rows. No family is added to Leads yet.">
            {busy === 'stage' ? 'Staging…' : 'Stage only (safe)'}</button>
        )}
        <button className={`btn ${staged ? 'btn--primary' : 'btn--secondary'} btn--lg`} disabled={!pv.can_load || !!busy}
                onClick={() => run('load')}>
          {busy === 'load' ? 'Loading…' : 'Load into SCI families'}</button>
      </div>
    </section>
  )
}

function SciResultsStep({ batch, onLoad, onLeads }) {
  const r = batch.commit_report || {}
  const loaded = batch.commit_mode === 'program_load'
  const lc = r.line_check || {}
  if (batch.status === 'committing') {
    return <section className="panel ic-panel"><div className="empty-state">Loading into SCI…</div></section>
  }
  return (
    <section className="panel ic-panel" data-testid="sci-results">
      <div className="panel-header"><h2 className="panel-title">Step 6 — Results</h2>
        <Pill tone={loaded ? 'green' : 'amber'}>{loaded ? 'Loaded into SCI' : 'Staged in SCI'}</Pill></div>
      <div className="ic-tiles">
        <Tile label="Rows staged in SCI" value={r.staged} tone="blue" />
        <Tile label="New families in Leads" value={r.families_created} tone="green" />
        <Tile label="Newly held for review" value={r.held} tone="amber" />
        <Tile label="Location not recognized" value={r.location_review} tone="amber" />
        <Tile label="Possible duplicates" value={r.duplicate_review_rows} tone="amber" />
        <Tile label="Already in SCI Leads" value={r.already_live} tone="blue" />
        <Tile label="Left out on Review" value={r.excluded?.total} tone="muted" />
      </div>
      {loaded && (
        <div className="ic-note" data-testid="sci-line-check">Phone check (mobile or landline): {lineCheckText(lc)}</div>
      )}
      <div className="ic-note"><b>Nothing was sent.</b> {NOTHING_SENT}</div>
      {!loaded && <div className="ic-note">The rows are staged in SCI — no family has been added to Leads yet.</div>}
      <div className="ic-actions">
        {!loaded && <button className="btn btn--secondary" onClick={onLoad}>Load into SCI families →</button>}
        <button className="btn btn--primary" onClick={onLeads}>Back to SCI Leads</button>
      </div>
    </section>
  )
}

export { SMS_LABEL, EMAIL_LABEL }
