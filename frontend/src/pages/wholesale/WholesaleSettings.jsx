/* Wholesale settings — everything the module will not decide for you.
 *
 * The investor percentage, the fee, the qualification bands, the pipeline, the
 * approval gates and the automation switches are all here because none of them
 * is a platform truth. The "70% rule" in particular is a number this customer
 * sets; the code does not know it.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import '../../styles/shared.css'
import './wholesale.css'
import { errText, fmtLabel, Note, Reads, Why } from './wsShared'
import { TemplateLibrary } from './wsContracts'
import { Alert, EvoApp, Hero, PageSkeleton } from './ds/ds'
import './ds/evo-pages.css'

const SECTIONS = [
  ['rules', 'Deal rules'], ['markets', 'Markets'], ['approvals', 'Approvals'], ['automation', 'Automation'],
  ['providers', 'Providers'], ['outreach', 'Buyer outreach'], ['budget', 'Budget'], ['pipeline', 'Pipeline'],
  ['contracts', 'Contracts'], ['assistant', 'Seller assistant'], ['contact', 'Public contact'],
  ['sms', 'Seller SMS'],
]

const PROVIDER_GROUPS = {
  enrichment: 'Skip trace / enrichment',
  comps: 'Comparable sales',
  esign: 'Document signing',
}

// The fourth item says how to READ the number back — see `Reads`. The box
// itself always holds the raw figure: an input that reformats as you type
// loses precision the moment somebody pastes one in.
const NUMBER_FIELDS = [
  ['investor_percentage', 'Investor percentage',
   'The share of ARV an investor will pay before repairs and costs. Yours, not ours.',
   'percent'],
  ['default_wholesale_fee', 'Default wholesale fee',
   'Applied to a new deal that does not set its own.', 'money'],
  ['transaction_cost_percent', 'Transaction costs (% of ARV)', '', 'percent'],
  ['transaction_cost_flat', 'Transaction costs (flat)', '', 'money'],
  ['min_buyer_margin', 'Minimum buyer margin',
   'Flags a thin deal. Never blocks one — the person deciding knows why.',
   'money'],
  ['high_threshold', 'HIGH band at or above', '', 'plain'],
  ['medium_threshold', 'MEDIUM band at or above', '', 'plain'],
  ['review_below_completeness', 'Send to REVIEW below completeness %',
   'A confident band computed from very little is a confident wrong answer.',
   'percent'],
]

/* ONE LIST BECAME TWO, BECAUSE IT WAS ANSWERING TWO QUESTIONS.
 *
 * Ten checkboxes in one column mixed "nothing happens here without me" with
 * "do this for me while I am not looking". Those are opposite intentions and a
 * person scanning for one had to read past the other. They are now the two
 * questions a wholesaler actually asks, in their own words. No switch was
 * added, removed or renamed in the process — only regrouped. */
const APPROVALS = [
  ['require_offer_approval', 'Sending an offer to a seller'],
  ['require_contract_approval', 'Going under contract'],
  ['require_assignment_approval', 'Assigning a deal to a buyer'],
  ['enrichment_requires_approval', 'Spending money on a skip trace'],
]

const AUTOMATION = [
  ['auto_enrich_on_import', 'Look up contact details on imported properties'],
  ['auto_stage_on_enrichment', 'Move to Ready for Outreach once I have a phone or email'],
  ['auto_qualify_on_reply', 'Re-read the seller\u2019s answers when they reply'],
  ['auto_analysis_on_qualified', 'Move a qualified seller into Analysis'],
  ['auto_match_on_contract', 'Match buyers when a deal goes under contract'],
  ['ai_qualification_enabled', 'Let the assistant read seller replies'],
]

const TARGET_LISTS = [
  ['markets', 'Markets'], ['target_states', 'States'],
  ['target_counties', 'Counties'], ['target_cities', 'Cities'],
  ['target_zips', 'ZIPs'],
]

export default function WholesaleSettings() {
  const [tab, setTab] = useState(() => {
    const h = (typeof window !== 'undefined' && window.location.hash || '').replace('#set-', '')
    return SECTIONS.some(([k]) => k === h) ? h : 'rules'
  })
  const [settings, setSettings] = useState(null)
  const [channels, setChannels] = useState([])
  const [draft, setDraft] = useState({})
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setError(null)
    try {
      const data = await api.get('/wholesale/settings')
      setSettings(data)
      setDraft({})
      // A separate read because it answers a different question: the settings
      // above are this organization's, and these are the deployment's. A failure
      // here must not blank the settings screen, so it is caught on its own.
      try {
        const ch = await api.get('/wholesale/disposition/channels')
        setChannels(ch.channels || [])
      } catch {
        setChannels([])
      }
    } catch (e) {
      setError(errText(e))
    }
  }, [])

  useEffect(() => { load() }, [load])

  function set(key, value) { setDraft((d) => ({ ...d, [key]: value })) }
  function value(key) {
    return draft[key] !== undefined ? draft[key] : settings?.[key]
  }

  async function save() {
    setBusy(true); setError(null); setNotice(null)
    try {
      const payload = {}
      Object.entries(draft).forEach(([k, v]) => {
        if (NUMBER_FIELDS.some(([key]) => key === k)
            || ['enrichment_daily_cap', 'enrichment_monthly_cap',
                'enrichment_max_records_per_run'].includes(k)) {
          payload[k] = v === '' || v === null ? null : Number(v)
        } else if (TARGET_LISTS.some(([key]) => key === k)) {
          payload[k] = String(v).split(',').map((s) => s.trim()).filter(Boolean)
        } else {
          payload[k] = v
        }
      })
      // A cleared intake-key box means "leave it", never "disconnect the
      // public seller page" - that would silently drop every inquiry.
      if (payload.public_intake_key === '') delete payload.public_intake_key
      setSettings(await api.patch('/wholesale/settings', payload))
      setDraft({})
      setNotice('Settings saved.')
    } catch (e) {
      setError(errText(e))
    } finally {
      setBusy(false)
    }
  }

  if (!settings) {
    return <EvoApp world="settings">{error ? <Alert>{error}</Alert> : <PageSkeleton />}</EvoApp>
  }

  const dirty = Object.keys(draft).length > 0

  return (
    <EvoApp world="settings">
      <Hero scene="gears" eyebrow="System Configuration" title="Wholesale Settings"
            sub="Configure. Control. Scale. Everything the module will not decide for you — these belong to this workspace and are never shared with another organization."
                actions={<>
                  {dirty ? <span className="evo-status is-attention">Unsaved changes</span> : null}
                  <button type="button" className="evo-btn evo-btn--primary" disabled={!dirty || busy}
                          onClick={save}>{busy ? 'Saving…' : 'Save changes'}</button>
                </>} />
      <Alert>{error}</Alert>
      <Alert kind="ok">{notice}</Alert>
      <div className="evo-settings">
        <nav className="evo-settings__rail evo-settings__rail--tabs" aria-label="Settings sections">
          {SECTIONS.map(([k, label]) => (
            <a key={k} href={`#set-${k}`} className={tab === k ? 'is-active' : ''}
               aria-current={tab === k ? 'page' : undefined}
               onClick={(e) => { e.preventDefault(); setTab(k) }}>{label}</a>
          ))}
        </nav>
        <div className="evo-settings__body evo-stack">

      <div className="panel ws-panel" id="set-rules" hidden={tab !== 'rules'}>
        <div className="panel-title ws-panel-title">How do you calculate offers?</div>
        <FormulaLine value={value} />
        <div className="ws-grid">
          {NUMBER_FIELDS.map(([key, label, hint, as]) => (
            <div className="ws-field" key={key}>
              <label htmlFor={`set-${key}`}>{label}</label>
              <input id={`set-${key}`} inputMode="decimal" value={value(key) ?? ''}
                     onChange={(e) => set(key, e.target.value)} />
              {as !== 'plain' ? <Reads value={value(key)} as={as} /> : null}
              {hint ? <span className="ws-hint">{hint}</span> : null}
            </div>
          ))}
        </div>
        <Note>
          MAO = (ARV × investor %) − repairs − transaction costs − wholesale fee.
        </Note>
        <Why label="Which values a past calculation used">
          <p className="ws-comp__sub">
            Every deal shows this worked out line by line, using whichever values
            were in force when it was calculated rather than today's.
          </p>
        </Why>
      </div>

      <div className="panel ws-panel" id="set-markets" hidden={tab !== 'markets'}>
        <div className="panel-title ws-panel-title">Where do you buy?</div>
        <div className="ws-grid">
          {TARGET_LISTS.map(([key, label]) => (
            <div className="ws-field" key={key}>
              <label htmlFor={`tgt-${key}`}>{label}</label>
              <input id={`tgt-${key}`}
                value={draft[key] !== undefined ? draft[key]
                  : (settings[key] || []).join(', ')}
                onChange={(e) => set(key, e.target.value)}
                placeholder="Comma separated" />
            </div>
          ))}
        </div>
        {/* The sentence was a placeholder, where it was cut off mid-word in
            every empty field. It is the same sentence, said once, where there
            is room for it. */}
        <p className="ws-panel-note">
          Comma separated. A blank list is no restriction on that field.
        </p>
      </div>

      <div className="panel ws-panel" id="set-approvals" hidden={tab !== 'approvals'}>
        <div className="panel-title ws-panel-title">What needs your approval?</div>
        <Note>
          These stop dead until a person says yes. The first three are on by
          default.
        </Note>
        <div className="ws-toggle-col">
          {APPROVALS.map(([key, label]) => (
            <label className="ws-checkbox" key={key}>
              <input type="checkbox" checked={!!value(key)}
                     onChange={(e) => set(key, e.target.checked)} />
              {label}
            </label>
          ))}
        </div>
        <Why label="What the gates actually stop">
          <p className="ws-comp__sub">
            No automation in this module can sign a document, bind the company or
            move money. Those transitions refuse until a person has approved
            them, on the server, not in the screen.
          </p>
        </Why>
      </div>

      <div className="panel ws-panel" id="set-automation" hidden={tab !== 'automation'}>
        <div className="panel-title ws-panel-title">
          What should happen automatically?
        </div>
        <Note>
          Things this workspace does for you without being asked each time.
          Everything here is reversible and none of it binds you to anything.
        </Note>
        <div className="ws-toggle-col">
          {AUTOMATION.map(([key, label]) => (
            <label className="ws-checkbox" key={key}>
              <input type="checkbox" checked={!!value(key)}
                     onChange={(e) => set(key, e.target.checked)} />
              {label}
            </label>
          ))}
        </div>
      </div>

      <div className="panel ws-panel" id="set-providers" hidden={tab !== 'providers'}>
        <div className="panel-title ws-panel-title">Providers</div>
        <Note>
          Nothing here needs to be connected for the module to work.
        </Note>
        <Why label="What happens with no provider connected">
          <p className="ws-comp__sub">
            Manual entry and CSV import write exactly the same records an API
            response would, and the enrichment history counts them the same way.
            API keys live in the server's environment and are never stored here.
          </p>
        </Why>

        <div className="ws-field" style={{ maxWidth: 320, marginBottom: 14 }}>
          <label htmlFor="set-enrich-provider">Skip trace / enrichment provider</label>
          <select id="set-enrich-provider" value={value('enrichment_provider') || 'manual'}
                  onChange={(e) => set('enrichment_provider', e.target.value)}>
            {settings.providers.enrichment.map((p) => (
              <option key={p.key} value={p.key}>{p.label}</option>
            ))}
          </select>
        </div>

        {['enrichment', 'comps', 'esign'].map((group) => (
          <div key={group} style={{ marginBottom: 14 }}>
            {/* The API keys these groups by are `enrichment`, `comps`, `esign`.
                Printed straight into the heading they were three lowercase
                database keys stacked down the page. */}
            <div className="ws-k">{PROVIDER_GROUPS[group] || fmtLabel(group)}</div>
            {settings.providers[group].map((p) => (
              <div className="ws-provider-row" key={p.key}>
                <span>
                  {p.label}
                  {p.billable ? <span className="ws-source">paid</span> : null}
                </span>
                <span className={`ws-pill ${p.configured ? 'is-ok' : 'is-warn'}`}>
                  {p.configured ? 'ready' : 'not connected'}
                  {p.missing_env && p.missing_env.length
                    ? ` · needs ${p.missing_env.join(', ')}` : ''}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>

      <div className="panel ws-panel" id="set-outreach" hidden={tab !== 'outreach'}>
        <div className="panel-title ws-panel-title">Buyer outreach</div>
        <Note>
          Whether a deal sheet can actually leave this deployment. Reported here,
          not edited here.
        </Note>
        <Why label="Why these cannot be changed from this screen">
          <p className="ws-comp__sub">
            They are server switches rather than per-organization settings,
            because turning on a new outbound channel is a decision about the
            whole install.
          </p>
        </Why>
        {/* The explanation names an environment variable and runs to a couple
            of lines. Squeezed into a flex column beside a badge it wrapped into
            a narrow ribbon with the badge stranded across a gap; it gets the
            full row and sits under the name and the badge. */}
        {(channels || []).map((c) => (
          <div className="ws-channel-row" key={c.channel}>
            <div className="ws-channel-head">
              <span>Buyer {fmtLabel(c.channel === 'sms' ? 'SMS' : c.channel)}</span>
              <span className={`ws-pill ${c.enabled ? 'is-ok' : 'is-warn'}`}>
                {c.enabled ? 'ready' : 'not enabled'}
              </span>
            </div>
            <p className="ws-hint">{c.detail}</p>
          </div>
        ))}
      </div>

      <div className="panel ws-panel" id="set-budget" hidden={tab !== 'budget'}>
        <div className="panel-title ws-panel-title">What are you willing to spend?</div>
        <p className="ws-panel-note">
          Caps apply only to PAID lookups. Manual entry and CSV import are never
          capped. A cap of 0 means no paid calls at all, which is the default.
        </p>
        <div className="ws-grid">
          {[['enrichment_daily_cap', 'Paid lookups per day'],
            ['enrichment_monthly_cap', 'Paid lookups per month'],
            ['enrichment_max_records_per_run', 'Maximum records per run']].map(
            ([key, label]) => (
              <div className="ws-field" key={key}>
                <label htmlFor={`cap-${key}`}>{label}</label>
                <input id={`cap-${key}`} value={value(key) ?? ''}
                       onChange={(e) => set(key, e.target.value)}
                       placeholder="Blank = unlimited" />
              </div>
            ))}
        </div>
      </div>

      <div className="panel ws-panel" id="set-pipeline" hidden={tab !== 'pipeline'}>
        <div className="panel-title ws-panel-title">Pipeline</div>
        <p className="ws-panel-note">
          Stages are configuration, not code. A deal stores the stage key, so
          renaming a label never moves a deal.
        </p>
        <div className="ws-board">
          {settings.pipeline_stages_effective.map((s) => (
            <div key={s.key}
                 className={`ws-board-col ${s.terminal ? 'is-terminal' : ''}`}>
              <div className="ws-board-label"><strong>{s.label}</strong></div>
              <div className="ws-board-label ws-muted">{s.key}</div>
            </div>
          ))}
        </div>
      </div>

      <div id="set-contracts" hidden={tab !== 'contracts'}><TemplateLibrary /></div>

      <div id="set-assistant" hidden={tab !== 'assistant'}><AiAssistantPanel value={value} set={set} /></div>
      <div id="set-contact" hidden={tab !== 'contact'}><PublicContactPanel value={value} set={set} /></div>
      <div id="set-sms" hidden={tab !== 'sms'}>
        <SellerSmsPanel value={value} set={set} draft={draft} savedAt={settings} />
      </div>

      <div className="evo-actionbar">
        <button type="button" className="evo-btn evo-btn--primary" disabled={!dirty || busy} onClick={save}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
        {dirty ? <span className="evo-muted">Unsaved changes are kept while you move between sections.</span> : null}
      </div>
        </div>
      </div>
    </EvoApp>
  )
}


/* THE FORMULA, IN A SENTENCE, USING THIS WORKSPACE'S OWN NUMBERS.
 *
 * The boxes below it are the same boxes as before. This line exists because
 * "investor_percentage 70, transaction_cost_percent 3, default_wholesale_fee
 * 10000" is three settings, and "on a $300,000 ARV you would offer up to
 * $191,000" is the thing the settings are FOR. It is computed from whatever is
 * in the boxes right now, including unsaved edits, so changing a number shows
 * its effect before anybody presses Save.
 */
function FormulaLine({ value }) {
  const pct = Number(value('investor_percentage'))
  const costPct = Number(value('transaction_cost_percent')) || 0
  const costFlat = Number(value('transaction_cost_flat')) || 0
  const fee = Number(value('default_wholesale_fee')) || 0
  if (!Number.isFinite(pct) || pct <= 0) return null

  // A worked example on a round number, so the arithmetic is checkable.
  const arv = 300000
  const mao = (arv * pct / 100) - (arv * costPct / 100) - costFlat - fee
  const money = (n) => n.toLocaleString('en-US',
    { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })

  return (
    <div className="ws-formula">
      <div className="ws-formula__line">
        You pay up to <strong>{pct}%</strong> of what a house will be worth
        fixed up, then take off repairs, <strong>{costPct}%</strong> of ARV
        {costFlat ? <> plus {money(costFlat)}</> : null} in transaction costs,
        and your <strong>{money(fee)}</strong> fee.
      </div>
      <div className="ws-formula__eg">
        On a {money(arv)} house needing no repairs, that is a maximum offer of{' '}
        <strong>{money(mao)}</strong>. Repairs come off on top of that, per deal.
      </div>
    </div>
  )
}


/* ── The seller assistant, described as what it is ───────────────────────────
 *
 * A14 asked for tone, goal, information-to-identify and escalation. Three of
 * those are real here and one is not, and the difference matters more than the
 * symmetry:
 *
 *   goal        — fixed, and not a choice: read one inbound message and report
 *                 only what the owner actually said. It is the whole prompt.
 *   identifies  — a fixed list of fifteen fields. Worth showing, because an
 *                 operator should know what comes back; not worth making
 *                 editable, because the columns it writes into are fixed.
 *   escalation  — real, automatic and partly deterministic. Also worth showing.
 *   tone        — NOT a setting, because nothing in this module writes or sends
 *                 a message to an owner. A tone selector here would change
 *                 nothing at all, which is precisely the cosmetic setting the
 *                 brief forbids. The panel says so instead of shipping one.
 *
 * The only control the backend genuinely honours is the free-text direction,
 * which is appended to the reader's system prompt. It is behind a disclosure
 * because most workspaces never need it.
 */
const ASSISTANT_READS = [
  ['Where they stand', 'Interested, maybe later, not interested, wrong person, already sold, or asked not to be contacted'],
  ['The property', 'Still theirs and unsold, condition, major repairs, who is living in it'],
  ['The money', 'A price — only when the owner states one'],
  ['The timing', 'ASAP through no rush'],
  ['The why', 'Motivation, reason for selling, anything said about a mortgage'],
  ['The people', 'Who else decides, when they are reachable'],
]

/* THE ORGANIZATION'S OWN PUBLIC CONTACT (Phase 7.3 closeout).
 *
 * What the Investor Deal Room and the Seller Portal show as this company's
 * phone and email. There is no fallback: blank here means those pages show no
 * public contact - never the platform's support line and never another
 * organization's.
 */
function PublicContactPanel({ value, set }) {
  const phone = value('public_contact_phone') || ''
  const email = value('public_contact_email') || ''
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Public contact</span>
        <span className={`ws-pill ${phone || email ? 'is-ok' : 'is-muted'}`}>
          {phone || email ? 'Shown to investors and sellers' : 'Not configured'}
        </span>
      </div>
      <Note>
        Shown on the Investor Deal Room and the Seller Portal as how to reach this
        company. Leave a field blank to show nothing - these pages never fall back to
        another number or address.
      </Note>
      <div className="ws-grid">
        <div className="ws-field">
          <label htmlFor="ws-public-phone">Public phone</label>
          <input id="ws-public-phone" className="ws-input" type="tel" autoComplete="off"
                 value={phone} onChange={(e) => set('public_contact_phone', e.target.value)} />
        </div>
        <div className="ws-field">
          <label htmlFor="ws-public-email">Public email</label>
          <input id="ws-public-email" className="ws-input" type="email" autoComplete="off"
                 value={email} onChange={(e) => set('public_contact_email', e.target.value)} />
        </div>
      </div>
    </div>
  )
}


/* THE SELLER SMS PROGRAM (A2P 10DLC, Low Volume Mixed).
 *
 * Off until an administrator turns it on, and even then nothing is sent to a
 * seller unless the server's gate passes: program consent from the seller
 * inquiry form, no STOP, no DNC or suppression, the seller's own daytime, and
 * a Messaging Service configured here. Finding a phone number is never
 * consent. The readiness pills come from the server, not from this form.
 */
const SMS_FIELDS = [
  ['sms_sender_number', 'Dedicated SMS number', 'The Twilio number assigned to this program. Shown for reference; sends go through the Messaging Service.', 'tel'],
  ['sms_messaging_service_sid', 'Messaging Service SID', 'MG… — the Messaging Service that holds the dedicated number.', 'text'],
  ['sms_campaign_sid', 'Campaign SID', 'CM… — added after the campaign is approved. Recorded on each consent for audit.', 'text'],
  ['sms_brand_sid', 'Brand SID', 'BN… — the approved A2P brand. Optional; for audit.', 'text'],
]

function SellerSmsPanel({ value, set, draft, savedAt }) {
  const [status, setStatus] = useState(null)
  useEffect(() => {
    let live = true
    api.get('/wholesale/sms/status').then((s) => { if (live) setStatus(s) }).catch(() => {})
    return () => { live = false }
  }, [savedAt])
  const on = !!value('sms_program_enabled')
  const keyDraft = draft.public_intake_key
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Seller SMS program</span>
        <span className={`ws-pill ${status?.can_send ? 'is-ok' : 'is-warn'}`}>
          {status?.can_send ? 'Ready to send to opted-in sellers' : 'Not sending'}
        </span>
      </div>
      <Note>
        Texts go only to sellers who checked the optional SMS box on your seller
        inquiry page, and only through this program's Messaging Service. A phone
        number found by research, enrichment or import is never permission to text.
      </Note>
      {status ? (
        <div className="ws-toggle-col" aria-live="polite">
          <div className="ws-provider-row"><span>Program switch</span>
            <span className={`ws-pill ${status.enabled ? 'is-ok' : 'is-muted'}`}>{status.enabled ? 'on' : 'off'}</span></div>
          <div className="ws-provider-row"><span>Messaging Service</span>
            <span className={`ws-pill ${status.messaging_service_configured ? 'is-ok' : 'is-warn'}`}>
              {status.messaging_service_configured ? 'configured' : 'not configured'}</span></div>
          <div className="ws-provider-row"><span>Public seller page</span>
            <span className={`ws-pill ${status.public_intake_enabled ? 'is-ok' : 'is-muted'}`}>
              {status.public_intake_enabled ? 'connected' : 'not connected'}</span></div>
          {status.kill_switch ? <div className="ws-provider-row"><span>Server kill switch</span>
            <span className="ws-pill is-warn">engaged</span></div> : null}
        </div>
      ) : null}
      <label className="ws-checkbox">
        <input type="checkbox" checked={on}
               onChange={(e) => set('sms_program_enabled', e.target.checked)} />
        Send program messages to opted-in sellers
      </label>
      <div className="ws-grid">
        {SMS_FIELDS.map(([key, label, hint, type]) => (
          <div className="ws-field" key={key}>
            <label htmlFor={`sms-${key}`}>{label}</label>
            <input id={`sms-${key}`} className="ws-input" type={type} autoComplete="off"
                   value={value(key) || ''} onChange={(e) => set(key, e.target.value)} />
            <span className="ws-hint">{hint}</span>
          </div>
        ))}
        <div className="ws-field">
          <label htmlFor="sms-intake-key">Seller page intake key</label>
          <input id="sms-intake-key" className="ws-input" type="password" autoComplete="off"
                 placeholder={value('public_intake_key_set')
                   ? `Set (${value('public_intake_key_hint')}) — type to replace` : 'Not set'}
                 value={keyDraft || ''} onChange={(e) => set('public_intake_key', e.target.value)} />
          <span className="ws-hint">
            Links your public seller page to this workspace. It lives in the website
            server's config and is never shown in full here.
          </span>
        </div>
      </div>
      <Why label="What stops a message, every time">
        <p className="ws-comp__sub">
          No consent of record, a STOP or other opt-out, the do-not-contact or
          suppression list, outside 9am–8pm in the seller's own time zone, this
          switch off, or no Messaging Service. Each is checked on the server before
          every send and reported by name. No assistant, cadence or automation can
          skip it.
        </p>
      </Why>
    </div>
  )
}


function AiAssistantPanel({ value, set }) {
  const on = !!value('ai_qualification_enabled')
  return (
    <div className="panel ws-panel">
      <div className="panel-title ws-panel-title">
        <span>Seller assistant</span>
        <span className={`ws-pill ${on ? 'is-ok' : 'is-muted'}`}>
          {on ? 'Reading replies' : 'Off'}
        </span>
      </div>
      <Note>
        When an owner replies, the assistant reads that one message and fills in
        what they said. It does not write, reply, send or decide anything.
      </Note>

      <div className="ws-assistant">
        <div className="ws-assistant__col">
          <h4>What it looks for</h4>
          <dl className="ws-assistant__list">
            {ASSISTANT_READS.map(([term, detail]) => (
              <div key={term}>
                <dt>{term}</dt>
                <dd>{detail}</dd>
              </div>
            ))}
          </dl>
          <p className="ws-hint">
            Anything the message does not establish is left blank. It is not
            allowed to guess a number nobody stated.
          </p>
        </div>

        <div className="ws-assistant__col">
          <h4>When it hands back to you</h4>
          <ul className="ws-assistant__esc">
            <li>
              <strong>Any request to stop.</strong> STOP, "remove me", "don't
              text me again" — recognised without the assistant, recorded as
              do-not-contact, and never sent to a model at all.
            </li>
            <li>
              <strong>Anger, confusion, a death, a dispute, anything legal.</strong>
              {' '}Marked for a person and left for you.
            </li>
            <li>
              <strong>Low confidence.</strong> The reading is kept and flagged
              rather than acted on.
            </li>
            <li>
              <strong>No assistant available.</strong> A plain pattern reader
              runs instead and its result is marked for review, so a reply is
              never dropped because a provider is down.
            </li>
          </ul>
        </div>
      </div>

      {/* HOW IT WRITES ITS OWN SUMMARY — and the honest statement of how far
          that reaches. The assistant does not message owners, so this cannot
          change any message anybody receives; it changes the one sentence the
          assistant writes back to YOU. Saying so is the difference between a
          real preference and a cosmetic one. */}
      <div className="ws-field" style={{ maxWidth: 420, marginTop: 4 }}>
        <label htmlFor="ai-tone">How should it write its summaries?</label>
        <select id="ai-tone" value={value('ai_tone') || ''}
                onChange={(e) => set('ai_tone', e.target.value || null)}>
          <option value="">No preference</option>
          {(value('ai_tones') || ['professional', 'conversational', 'direct'])
            .map((t) => <option key={t} value={t}>{fmtLabel(t)}</option>)}
        </select>
        <span className="ws-hint">
          This changes the sentence the assistant writes back to you after
          reading a reply. It does not change any message an owner receives,
          because nothing in this module sends one — every message to a seller
          is written by a person on the deal.
        </span>
      </div>

      <Note>
        It cannot be told to ignore a stop request, to keep going when somebody
        is angry, or to decide anything. Those run before the assistant is
        consulted at all.
      </Note>

      <Why label="Advanced — extra instruction for the reader">
        <p>
          Appended to the assistant's instructions for this workspace. Use it for
          how your sellers actually talk — a local term for a neighbourhood, the
          way people in your market phrase a payoff. It cannot change what the
          assistant is allowed to do, and it cannot make it write to anyone.
        </p>
        <div className="ws-field">
          <label htmlFor="ai-direction">Extra instruction</label>
          <textarea id="ai-direction" rows={3} value={value('ai_direction') || ''}
                    onChange={(e) => set('ai_direction', e.target.value)}
                    placeholder={'Optional. e.g. round here "the back forty" '
                                 + 'means the whole parcel, not a lot split.'} />
          <span className="ws-hint">
            The first 1,000 characters are used. Turn the assistant on or off in
            Approval gates and automation, above.
          </span>
        </div>
      </Why>
    </div>
  )
}
