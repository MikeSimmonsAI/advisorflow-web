import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { API_BASE } from '../../api/client';

// Public, unauthenticated booking page served on the BRANDED domain:
//   https://app.evosyspro.live/book/:token
//
// Every identity value on this page (business name, address, phone, brand color)
// comes from the backend public-identity resolver via GET /calendar/booking/{token}.
// Nothing is hard-coded, and no platform or infrastructure hostname is ever shown
// to a family. This page reuses the EXISTING booking endpoints - it is not a
// parallel booking system:
//
//   GET  /calendar/booking/{token}   -> context (advisor_id, org identity, status)
//   GET  /calendar/slots             -> availability (fails closed, returns `reason`)
//   POST /calendar/booking-confirmed -> the same webhook the Vercel app called
//
// Slot times are the funeral home's wall-clock times. They are formatted by
// string parsing, never through `new Date()`, so a slot published as 10:00
// renders as 10:00 AM for a family in any timezone.

const DAY_LABELS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTH_LABELS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

// The backend accepts weekdays only, today through today+14. Offering a day the
// backend will reject with a 400 is a dead end for the family, so the chips only
// ever contain days the backend will actually answer for.
function bookableDays() {
  const out = [];
  const now = new Date();
  for (let i = 0; i <= 14; i += 1) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
    const dow = d.getDay();
    if (dow === 0 || dow === 6) continue;
    out.push({
      iso: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`,
      weekday: DAY_LABELS[dow],
      month: MONTH_LABELS[d.getMonth()],
      day: d.getDate(),
      isToday: i === 0,
    });
  }
  return out;
}

// Format "2026-08-31T14:30:00" as "2:30 PM" without constructing a Date.
function formatSlotLabel(value) {
  if (!value) return '';
  const raw = String(value);
  const tIndex = raw.indexOf('T');
  const timePart = tIndex >= 0 ? raw.slice(tIndex + 1) : raw;
  const match = timePart.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return raw;
  let hour = parseInt(match[1], 10);
  const minute = match[2];
  const suffix = hour >= 12 ? 'PM' : 'AM';
  if (hour === 0) hour = 12;
  else if (hour > 12) hour -= 12;
  return `${hour}:${minute} ${suffix}`;
}

function labelForDay(days, iso) {
  const match = days.find((d) => d.iso === iso);
  if (!match) return iso;
  return `${match.weekday}, ${match.month} ${match.day}`;
}

// The backend stores E.164. Families read (469) 553-7417.
function formatPhone(value) {
  if (!value) return '';
  const digits = String(value).replace(/\D/g, '');
  const ten = digits.length === 11 && digits[0] === '1' ? digits.slice(1) : digits;
  if (ten.length !== 10) return String(value);
  return `(${ten.slice(0, 3)}) ${ten.slice(3, 6)}-${ten.slice(6)}`;
}

export default function BookingPage() {
  const { token } = useParams();

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [context, setContext] = useState(null);

  const days = useMemo(() => bookableDays(), []);
  const [selectedDay, setSelectedDay] = useState(days[0] ? days[0].iso : '');
  const [slots, setSlots] = useState([]);
  const [slotsLoading, setSlotsLoading] = useState(false);
  const [slotsReason, setSlotsReason] = useState('');
  const [selectedSlot, setSelectedSlot] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [confirmed, setConfirmed] = useState(null);
  // Outreach program families choose a phone call or a visit first.
  const [meeting, setMeeting] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setLoadError('');
      try {
        const res = await fetch(`${API_BASE}/calendar/booking/${encodeURIComponent(token)}`);
        if (!res.ok) {
          throw new Error(
            res.status === 404
              ? 'This scheduling link is no longer active.'
              : 'We could not open this scheduling link.'
          );
        }
        const data = await res.json();
        if (cancelled) return;
        setContext(data);
        // An already-booked token must not present an empty picker.
        if (data.status === 'booked' || data.status === 'confirmed') {
          setConfirmed({ alreadyBooked: true, slot: '', day: '' });
        }
      } catch (err) {
        if (!cancelled) setLoadError(err.message || 'This scheduling link is no longer active.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [token]);

  // THE BROWSER TAB IS PART OF THE BRANDING.
  //
  // index.html ships a single static <title> for the whole SPA, which is the
  // platform's name. On a page a funeral home's family opens, that read
  // "EvoSys Pro" - a company they have never heard of, sitting in their tab
  // and in their history next to the appointment they just made. The resolved
  // business name replaces it; an organization with no name resolved leaves
  // the static title alone rather than inventing one.
  const resolvedTitle = context?.branding?.document_title || context?.org_name || '';
  useEffect(() => {
    if (!resolvedTitle) return undefined;
    const previous = document.title;
    document.title = resolvedTitle;
    return () => { document.title = previous; };
  }, [resolvedTitle]);

  const advisorId = context?.advisor_id || '';

  const loadSlots = useCallback(async (dayIso) => {
    if (!dayIso || !advisorId) return;
    setSlotsLoading(true);
    setSlotsReason('');
    setSlots([]);
    setSelectedSlot('');
    try {
      const params = new URLSearchParams({ advisor_id: advisorId, date: dayIso, token });
      const res = await fetch(`${API_BASE}/calendar/slots?${params.toString()}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setSlotsReason(
          typeof data.detail === 'string' ? data.detail : 'We could not load times for that day.'
        );
        return;
      }
      const list = Array.isArray(data.slots) ? data.slots : [];
      setSlots(list);
      if (list.length === 0) {
        // /calendar/slots FAILS CLOSED: when the advisor's calendar cannot be
        // read it returns no slots plus a reason. Showing the day as simply
        // "full" would hide that; showing the reason lets the family call.
        setSlotsReason(data.reason || 'There are no remaining times on that day.');
      }
    } catch (err) {
      setSlotsReason('We could not load times for that day. Please try again in a moment.');
    } finally {
      setSlotsLoading(false);
    }
  }, [advisorId, token]);

  useEffect(() => {
    if (!context || confirmed) return;
    loadSlots(selectedDay);
  }, [context, confirmed, selectedDay, loadSlots]);

  async function confirmBooking() {
    if (!selectedSlot || submitting) return;   // guards double submit
    if (meetingOptions && !chosenMeeting) return;
    setSubmitting(true);
    setSubmitError('');
    try {
      const res = await fetch(`${API_BASE}/calendar/booking-confirmed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Field names are the existing webhook's contract, unchanged.
        // slot_display is the naive ISO the backend parses with %Y-%m-%dT%H:%M:%S.
        body: JSON.stringify({
          booking_token: token,
          slot_display: selectedSlot,
          lead_name: context?.lead_name || '',
          lead_phone: context?.lead_phone || '',
          appt_label: chosenMeeting ? chosenMeeting.appt_label : (context?.appt_label || 'Appointment'),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          typeof data.detail === 'string'
            ? data.detail
            : 'We could not confirm that time. Please choose another.'
        );
      }
      setConfirmed({ slot: selectedSlot, day: selectedDay });
    } catch (err) {
      setSubmitError(err.message || 'We could not confirm that time.');
      loadSlots(selectedDay);            // exits loading on the failure path too
    } finally {
      setSubmitting(false);
    }
  }

  // `branding` is the block the public-identity resolver returns; the flat
  // fields are the older shape and stay as the fallback so a link issued
  // before this deploy still renders.
  const brand = context?.branding || {};
  const accent = brand.brand_color || context?.brand_color || '#1f4e79';
  const place = context?.program_location || null;
  const businessName = (place && place.name) || brand.name || context?.org_name || '';
  const businessAddress = place ? [place.street, place.city].filter(Boolean).join(' · ') : (brand.address || context?.org_address || '');
  const businessLogo = brand.logo_url || '';
  const rawPhone = (place && place.phone) || brand.phone || context?.org_phone || '';
  const businessPhone = formatPhone(rawPhone);
  const firstName = context?.lead_first_name || '';
  const hostName = (place && place.contact_name) || '';
  const hostTitle = (place && place.contact_title) || 'Family Service Advisor';
  const duration = context?.appt_duration || 30;
  const meetingOptions = Array.isArray(context?.meeting_options) && context.meeting_options.length
    ? context.meeting_options : null;
  const chosenMeeting = meetingOptions ? meetingOptions.find((m) => m.key === meeting) : null;
  const apptLabel = chosenMeeting ? chosenMeeting.appt_label : (context?.appt_label || 'appointment');
  const S = styles(accent);
  const morning = slots.filter((x) => parseInt(String(x).split('T')[1] || '0', 10) < 12);
  const afternoon = slots.filter((x) => parseInt(String(x).split('T')[1] || '0', 10) >= 12);
  const step = confirmed ? 3 : selectedSlot ? 3 : 2;
  const mapsUrl = place && (place.street || place.city)
    ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([businessName, place.street, place.city].filter(Boolean).join(', '))}`
    : '';

  function downloadIcs() {
    if (!confirmed || !confirmed.slot) return;
    const raw = String(confirmed.slot).replace(/[-:]/g, '').slice(0, 15);
    const [d, t] = raw.split('T');
    const startH = parseInt(t.slice(0, 2), 10);
    const startM = parseInt(t.slice(2, 4), 10);
    const endTotal = startH * 60 + startM + duration;
    const end = `${d}T${String(Math.floor(endTotal / 60)).padStart(2, '0')}${String(endTotal % 60).padStart(2, '0')}00`;
    const tz = context?.timezone || 'America/Chicago';
    const ics = [
      'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Booking//EN', 'BEGIN:VEVENT',
      `UID:${token}@booking`, `DTSTART;TZID=${tz}:${d}T${t.slice(0, 4)}00`, `DTEND;TZID=${tz}:${end}`,
      `SUMMARY:${apptLabel}`, `LOCATION:${[businessName, businessAddress].filter(Boolean).join(', ')}`,
      `DESCRIPTION:${hostName ? `With ${hostName}. ` : ''}${businessPhone ? `Questions? Call ${businessPhone}.` : ''}`,
      'END:VEVENT', 'END:VCALENDAR',
    ].join('\r\n');
    const url = URL.createObjectURL(new Blob([ics], { type: 'text/calendar' }));
    const a = document.createElement('a');
    a.href = url; a.download = 'appointment.ics'; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }

  if (loading) {
    return (
      <div style={S.page}>
        <style>{KEYFRAMES}</style>
        <div style={{ ...S.card, textAlign: 'center' }}><div style={S.spinner} /><p style={S.muted}>Opening your calendar…</p></div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div style={S.page}>
        <div style={S.card}>
          <h1 style={S.h1}>This link is no longer active</h1>
          <p style={S.body}>{loadError}</p>
          <p style={S.body}>Please contact us directly and we will find a time with you.</p>
        </div>
      </div>
    );
  }

  const SlotButton = ({ slot }) => {
    const active = slot === selectedSlot;
    return (
      <button key={slot} type="button" aria-pressed={active} onClick={() => setSelectedSlot(slot)}
        style={active ? S.slotActive : S.slot}>
        {formatSlotLabel(slot)}
      </button>
    );
  };

  return (
    <div style={S.page}>
      <style>{KEYFRAMES}</style>
      <div style={S.glow} aria-hidden="true" />
      <div style={S.shell}>
        <header style={S.hero}>
          {businessLogo ? <img src={businessLogo} alt={businessName} style={S.logo} /> : null}
          <div style={S.eyebrow}>{confirmed && !confirmed.alreadyBooked ? 'Confirmed' : 'Private appointment'}</div>
          {businessName ? <div style={S.brand}>{businessName}</div> : null}
          <div style={S.pills}>
            {businessAddress ? (
              mapsUrl
                ? <a style={S.pill} href={mapsUrl} target="_blank" rel="noreferrer">📍 {businessAddress}</a>
                : <span style={S.pill}>📍 {businessAddress}</span>
            ) : null}
            {businessPhone ? <a style={S.pill} href={`tel:${rawPhone}`}>📞 {businessPhone}</a> : null}
            <span style={S.pill}>⏱ {duration} minutes</span>
          </div>
          {hostName ? (
            <div style={S.host}>
              <div style={S.avatar}>{hostName.split(' ').map((x) => x[0]).slice(0, 2).join('')}</div>
              <div>
                <div style={S.hostName}>{hostName}</div>
                <div style={S.hostTitle}>{hostTitle} · will meet you personally</div>
              </div>
            </div>
          ) : null}
        </header>

        <main style={S.card}>
          {confirmed ? (
            <section style={{ textAlign: 'center', animation: 'bkRise .5s ease both' }}>
              <div style={S.check}>✓</div>
              <h1 style={{ ...S.h1, textAlign: 'center' }}>
                {confirmed.alreadyBooked ? 'You’re already on the calendar' : `You’re all set${firstName ? `, ${firstName}` : ''}`}
              </h1>
              {confirmed.alreadyBooked ? (
                <p style={S.body}>We already have your appointment. If you need to change it, call us and we will take care of it.</p>
              ) : (
                <>
                  <div style={S.ticket}>
                    <div style={S.ticketDay}>{labelForDay(days, confirmed.day)}</div>
                    <div style={S.ticketTime}>{formatSlotLabel(confirmed.slot)}</div>
                    <div style={S.ticketWhere}>{apptLabel}</div>
                  </div>
                  <p style={S.body}>
                    A confirmation is on its way.{hostName ? ` ${hostName.split(' ')[0]} will be expecting you.` : ''}
                    {businessPhone ? <> Need to change anything? Call <a style={S.link} href={`tel:${rawPhone}`}>{businessPhone}</a>.</> : ''}
                  </p>
                  <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
                    <button type="button" style={S.ghost} onClick={downloadIcs}>Add to my calendar</button>
                    {mapsUrl ? <a style={{ ...S.ghost, textDecoration: 'none' }} href={mapsUrl} target="_blank" rel="noreferrer">Get directions</a> : null}
                  </div>
                </>
              )}
            </section>
          ) : (
            <section style={{ animation: 'bkRise .45s ease both' }}>
              <ol style={S.steps} aria-label="Steps">
                {['Pick a day', 'Pick a time', 'Confirm'].map((label, i) => (
                  <li key={label} style={S.stepItem(i + 1 <= step, i + 1 === step)}>
                    <span style={S.stepDot(i + 1 <= step)}>{i + 1}</span>{label}
                  </li>
                ))}
              </ol>
              <h1 style={S.h1}>
                {firstName ? `${firstName}, let’s find a time that works for you` : 'Let’s find a time that works for you'}
              </h1>
              <p style={S.body}>
                {meetingOptions ? 'Choose how you would like to meet, then a day and a time.' : 'Pick a day, then a time. It takes about 20 seconds.'}
                {' '}There is no cost and no obligation.
              </p>

              {meetingOptions ? (
                <div style={{ display: 'flex', gap: 10, margin: '6px 0 18px', flexWrap: 'wrap' }} role="radiogroup" aria-label="How would you like to meet?">
                  {meetingOptions.map((m) => {
                    const on = m.key === meeting;
                    return (
                      <button key={m.key} type="button" role="radio" aria-checked={on} onClick={() => setMeeting(m.key)}
                        style={{
                          flex: '1 1 200px', textAlign: 'left', padding: '14px 16px', borderRadius: 14, cursor: 'pointer',
                          border: on ? `2px solid ${accent}` : '1.5px solid #e2ded4', background: on ? '#f8f1e1' : '#fff',
                          font: 'inherit', fontFamily: 'inherit', color: '#1c1d1f',
                        }}>
                        <div style={{ fontWeight: 700, fontSize: 16 }}>{m.label}</div>
                        <div style={{ fontSize: 13.5, color: '#5f6368', marginTop: 3 }}>{m.note}</div>
                      </button>
                    );
                  })}
                </div>
              ) : null}

              <div style={S.dayRow}>
                {days.map((d) => {
                  const active = d.iso === selectedDay;
                  return (
                    <button key={d.iso} type="button" aria-pressed={active} onClick={() => setSelectedDay(d.iso)}
                      style={active ? S.dayChipActive : S.dayChip}>
                      <span style={S.dayWeekday}>{d.isToday ? 'Today' : d.weekday}</span>
                      <span style={S.dayNumber}>{d.day}</span>
                      <span style={S.dayMonth}>{d.month}</span>
                    </button>
                  );
                })}
              </div>

              <div style={S.slotArea}>
                {slotsLoading ? (
                  <div style={S.skeletonGrid}>{[0, 1, 2, 3, 4, 5].map((i) => <div key={i} style={S.skeleton} />)}</div>
                ) : slots.length > 0 ? (
                  <>
                    {morning.length ? <div style={S.slotHead}>☀️ Morning</div> : null}
                    {morning.length ? <div style={S.slotGrid}>{morning.map((x) => <SlotButton key={x} slot={x} />)}</div> : null}
                    {afternoon.length ? <div style={S.slotHead}>🌤 Afternoon</div> : null}
                    {afternoon.length ? <div style={S.slotGrid}>{afternoon.map((x) => <SlotButton key={x} slot={x} />)}</div> : null}
                  </>
                ) : (
                  <p style={S.notice}>{slotsReason || 'There are no remaining times on that day.'} Try another day above.</p>
                )}
              </div>

              {submitError ? <p style={S.error}>{submitError}</p> : null}

              <div style={S.ctaBar}>
                <button type="button"
                  disabled={!selectedSlot || submitting || (meetingOptions && !chosenMeeting)}
                  onClick={confirmBooking}
                  style={(!selectedSlot || submitting || (meetingOptions && !chosenMeeting)) ? S.ctaDisabled : S.cta}>
                  {submitting
                    ? 'Confirming…'
                    : meetingOptions && !chosenMeeting
                      ? 'Choose a phone call or a visit'
                      : selectedSlot
                        ? `Confirm ${labelForDay(days, selectedDay)} at ${formatSlotLabel(selectedSlot)}`
                        : 'Select a time'}
                </button>
              </div>
              <ul style={S.assure}>
                <li>✔ No cost, no obligation</li>
                <li>✔ About {duration} minutes</li>
                <li>✔ Bring any questions you have</li>
              </ul>
            </section>
          )}
        </main>
        <footer style={S.footer}>{businessName ? `${businessName}` : ''}{businessPhone ? ` · ${businessPhone}` : ''}</footer>
      </div>
    </div>
  );
}

const KEYFRAMES = `
@import url('https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Inter:wght@400;500;600;700&display=swap');
@keyframes bkRise { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
@keyframes bkPop { 0% { transform: scale(.4); opacity: 0; } 70% { transform: scale(1.08); opacity: 1; } 100% { transform: scale(1); } }
@keyframes bkShimmer { 0% { background-position: -200px 0; } 100% { background-position: 200px 0; } }
@keyframes bkSpin { to { transform: rotate(360deg); } }
`;

function styles(accent) {
  const gold = '#c9a24a';
  const ink = '#16221f';
  const slotBase = {
    padding: '13px 8px', borderRadius: 12, cursor: 'pointer', font: 'inherit',
    fontSize: 15, fontWeight: 600, transition: 'transform .12s ease, box-shadow .12s ease',
  };
  const chipBase = {
    flex: '0 0 auto', width: 66, padding: '10px 0', borderRadius: 14, cursor: 'pointer',
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 1, font: 'inherit',
    transition: 'transform .12s ease',
  };
  return {
    page: {
      minHeight: '100vh', position: 'relative', overflow: 'hidden',
      background: `radial-gradient(1200px 600px at 10% -10%, ${accent}ee 0%, transparent 60%), linear-gradient(160deg, #0f1f1d 0%, ${accent} 55%, #0d1716 100%)`,
      padding: '28px 16px 40px', boxSizing: 'border-box',
      fontFamily: "Inter, system-ui, -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif", color: ink,
    },
    glow: {
      position: 'absolute', width: 520, height: 520, right: -160, top: -120, borderRadius: '50%',
      background: `radial-gradient(circle, ${gold}55 0%, transparent 65%)`, pointerEvents: 'none',
    },
    shell: { position: 'relative', maxWidth: 680, margin: '0 auto' },
    hero: { color: '#fff', textAlign: 'center', padding: '8px 4px 22px', animation: 'bkRise .4s ease both' },
    logo: { maxHeight: 56, maxWidth: 240, display: 'block', margin: '0 auto 12px' },
    eyebrow: { fontSize: 12, letterSpacing: '0.22em', textTransform: 'uppercase', color: gold, fontWeight: 700 },
    brand: {
      fontFamily: "'Cormorant Garamond', Georgia, 'Times New Roman', serif", fontSize: 'clamp(30px, 6vw, 44px)',
      fontWeight: 700, lineHeight: 1.08, margin: '8px 0 14px', letterSpacing: '-0.01em',
    },
    pills: { display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' },
    pill: {
      fontSize: 13, color: '#f3efe6', background: 'rgba(255,255,255,0.10)', border: '1px solid rgba(255,255,255,0.18)',
      padding: '6px 12px', borderRadius: 999, textDecoration: 'none', backdropFilter: 'blur(6px)',
    },
    host: {
      display: 'inline-flex', alignItems: 'center', gap: 12, marginTop: 18, textAlign: 'left',
      background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.16)', borderRadius: 16, padding: '10px 16px 10px 10px',
    },
    avatar: {
      width: 44, height: 44, borderRadius: '50%', display: 'grid', placeItems: 'center', fontWeight: 700,
      background: `linear-gradient(135deg, ${gold}, #f1dc9c)`, color: ink, fontSize: 16,
    },
    hostName: { fontWeight: 700, fontSize: 15.5 },
    hostTitle: { fontSize: 12.5, opacity: 0.8 },
    card: {
      background: 'rgba(255,255,255,0.97)', borderRadius: 22, padding: '26px 22px 24px', boxSizing: 'border-box',
      boxShadow: '0 30px 80px rgba(0,0,0,0.35), 0 2px 0 rgba(255,255,255,0.6) inset', border: `1px solid ${gold}40`,
    },
    steps: { listStyle: 'none', padding: 0, margin: '0 0 18px', display: 'flex', gap: 6, flexWrap: 'wrap' },
    stepItem: (done, current) => ({
      display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, fontWeight: 600, padding: '6px 11px 6px 6px', borderRadius: 999,
      background: current ? `${accent}14` : 'transparent', color: done ? accent : '#9aa0a6',
    }),
    stepDot: (done) => ({
      width: 22, height: 22, borderRadius: '50%', display: 'grid', placeItems: 'center', fontSize: 12,
      background: done ? accent : '#e8eaed', color: done ? '#fff' : '#80868b',
    }),
    h1: {
      fontFamily: "'Cormorant Garamond', Georgia, 'Times New Roman', serif", fontSize: 'clamp(25px, 4.6vw, 31px)',
      lineHeight: 1.15, margin: '0 0 8px', fontWeight: 700, color: ink,
    },
    body: { fontSize: 15.5, lineHeight: 1.6, color: '#3b4442', margin: '0 0 16px' },
    muted: { fontSize: 14.5, color: '#6b7075', margin: '10px 0 0' },
    spinner: {
      width: 34, height: 34, margin: '6px auto 4px', borderRadius: '50%', border: '3px solid #e7e2d6',
      borderTopColor: accent, animation: 'bkSpin .8s linear infinite',
    },
    notice: {
      fontSize: 14.5, lineHeight: 1.55, color: '#6b4a00', background: '#fff8e6', border: '1px solid #f2e0b0',
      borderRadius: 12, padding: '12px 14px', margin: 0,
    },
    error: {
      fontSize: 14.5, color: '#8a1c1c', background: '#fdecec', border: '1px solid #f3c9c9',
      borderRadius: 12, padding: '10px 12px', margin: '0 0 14px',
    },
    link: { color: accent, fontWeight: 700, textDecoration: 'none' },
    dayRow: {
      display: 'flex', gap: 9, overflowX: 'auto', padding: '6px 2px 12px', margin: '4px -2px 6px',
      WebkitOverflowScrolling: 'touch', scrollSnapType: 'x mandatory',
    },
    dayChip: { ...chipBase, border: '1.5px solid #e2ded4', background: '#fff', color: '#3b4442' },
    dayChipActive: {
      ...chipBase, border: `1.5px solid ${accent}`, color: '#fff', transform: 'translateY(-2px)',
      background: `linear-gradient(160deg, ${accent}, #0f1f1d)`, boxShadow: `0 10px 22px ${accent}55`,
    },
    dayWeekday: { fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.08em', opacity: 0.85, fontWeight: 600 },
    dayNumber: { fontSize: 21, fontWeight: 700, lineHeight: 1.15 },
    dayMonth: { fontSize: 11.5, opacity: 0.85 },
    slotArea: { minHeight: 110, margin: '8px 0 18px' },
    slotHead: { fontSize: 12.5, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: '#6b6f6c', margin: '10px 0 8px' },
    slotGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 10 },
    slot: { ...slotBase, border: '1.5px solid #e2ded4', background: '#fff', color: ink },
    slotActive: {
      ...slotBase, border: `1.5px solid ${gold}`, color: ink, transform: 'translateY(-2px)',
      background: `linear-gradient(135deg, ${gold}, #f1dc9c)`, boxShadow: `0 10px 22px ${gold}66`,
    },
    skeletonGrid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 10 },
    skeleton: {
      height: 46, borderRadius: 12, background: 'linear-gradient(90deg, #f1eee7 0px, #faf8f3 80px, #f1eee7 160px)',
      backgroundSize: '400px 100%', animation: 'bkShimmer 1.2s linear infinite',
    },
    ctaBar: { position: 'sticky', bottom: 12, zIndex: 2 },
    cta: {
      width: '100%', padding: '16px 18px', border: 'none', borderRadius: 14, color: '#fff', fontSize: 16.5, fontWeight: 700,
      cursor: 'pointer', font: 'inherit', background: `linear-gradient(135deg, ${accent}, #0f1f1d)`,
      boxShadow: `0 14px 30px ${accent}66`,
    },
    ctaDisabled: {
      width: '100%', padding: '16px 18px', border: 'none', borderRadius: 14, background: '#e4e2dc', color: '#8a8f95',
      fontSize: 16.5, fontWeight: 700, cursor: 'not-allowed', font: 'inherit',
    },
    assure: {
      listStyle: 'none', padding: 0, margin: '14px 0 0', display: 'flex', flexWrap: 'wrap', gap: '6px 16px',
      justifyContent: 'center', fontSize: 13, color: '#5d6461',
    },
    check: {
      width: 74, height: 74, borderRadius: '50%', margin: '4px auto 14px', display: 'grid', placeItems: 'center',
      fontSize: 38, color: ink, background: `linear-gradient(135deg, ${gold}, #f1dc9c)`,
      boxShadow: `0 16px 36px ${gold}77`, animation: 'bkPop .55s cubic-bezier(.2,1.4,.4,1) both',
    },
    ticket: {
      margin: '6px auto 18px', maxWidth: 380, borderRadius: 18, padding: '18px 20px', color: '#fff',
      background: `linear-gradient(150deg, ${accent}, #0f1f1d)`, boxShadow: `0 18px 40px ${accent}55`,
    },
    ticketDay: { fontSize: 13, letterSpacing: '0.14em', textTransform: 'uppercase', color: gold, fontWeight: 700 },
    ticketTime: { fontFamily: "'Cormorant Garamond', Georgia, serif", fontSize: 40, fontWeight: 700, lineHeight: 1.1, margin: '4px 0' },
    ticketWhere: { fontSize: 14, opacity: 0.85 },
    ghost: {
      display: 'inline-block', padding: '11px 18px', borderRadius: 12, border: `1.5px solid ${accent}`, background: '#fff',
      color: accent, fontWeight: 700, fontSize: 14.5, cursor: 'pointer', font: 'inherit',
    },
    footer: { textAlign: 'center', color: 'rgba(255,255,255,0.65)', fontSize: 12.5, marginTop: 18 },
  };
}
