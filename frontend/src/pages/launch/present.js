/**
 * The Launch Engine's presentation layer — one place that turns stored answers
 * into something a person reads.
 *
 * ===========================================================================
 * THE BUG THIS EXISTS TO KILL
 * ===========================================================================
 *
 * The staff review screen rendered the intake like this:
 *
 *     {Object.entries(step.answers).map(([k, v]) => <span>{k}: {String(v)}</span>)}
 *
 * which produces, on a real customer:
 *
 *     sigAffirm: true
 *     cpSandbox: not_requested
 *     hostProvider: SiteGround
 *
 * The first is a database column shown to a human. The second is an enum code.
 * Only the third reads as English, and it does so by accident.
 *
 * THE LABELS ALREADY EXISTED. `GET /launch/config` returns the full step
 * schema — every field with a `label`, a `kind`, and for selects a set of
 * `options` each carrying its own label. `sigAffirm`'s label has always been
 * "I confirm this information is accurate". Nothing needed inventing; the
 * screen simply never asked the server what things are called.
 *
 * So this module is an adapter, not a dictionary. It holds no copy of the
 * schema, which means a field renamed on the server renames everywhere with no
 * frontend release — and a field ADDED on the server cannot appear as a raw key
 * here, because unknown keys go through `humanise()` rather than through.
 *
 * ===========================================================================
 * SECRETS
 * ===========================================================================
 *
 * A secret has no read path out of the backend at all: `read_step` returns
 * `secrets_set`, a list of KEYS, and never a value. This module renders those
 * as "Stored securely" and there is deliberately no branch that could render
 * anything else — a formatter that could print a credential given the right
 * input is a formatter somebody will eventually give the right input.
 */

// ── the two progress concepts, named once ───────────────────────────────────
//
// THESE ARE NOT THE SAME NUMBER AND THE UI MUST NEVER IMPLY THEY ARE.
// A customer at 8/8 sections and 0/8 milestones is entirely normal: they have
// finished their homework and the build has not started. Showing "100%" beside
// "0%" without saying which is which is what made the platform look broken.
export const PROGRESS = {
  intake: {
    key: 'intake',
    label: 'Customer intake',
    short: 'Intake',
    unit: 'sections',
    // Shown next to the figure wherever both appear together.
    meaning: 'How much of the onboarding questionnaire the customer has completed.',
  },
  implementation: {
    key: 'implementation',
    label: 'Implementation',
    short: 'Build',
    unit: 'milestones',
    meaning: 'How much of the internal build and launch work your team has completed.',
  },
}

// ── intake status model, derived from what the backend already reports ──────
export const INTAKE_STATES = {
  not_started: {
    key: 'not_started', label: 'Not started', tone: 'neutral',
    staff: 'Nobody has opened this yet.',
    customer: 'You have not started yet.',
  },
  in_progress: {
    key: 'in_progress', label: 'In progress', tone: 'warning',
    staff: 'The customer is part-way through.',
    customer: 'You are part-way through. Pick up where you left off.',
  },
  submitted: {
    key: 'submitted', label: 'Submitted — needs review', tone: 'info',
    staff: 'Waiting on someone to review it.',
    customer: 'Submitted. Your implementation team is reviewing it.',
  },
  reviewed: {
    key: 'reviewed', label: 'Reviewed', tone: 'positive',
    staff: 'Reviewed. The customer can edit again.',
    customer: 'Reviewed by your team. You can make changes again if you need to.',
  },
}

export function intakeState(key) {
  return INTAKE_STATES[key] || INTAKE_STATES.not_started
}

/**
 * A last-resort label for a stored key with no schema entry.
 *
 * This is the safety net that makes "no raw keys" a property rather than a
 * hope: a field added to the database and not yet to the schema still reads as
 * "Host provider" rather than as `hostProvider`.
 */
export function humanise(key) {
  if (!key) return ''
  return String(key)
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/^./, c => c.toUpperCase())
}

/** Build a lookup from the config payload so every render is O(1). */
export function buildSchemaIndex(config) {
  const steps = (config && config.steps) || []
  const byKey = {}
  for (const step of steps) {
    const fields = {}
    for (const f of step.fields || []) fields[f.key] = f
    byKey[step.key] = { ...step, fieldsByKey: fields }
  }
  return { steps, byKey }
}

const EMPTY = ['', null, undefined]

/**
 * One stored value, in business language.
 *
 * Returns null when there is nothing worth showing, so callers can drop the
 * row entirely rather than printing "—" forty times.
 */
export function formatValue(field, value) {
  const kind = (field && field.kind) || 'text'

  if (kind === 'checkbox') {
    // "sigAffirm: true" becomes "Yes" under the question it answers. `false`
    // is a real answer to a confirmation and is shown, not hidden.
    if (value === true) return 'Yes'
    if (value === false) return 'No'
    return null
  }

  if (EMPTY.includes(value)) return null

  if (kind === 'select') {
    const opts = (field && field.options) || []
    const hit = opts.find(o => String(o.value) === String(value))
    // An enum code with no matching option is still not shown raw.
    return hit ? hit.label : humanise(value)
  }

  if (kind === 'date') {
    const d = new Date(value)
    if (!Number.isNaN(d.getTime())) {
      return d.toLocaleDateString(undefined,
        { year: 'numeric', month: 'long', day: 'numeric' })
    }
    return String(value)
  }

  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (Array.isArray(value)) {
    const parts = value.filter(v => !EMPTY.includes(v)).map(String)
    return parts.length ? parts.join(', ') : null
  }
  if (typeof value === 'object') {
    // Never JSON.stringify into a screen. If a value is structured and has no
    // formatter, say it exists rather than showing its shape.
    return 'Provided'
  }

  const s = String(value).trim()
  return s === '' ? null : s
}

/** The one sentence shown in place of a credential, everywhere. */
export const SECRET_DISPLAY = 'Stored securely'
export const SECRET_NOTE = 'Encrypted when saved. It is never displayed again, to anyone.'

/**
 * One step, ready to render: ordered rows of {label, value}, with secrets
 * marked and unanswered fields dropped.
 *
 * ORDER FOLLOWS THE SCHEMA, not the JSON column. The customer answered these
 * in a deliberate order and a reviewer should read them in the same one;
 * `Object.entries` order is insertion order, which is the order somebody
 * happened to type in.
 */
export function presentStep(schemaStep, stepData) {
  const answers = (stepData && stepData.answers) || {}
  const secretsSet = (stepData && stepData.secrets_set) || []
  const fields = (schemaStep && schemaStep.fields) || []
  const rows = []
  const seen = new Set()

  for (const f of fields) {
    seen.add(f.key)
    if (f.kind === 'secret') {
      // Presence only — see the header. There is no value to print.
      if (secretsSet.includes(f.key)) {
        rows.push({ key: f.key, label: f.label, value: SECRET_DISPLAY,
                    secure: true, kind: 'secret' })
      }
      continue
    }
    const shown = formatValue(f, answers[f.key])
    if (shown === null) continue
    rows.push({ key: f.key, label: f.label, value: shown, kind: f.kind,
                long: f.kind === 'textarea' })
  }

  // Anything stored that the schema no longer describes. Shown, because hiding
  // a customer's answer is worse than an imperfect label, but humanised.
  for (const [k, v] of Object.entries(answers)) {
    if (seen.has(k)) continue
    const shown = formatValue({ kind: 'text' }, v)
    if (shown === null) continue
    rows.push({ key: k, label: humanise(k), value: shown, kind: 'text',
                unknownField: true })
  }

  return {
    key: schemaStep ? schemaStep.key : (stepData && stepData.key),
    label: (schemaStep && schemaStep.label) || humanise(stepData && stepData.key),
    title: (schemaStep && schemaStep.title) || null,
    blurb: (schemaStep && schemaStep.blurb) || null,
    pct: (stepData && stepData.pct) || 0,
    status: (stepData && stepData.status) || 'not_started',
    missing: (stepData && stepData.missing) || [],
    rows,
    answeredCount: rows.length,
  }
}

/**
 * Every step of a staff detail payload, in schema order.
 *
 * `answers` on that payload is keyed by step, and object key order is not a
 * promise — driving the render from the schema is what keeps Company first.
 */
export function presentAllSteps(schemaIndex, answersByStep) {
  const out = []
  for (const step of schemaIndex.steps || []) {
    out.push(presentStep(step, (answersByStep || {})[step.key]))
  }
  return out
}

/** Percent → a short, honest phrase. Never a bare number without its noun. */
export function progressPhrase(concept, done, total, pct) {
  const c = PROGRESS[concept] || PROGRESS.intake
  if (total === null || total === undefined) return `${pct}% ${c.unit} complete`
  return `${done} of ${total} ${c.unit} · ${pct}%`
}


/* ── history ────────────────────────────────────────────────────────────────
   The implementation history is read from the audit log, whose `action` is a
   machine code. The screen used to render it with the underscores swapped for
   spaces, which produced lines like "implementation milestone changed — Mike"
   and "customer admin invite revoked — Mike": readable in the sense that they
   are words, unreadable in the sense that nobody can tell what happened.

   Unknown codes still degrade to the old behaviour rather than being hidden,
   because a history that silently drops events is worse than one that shows a
   code. Anything added here should read as a sentence a person would say. */

const EVENT_LABELS = {
  // provisioning
  customer_provisioned: 'Customer provisioned',
  customer_created: 'Customer created',
  customer_location_created: 'Customer location added',
  customer_user_added: 'Customer user added',
  customer_admin_created: 'Customer administrator created',
  customer_admin_invited: 'Customer administrator invited',
  customer_admin_activated: 'Customer administrator activated their account',
  customer_admin_invite_revoked: 'Customer administrator invite revoked',
  customer_activated: 'Customer activated',
  customer_deactivated: 'Customer deactivated',

  // the intake
  launch_intake_started: 'Intake started',
  launch_intake_submitted: 'Intake submitted by the customer',
  launch_intake_reviewed: 'Staff reviewed the intake',
  launch_intake_reopened: 'Intake reopened for edits',

  // the build
  implementation_owner_assigned: 'Implementation owner changed',
  implementation_milestone_added: 'Milestone added',
  implementation_milestone_changed: 'Milestone updated',
  implementation_status_changed: 'Implementation status changed',
  billing_configuration_changed: 'Billing details changed',
  customer_marked_ready: 'Customer marked ready for launch',
  customer_marked_live: 'Customer marked live',
}

/**
 * One history row as a sentence.
 *
 * `details` carries the milestone label for milestone events, so "Milestone
 * updated" becomes "Milestone updated — Kickoff call" without the reader
 * having to open anything.
 */
export function eventPhrase(entry) {
  const e = entry || {}
  const base = EVENT_LABELS[e.action]
    || String(e.action || 'Activity').replace(/[_.]/g, ' ')
  const d = e.details || {}
  const subject = d.label || d.milestone_label || d.key || null
  if (!subject) return base
  if (e.action === 'implementation_milestone_changed'
      || e.action === 'implementation_milestone_added') {
    const status = (e.after && e.after.status) || null
    return status ? `${base} — ${subject} → ${humanise(status)}`
                  : `${base} — ${subject}`
  }
  return `${base} — ${subject}`
}
