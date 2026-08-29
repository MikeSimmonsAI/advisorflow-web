import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { API_BASE } from '../../api/client';

// Public, unauthenticated survey page served on the BRANDED domain:
//   https://app.evosyspro.live/survey/:token
//
// Reuses the EXISTING survey business logic - it is not a second survey system:
//   GET  /survey/{token}/context  -> narrow public payload (name, business, links)
//   POST /survey/{token}          -> the same idempotent submit the HTML page used
//
// The backend-rendered HTML survey at GET /survey/{token} is deliberately still
// served, because links already delivered to families point at it. New links are
// issued against this route by the public-identity resolver.
//
// Identity comes entirely from the resolver: `business_name` is the funeral home,
// never the platform. No infrastructure hostname appears anywhere on this page.

const STARS = [1, 2, 3, 4, 5];
const HIGH_RATING = 4;   // 4-5 -> invite a public review; 1-3 -> private feedback

function formatPhone(value) {
  if (!value) return '';
  const digits = String(value).replace(/\D/g, '');
  const ten = digits.length === 11 && digits[0] === '1' ? digits.slice(1) : digits;
  if (ten.length !== 10) return String(value);
  return `(${ten.slice(0, 3)}) ${ten.slice(3, 6)}-${ten.slice(6)}`;
}

export default function SurveyPage() {
  const { token } = useParams();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [ctx, setCtx] = useState(null);

  const [rating, setRating] = useState(0);
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

  async function submit() {
    if (!rating || submitting) return;     // guards double submit
    setSubmitting(true);
    setSubmitError('');
    try {
      const res = await fetch(`${API_BASE}/survey/${encodeURIComponent(token)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, feedback: feedback.trim() || null }),
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
      setSubmitting(false);           // exits loading on the failure path too
    }
  }

  const accent = ctx?.brand_color || '#1f4e79';
  const business = ctx?.business_name || '';
  const rawPhone = ctx?.business_phone || '';
  const phone = formatPhone(rawPhone);
  const firstName = ctx?.first_name && ctx.first_name !== 'there' ? ctx.first_name : '';
  const S = styles(accent);

  const reviewLinks = [
    { url: ctx?.review_url, label: 'Leave a Google review' },
    { url: ctx?.facebook_url, label: 'Leave a Facebook review' },
    { url: ctx?.instagram_url, label: 'Find us on Instagram' },
  ].filter((l) => typeof l.url === 'string' && /^https?:\/\//i.test(l.url));

  if (loading) {
    return (
      <div style={S.page}>
        <div style={S.card}><p style={S.muted}>Loading…</p></div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={S.page}>
        <div style={S.card}>
          <h1 style={S.h1}>This link is no longer active</h1>
          <p style={S.body}>{loadError}</p>
        </div>
      </div>
    );
  }

  return (
    <div style={S.page}>
      <div style={S.card}>
        <header style={S.header}>
          {business ? <div style={S.brand}>{business}</div> : null}
          {phone ? (
            <div style={S.brandSub}>
              <a style={S.link} href={`tel:${rawPhone}`}>{phone}</a>
            </div>
          ) : null}
        </header>

        {done ? (
          <section>
            <h1 style={S.h1}>Thank you</h1>
            <p style={S.body}>
              We appreciate you taking the time to tell us how we did. Your feedback
              goes directly to our team.
            </p>
            {rating >= HIGH_RATING && reviewLinks.length > 0 ? (
              <>
                <p style={S.body}>
                  If you would be willing to share that publicly, it genuinely helps
                  other families find us.
                </p>
                <div style={S.linkStack}>
                  {reviewLinks.map((l) => (
                    <a
                      key={l.url}
                      href={l.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={S.linkButton}
                    >
                      {l.label}
                    </a>
                  ))}
                </div>
              </>
            ) : null}
          </section>
        ) : (
          <section>
            <h1 style={S.h1}>
              {firstName ? `${firstName}, how did we do?` : 'How did we do?'}
            </h1>
            <p style={S.body}>
              Your answer is read by our team{business ? ` at ${business}` : ''}. It takes
              less than a minute.
            </p>

            <div style={S.starRow} role="group" aria-label="Rating, 1 to 5">
              {STARS.map((n) => {
                const on = n <= rating;
                return (
                  <button
                    key={n}
                    type="button"
                    aria-label={`${n} of 5`}
                    aria-pressed={on}
                    onClick={() => setRating(n)}
                    style={on ? S.starOn : S.starOff}
                  >
                    ★
                  </button>
                );
              })}
            </div>

            {rating > 0 ? (
              <>
                <label htmlFor="survey-feedback" style={S.label}>
                  {rating >= HIGH_RATING
                    ? 'Anything you would like us to know? (optional)'
                    : 'Please tell us what went wrong, so we can put it right.'}
                </label>
                <textarea
                  id="survey-feedback"
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  rows={5}
                  style={S.textarea}
                  placeholder={rating >= HIGH_RATING ? 'Optional' : 'This goes straight to our team'}
                />
              </>
            ) : null}

            {submitError ? <p style={S.error}>{submitError}</p> : null}

            <button
              type="button"
              disabled={!rating || submitting}
              onClick={submit}
              style={(!rating || submitting) ? S.ctaDisabled : S.cta}
            >
              {submitting ? 'Sending…' : rating ? 'Send my feedback' : 'Choose a rating'}
            </button>
          </section>
        )}
      </div>
    </div>
  );
}

function styles(accent) {
  return {
    page: {
      minHeight: '100vh',
      background: '#f5f6f8',
      padding: '32px 16px',
      fontFamily: "system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
      color: '#1c1d1f',
      boxSizing: 'border-box',
    },
    card: {
      maxWidth: 560,
      margin: '0 auto',
      background: '#ffffff',
      borderRadius: 14,
      boxShadow: '0 2px 18px rgba(15,20,30,0.08)',
      padding: '28px 22px 30px',
      boxSizing: 'border-box',
    },
    header: { borderBottom: '1px solid #e8eaed', paddingBottom: 16, marginBottom: 22 },
    brand: { fontSize: 19, fontWeight: 700, color: accent, letterSpacing: '-0.01em' },
    brandSub: { fontSize: 13.5, color: '#5f6368', marginTop: 3 },
    h1: { fontSize: 23, lineHeight: 1.25, margin: '0 0 10px', fontWeight: 650 },
    body: { fontSize: 15.5, lineHeight: 1.6, color: '#33363a', margin: '0 0 14px' },
    muted: { fontSize: 14.5, color: '#6b7075', margin: 0 },
    label: { display: 'block', fontSize: 14.5, fontWeight: 600, color: '#33363a', margin: '6px 0 7px' },
    error: {
      fontSize: 14.5, color: '#8a1c1c', background: '#fdecec',
      border: '1px solid #f3c9c9', borderRadius: 8,
      padding: '10px 12px', margin: '0 0 14px',
    },
    link: { color: accent, fontWeight: 600, textDecoration: 'none' },
    starRow: { display: 'flex', gap: 6, margin: '6px 0 18px' },
    starOff: {
      flex: '1 1 0', padding: '12px 0', fontSize: 30, lineHeight: 1,
      border: '1px solid #dcdfe3', borderRadius: 10, background: '#fff',
      color: '#d3d7dc', cursor: 'pointer', font: 'inherit', fontFamily: 'inherit',
    },
    starOn: {
      flex: '1 1 0', padding: '12px 0', fontSize: 30, lineHeight: 1,
      border: `1px solid ${accent}`, borderRadius: 10, background: '#fff',
      color: accent, cursor: 'pointer', font: 'inherit', fontFamily: 'inherit',
    },
    textarea: {
      width: '100%', boxSizing: 'border-box', padding: '11px 12px',
      border: '1px solid #dcdfe3', borderRadius: 9, font: 'inherit',
      fontSize: 15, lineHeight: 1.5, resize: 'vertical', margin: '0 0 16px',
      fontFamily: 'inherit', color: '#1c1d1f', background: '#fff',
    },
    linkStack: { display: 'flex', flexDirection: 'column', gap: 9, marginTop: 4 },
    linkButton: {
      display: 'block', textAlign: 'center', padding: '13px 14px',
      border: `1px solid ${accent}`, borderRadius: 10, color: accent,
      fontSize: 15.5, fontWeight: 600, textDecoration: 'none', background: '#fff',
    },
    cta: {
      width: '100%', padding: '14px 16px', border: 'none', borderRadius: 10,
      background: accent, color: '#fff', fontSize: 16, fontWeight: 650,
      cursor: 'pointer', font: 'inherit',
    },
    ctaDisabled: {
      width: '100%', padding: '14px 16px', border: 'none', borderRadius: 10,
      background: '#d6d9dd', color: '#8a8f95', fontSize: 16, fontWeight: 650,
      cursor: 'not-allowed', font: 'inherit',
    },
  };
}
