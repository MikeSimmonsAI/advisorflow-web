/* THE SELLER'S OWN PAGE.
 *
 * A property owner asking "what is happening with my house" is owed a straight
 * answer. They are not owed, and must never be shown, the disposition side:
 * who the buyers are, what they offered, what this workspace makes on it.
 *
 * DELIBERATELY SMALL. Five steps, the dates that concern them, whatever
 * documents somebody published to them, and a name to call. No login, no
 * dashboard, no numbers they would have to interpret. The page answers the six
 * questions a seller actually has and stops.
 *
 * READ-ONLY BY CONSTRUCTION. There is no write endpoint on the seller surface
 * at all — a test asserts that. Anything a seller needs to change, they change
 * by talking to the person named at the bottom of this page.
 */
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE } from '../../api/client'
import { Hero } from '../wholesale/ds/ds'
import './rooms.css'

const dateText = (iso) => {
  if (!iso) return null
  const d = new Date(iso + (iso.length === 10 ? 'T12:00:00' : ''))
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleDateString(undefined, { weekday: 'short', year: 'numeric',
                                        month: 'long', day: 'numeric' })
}

const STATE_WORD = { done: 'Done', current: 'In progress', upcoming: 'Not started' }

const titleCase = (s) =>
  !s ? null : String(s).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

/* What each step MEANS to the person whose house it is.
 *
 * These are descriptions of the STEP, not statements about this transaction:
 * they say what generally happens during "Property review", not what has
 * happened here. Nothing in this map can contradict the deal, because nothing
 * in it refers to the deal. The keys are the seller-safe step keys the server
 * publishes; a step the server adds later renders with no description rather
 * than with a guess. */
const STEP_MEANING = {
  offer: 'An offer has been made on your property and is being worked through.',
  agreement: 'The purchase agreement is being prepared and signed.',
  review: 'The property is being inspected and reviewed before closing.',
  title: 'The title company is preparing the paperwork for closing day.',
  closed: 'The sale is complete.',
}

/* Document types, in the words an owner would use. An unmapped type falls
   back to its own name rather than to a label somebody invented. */
const DOC_TYPE_LABELS = {
  purchase_contract: 'Purchase agreement',
  assignment: 'Assignment',
  addendum: 'Addendum',
  disclosure: 'Disclosure',
  title_commitment: 'Title commitment',
  settlement_statement: 'Settlement statement',
  deed: 'Deed',
}

export default function SellerTransaction() {
  const { token } = useParams()
  const [page, setPage] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `${API_BASE}/wholesale-rooms/seller/${encodeURIComponent(token)}`)
      if (!res.ok) {
        setError(res.status === 404
          ? 'This link is no longer available. Please contact the person you have been working with.'
          : 'This page could not be loaded right now.')
        return
      }
      setPage(await res.json())
    } catch {
      setError('This page could not be loaded right now.')
    }
  }, [token])

  useEffect(() => { load() }, [load])

  /* The tab, for the same reason it matters on the investor page — more so
   * here, because this is the page an OWNER keeps open for weeks while their
   * house closes. See the note in InvestorRoom.jsx. */
  const tabTitle = page?.brand?.name || ''
  useEffect(() => {
    if (!tabTitle) return undefined
    const previous = document.title
    document.title = tabTitle
    return () => { document.title = previous }
  }, [tabTitle])

  if (error) {
    return (
      <div className="wr"><div className="wr__shell">
        <div className="wr__panel"><p className="wr__error">{error}</p></div>
      </div></div>
    )
  }
  if (!page) {
    return (
      <div className="wr"><div className="wr__shell">
        <div className="wr__panel"><p className="wr__prose">Loading…</p></div>
      </div></div>
    )
  }

  const p = page.property || {}
  const d = page.dates || {}
  const contact = page.contact || {}
  const brand = page.brand || {}
  const steps = page.progress || []
  const current = steps.find((s) => s.state === 'current')
  const done = steps.filter((s) => s.state === 'done').length
  const cover = (page.photos || [])[0] || null

  return (
    <div className="wr" style={brand.accent ? { '--wr-accent': brand.accent } : undefined}>
      <Masthead brand={brand} />

      <div className="wr__shell">
        {/* Phase 7.3: the contextual banner every EvoSys page opens with. An
            illustration, never a picture of this property. */}
        <div className="evo-tokens wr__banner">
          <Hero compact level="p" scene="home" eyebrow="Seller Portal" title="Sell your property with confidence"
                sub="Where your sale stands, the dates that matter, and who to call — all in one place." />
        </div>
        {/* The owner's own house, and where the sale has got to — said once,
            at the top, in the words the operator chose. Everything below is
            the detail behind this line. */}
        <div className={`wr__hero${cover ? ' wr__hero--split' : ''}`}>
          {cover ? (
            <div className="wr__cover" style={{ cursor: 'default' }}>
              <img alt=""
                   src={`${API_BASE}/wholesale-rooms/seller/`
                        + `${encodeURIComponent(token)}/photo/${cover.id}`} />
            </div>
          ) : null}
          <div className="wr__hero-body">
            <div className="wr__eyebrow">Your property</div>
            <h1 className="wr__title">{p.line1 || 'Your property'}</h1>
            <p className="wr__sub">
              {[p.city, p.state, p.zip_code].filter(Boolean).join(', ')}
            </p>
            {current ? (
              <div className="wr__price" style={{ gap: 24 }}>
                <div className="wr__price-main">
                  <div className="wr__price-label">Where things stand</div>
                  <div className="wr__price-value" style={{ fontSize: 34 }}>
                    {current.label}
                  </div>
                  {/* What that step MEANS, in the owner's language. Phase 6
                      gave them a label and left them to guess. */}
                  {STEP_MEANING[current.key] ? (
                    <p className="wr__note" style={{ margin: '8px 0 0', maxWidth: '46ch' }}>
                      {STEP_MEANING[current.key]}
                    </p>
                  ) : null}
                </div>
                <div className="wr__price-side">
                  <div>
                    <div className="wr__price-label">Progress</div>
                    <div className="wr__price-value">
                      Step {Math.min(done + 1, steps.length)} of {steps.length}
                    </div>
                  </div>
                  {d.closing_date ? (
                    <div>
                      <div className="wr__price-label">Closing</div>
                      <div className="wr__price-value">{dateText(d.closing_date)}</div>
                    </div>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        </div>

        {/* The ladder sits directly under the hero and spans the full width,
            because "where am I" is the question this page answers and it
            should not be competing with a file number for attention. */}
        <div className="wr__panel">
          <div className="wr__panel-title">Your transaction</div>
          <ul className="wr__steps wr__steps--rail">
            {(page.progress || []).map((step) => (
              <li key={step.key} className={`wr__step is-${step.state}`}>
                <span className="wr__step-dot" aria-hidden="true">
                  {step.state === 'done' ? '✓' : ''}
                </span>
                <span className="wr__step-name">{step.label}</span>
                <span className="wr__step-state">
                  {STATE_WORD[step.state] || 'Not started'}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div className="wr__layout">
        <div className="wr__main">

        {/* THE ONE SENTENCE THEY OPENED THE PAGE FOR.
            An owner waiting on a sale checks this page for the update, not
            for the escrow file number. It is now the largest body text on
            the page and it is the operator's words verbatim — nothing here
            writes, summarises or softens a message on their behalf. */}
        <div className="wr__panel wr__update">
          <div className="wr__panel-title">
            Latest update{page.message ? '' : ''}
          </div>
          {page.message ? (
            <p className="wr__update-text">{page.message}</p>
          ) : (
            <p className="wr__update-empty">
              There is no new update right now. Your transaction is moving
              through the steps above, and the person handling your sale will
              post here when something changes.
            </p>
          )}
        </div>

        {(page.title?.company || page.title?.escrow_officer
          || d.closing_location) ? (
          <div className="wr__panel">
            <div className="wr__panel-title">Title and closing</div>
            <div className="wr__facts">
              <Fact label="Title company" value={page.title?.company} />
              <Fact label="Closing officer" value={page.title?.escrow_officer} />
              <Fact label="File number" value={page.title?.file_number} />
              <Fact label="Closing date" value={dateText(d.closing_date)} />
              <Fact label="Closing time" value={d.closing_time} />
              <Fact label="Where" value={d.closing_location} />
            </div>
          </div>
        ) : null}

        {(page.documents || []).some((doc) => doc.has_file) ? (
          <div className="wr__panel">
            <div className="wr__panel-title">Your documents</div>
            <ul className="wr__docs">
              {page.documents.filter((doc) => doc.has_file).map((doc) => (
                <li key={doc.id}>
                  <span className="wr__doc-name">
                    {doc.title}
                    {/* Type and date, because "Agreement" alone does not tell
                        an owner which agreement or when they signed it. */}
                    <span className="wr__doc-sub">
                      {[DOC_TYPE_LABELS[doc.doc_type] || titleCase(doc.doc_type),
                        dateText(doc.uploaded_at)].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                  <a className="wr__btn wr__btn--sm"
                     href={`${API_BASE}/wholesale-rooms/seller/`
                           + `${encodeURIComponent(token)}/document/${doc.id}`}
                     target="_blank" rel="noreferrer">Open</a>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        </div>{/* /wr__main */}

        <aside className="wr__rail">
        <div className="wr__panel">
          <div className="wr__panel-title">Important dates</div>
          <div className="wr__facts">
            <Fact label="Agreement signed" value={dateText(d.agreement_date)} />
            <Fact label="Inspection by" value={dateText(d.inspection_deadline)} />
            <Fact label="Target closing" value={dateText(d.closing_date)} />
            <Fact label="Sale completed" value={dateText(d.closed_at)} />
          </div>
          {!d.agreement_date && !d.closing_date && !d.inspection_deadline ? (
            /* An empty calendar is a real answer. Saying "no dates are set
               yet" beats four em dashes a seller has to interpret. */
            <p className="wr__note" style={{ margin: 0 }}>
              No dates are set yet. They will appear here as they are agreed.
            </p>
          ) : null}
        </div>

        {(contact.name || contact.phone || contact.email) ? (
          <div className="wr__panel">
            <div className="wr__panel-title">Who to contact</div>
            <div className="wr__facts">
              <Fact label="Name" value={contact.name} />
              <Fact label="Role" value={contact.role} />
              <Fact label="Phone" value={contact.phone
                ? <a href={`tel:${contact.phone.replace(/[^\d+]/g, '')}`}>
                    {contact.phone}
                  </a> : null} />
              <Fact label="Email" value={contact.email
                ? <a href={`mailto:${contact.email}`}>{contact.email}</a> : null} />
            </div>
          </div>
        ) : null}
        </aside>
        </div>{/* /wr__layout */}

        <p className="wr__foot">
          This page is for the owner of this property. If anything here looks
          wrong, contact the person handling your sale rather than replying to
          this page — nothing typed here is sent.
        </p>
      </div>
    </div>
  )
}


/* Whose page this is. Same rule as the investor room: the operator's own
 * identity, never the software vendor's — and for an owner it matters more,
 * because this is the company they signed an agreement with. */
function Masthead({ brand }) {
  const initial = (brand.name || '?').trim().charAt(0).toUpperCase()
  return (
    <div className="wr__mast">
      <div className="wr__mast-in">
        <div className="wr__brand">
          {brand.logo_url
            ? <img src={brand.logo_url} alt={brand.name || ''} />
            : <span className="wr__brand-mark" aria-hidden="true">{initial}</span>}
          {brand.name ? <span className="wr__brand-name">{brand.name}</span> : null}
        </div>
        {/* The company's configured public contact (Wholesale Settings >
            Public contact) - the same source as the Investor Deal Room. Never a
            platform or another organization's number; absent means absent. */}
        {(brand.support_phone || brand.support_email) ? (
          <div className="wr__mast-contact">
            {brand.support_phone ? (
              <a href={`tel:${brand.support_phone.replace(/[^\d+]/g, '')}`}>
                {brand.support_phone}
              </a>
            ) : null}
            {brand.support_email ? (
              <a href={`mailto:${brand.support_email}`}>{brand.support_email}</a>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}


function Fact({ label, value }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div>
      <div className="wr__fact-label">{label}</div>
      <div className="wr__fact-value">{value}</div>
    </div>
  )
}
