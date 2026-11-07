import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { API_BASE } from '../../api/client';

// Public post-visit survey (Issue #507, Restland). The URL carries only an
// unguessable token. Four questions, nothing about the family's file, no
// review links. One answer per visit.

const GREEN = '#193c32';
const BRASS = '#c3a66a';

export default function VisitSurvey() {
  const { token } = useParams();
  const [ctx, setCtx] = useState(null);
  const [err, setErr] = useState('');
  const [rating, setRating] = useState(0);
  const [helpful, setHelpful] = useState('');
  const [comments, setComments] = useState('');
  const [follow, setFollow] = useState(false);
  const [method, setMethod] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState('');

  useEffect(() => {
    fetch(`${API_BASE}/visit-survey/${encodeURIComponent(token)}`)
      .then(async (r) => { const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.detail || 'This survey link isn’t valid.'); return d; })
      .then(setCtx).catch((e) => setErr(e.message));
  }, [token]);

  useEffect(() => { document.title = 'Restland Cemetery and Funeral Home'; }, []);

  async function submit() {
    if (!rating || !helpful || (follow && !method) || busy) return;
    setBusy(true); setErr('');
    try {
      const r = await fetch(`${API_BASE}/visit-survey/${encodeURIComponent(token)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating, helpful, comments, follow_up: follow, follow_up_method: follow ? method : null }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || 'We couldn’t save your answers. Please try again.');
      setDone(follow ? 'Thank you. Someone will be in touch the way you asked.' : 'Thank you for sharing.');
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  const S = {
    page: { minHeight: '100vh', background: '#f7f5ed', padding: '28px 16px', boxSizing: 'border-box', fontFamily: 'Inter, system-ui, sans-serif', color: '#24392f' },
    card: { maxWidth: 560, margin: '0 auto', background: '#fffefa', borderRadius: 18, padding: '24px 20px', boxShadow: '0 10px 30px rgba(25,60,50,.12)', border: `1px solid ${BRASS}55` },
    h1: { fontFamily: "Georgia, 'Times New Roman', serif", fontSize: 26, margin: '0 0 6px', color: GREEN },
    q: { fontWeight: 700, margin: '20px 0 8px' },
    row: { display: 'flex', gap: 8, flexWrap: 'wrap' },
    pick: (on) => ({ padding: '10px 14px', borderRadius: 12, border: on ? `2px solid ${GREEN}` : '1.5px solid #d9d4c5', background: on ? '#e9efe9' : '#fff', cursor: 'pointer', font: 'inherit', minWidth: 48 }),
    cta: (off) => ({ marginTop: 22, width: '100%', padding: '14px', borderRadius: 12, border: 'none', background: off ? '#d9d6cd' : GREEN, color: off ? '#7b7f7c' : '#fff', fontWeight: 700, fontSize: 16, cursor: off ? 'not-allowed' : 'pointer' }),
  };

  if (err && !ctx) return <div style={S.page}><div style={S.card}><h1 style={S.h1}>Survey</h1><p>{err}</p></div></div>;
  if (!ctx) return <div style={S.page}><div style={S.card}><p>Opening…</p></div></div>;
  if (done || ctx.state === 'submitted') return <div style={S.page}><div style={S.card}><h1 style={S.h1}>Thank you</h1><p>{done || 'We already have your answers. Thank you for taking the time.'}</p></div></div>;
  if (ctx.state === 'expired') return <div style={S.page}><div style={S.card}><h1 style={S.h1}>This survey has closed</h1><p>If you’d like to share anything, just reply to our message and we’ll be glad to hear from you.</p></div></div>;

  const off = !rating || !helpful || (follow && !method) || busy;
  return (
    <div style={S.page}>
      <div style={S.card}>
        <div style={{ fontSize: 12, letterSpacing: '.18em', textTransform: 'uppercase', color: BRASS, fontWeight: 700 }}>{ctx.business}</div>
        <h1 style={S.h1}>{ctx.first_name ? `Thank you, ${ctx.first_name}` : 'Thank you'}</h1>
        <p style={{ margin: 0 }}>for meeting with {ctx.advisor}. Four quick questions.</p>

        <div style={S.q}>1. Overall, how was your experience?</div>
        <div style={S.row} role="radiogroup" aria-label="Overall experience, 1 to 5">
          {[1, 2, 3, 4, 5].map((n) => <button key={n} type="button" role="radio" aria-checked={rating === n} style={S.pick(rating === n)} onClick={() => setRating(n)}>{n}</button>)}
        </div>
        <div style={{ fontSize: 12.5, color: '#6b746f', marginTop: 4 }}>1 = poor · 5 = excellent</div>

        <div style={S.q}>2. Was the review helpful?</div>
        <div style={S.row} role="radiogroup" aria-label="Was the review helpful">
          {[['yes', 'Yes'], ['somewhat', 'Somewhat'], ['no', 'No']].map(([k, l]) => <button key={k} type="button" role="radio" aria-checked={helpful === k} style={S.pick(helpful === k)} onClick={() => setHelpful(k)}>{l}</button>)}
        </div>

        <div style={S.q}>3. Anything you’d like to share? <span style={{ fontWeight: 400, color: '#6b746f' }}>(optional)</span></div>
        <textarea value={comments} onChange={(e) => setComments(e.target.value)} maxLength={2000} rows={4} aria-label="Comments"
          style={{ width: '100%', boxSizing: 'border-box', borderRadius: 12, border: '1.5px solid #d9d4c5', padding: 12, font: 'inherit' }} />

        <div style={S.q}>4. Would you like someone to follow up with you? <span style={{ fontWeight: 400, color: '#6b746f' }}>(optional)</span></div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input type="checkbox" checked={follow} onChange={(e) => { setFollow(e.target.checked); if (!e.target.checked) setMethod(''); }} /> Yes, please follow up
        </label>
        {follow ? <div style={{ ...S.row, marginTop: 10 }} role="radiogroup" aria-label="Preferred contact method">
          {[['phone', 'Phone call'], ['text', 'Text'], ['email', 'Email']].map(([k, l]) => <button key={k} type="button" role="radio" aria-checked={method === k} style={S.pick(method === k)} onClick={() => setMethod(k)}>{l}</button>)}
        </div> : null}

        {err ? <p style={{ color: '#8a1c1c' }}>{err}</p> : null}
        <button type="button" disabled={off} onClick={submit} style={S.cta(off)}>{busy ? 'Sending…' : 'Send'}</button>
      </div>
    </div>
  );
}
