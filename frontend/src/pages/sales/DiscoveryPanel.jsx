/**
 * DISCOVERY — the seller's version.
 *
 * WHAT THIS REPLACES. Fourteen full-width textareas, every one of them empty,
 * stacked down the Opportunity page. A rep on a call does not write fourteen
 * paragraphs; they tick what is true. The old screen was the discovery TABLE
 * with a form drawn around it.
 *
 * Every control here is driven by `opp.discovery_schema`, which the server
 * sends — the browser does not know the question list, exactly as it does not
 * know the stage list. The answers go back as `structured`, the server renders
 * them into the same Text columns discovery has always used, and everything
 * downstream (the demo requirements carry-forward, provisioning, proposals)
 * keeps reading the prose it always read.
 *
 * NOTHING OLD IS THROWN AWAY. Long-form notes captured before this existed are
 * returned as `discovery.legacy` and shown under a collapsed "Previous /
 * detailed notes", read-only, per question. A field being re-answered with tick
 * boxes never silently eats the paragraph a rep typed months ago.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Card, Chip, dateTime } from './parts'

/* An answer the seller has actually given something to. Mirrors
   discovery_schema.is_answered on the server — the progress figure comes from
   the server, this is only for the per-question tick in the margin. */
function answered(val) {
  if (!val || typeof val !== 'object') return false
  if ((val.options || []).length) return true
  if ((val.other || '').trim()) return true
  if ((val.note || '').trim()) return true
  return Object.values(val.parts || {}).some(p =>
    typeof p === 'object' ? ((p.options || []).length || (p.other || '').trim())
                          : String(p || '').trim())
}

/** One tick-box, drawn as a chip rather than a checkbox: bigger tap target,
    and a row of them reads as a set of answers rather than a form. */
function Opt({ on, disabled, onClick, children }) {
  return (
    <button type="button" className={'sw-opt' + (on ? ' is-on' : '')}
            disabled={disabled} aria-pressed={on} onClick={onClick}>
      {children}
    </button>
  )
}

/** Multi- and single-select share everything except how many survive a click. */
function Choice({ spec, value, single, disabled, onChange }) {
  const chosen = value.options || []
  const has = v => chosen.indexOf(v) !== -1
  function toggle(v) {
    let next
    if (single) next = has(v) ? [] : [v]
    else next = has(v) ? chosen.filter(x => x !== v) : chosen.concat([v])
    const out = { ...value, options: next }
    // Dropping "Other" drops the text that only existed to explain it.
    if (next.indexOf('other') === -1) delete out.other
    onChange(out)
  }
  return (
    <>
      <div className="sw-opts">
        {(spec.options || []).map(o => (
          <Opt key={o.value} on={has(o.value)} disabled={disabled}
               onClick={() => toggle(o.value)}>{o.label}</Opt>
        ))}
      </div>
      {has('other') && (
        <input className="sw-input sw-mini-input" value={value.other || ''}
               placeholder="What is it?" disabled={disabled}
               onChange={e => onChange({ ...value, other: e.target.value })} />
      )}
    </>
  )
}

/** The named parts of a composite question, on one compact grid. */
function Parts({ spec, value, disabled, contactHints, onChange }) {
  const parts = value.parts || {}
  function setPart(k, v) {
    const next = { ...parts }
    if (v === null || v === '') delete next[k]
    else next[k] = v
    onChange({ ...value, parts: next })
  }
  return (
    <div className="sw-parts">
      {(spec.parts || []).map(p => {
        const raw = parts[p.key]
        if (p.control === 'single') {
          const cur = (raw && raw.options && raw.options[0]) || ''
          return (
            <div className="sw-dfield" key={p.key}>
              <label>{p.label}</label>
              <select className="sw-select" value={cur} disabled={disabled}
                      onChange={e => setPart(p.key, e.target.value
                        ? { options: [e.target.value],
                            ...(raw && raw.other ? { other: raw.other } : {}) }
                        : null)}>
                <option value="">—</option>
                {(p.options || []).map(o =>
                  <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              {cur === 'other' && (
                <input className="sw-input sw-mini-input"
                       value={(raw && raw.other) || ''} placeholder="Which one?"
                       disabled={disabled}
                       onChange={e => setPart(p.key,
                         { options: ['other'], other: e.target.value })} />
              )}
            </div>
          )
        }
        return (
          <div className="sw-dfield" key={p.key}>
            <label>{p.label}</label>
            <input
              className="sw-input"
              type={p.control === 'number' ? 'number' : 'text'}
              min={p.min} max={p.max}
              list={p.control === 'contact' ? 'sw-contact-hints' : undefined}
              value={raw == null ? '' : String(raw)}
              disabled={disabled}
              placeholder={p.control === 'contact' ? 'Name' : ''}
              onChange={e => setPart(p.key, e.target.value)}
            />
          </div>
        )
      })}
    </div>
  )
}

function Question({ spec, value, disabled, contactHints, onChange, done }) {
  const [noteOpen, setNoteOpen] = useState(!!(value.note || '').trim())
  const isNote = spec.control === 'note'

  return (
    <div className="sw-dq">
      <div className="sw-dq-h">
        <label>{spec.label}</label>
        {/* `done` is answered-in-ANY-form, matching what the server counts.
            A question captured as a paragraph before this panel existed is
            answered, and marking it "needed" would send a rep to re-ask a
            customer something they already told them. */}
        {spec.required && !done && <span className="sw-dq-req">needed</span>}
        {done && <span className="sw-dq-ok" aria-hidden="true">✓</span>}
      </div>

      {spec.control === 'multi' && (
        <Choice spec={spec} value={value} disabled={disabled} onChange={onChange} />
      )}
      {spec.control === 'single' && (
        <Choice spec={spec} value={value} single disabled={disabled} onChange={onChange} />
      )}
      {spec.control === 'composite' && (
        <Parts spec={spec} value={value} disabled={disabled}
               contactHints={contactHints} onChange={onChange} />
      )}

      {/* THE FREE-TEXT ESCAPE HATCH, AND ITS SIZE IS THE POINT. One line, and
          on questions that do not normally need it, not even on screen until
          somebody asks for it. That is the whole difference between "you may
          add a note" and a page of empty boxes. */}
      {isNote || noteOpen ? (
        <input
          className="sw-input sw-mini-input"
          value={value.note || ''}
          disabled={disabled}
          placeholder={spec.note_label || 'Note'}
          onChange={e => onChange({ ...value, note: e.target.value })}
        />
      ) : (
        <button type="button" className="sw-addnote" disabled={disabled}
                onClick={() => setNoteOpen(true)}>
          + {spec.note_label || 'note'}
        </button>
      )}
    </div>
  )
}

export default function DiscoveryPanel({ opp, onSave, saving, compact }) {
  const schema = opp.discovery_schema || []
  const disc = opp.discovery || {}
  const progress = disc.progress || { answered: 0, required: 0, missing: [], complete: false }
  const legacy = disc.legacy || {}

  const [vals, setVals] = useState({})
  const [dirty, setDirty] = useState(false)
  // What the server last gave us, so a save can send the questions that were
  // actually touched rather than all fourteen.
  const seedRef = useRef({})

  // Re-seed from the server's answer, never from what was half-typed before a
  // background refresh landed. `discovery.structured` changes identity on every
  // load, so this is keyed on the deal and on the stored answer itself.
  useEffect(() => {
    const seed = {}
    schema.forEach(f => { seed[f.key] = (disc.structured && disc.structured[f.key]) || {} })
    seedRef.current = seed
    setVals(seed)
    setDirty(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opp.id, JSON.stringify(disc.structured || {})])

  /* SEND WHAT CHANGED, NOT THE WHOLE FORM.
     A question the seller never opened has no opinion about its field, and a
     payload that says {} for it looks exactly like "clear this" — which is how
     a page of tick boxes would quietly erase a long-form note captured before
     any of this existed. (The server refuses that too; this keeps the request
     honest as well.) */
  function payload() {
    const out = {}
    Object.keys(vals).forEach(k => {
      const before = JSON.stringify(seedRef.current[k] || {})
      const now = JSON.stringify(vals[k] || {})
      if (now !== before || answered(vals[k])) out[k] = vals[k] || {}
    })
    return out
  }

  const contactHints = useMemo(
    () => [opp.contact_name].filter(Boolean), [opp.contact_name])

  const groups = useMemo(() => {
    const out = []
    schema.forEach(f => {
      const g = out.find(x => x.name === f.group)
      if (g) g.fields.push(f)
      else out.push({ name: f.group, fields: [f] })
    })
    return out
  }, [schema])

  function set(key, v) {
    setVals(s => ({ ...s, [key]: v }))
    setDirty(true)
  }

  const completed = disc.completed_at
  const pct = progress.required
    ? Math.round((progress.answered / progress.required) * 100) : 0
  const labels = {}
  schema.forEach(f => { labels[f.key] = f.label })
  const legacyKeys = Object.keys(legacy).filter(k => (legacy[k] || '').trim())

  // The stored sentence per question — what provisioning, the demo brief and
  // the proposal will actually read. Shown so a rep can check it rather than
  // trust it, and never as an editable box competing with the controls above.
  const storedKeys = (opp.discovery_fields || [])
    .map(f => f.key).filter(k => (disc[k] || '').toString().trim())

  return (
    <Card
      title="DISCOVERY"
      sub={completed
        ? 'Completed ' + dateTime(completed)
          + (disc.completed_by_name ? ' by ' + disc.completed_by_name : '')
        : 'Tick what is true — the answers build the demo brief'}
      right={
        <Chip tone={progress.complete ? 'green' : (progress.answered ? 'amber' : null)}>
          {progress.answered}/{progress.required}
        </Chip>
      }
    >
      {/* One list for every contact-shaped part on the panel. Declared here
          rather than beside each input, because two inputs sharing an id is
          two elements claiming to be the same list. */}
      {contactHints.length > 0 && (
        <datalist id="sw-contact-hints">
          {contactHints.map(c => <option value={c} key={c} />)}
        </datalist>
      )}

      <div className="sw-prog">
        <div className="sw-prog-bar">
          <div className="sw-prog-fill" style={{ width: pct + '%' }} />
        </div>
        <span className="sw-prog-t">
          {progress.complete
            ? 'Everything needed is captured'
            : (progress.missing || []).map(m => m.label).join(' · ') || 'Nothing captured yet'}
        </span>
      </div>

      {!compact && groups.map(g => (
        <div className="sw-dgroup" key={g.name}>
          <div className="sw-dgroup-h">{g.name}</div>
          {g.fields.map(f => (
            <Question key={f.key} spec={f} value={vals[f.key] || {}}
                      disabled={saving} contactHints={contactHints}
                      done={answered(vals[f.key])
                            || !!String(disc[f.key] || '').trim()}
                      onChange={v => set(f.key, v)} />
          ))}
        </div>
      ))}

      {!compact && legacyKeys.length > 0 && (
        <details className="sw-disclose">
          <summary>Previous / detailed notes ({legacyKeys.length})</summary>
          <p className="sw-subtle" style={{ margin: '8px 0 0' }}>
            Captured before discovery was structured. Kept exactly as it was
            written — answering a question above never overwrites it.
          </p>
          {legacyKeys.map(k => (
            <div className="sw-legacy" key={k}>
              <b>{labels[k] || k}</b>
              <pre>{legacy[k]}</pre>
            </div>
          ))}
        </details>
      )}

      {!compact && storedKeys.length > 0 && (
        <details className="sw-disclose">
          <summary>What gets handed to the builder</summary>
          <p className="sw-subtle" style={{ margin: '8px 0 0' }}>
            The discovery text the demo brief, the proposal and onboarding read.
            Built from the answers above.
          </p>
          {storedKeys.map(k => (
            <div className="sw-legacy" key={k}>
              <b>{labels[k] || k}</b>
              <pre>{disc[k]}</pre>
            </div>
          ))}
        </details>
      )}

      {!compact && (
        <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
          {/* SAVING IS PROGRESSIVE. A rep half way through a call must be able
              to keep what they have without declaring discovery finished. */}
          <button className="sw-btn" disabled={!dirty || saving}
                  onClick={() => onSave(payload(), false)}>
            {saving ? 'Saving…' : 'Save progress'}
          </button>
          {!completed && (
            <button className="sw-btn sw-primary" disabled={saving}
                    onClick={() => onSave(payload(), true)}>
              Save &amp; mark complete
            </button>
          )}
        </div>
      )}
    </Card>
  )
}
