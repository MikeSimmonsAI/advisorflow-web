/* THE INVESTOR DEAL ROOM.
 *
 * What a cash buyer sees when an operator sends them a deal. It is NOT the
 * internal buyer-matching screen with fields removed — it is a different page
 * served by a different endpoint, and the server decides what is on it.
 *
 * THIS FILE CANNOT LEAK ANYTHING, AND THAT IS BY CONSTRUCTION.
 * It renders whatever `/wholesale-rooms/buyer/{token}` returns and asks for
 * nothing else. There is no second request, no id it can widen to, and no
 * "hidden" field waiting for somebody to delete a `display: none`. The
 * publication boundary lives in app/services/wholesale_publication.py; this is
 * a reader.
 *
 * WHAT PHASE 6 CHANGED, AND WHY.
 * The first version was correct and unreadable: eight identical cards on grey,
 * the photos in a flat strip below the facts, the asking price the same size as
 * everything else, and the five things an investor might actually DO sitting
 * 1,600px down the page. It was a data dump that happened to be safe. This is
 * the same data, published under the operator's own brand, arranged the way
 * somebody decides whether to buy a house: the picture, the price, the facts,
 * the workings, then the decision — with the decision never more than a thumb
 * away on a phone.
 *
 * WHAT IT WILL NOT PRETEND. Every button here writes a real record on the
 * operator's own board. There is no "thanks, we'll be in touch" that goes
 * nowhere: if the server refuses, the refusal is shown, in the server's words.
 */
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { API_BASE } from '../../api/client'
import { Hero } from '../wholesale/ds/ds'
import './rooms.css'

const money = (n) =>
  n === null || n === undefined || n === ''
    ? null
    : Number(n).toLocaleString('en-US', { style: 'currency', currency: 'USD',
                                          maximumFractionDigits: 0 })

const num = (n) =>
  n === null || n === undefined || n === '' ? null : Number(n).toLocaleString()

const dateText = (iso) => {
  if (!iso) return null
  const d = new Date(iso + (iso.length === 10 ? 'T12:00:00' : ''))
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

const titleCase = (raw) => raw
  ? String(raw).replace(/_/g, ' ').replace(/^\w/, (c) => c.toUpperCase())
  : null

const CATEGORY_LABELS = {
  exterior_front: 'Front', exterior_rear: 'Rear', exterior_side: 'Side',
  kitchen: 'Kitchen', living_room: 'Living room', bedroom: 'Bedroom',
  bathroom: 'Bathroom', garage: 'Garage', roof: 'Roof', hvac: 'HVAC',
  electrical: 'Electrical', plumbing: 'Plumbing', foundation: 'Foundation',
  damage: 'Damage', repair_area: 'Repair area', yard: 'Yard',
  neighborhood: 'Neighborhood', other: 'Other',
}

const OCCUPANCY_LABELS = {
  vacant: 'Vacant', owner_occupied: 'Owner occupied', tenant: 'Tenant occupied',
  unknown: 'Occupancy unknown',
}

/* The five things an investor can do, with what each one actually means. The
 * note matters: "I'm interested" and "Make an offer" are different commitments
 * and a row of five identical buttons said they were the same. */
/* MAKE AN OFFER IS THE PRIMARY, AND IT WAS NOT.
 * Phase 6 gave the primary treatment to "I'm interested", so the strongest
 * button on the page committed the reader to nothing. The page exists to
 * produce offers; the emphasis now says so. PASS keeps its place in the list
 * — it is a real answer and hiding it would be a dark pattern — but it is
 * drawn as the quietest thing on the panel. */
const CHOICES = [
  { key: 'offer', name: 'Make an offer', kind: 'primary',
    note: 'Price, timing and how you are funding it.' },
  { key: 'interested', name: "I'm interested",
    note: 'Tell the team to keep you on this one.' },
  { key: 'walkthrough', name: 'Request a walkthrough',
    note: 'Ask to see it before you commit.' },
  { key: 'question', name: 'Ask a question',
    note: 'Anything not answered on this page.' },
  { key: 'pass', name: 'Pass on this one', kind: 'quiet',
    note: 'Not a fit. It stops reaching you.' },
]

/* What the operator's board will say after each action, in the investor's own
 * language. These are the statuses the server actually sets — nothing here
 * invents a state the record does not have. */
const STATUS_TEXT = {
  interested: 'You are marked as interested.',
  offer_submitted: 'Your offer is with the team handling this deal.',
  passed: 'You passed on this one.',
  requested_info: 'Somebody will come back to you.',
}

const FINANCING = [
  ['cash', 'Cash'], ['hard_money', 'Hard money'],
  ['conventional', 'Conventional'], ['other', 'Something else'],
]
const POF = [
  ['on_file', 'Already on file with you'],
  ['can_provide', 'I can send it'],
  ['not_yet', 'Not yet'],
]

const EMPTY_FORM = {
  amount: '', closing_date: '', financing: '', proof_of_funds: '',
  contact_name: '', contact_email: '', contact_phone: '', message: '',
}


export default function InvestorRoom() {
  const { token } = useParams()
  const [room, setRoom] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [mode, setMode] = useState(null)        // which action form is open
  const [form, setForm] = useState(EMPTY_FORM)
  const [result, setResult] = useState(null)
  const [lightbox, setLightbox] = useState(null)   // index into photos

  const load = useCallback(async () => {
    try {
      const res = await fetch(
        `${API_BASE}/wholesale-rooms/buyer/${encodeURIComponent(token)}`)
      if (!res.ok) {
        setError(res.status === 404
          ? 'This link is no longer available. Ask whoever sent it for a new one.'
          : 'This page could not be loaded right now.')
        return
      }
      setRoom(await res.json())
    } catch {
      setError('This page could not be loaded right now.')
    }
  }, [token])

  useEffect(() => { load() }, [load])

  /* THE BROWSER TAB IS PART OF THE BRANDING, AND IT WAS THE VENDOR'S.
   *
   * index.html picks a title from the HOSTNAME before React mounts, so an
   * investor who opened this link got "BookaBoost" — the wholesaler's software
   * supplier — in their tab, their history and any link preview they shared.
   * Section 40.2 rules that out of the page; leaving it in the tab is the same
   * leak through a different hole, and it is the part that survives after the
   * tab is closed.
   *
   * Same treatment as BookingPage/SurveyPage: the operator's resolved name
   * replaces it, and a brand that resolved to nothing leaves the existing
   * title alone rather than inventing one. Restored on unmount so this never
   * bleeds into the rest of the SPA. */
  const tabTitle = room?.brand?.name || ''
  useEffect(() => {
    if (!tabTitle) return undefined
    const previous = document.title
    document.title = tabTitle
    return () => { document.title = previous }
  }, [tabTitle])

  const photos = room?.photos || []

  /* Keyboard in the lightbox. A gallery you cannot arrow through is a gallery
   * nobody looks past the second photo of. */
  useEffect(() => {
    if (lightbox === null) return undefined
    function onKey(e) {
      if (e.key === 'Escape') setLightbox(null)
      if (e.key === 'ArrowRight') setLightbox((i) => (i + 1) % photos.length)
      if (e.key === 'ArrowLeft') {
        setLightbox((i) => (i - 1 + photos.length) % photos.length)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [lightbox, photos.length])

  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  /* Open a form from anywhere — the hero, the rail, the phone bar — and put
   * it in front of the reader. On a wide screen the rail is already visible
   * so the scroll is a no-op; on a phone it is the whole point. */
  const openAction = (key) => {
    setMode(key)
    requestAnimationFrame(() => {
      document.getElementById('wr-respond')
        ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    })
  }

  const photoSrc = (photo) =>
    `${API_BASE}/wholesale-rooms/buyer/${encodeURIComponent(token)}`
    + `/photo/${photo.id}`

  async function send(action) {
    setBusy(true)
    try {
      const body = { action, message: form.message || null }
      if (action === 'offer') {
        body.amount = form.amount !== '' ? Number(form.amount) : null
        body.closing_date = form.closing_date || null
        body.financing = form.financing || null
        body.proof_of_funds = form.proof_of_funds || null
      }
      // Contact details go with anything that expects a reply.
      if (['offer', 'walkthrough', 'question', 'interested'].includes(action)) {
        body.contact_name = form.contact_name || null
        body.contact_email = form.contact_email || null
        body.contact_phone = form.contact_phone || null
      }
      const res = await fetch(
        `${API_BASE}/wholesale-rooms/buyer/${encodeURIComponent(token)}/action`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body) })
      const payload = await res.json().catch(() => ({}))
      if (!res.ok) {
        // The server's reason, shown as the server wrote it. A generic
        // "something went wrong" here would hide the one useful sentence.
        setResult({ ok: false,
                    text: payload.detail || 'That could not be recorded.' })
      } else {
        setResult({ ok: true,
                    text: STATUS_TEXT[payload.status] || 'Recorded.' })
        setMode(null); setForm(EMPTY_FORM)
        load()
      }
    } catch {
      setResult({ ok: false, text: 'That could not be recorded. Please try again.' })
    } finally {
      setBusy(false)
    }
  }

  if (error) {
    return (
      <div className="wr"><div className="wr__shell">
        <div className="wr__panel"><p className="wr__error">{error}</p></div>
      </div></div>
    )
  }
  if (!room) {
    return (
      <div className="wr"><div className="wr__shell">
        <div className="wr__panel"><p className="wr__prose">Loading…</p></div>
      </div></div>
    )
  }

  const brand = room.brand || {}
  const p = room.property || {}
  const you = room.you || null
  const cover = photos.find((x) => x.is_cover) || photos[0] || null
  const rest = photos.filter((x) => x !== cover)
  const location = [p.city, p.state].filter(Boolean).join(', ')
  const addressLine = [location, p.zip_code].filter(Boolean).join(' ')

  const chips = [
    titleCase(p.property_type),
    p.occupancy_status ? (OCCUPANCY_LABELS[p.occupancy_status]
                          || titleCase(p.occupancy_status)) : null,
    p.market ? `${p.market} market` : null,
    p.county ? `${p.county} County` : null,
  ].filter(Boolean)

  // Only documents with a file behind them. An investor does not need to know
  // that a row exists for a report nobody has uploaded — that is the
  // operator's checklist, not theirs.
  const docs = (room.documents || []).filter((d) => d.has_file)

  return (
    <div className="wr" style={brand.accent ? { '--wr-accent': brand.accent } : undefined}>
      <Masthead brand={brand} />

      <div className="wr__shell">
        {/* Phase 7.3: the contextual banner every EvoSys page opens with. An
            illustration, never a picture of this property. */}
        <div className="evo-tokens wr__banner">
          <Hero compact level="p" scene="invest" eyebrow="Investor Deal Room" title="An off-market opportunity"
                sub="Everything you need to decide, and one step to make an offer." />
        </div>
        {/* ── Hero: the picture, the address, the price ───────────────────
            `--split` is what makes this a desktop page rather than a phone
            page with margins: above 900px the photograph takes the left half
            at full height and the deal takes the right, so the cover, the
            address, the chips, the asking price and the primary action are
            all on the first screen. Below that it stacks, cover first. */}
        <div className="wr__hero wr__hero--split">
          {cover ? (
            <button type="button" className="wr__cover"
                    aria-label="Open photo gallery"
                    onClick={() => setLightbox(photos.indexOf(cover))}>
              <img src={photoSrc(cover)} alt={cover.caption
                || CATEGORY_LABELS[cover.category] || 'Property'} />
              {photos.length > 1 ? (
                <span className="wr__cover-count">
                  {photos.length} photos
                </span>
              ) : null}
            </button>
          ) : (
            /* No photo is a fact, not a broken image. */
            <div className="wr__cover wr__cover--none">
              No photos have been shared for this property yet
            </div>
          )}

          <div className="wr__hero-body">
            <div className="wr__eyebrow">Off-market opportunity</div>
            <h1 className="wr__title">{p.line1 || 'Property'}</h1>
            <p className="wr__sub">{addressLine || '—'}</p>

            {chips.length ? (
              <div className="wr__chips">
                {chips.map((c, i) => (
                  <span key={c} className={`wr__chip ${i === 0 ? 'wr__chip--accent' : ''}`}>
                    {c}
                  </span>
                ))}
              </div>
            ) : null}

            <div className="wr__price">
              <div className="wr__price-main">
                <div className="wr__price-label">Asking price</div>
                <div className="wr__price-value">
                  {money(room.asking_price) || 'On application'}
                </div>
              </div>
              <div className="wr__price-side">
                {'arv' in room ? (
                  <div>
                    <div className="wr__price-label">ARV</div>
                    <div className="wr__price-value">{money(room.arv) || '—'}</div>
                  </div>
                ) : null}
                {'estimated_repairs' in room ? (
                  <div>
                    <div className="wr__price-label">Est. repairs</div>
                    <div className="wr__price-value">
                      {money(room.estimated_repairs) || '—'}
                    </div>
                  </div>
                ) : null}
                {room.closing?.closing_date || room.closing?.target_close ? (
                  <div>
                    <div className="wr__price-label">Closing</div>
                    <div className="wr__price-value">
                      {dateText(room.closing.closing_date)
                       || dateText(room.closing.target_close)}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            {/* The action, in the hero. An investor who has decided from the
                photograph and the price should not have to find the bottom of
                a 2,500px page to say so. It opens the same form the rail and
                the phone bar open — one component, one code path. */}
            <div className="wr__hero-cta">
              <button type="button" className="wr__btn wr__btn--primary"
                      disabled={busy}
                      onClick={() => { setResult(null); openAction('offer') }}>
                Make an offer
              </button>
              <button type="button" className="wr__btn" disabled={busy}
                      onClick={() => { setResult(null); openAction('interested') }}>
                I'm interested
              </button>
            </div>
          </div>
        </div>

        <div className="wr__layout">
        <div className="wr__main">

        {/* ── The property ──────────────────────────────────────────────── */}
        <div className="wr__panel">
          <div className="wr__panel-title">The property</div>
          <div className="wr__facts wr__facts--wide">
            <Fact label="Beds" value={num(p.bedrooms)} />
            <Fact label="Baths" value={num(p.bathrooms)} />
            <Fact label="Square feet" value={num(p.square_feet)} />
            <Fact label="Lot (sq ft)" value={num(p.lot_size_sqft)} />
            <Fact label="Year built" value={p.year_built} />
            <Fact label="Type" value={titleCase(p.property_type)} />
            <Fact label="Occupancy" value={p.occupancy_status
              ? (OCCUPANCY_LABELS[p.occupancy_status] || titleCase(p.occupancy_status))
              : null} />
            <Fact label="County" value={p.county} />
          </div>
        </div>

        {/* ONE panel, two headings. Phase 6 gave each paragraph its own
            rounded rectangle, which is why the page read as a stack of cards
            rather than as a document. Both are the operator's own words,
            rendered verbatim — nothing here rewrites or embellishes them. */}
        {(room.summary || room.condition_summary) ? (
          <div className="wr__panel">
            <div className="wr__panel-title">The opportunity</div>
            <div className="wr__split wr__split--two">
              {room.summary ? (
                <div>
                  <h2 className="wr__sub-title">About this property</h2>
                  <p className="wr__prose">{room.summary}</p>
                </div>
              ) : null}
              {room.condition_summary ? (
                <div>
                  <h2 className="wr__sub-title">Condition and repairs</h2>
                  <p className="wr__prose">{room.condition_summary}</p>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}

        {rest.length ? (
          <div className="wr__panel">
            <div className="wr__panel-title">More photos ({photos.length})</div>
            <div className="wr__thumbs">
              {rest.map((photo) => {
                const caption = [CATEGORY_LABELS[photo.category], photo.caption]
                  .filter(Boolean).join(' — ')
                return (
                  <button type="button" key={photo.id} className="wr__thumb"
                          onClick={() => setLightbox(photos.indexOf(photo))}>
                    <img src={photoSrc(photo)} alt={caption || 'Property photo'} />
                    {caption ? (
                      <span className="wr__thumb-cap">{caption}</span>
                    ) : null}
                  </button>
                )
              })}
            </div>
          </div>
        ) : null}

        {room.comparable_sales?.length ? (
          <div className="wr__panel">
            <div className="wr__panel-title">Comparable sales</div>
            <div className="wr__scroll">
              <table className="wr__table">
                <thead>
                  <tr>
                    <th>Address</th><th>Distance</th><th>Sold</th>
                    <th className="wr--num">Price</th>
                    <th className="wr--num">Sq ft</th>
                    <th className="wr--num">$/sq ft</th>
                    <th className="wr--num">Bd / Ba</th>
                  </tr>
                </thead>
                <tbody>
                  {room.comparable_sales.map((c, i) => (
                    <tr key={i}>
                      <td>{c.address || '—'}</td>
                      <td>{c.distance_miles != null
                        ? `${Number(c.distance_miles).toFixed(1)} mi` : '—'}</td>
                      {/* A missing sale date stays missing. */}
                      <td>{dateText(c.sale_date) || '—'}</td>
                      <td className="wr--num">{money(c.sale_price) || '—'}</td>
                      <td className="wr--num">{num(c.square_feet) || '—'}</td>
                      <td className="wr--num">
                        {c.price_per_sqft ? `$${c.price_per_sqft}` : '—'}
                      </td>
                      <td className="wr--num">
                        {[c.bedrooms, c.bathrooms].map((x) => x ?? '—').join(' / ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="wr__hint" style={{ marginTop: 10 }}>
              Sales shared by the team handling this property. Verify anything
              you intend to rely on.
            </p>
          </div>
        ) : null}

        {(room.closing?.closing_date || room.closing?.target_close
          || room.closing?.title_company) ? (
          <div className="wr__panel">
            <div className="wr__panel-title">Closing</div>
            <div className="wr__facts">
              <Fact label="Closing date" value={dateText(room.closing.closing_date)} />
              <Fact label="Target close" value={dateText(room.closing.target_close)} />
              <Fact label="Title company" value={room.closing.title_company} />
            </div>
          </div>
        ) : null}

        {docs.length ? (
          <div className="wr__panel">
            <div className="wr__panel-title">Documents</div>
            <ul className="wr__docs">
              {docs.map((d) => (
                <li key={d.id}>
                  <span className="wr__doc-name">
                    {d.title}
                    {d.uploaded_at ? (
                      <span className="wr__doc-sub"> · {dateText(d.uploaded_at)}</span>
                    ) : null}
                  </span>
                  <a className="wr__btn wr__btn--sm"
                     href={`${API_BASE}/wholesale-rooms/buyer/`
                           + `${encodeURIComponent(token)}/document/${d.id}`}
                     target="_blank" rel="noreferrer">Open</a>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        </div>{/* /wr__main */}

        {/* ── The decision ──────────────────────────────────────────────────
            Above 1180px this is a sticky rail beside the property, so the
            five actions are on screen at every scroll position. Below that
            the same node falls back into the flow under the detail, and the
            phone gets the fixed bar at the bottom instead. One panel, one
            form, one submit path — the layout moves it, nothing duplicates
            it, because two copies of an offer form is two ways to send two
            different offers. */}
        <aside className="wr__rail">
        <div className="wr__panel wr__respond" id="wr-respond">
          <div className="wr__panel-title">
            {mode ? CHOICES.find((c) => c.key === mode)?.name : 'What would you like to do?'}
          </div>

          {you?.status && STATUS_TEXT[you.status] ? (
            <div className="wr__yours">
              <div className="wr__yours-line">
                <strong>{STATUS_TEXT[you.status]}</strong> You can change it below.
              </div>
              {(you.your_offer || you.responded_at) ? (
                <div className="wr__yours-detail">
                  {you.your_offer ? <span>Offer: {money(you.your_offer)}</span> : null}
                  {you.responded_at
                    ? <span>Recorded {dateText(you.responded_at)}</span> : null}
                </div>
              ) : null}
            </div>
          ) : null}

          {result ? (
            <p className={result.ok ? 'wr__confirm' : 'wr__error'}>{result.text}</p>
          ) : null}

          {mode === null ? (
            <div className="wr__choices">
              {CHOICES.map((c) => (
                <button type="button" key={c.key} disabled={busy}
                        className={`wr__choice ${c.kind ? `wr__choice--${c.kind}` : ''}`}
                        onClick={() => {
                          setResult(null)
                          // These two are a single click; the rest ask for
                          // something first.
                          if (c.key === 'pass') { send('pass'); return }
                          openAction(c.key)
                        }}>
                  <div className="wr__choice-name">{c.name}</div>
                  <div className="wr__choice-note">{c.note}</div>
                </button>
              ))}
            </div>
          ) : (
            <ActionForm mode={mode} form={form} set={set} busy={busy}
                        onSend={() => send(mode)}
                        onCancel={() => { setMode(null); setResult(null) }} />
          )}
        </div>
        </aside>
        </div>{/* /wr__layout */}

        <p className="wr__foot">
          This page shows only what the team handling this property chose to
          share with you. Anything you send here is recorded against this deal
          and goes to that team — nothing is published anywhere else.
        </p>
      </div>

      {/* The decision, always within reach below 1180px — above that the
          sticky rail is already doing this and the bar is hidden. */}
      <div className="wr__sticky">
        <button className="wr__btn wr__btn--primary" disabled={busy}
                onClick={() => { setResult(null); openAction('offer') }}>
          Make an offer
        </button>
        <button className="wr__btn" disabled={busy}
                onClick={() => {
                  setResult(null); setMode(null)
                  document.getElementById('wr-respond')
                    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                }}>
          Other options
        </button>
      </div>

      {lightbox !== null && photos[lightbox] ? (
        <Lightbox photos={photos} index={lightbox} src={photoSrc(photos[lightbox])}
                  onClose={() => setLightbox(null)}
                  onStep={(d) => setLightbox(
                    (i) => (i + d + photos.length) % photos.length)} />
      ) : null}
    </div>
  )
}


/* ── Whose page this is ───────────────────────────────────────────────────
 * Never the software vendor. `brand` comes from the organization's own record
 * with the platform as a fallback, and a workspace that has set neither still
 * gets the row — it just says less. */
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


function Lightbox({ photos, index, src, onClose, onStep }) {
  const photo = photos[index]
  const caption = [CATEGORY_LABELS[photo.category], photo.caption]
    .filter(Boolean).join(' — ')
  return (
    <div className="wr__lb" role="dialog" aria-modal="true" aria-label="Photo">
      <button type="button" className="wr__lb-x" aria-label="Close"
              onClick={onClose}>✕</button>
      {photos.length > 1 ? (
        <>
          <button type="button" className="wr__lb-nav wr__lb-nav--prev"
                  aria-label="Previous photo" onClick={() => onStep(-1)}>‹</button>
          <button type="button" className="wr__lb-nav wr__lb-nav--next"
                  aria-label="Next photo" onClick={() => onStep(1)}>›</button>
        </>
      ) : null}
      <div className="wr__lb-stage"><img src={src} alt={caption || 'Property photo'} /></div>
      <div className="wr__lb-bar">
        {caption || ' '}
        <span className="wr__lb-count">{index + 1} of {photos.length}</span>
      </div>
    </div>
  )
}


/* ── What each action actually asks for ───────────────────────────────────
 * An offer is a price, a date and how it is funded. Collecting only an amount
 * — which is what the first version did — produces a record the operator
 * cannot act on without a phone call, which defeats the point of the page. */
function ActionForm({ mode, form, set, busy, onSend, onCancel }) {
  const isOffer = mode === 'offer'
  const canSend = !busy
    && (!isOffer || String(form.amount).trim() !== '')
    && (mode !== 'question' || form.message.trim() !== '')

  return (
    <div className="wr__form">
      {isOffer ? (
        <>
          <div className="wr__form-grid">
            <div className="wr__field">
              <label htmlFor="wr-amount">
                Your offer <span className="wr__req" aria-hidden="true">*</span>
              </label>
              <input id="wr-amount" className="wr__input" inputMode="decimal"
                     required aria-required="true"
                     value={form.amount} placeholder="165000"
                     onChange={(e) => set('amount', e.target.value)} />
            </div>
            <div className="wr__field">
              <label htmlFor="wr-close">Closing by</label>
              <input id="wr-close" className="wr__input" type="date"
                     value={form.closing_date}
                     onChange={(e) => set('closing_date', e.target.value)} />
            </div>
            <div className="wr__field">
              <label htmlFor="wr-fin">How are you funding it?</label>
              <select id="wr-fin" className="wr__input" value={form.financing}
                      onChange={(e) => set('financing', e.target.value)}>
                <option value="">Prefer not to say</option>
                {FINANCING.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </div>
            <div className="wr__field">
              <label htmlFor="wr-pof">Proof of funds</label>
              <select id="wr-pof" className="wr__input" value={form.proof_of_funds}
                      onChange={(e) => set('proof_of_funds', e.target.value)}>
                <option value="">Prefer not to say</option>
                {POF.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
              <span className="wr__hint">
                Recorded as what you told us — nobody here marks it verified.
              </span>
            </div>
          </div>
        </>
      ) : null}

      <div className="wr__field">
        <label htmlFor="wr-message">
          {isOffer ? 'Anything to add (optional)'
            : mode === 'walkthrough' ? 'When would suit you? (optional)'
              : mode === 'question' ? 'Your question'
                : 'Anything to add (optional)'}
          {mode === 'question'
            ? <span className="wr__req" aria-hidden="true"> *</span> : null}
        </label>
        <textarea id="wr-message" className="wr__input" rows={3}
                  required={mode === 'question'}
                  aria-required={mode === 'question' ? 'true' : undefined}
                  value={form.message}
                  onChange={(e) => set('message', e.target.value)} />
      </div>

      <div className="wr__form-grid">
        <div className="wr__field">
          <label htmlFor="wr-name">Your name (optional)</label>
          <input id="wr-name" className="wr__input" value={form.contact_name}
                 onChange={(e) => set('contact_name', e.target.value)} />
        </div>
        <div className="wr__field">
          <label htmlFor="wr-email">Email (optional)</label>
          <input id="wr-email" className="wr__input" type="email"
                 value={form.contact_email}
                 onChange={(e) => set('contact_email', e.target.value)} />
        </div>
        <div className="wr__field">
          <label htmlFor="wr-phone">Phone (optional)</label>
          <input id="wr-phone" className="wr__input" type="tel"
                 value={form.contact_phone}
                 onChange={(e) => set('contact_phone', e.target.value)} />
        </div>
      </div>

      <div className="wr__actions">
        <button className="wr__btn wr__btn--primary" disabled={!canSend}
                onClick={onSend}>
          {busy ? 'Sending…' : isOffer ? 'Submit offer' : 'Send'}
        </button>
        <button className="wr__btn wr__btn--quiet" disabled={busy}
                onClick={onCancel}>Cancel</button>
      </div>
      {/* Says WHY the button is off rather than leaving a dead control. */}
      {!busy && !canSend ? (
        <p className="wr__hint" role="status">
          {isOffer ? 'Enter an amount to submit your offer.'
            : 'Type your question to send it.'}
        </p>
      ) : null}
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
