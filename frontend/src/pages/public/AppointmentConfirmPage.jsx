import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { API_BASE } from '../../api/client';

// Public, unauthenticated meeting-confirmation page on the BRAND's own host:
//   https://app.evosyspro.live/appointments/confirm/:token
//
// A prospect has no account and never sees a login. This page exists so the
// link in their invitation carries the brand's hostname rather than the API's -
// `advisorflow-backend.onrender.com` in an invitation reads as phishing to a
// stranger, and it outlives the deployment in their inbox.
//
// It reuses the existing token logic exactly:
//   GET  /sales/appointments/confirm/{token}/context  -> side-effect free
//   POST /sales/appointments/confirm/{token}/respond  -> the same redeem_token
//
// The GET changing nothing is deliberate and load-bearing: corporate mail
// scanners (Safe Links, Proofpoint, Mimecast) fetch every link in an inbound
// message. If loading this page confirmed the meeting, a security appliance
// would auto-confirm invitations within seconds of delivery and the prospect's
// real answer would never be recorded.

export default function AppointmentConfirmPage() {
  const { token } = useParams();

  const [loading, setLoading] = useState(true);
  const [ctx, setCtx] = useState(null);
  const [loadError, setLoadError] = useState('');

  const [pending, setPending] = useState('');    // 'confirm' | 'decline' | ''
  const [actionError, setActionError] = useState('');
  const [result, setResult] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError('');
      try {
        const res = await fetch(
          `${API_BASE}/sales/appointments/confirm/${encodeURIComponent(token)}/context`
        );
        const data = await res.json().catch(() => ({}));
        if (cancelled) return;
        if (!res.ok || !data.ok) {
          setLoadError(data.error || 'This link is no longer valid.');
          setCtx(data && data.support_phone ? data : null);
          return;
        }
        setCtx(data);
      } catch (err) {
        if (!cancelled) setLoadError('We could not open this link. Please try again in a moment.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  // THE BRAND, NOT THE PLATFORM'S ONE STATIC TITLE.
  //
  // Unlike /book and /survey, the name a prospect should see here IS the
  // platform's - they are being sold EvoSys Pro or BookaBoost. The bug was
  // that the tab showed whatever index.html hard-codes, so a BookaBoost
  // prospect read "EvoSys Pro". Resolved per brand, server-side.
  const resolvedTitle = ctx?.document_title || ctx?.brand_name || '';
  useEffect(() => {
    if (!resolvedTitle) return undefined;
    const previous = document.title;
    document.title = resolvedTitle;
    return () => { document.title = previous; };
  }, [resolvedTitle]);

  async function respond(action) {
    if (pending) return;                 // guards double submit
    setPending(action);
    setActionError('');
    try {
      const res = await fetch(
        `${API_BASE}/sales/appointments/confirm/${encodeURIComponent(token)}/respond`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ action }),
        }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        throw new Error(data.error || 'Something went wrong. Please try the link again.');
      }
      setResult(data);
    } catch (err) {
      setActionError(err.message || 'Something went wrong. Please try the link again.');
    } finally {
      setPending('');                    // exits loading on the failure path too
    }
  }

  const accent = ctx?.accent || '#1d4ed8';
  const phone = ctx?.support_phone || result?.support_phone || '';
  const S = styles(accent);

  if (loading) {
    return (
      <div style={S.page}><div style={S.card}><p style={S.muted}>Loading…</p></div></div>
    );
  }

  if (loadError) {
    return (
      <div style={S.page}>
        <div style={S.card}>
          <h1 style={S.h1}>{loadError}</h1>
          <p style={S.muted}>
            {phone
              ? <>Please call {phone} and we will sort it out.</>
              : 'Please contact whoever arranged this meeting.'}
          </p>
        </div>
      </div>
    );
  }

  if (result) {
    return (
      <div style={S.page}>
        <div style={S.card}>
          {result.action === 'confirm' ? (
            <>
              <h1 style={S.h1}>You're confirmed</h1>
              <p style={S.when}>{result.when}</p>
            </>
          ) : (
            <>
              <h1 style={S.h1}>Thanks for letting us know</h1>
              <p style={S.muted}>We've told the team you can't make it.</p>
            </>
          )}
          {phone ? <p style={S.muted}>Need to change something? Call {phone}.</p> : null}
        </div>
      </div>
    );
  }

  if (ctx?.cancelled) {
    return (
      <div style={S.page}>
        <div style={S.card}>
          <h1 style={S.h1}>This meeting has been cancelled</h1>
          <p style={S.muted}>No action is needed.</p>
        </div>
      </div>
    );
  }

  const already =
    ctx?.confirmation_status === 'confirmed'
      ? 'You have already confirmed. You can change your answer below.'
      : ctx?.confirmation_status === 'declined'
        ? 'You previously declined. You can change your answer below.'
        : '';

  return (
    <div style={S.page}>
      <div style={S.card}>
        <h1 style={S.h1}>{ctx?.title || 'Your meeting'}</h1>
        <p style={S.muted}>with {ctx?.brand_name || 'us'}</p>
        <p style={S.when}>{ctx?.when}</p>
        {already ? <p style={S.muted}>{already}</p> : null}

        {actionError ? <p style={S.error}>{actionError}</p> : null}

        <div style={S.actions}>
          <button
            type="button"
            disabled={!!pending}
            onClick={() => respond('confirm')}
            style={pending ? S.yesBusy : S.yes}
          >
            {pending === 'confirm' ? 'Confirming…' : "Yes, I'll be there"}
          </button>
          <button
            type="button"
            disabled={!!pending}
            onClick={() => respond('decline')}
            style={pending ? S.noBusy : S.no}
          >
            {pending === 'decline' ? 'Sending…' : "I can't make it"}
          </button>
        </div>

        {phone ? <p style={S.muted}>Need a different time? Call {phone}.</p> : null}
      </div>
    </div>
  );
}

function styles(accent) {
  const base = {
    font: 'inherit', fontWeight: 600, padding: '12px 20px',
    borderRadius: 8, border: '1px solid transparent', cursor: 'pointer',
  };
  return {
    page: {
      minHeight: '100vh', background: '#f8fafc', margin: 0,
      padding: '40px 16px', color: '#111827', boxSizing: 'border-box',
      fontFamily: "-apple-system, system-ui, 'Segoe UI', Arial, sans-serif",
    },
    card: {
      maxWidth: 520, margin: '0 auto', background: '#fff',
      border: '1px solid #e5e7eb', borderRadius: 12, padding: 28,
      boxSizing: 'border-box',
    },
    h1: { fontSize: 20, margin: '0 0 6px', lineHeight: 1.3 },
    muted: { color: '#6b7280', fontSize: 14, margin: '0 0 10px', lineHeight: 1.55 },
    when: { fontSize: 17, fontWeight: 600, margin: '18px 0' },
    error: {
      fontSize: 14, color: '#8a1c1c', background: '#fdecec',
      border: '1px solid #f3c9c9', borderRadius: 8,
      padding: '10px 12px', margin: '0 0 14px',
    },
    actions: { display: 'flex', flexWrap: 'wrap', gap: 10, margin: '0 0 16px' },
    yes: { ...base, background: accent, color: '#fff' },
    yesBusy: { ...base, background: '#93a3c9', color: '#fff', cursor: 'wait' },
    no: { ...base, background: '#fff', borderColor: '#d1d5db', color: '#374151' },
    noBusy: { ...base, background: '#f3f4f6', borderColor: '#e5e7eb', color: '#9ca3af', cursor: 'wait' },
  };
}
