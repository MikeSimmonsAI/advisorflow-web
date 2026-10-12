import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { API_BASE } from '../../api/client';

// Public, unauthenticated survey page served on the BRANDED domain:
//   https://app.evosyspro.live/survey/:token
//
//   GET  /survey/{token}/context  -> narrow public payload (name, business, links)
//   POST /survey/{token}          -> the same idempotent submit the HTML page used
//
// Identity comes entirely from the backend: `business_name` is the funeral home
// or cemetery the family dealt with (for SCI, the LOCATION, never the
// corporation), and the phone is that location's own number or nothing at all.

const STARS = [1, 2, 3, 4, 5];
const WORDS = { 1: 'Poor', 2: 'Fair', 3: 'Good', 4: 'Very good', 5: 'Excellent' };
const HIGH_RATING = 4;   // 4-5 -> invite a public review; 1-3 -> private feedback
const GOOD_CHIPS = ['Caring staff', 'Clear answers', 'Beautiful grounds', 'Easy process', 'Felt no pressure'];
const FIX_CHIPS = ['Wait time', 'Communication', 'Pricing', 'Follow-up', 'Something else'];

const C = {
  ink: '#1d2a2c',
  deep: '#1f3b3f',
  deep2: '#2d5257',
  gold: '#c79a3a',
  goldSoft: '#f6ecd6',
  cream: '#f8f4ec',
  line: '#e6dfd2',
  muted: '#6b6f6c',
  serif: "Georgia, 'Iowan Old Style', 'Palatino Linotype', 'Times New Roman', serif",
  sans: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
};

function formatPhone(value) {
  if (!value) return '';
  const digits = String(value).replace(/\D/g, '');
  const ten = digits.length === 11 && digits[0] === '1' ? digits.slice(1) : digits;
  if (ten.length !== 10) return String(value);
  return `(${ten.slice(0, 3)}) ${ten.slice(3, 6)}-${ten.slice(6)}`;
}

function Star({ filled }) {
  return (
    <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
      <path
        d="M12 2.8l2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4-4.7-4.4 6.4-.8z"
        fill={filled ? C.gold : 'none'}
        stroke={filled ? C.gold : '#c9c2b4'}
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const CSS = `
.sv-star{transition:transform .15s ease, background .15s ease, border-color .15s ease}
.sv-star:hover{transform:translateY(-2px);border-color:${C.gold}}
.sv-star:focus-visible,.sv-chip:focus-visible,.sv-cta:focus-visible{outline:3px solid ${C.gold};outline-offset:2px}
.sv-chip{transition:background .15s ease, color .15s ease, border-color .15s ease}
.sv-cta{transition:filter .15s ease, transform .1s ease}
.sv-cta:not(:disabled):hover{filter:brightness(1.12)}
.sv-cta:not(:disabled):active{transform:scale(.99)}
.sv-fade{animation:svFade .35s ease both}
@keyframes svFade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.sv-check{animation:svPop .45s cubic-bezier(.2,1.4,.4,1) both}
@keyframes svPop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:scale(1)}}
`;

export default function SurveyPage() {
  const { token } = useParams();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [ctx, setCtx] = useState(null);

  const [rating, setRating] = useState(0);
  const [hover, setHover] = useState(0);
  const [chips, setChips] = useState([]);
  const [feedback, setFeedback] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError('');
      try {
        const res = await fetch(`${API_BASE}/survey/${encodeURIComponent(token)}/context`);
        if (!res.ok) {
          throw new Error(
            res.status === 404
              ? 'This feedback link is no longer active.'
              : 'We could not open this feedback link.'
          );
        }
        const data = await res.json();
        if (cancelled) return;
        setCtx(data);
        if (data.already_submitted) setDone(true);
      } catch (err) {
        if (!cancelled) setLoadError(err.message || 'This feedback link is no longer active.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  const brand = ctx?.branding || {};
  const business = brand.name || ctx?.business_name || '';
  const businessLogo = brand.logo_url || '';
  const rawPhone = brand.phone || ctx?.business_phone || '';
  const phone = formatPhone(rawPhone);
  const firstName = ctx?.first_name && ctx.first_name !== 'there' ? ctx.first_name : '';

  // The tab shows the business, never the platform.
  const resolvedTitle = brand.document_title || business;
  useEffect(() => {
    if (!resolvedTitle) return undefined;
    const previous = document.title;
    document.title = resolvedTitle;
    return () => { document.title = previous; };
  }, [resolvedTitle]);

  function pick(n) {
    if (n !== rating && (n >= HIGH_RATING) !== (rating >= HIGH_RATING)) setChips([]);
    setRating(n);
  }

  function toggleChip(c) {
    setChips((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));
  }

  async function submit() {
    if (!rating || submitting) return;     // guards double submit
    setSubmitting(true);
    setSubmitError('');
    const parts = [];
    if (chips.length) parts.push(`${rating >= HIGH_RATING ? 'What stood out' : 'What to improve'}: ${chips.join(', ')}`);
    if (feedback.trim()) parts.push(feedback.trim());
    try {
      const res = await fetch(`${API_BASE}/survey/${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, feedback: parts.join('\n\n') || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          typeof data.detail === 'string' ? data.detail : 'We could not record your response.'
        );
      }
      setDone(true);
    } catch (err) {
      setSubmitError(err.message || 'We could not record your response.');
    } finally {
      setSubmitting(false);
    }
  }

  const reviewLinks = [
    { url: ctx?.review_url, label: 'Share it on Google' },
    { url: ctx?.facebook_url, label: 'Share it on Facebook' },
    { url: ctx?.instagram_url, label: 'Find us on Instagram' },
  ].filter((l) => typeof l.url === 'string' && /^https?:\/\//i.test(l.url));

  const shown = hover || rating;
  const high = rating >= HIGH_RATING;

  const header = (
    <header style={S.header}>
      {businessLogo ? <img src={businessLogo} alt="" style={S.logo} /> : null}
      {business ? <div style={S.brand}>{business}</div> : null}
      {phone ? (
        <a style={S.phone} href={`tel:${rawPhone}`}>
          <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true" style={{ marginRight: 7 }}>
            <path fill="currentColor" d="M6.6 10.8a15.1 15.1 0 006.6 6.6l2.2-2.2a1 1 0 011-.25 11.4 11.4 0 003.6.57 1 1 0 011 1V20a1 1 0 01-1 1A17 17 0 013 4a1 1 0 011-1h3.5a1 1 0 011 1c0 1.25.2 2.45.57 3.6a1 1 0 01-.25 1z"/>
          </svg>
          {phone}
        </a>
      ) : null}
    </header>
  );

  if (loading) {
    return (
      <div style={S.page}><style>{CSS}</style>
        <div style={S.card}><div style={S.body}><p style={S.muted}>One moment…</p></div></div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={S.page}><style>{CSS}</style>
        <div style={S.card}>
          <div style={S.body}>
            <h1 style={S.h1}>This link is no longer active</h1>
            <p style={S.p}>{loadError}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={S.page}>
      <style>{CSS}</style>
      <div style={S.card}>
        {header}
        <div style={S.body}>
          {done ? (
            <section className="sv-fade" style={{ textAlign: 'center' }}>
              <div className="sv-check" style={S.checkCircle}>
                <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden="true">
                  <path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="#fff" strokeWidth="2.6"
                        strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </div>
              <h1 style={S.h1}>{firstName ? `Thank you, ${firstName}` : 'Thank you'}</h1>
              <p style={S.p}>
                Your words go straight to our team{business ? ` at ${business}` : ''}. We read
                every one.
              </p>
              {high && reviewLinks.length > 0 ? (
                <>
                  <p style={{ ...S.p, marginTop: 18 }}>
                    Would you be willing to share that with others? It helps families find us
                    when they need us most.
                  </p>
                  <div style={S.linkStack}>
                    {reviewLinks.map((l) => (
                      <a key={l.url} href={l.url} target="_blank" rel="noopener noreferrer"
                         className="sv-cta" style={S.linkButton}>
                        {l.label}
                      </a>
                    ))}
                  </div>
                </>
              ) : null}
            </section>
          ) : (
            <section>
              <p style={S.eyebrow}>A moment of your time</p>
              <h1 style={S.h1}>
                {firstName ? `${firstName}, how was your experience with us?` : 'How was your experience with us?'}
              </h1>
              <p style={S.p}>
                One tap is all it takes. Your answer goes only to our team
                {business ? ` at ${business}` : ''}.
              </p>

              <div style={S.starRow} role="radiogroup" aria-label="Rating, 1 to 5"
                   onMouseLeave={() => setHover(0)}>
                {STARS.map((n) => {
                  const on = n <= shown;
                  return (
                    <button
                      key={n}
                      type="button"
                      role="radio"
                      aria-checked={rating === n}
                      aria-label={`${n} of 5, ${WORDS[n]}`}
                      className="sv-star"
                      onClick={() => pick(n)}
                      onMouseEnter={() => setHover(n)}
                      style={{ ...S.star, ...(on ? S.starOn : null) }}
                    >
                      <Star filled={on} />
                    </button>
                  );
                })}
              </div>
              <div style={S.word} aria-live="polite">{shown ? WORDS[shown] : 'Tap a star'}</div>

              {rating > 0 ? (
                <div className="sv-fade">
                  <div style={S.label}>{high ? 'What stood out?' : 'What should we do better?'}</div>
                  <div style={S.chipRow}>
                    {(high ? GOOD_CHIPS : FIX_CHIPS).map((c) => {
                      const on = chips.includes(c);
                      return (
                        <button key={c} type="button" className="sv-chip" aria-pressed={on}
                                onClick={() => toggleChip(c)}
                                style={{ ...S.chip, ...(on ? S.chipOn : null) }}>
                          {c}
                        </button>
                      );
                    })}
                  </div>
                  <label htmlFor="survey-feedback" style={S.label}>
                    {high ? 'Anything else you would like to share? (optional)'
                          : 'Tell us what happened, so we can make it right.'}
                  </label>
                  <textarea
                    id="survey-feedback"
                    value={feedback}
                    onChange={(e) => setFeedback(e.target.value)}
                    rows={4}
                    style={S.textarea}
                    placeholder={high ? 'Optional' : 'This goes straight to our team'}
                  />
                </div>
              ) : null}

              {submitError ? <p style={S.error}>{submitError}</p> : null}

              <button
                type="button"
                className="sv-cta"
                disabled={!rating || submitting}
                onClick={submit}
                style={(!rating || submitting) ? S.ctaDisabled : S.cta}
              >
                {submitting ? 'Sending…' : rating ? 'Send my feedback' : 'Choose a rating above'}
              </button>
            </section>
          )}
        </div>
      </div>
      {business ? <p style={S.foot}>{business}</p> : null}
    </div>
  );
}

const S = {
  page: {
    minHeight: '100vh',
    background: `radial-gradient(1200px 500px at 50% -120px, #e9e1d1 0%, ${C.cream} 55%, #f3eee4 100%)`,
    padding: '28px 16px 40px',
    fontFamily: C.sans,
    color: C.ink,
    boxSizing: 'border-box',
  },
  card: {
    maxWidth: 540,
    margin: '0 auto',
    background: '#fff',
    borderRadius: 18,
    overflow: 'hidden',
    boxShadow: '0 1px 2px rgba(31,59,63,.06), 0 18px 48px rgba(31,59,63,.12)',
    border: `1px solid ${C.line}`,
  },
  header: {
    background: `linear-gradient(135deg, ${C.deep} 0%, ${C.deep2} 100%)`,
    color: '#fff',
    padding: '26px 24px 22px',
    textAlign: 'center',
    borderBottom: `3px solid ${C.gold}`,
  },
  logo: { maxHeight: 54, maxWidth: 220, display: 'block', margin: '0 auto 12px' },
  brand: { fontFamily: C.serif, fontSize: 23, lineHeight: 1.25, letterSpacing: '.01em' },
  phone: {
    display: 'inline-flex', alignItems: 'center', marginTop: 12, padding: '7px 14px',
    borderRadius: 999, background: 'rgba(255,255,255,.12)', color: '#fff',
    fontSize: 14.5, fontWeight: 600, textDecoration: 'none', letterSpacing: '.02em',
  },
  body: { padding: '26px 24px 28px' },
  eyebrow: {
    fontSize: 12, letterSpacing: '.14em', textTransform: 'uppercase', color: C.gold,
    fontWeight: 700, margin: '0 0 8px',
  },
  h1: { fontFamily: C.serif, fontSize: 26, lineHeight: 1.25, margin: '0 0 10px', fontWeight: 600, color: C.ink },
  p: { fontSize: 15.5, lineHeight: 1.6, color: '#3f4644', margin: '0 0 6px' },
  muted: { fontSize: 15, color: C.muted, margin: 0, textAlign: 'center' },
  starRow: { display: 'flex', gap: 8, margin: '22px 0 8px', justifyContent: 'space-between' },
  star: {
    flex: '1 1 0', height: 62, display: 'flex', alignItems: 'center', justifyContent: 'center',
    border: `1.5px solid ${C.line}`, borderRadius: 14, background: '#fff', cursor: 'pointer', padding: 0,
  },
  starOn: { background: C.goldSoft, borderColor: C.gold },
  word: {
    textAlign: 'center', fontFamily: C.serif, fontSize: 18, color: C.deep, minHeight: 26,
    margin: '0 0 18px',
  },
  label: { display: 'block', fontSize: 14.5, fontWeight: 650, color: C.ink, margin: '4px 0 9px' },
  chipRow: { display: 'flex', flexWrap: 'wrap', gap: 8, margin: '0 0 18px' },
  chip: {
    padding: '9px 14px', borderRadius: 999, border: `1.5px solid ${C.line}`, background: '#fff',
    color: C.ink, fontSize: 14, cursor: 'pointer', fontFamily: 'inherit',
  },
  chipOn: { background: C.deep, borderColor: C.deep, color: '#fff' },
  textarea: {
    width: '100%', boxSizing: 'border-box', padding: '12px 13px',
    border: `1.5px solid ${C.line}`, borderRadius: 12, fontSize: 15, lineHeight: 1.5,
    resize: 'vertical', margin: '0 0 18px', fontFamily: 'inherit', color: C.ink, background: '#fffdf9',
  },
  error: {
    fontSize: 14.5, color: '#8a1c1c', background: '#fdecec', border: '1px solid #f3c9c9',
    borderRadius: 10, padding: '10px 12px', margin: '0 0 14px',
  },
  cta: {
    width: '100%', padding: '15px 16px', border: 'none', borderRadius: 12,
    background: C.deep, color: '#fff', fontSize: 16.5, fontWeight: 650, cursor: 'pointer',
    fontFamily: 'inherit', boxShadow: '0 6px 16px rgba(31,59,63,.22)',
  },
  ctaDisabled: {
    width: '100%', padding: '15px 16px', border: `1.5px dashed ${C.line}`, borderRadius: 12,
    background: '#faf7f1', color: '#9a978f', fontSize: 16, fontWeight: 600, cursor: 'default',
    fontFamily: 'inherit',
  },
  checkCircle: {
    width: 68, height: 68, borderRadius: '50%', background: C.deep, margin: '4px auto 16px',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    boxShadow: `0 0 0 6px ${C.goldSoft}`,
  },
  linkStack: { display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14 },
  linkButton: {
    display: 'block', textAlign: 'center', padding: '13px 14px', borderRadius: 12,
    background: C.deep, color: '#fff', fontSize: 15.5, fontWeight: 600, textDecoration: 'none',
  },
  foot: { textAlign: 'center', fontSize: 12.5, color: '#8b877e', marginTop: 18, letterSpacing: '.02em' },
};
