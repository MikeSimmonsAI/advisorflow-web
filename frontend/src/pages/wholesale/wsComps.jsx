/* The comparable-sales workspace.
 *
 * THE DISTINCTION THIS FILE EXISTS TO MAKE. Phase 1 gave a comp a checkbox and
 * a button labelled "Remove", and nobody could tell which of the two meanings
 * of that word was on offer: take it out of the ARV, or delete the record a
 * person spent ten minutes typing. They are different acts with different
 * consequences and they now have different controls, different colours and
 * different words:
 *
 *   INCLUDE IN ARV / EXCLUDE FROM ARV   changes the arithmetic. Reversible in
 *                                       one click. Never asks.
 *   DELETE COMP                          destroys the record. Asks first, by
 *                                       name, and says it cannot be undone.
 *
 * Every comp is editable in place with an explicit SAVE and CANCEL, because a
 * comp is typed from a county record and typed figures are wrong the first
 * time. Save is disabled until something actually changed.
 *
 * NOTHING HERE INVENTS A COMP. No provider is connected, so a row appears only
 * because somebody entered it, and the panel says so rather than leaving a
 * user to assume the list came from an MLS feed. Every derived figure — the
 * medians, the averages, the spread — is computed on the server from the rows
 * that are actually included, and reads as an em dash when it cannot be
 * computed at all.
 */
import { useMemo, useState } from 'react'
import { api } from '../../api/client'
import { fmtMoney, fmtNum, fmtDate, Note, Why } from './wsShared'
import { AuthImage, UploadZone, ConfirmDelete, openFile } from './wsFiles'

/* Every editable field on a comp, once. The add form and the edit form are the
 * same list, so a field can never exist in one and be missing from the other. */
const FIELDS = [
  ['street_address', 'Address', 'text'],
  ['city', 'City', 'text'],
  ['state', 'State', 'text'],
  ['zip_code', 'ZIP', 'text'],
  ['sale_price', 'Sale price', 'number'],
  ['sale_date', 'Sale date', 'date'],
  ['square_feet', 'Sq ft', 'number'],
  ['bedrooms', 'Beds', 'number'],
  ['bathrooms', 'Baths', 'number'],
  ['year_built', 'Year built', 'number'],
  ['distance_miles', 'Distance (mi)', 'number'],
  ['property_type', 'Property type', 'text'],
]

const NUMERIC = new Set(['sale_price', 'square_feet', 'bedrooms', 'bathrooms',
                         'distance_miles', 'year_built'])

const EMPTY = FIELDS.reduce((acc, [key]) => ({ ...acc, [key]: '' }), { notes: '' })

function toDraft(comp) {
  const draft = { notes: comp.notes ?? '' }
  FIELDS.forEach(([key]) => { draft[key] = comp[key] ?? '' })
  return draft
}

/* An emptied field must clear the stored value, so it sends null rather than
 * being skipped. Skipping is how a user deletes a wrong sale price, saves, and
 * finds it still there. */
function editPayload(draft) {
  const out = {}
  Object.entries(draft).forEach(([key, value]) => {
    const text = typeof value === 'string' ? value.trim() : value
    if (text === '' || text === null || text === undefined) { out[key] = null; return }
    out[key] = NUMERIC.has(key) ? Number(text) : text
  })
  return out
}

/* On create, an untouched field is simply absent. */
function addPayload(draft) {
  const out = {}
  Object.entries(draft).forEach(([key, value]) => {
    const text = typeof value === 'string' ? value.trim() : value
    if (text === '' || text === null || text === undefined) return
    out[key] = NUMERIC.has(key) ? Number(text) : text
  })
  return out
}

/* MANUAL / ESTIMATED / PROVIDER, in those words. `arv_source` is what the
 * record says and `arv_method` is how it got there; neither is shown raw. */
function arvProvenance(deal) {
  if (deal.arv === null || deal.arv === undefined) return 'not set'
  const method = String(deal.arv_method || '')
  if (method.startsWith('comps:')) {
    return method.endsWith('median_psf')
      ? 'ESTIMATED · median $/sqft from comps'
      : 'ESTIMATED · median comp sale price'
  }
  const source = String(deal.arv_source || '').toLowerCase()
  if (source === 'manual') return 'MANUAL · typed by a person'
  if (source === 'provider') return 'PROVIDER'
  if (source) return source.toUpperCase()
  return 'source not recorded'
}


function money(value) {
  if (value === null || value === undefined) return '—'
  const n = Number(value)
  if (Number.isNaN(n)) return '—'
  return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2,
                                             maximumFractionDigits: 2 })
}


/* ── The comparison ────────────────────────────────────────────────────────
 *
 * "Clearly show: MEDIAN $/SQ FT, AVERAGE $/SQ FT, ESTIMATED ARV." Median and
 * average sit side by side deliberately: when they disagree, one comp is
 * dragging the set, and the per-row distance from the median in the table
 * below says which one. */
function Comparison({ stats, arvCalc, deal }) {
  if (!stats) return null
  const psf = stats.median_price_per_sqft
  const avg = stats.average_price_per_sqft
  const spread = (stats.low_price_per_sqft !== null
                  && stats.high_price_per_sqft !== null)
    ? `${money(stats.low_price_per_sqft)} – ${money(stats.high_price_per_sqft)}`
    : '—'

  return (
    <div className="ws-compare">
      <div className="ws-compare__subject">
        <div className="ws-compare__head">This property</div>
        <div className="ws-compare__big">
          {fmtNum(stats.subject_square_feet)}<span className="ws-compare__unit">sq ft</span>
        </div>
        <dl className="ws-compare__list">
          <div>
            <dt>Working ARV</dt>
            <dd>
              {fmtMoney(deal.arv)}
              <span className="ws-source">{arvProvenance(deal)}</span>
            </dd>
          </div>
          <div>
            <dt>At that ARV</dt>
            <dd>
              {money(stats.subject_price_per_sqft)}
              <span className="ws-compare__unit">/sq ft</span>
            </dd>
          </div>
        </dl>
        {stats.subject_square_feet === null ? (
          <p className="ws-compare__note">
            No square footage on file for this property, so it cannot be compared
            per square foot. Add it on the Overview tab.
          </p>
        ) : null}
      </div>

      <div className="ws-compare__set">
        <div className="ws-compare__head">
          The comp set
          <span className="ws-compare__count">
            {stats.included_count} in the ARV
            {stats.excluded_count ? ` · ${stats.excluded_count} excluded` : ''}
          </span>
        </div>
        <div className="ws-compare__figures">
          <div className="ws-compare__figure is-lead">
            <span className="ws-k">Median $/sq ft</span>
            <strong>{money(psf)}</strong>
            <span className="ws-compare__sub">
              {stats.sized_count
                ? `over ${stats.sized_count} comp${stats.sized_count === 1 ? '' : 's'} with a size`
                : 'no included comp has a square footage'}
            </span>
          </div>
          <div className="ws-compare__figure">
            <span className="ws-k">Average $/sq ft</span>
            <strong>{money(avg)}</strong>
            <span className="ws-compare__sub">
              {psf !== null && avg !== null && Math.abs(avg - psf) > psf * 0.1
                ? 'well apart from the median — check the outliers below'
                : 'close to the median'}
            </span>
          </div>
          <div className="ws-compare__figure">
            <span className="ws-k">Range</span>
            <strong className="is-small">{spread}</strong>
            <span className="ws-compare__sub">low to high, included comps</span>
          </div>
          <div className="ws-compare__figure">
            <span className="ws-k">Median sale price</span>
            <strong>{fmtMoney(stats.median_sale_price)}</strong>
            <span className="ws-compare__sub">
              average {fmtMoney(stats.average_sale_price)}
            </span>
          </div>
        </div>

        <div className="ws-compare__arv">
          <span className="ws-k">Estimated ARV from these comps</span>
          <strong>{fmtMoney(arvCalc?.arv)}</strong>
          <span className="ws-source">
            {arvCalc?.arv === null || arvCalc?.arv === undefined
              ? 'nothing to compute from'
              : (arvCalc.method === 'comps:median_psf'
                  ? 'ESTIMATED · median $/sqft × subject sqft'
                  : 'ESTIMATED · median sale price, no size adjustment')}
          </span>
        </div>
      </div>
    </div>
  )
}


/* ── One comp ──────────────────────────────────────────────────────────── */

function CompRow({ comp, vsMedian, capability, busy, act, onEdit, editing,
                  onCancel, onDeleted }) {
  const [draft, setDraft] = useState(() => toDraft(comp))
  const [confirming, setConfirming] = useState(false)
  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(toDraft(comp)),
    [draft, comp])

  const inArv = !!comp.included
  const name = comp.street_address || 'this comp'

  if (editing) {
    return (
      <tr className="ws-comp is-editing">
        <td colSpan={9}>
          <div className="ws-comp__editor">
            <div className="ws-comp__editor-head">
              Editing <strong>{name}</strong>
            </div>
            <div className="ws-grid">
              {FIELDS.map(([key, label, type]) => (
                <div className="ws-field" key={key}>
                  <label htmlFor={`comp-${comp.id}-${key}`}>{label}</label>
                  <input id={`comp-${comp.id}-${key}`} className="ws-input"
                         type={type === 'date' ? 'date' : 'text'}
                         inputMode={type === 'number' ? 'decimal' : undefined}
                         value={draft[key] ?? ''}
                         onChange={(e) => setDraft(
                           (d) => ({ ...d, [key]: e.target.value }))} />
                </div>
              ))}
            </div>
            <div className="ws-field" style={{ marginTop: 10 }}>
              <label htmlFor={`comp-${comp.id}-notes`}>Notes</label>
              <textarea id={`comp-${comp.id}-notes`} className="ws-input" rows={2}
                        value={draft.notes ?? ''}
                        onChange={(e) => setDraft(
                          (d) => ({ ...d, notes: e.target.value }))} />
            </div>

            <div className="ws-comp__photo-edit">
              <span className="ws-k">Photo</span>
              {comp.photo ? (
                <div className="ws-comp__photo-current">
                  <AuthImage path={comp.photo.url} alt={name}
                             className="ws-comp__thumb-lg" />
                  <span className="ws-muted">
                    Uploading another replaces this one.
                  </span>
                </div>
              ) : null}
              <UploadZone capability={capability} accept="image/*"
                          label={comp.photo ? 'Replace photo' : 'Upload photo'}
                          busy={busy}
                          onFiles={(files) => act(async () => {
                            const fd = new FormData()
                            fd.append('file', files[0])
                            await api.upload(`/wholesale/comps/${comp.id}/photo`, fd)
                          }, 'Comp photo uploaded.')} />
            </div>

            <div className="ws-actions" style={{ marginTop: 12 }}>
              <button className="btn btn--primary btn--sm"
                      disabled={busy || !dirty}
                      onClick={async () => {
                        // Closes only on success. A failed save keeps the form
                        // open with the typing still in it.
                        if (await act(
                          () => api.patch(`/wholesale/comps/${comp.id}`,
                                          editPayload(draft)),
                          'Comp saved.')) onCancel()
                      }}>
                Save comp
              </button>
              <button className="btn btn--secondary btn--sm" disabled={busy}
                      onClick={() => { setDraft(toDraft(comp)); onCancel() }}>
                Cancel
              </button>
            </div>
          </div>
        </td>
      </tr>
    )
  }

  return (
    <>
      <tr className={`ws-comp ${inArv ? '' : 'is-excluded'}`}>
        <td className="ws-comp__thumb-cell">
          {comp.photo ? (
            <button type="button" className="ws-comp__thumb-btn"
                    title="Open full size"
                    onClick={() => openFile(comp.photo.url)}>
              <AuthImage path={comp.photo.url} alt={name} className="ws-comp__thumb" />
            </button>
          ) : <span className="ws-comp__thumb is-empty">No photo</span>}
        </td>
        <td>
          <div className="ws-comp__addr">{comp.street_address || '(no address)'}</div>
          <div className="ws-comp__sub">
            {[comp.city, comp.state, comp.zip_code].filter(Boolean).join(', ') || '—'}
            {comp.year_built ? ` · built ${comp.year_built}` : ''}
          </div>
        </td>
        {/* Distance decides whether a comp is a comp at all, so it is a
            column you can scan rather than a line of small print. A comp with
            no distance on file says so instead of reading as nearby. */}
        <td className="ws-num ws-comp__dist">
          {comp.distance_miles === null || comp.distance_miles === undefined
            ? <span className="ws-muted">not set</span>
            : `${Number(comp.distance_miles).toFixed(1)} mi`}
        </td>
        <td>{fmtDate(comp.sale_date)}</td>
        <td className="ws-num">{fmtMoney(comp.sale_price)}</td>
        <td className="ws-num">{fmtNum(comp.square_feet)}</td>
        <td className="ws-num">
          {money(comp.price_per_sqft)}
          {vsMedian === null || vsMedian === undefined
            || Math.abs(vsMedian) < 1 ? null : (
            <span className={`ws-delta ${vsMedian > 0 ? 'is-over' : 'is-under'}`}>
              {vsMedian > 0 ? '+' : ''}{Math.round(vsMedian)}%
            </span>
          )}
        </td>
        <td className="ws-num">
          {comp.bedrooms ?? '—'} / {comp.bathrooms ?? '—'}
        </td>
        <td>
          <span className={`ws-pill ${inArv ? 'is-in-arv' : 'is-muted'}`}>
            {inArv ? 'In ARV' : 'Excluded'}
          </span>
          <span className="ws-source">{String(comp.source || 'manual').toUpperCase()}</span>
        </td>
        <td>
          <span className="ws-actions ws-actions--wrap">
            {/* Arithmetic, not existence. One click, no confirmation, and the
                word says exactly which of the two it is. */}
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => act(
                      () => api.patch(`/wholesale/comps/${comp.id}`,
                                      { included: !inArv }),
                      inArv ? 'Comp excluded from the ARV.'
                            : 'Comp included in the ARV.')}>
              {inArv ? 'Exclude from ARV' : 'Include in ARV'}
            </button>
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={onEdit}>Edit</button>
            <button className="btn btn--secondary btn--sm ws-btn-delete"
                    disabled={busy}
                    onClick={() => setConfirming(true)}>Delete comp</button>
          </span>
        </td>
      </tr>
      {confirming ? (
        <tr className="ws-comp__confirm-row">
          <td colSpan={9}>
            <ConfirmDelete busy={busy} what={`the comp at ${name}`}
                           onCancel={() => setConfirming(false)}
                           onConfirm={async () => {
                             if (await act(
                               () => api.delete(`/wholesale/comps/${comp.id}`),
                               'Comp deleted.')) {
                               setConfirming(false)
                               if (onDeleted) onDeleted()
                             }
                           }} />
          </td>
        </tr>
      ) : null}
    </>
  )
}


/* ── The workspace ─────────────────────────────────────────────────────── */

export function CompsWorkspace({ dealId, deal, comps, stats, arvCalc,
                                capability, act, busy }) {
  const [editing, setEditing] = useState(null)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState(EMPTY)

  const deltas = useMemo(() => {
    const map = {}
    ;(stats?.rows || []).forEach((r) => { map[r.id] = r.vs_median_pct })
    return map
  }, [stats])

  const canAdd = Object.values(draft).some(
    (v) => String(v ?? '').trim() !== '')

  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Comparable sales ({comps.length})</span>
        <span className="ws-actions">
          <button className="btn btn--primary btn--sm"
                  onClick={() => { setAdding((a) => !a); setEditing(null) }}>
            {adding ? 'Close' : 'Add a comp'}
          </button>
        </span>
      </div>
      <Note>
        <strong>Exclude from ARV</strong> takes a comp out of the arithmetic and
        leaves the record alone. <strong>Delete comp</strong> destroys it.
      </Note>
      <Why label="Where these comps came from">
        <p className="ws-comp__sub">
          No comps provider is connected to this deployment, so every row here
          was typed by somebody in this organization. Nothing in this module
          invents a comp or fetches one from a source it cannot name.
        </p>
      </Why>

      <Comparison stats={stats} arvCalc={arvCalc} deal={deal} />

      {arvCalc?.warnings?.length ? (
        <div className="ws-warn" style={{ marginTop: 12 }}>
          <ul className="ws-warn__list">
            {arvCalc.warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      ) : null}

      {adding ? (
        <div className="ws-comp__editor is-new">
          <div className="ws-comp__editor-head">New comp</div>
          <div className="ws-grid">
            {FIELDS.map(([key, label, type]) => (
              <div className="ws-field" key={key}>
                <label htmlFor={`newcomp-${key}`}>{label}</label>
                <input id={`newcomp-${key}`} className="ws-input"
                       type={type === 'date' ? 'date' : 'text'}
                       inputMode={type === 'number' ? 'decimal' : undefined}
                       value={draft[key] ?? ''}
                       onChange={(e) => setDraft(
                         (d) => ({ ...d, [key]: e.target.value }))} />
              </div>
            ))}
          </div>
          <div className="ws-field" style={{ marginTop: 10 }}>
            <label htmlFor="newcomp-notes">Notes</label>
            <textarea id="newcomp-notes" className="ws-input" rows={2}
                      value={draft.notes ?? ''}
                      onChange={(e) => setDraft(
                        (d) => ({ ...d, notes: e.target.value }))} />
          </div>
          <p className="ws-panel-note">
            A photo can be attached once the comp is saved.
          </p>
          <div className="ws-actions" style={{ marginTop: 10 }}>
            <button className="btn btn--primary btn--sm" disabled={busy || !canAdd}
                    onClick={async () => {
                      if (await act(
                        () => api.post(`/wholesale/deals/${dealId}/comps`,
                                       addPayload(draft)),
                        'Comp added.')) { setDraft(EMPTY); setAdding(false) }
                    }}>
              Save comp
            </button>
            <button className="btn btn--secondary btn--sm" disabled={busy}
                    onClick={() => { setDraft(EMPTY); setAdding(false) }}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {comps.length ? (
        <div className="ws-scroll" style={{ marginTop: 14 }}>
          <table className="ws-table ws-comps">
            <thead>
              <tr>
                <th>Photo</th><th>Address</th>
                <th className="ws-num">Distance</th><th>Sold</th>
                <th className="ws-num">Sale price</th><th className="ws-num">Sq ft</th>
                <th className="ws-num">$/sq ft</th><th className="ws-num">Bd / Ba</th>
                <th>In ARV</th><th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {comps.map((c) => (
                <CompRow key={c.id} comp={c} vsMedian={deltas[c.id]}
                         capability={capability} busy={busy} act={act}
                         editing={editing === c.id}
                         onEdit={() => { setEditing(c.id); setAdding(false) }}
                         onCancel={() => setEditing(null)}
                         onDeleted={() => setEditing(null)} />
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="ws-panel-note" style={{ marginTop: 14 }}>
          No comps yet. Until at least one is entered there is no comp-derived
          ARV — the figure stays whatever a person typed, labelled MANUAL.
        </p>
      )}
    </div>
  )
}
