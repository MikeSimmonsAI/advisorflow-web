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
          appt_label: context?.appt_label || 'Appointment',
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

  const accent = context?.brand_color || '#1f4e79';
  const businessName = context?.org_name || '';
  const businessAddress = context?.org_address || '';
  const rawPhone = context?.org_phone || '';
  const businessPhone = formatPhone(rawPhone);
  const firstName = context?.lead_first_name || '';
  const apptLabel = context?.appt_label || 'appointment';
  const S = styles(accent);

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
          <p style={S.body}>
            Please contact us directly and we will find a time with you.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={S.page}>
      <div style={S.card}>
        <header style={S.header}>
          {businessName ? <div style={S.brand}>{businessName}</div> : null}
          {businessAddress ? <div style={S.brandSub}>{businessAddress}</div> : null}
          {businessPhone ? (
            <div style={S.brandSub}>
              <a style={S.link} href={`tel:${rawPhone}`}>{businessPhone}</a>
            </div>
          ) : null}
        </header>

        {confirmed ? (
          <section>
            <h1 style={S.h1}>
              {confirmed.alreadyBooked ? 'This appointment is already scheduled' : "You're scheduled"}
            </h1>
            {confirmed.alreadyBooked ? (
              <p style={S.body}>
                We already have an appointment on the calendar for this request. If you
                need to change it, please call us and we will take care of it.
              </p>
            ) : (
              <>
                <p style={S.body}>
                  {firstName ? `${firstName}, your ` : 'Your '}{apptLabel.toLowerCase()} is confirmed for{' '}
                  <strong>{labelForDay(days, confirmed.day)}</strong> at{' '}
                  <strong>{formatSlotLabel(confirmed.slot)}</strong>.
                </p>
                <p style={S.body}>
                  You will receive a confirmation shortly. If anything needs to change,
                  {businessPhone
                    ? <> call us at <a style={S.link} href={`tel:${rawPhone}`}>{businessPhone}</a>.</>
                    : ' just reply to our message.'}
                </p>
              </>
            )}
          </section>
        ) : (
          <section>
            <h1 style={S.h1}>
              {firstName
                ? `${firstName}, choose a time that works for you`
                : 'Choose a time that works for you'}
            </h1>
            <p style={S.body}>
              Pick a day, then a time. There is no cost or obligation to speak with us.
            </p>

            <div style={S.dayRow}>
              {days.map((d) => {
                const active = d.iso === selectedDay;
                return (
                  <button
                    key={d.iso}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setSelectedDay(d.iso)}
                    style={active ? S.dayChipActive : S.dayChip}
                  >
                    <span style={S.dayWeekday}>{d.isToday ? 'Today' : d.weekday}</span>
                    <span style={S.dayNumber}>{d.day}</span>
                    <span style={S.dayMonth}>{d.month}</span>
                  </button>
                );
              })}
            </div>

            <div style={S.slotArea}>
              {slotsLoading ? (
                <p style={S.muted}>Checking availability…</p>
              ) : slots.length > 0 ? (
                <div style={S.slotGrid}>
                  {slots.map((slot) => {
                    const active = slot === selectedSlot;
                    return (
                      <button
                        key={slot}
                        type="button"
                        aria-pressed={active}
                        onClick={() => setSelectedSlot(slot)}
                        style={active ? S.slotActive : S.slot}
                      >
                        {formatSlotLabel(slot)}
                      </button>
                    );
                  })}
                </div>
              ) : (
                <p style={S.notice}>{slotsReason || 'There are no remaining times on that day.'}</p>
              )}
            </div>

            {submitError ? <p style={S.error}>{submitError}</p> : null}

            <button
              type="button"
              disabled={!selectedSlot || submitting}
              onClick={confirmBooking}
              style={(!selectedSlot || submitting) ? S.ctaDisabled : S.cta}
            >
              {submitting
                ? 'Confirming…'
                : selectedSlot
                  ? `Confirm ${formatSlotLabel(selectedSlot)}`
                  : 'Select a time'}
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
      maxWidth: 640,
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
    notice: {
      fontSize: 14.5, lineHeight: 1.55, color: '#6b4a00',
      background: '#fff8e6', border: '1px solid #f2e0b0',
      borderRadius: 8, padding: '11px 13px', margin: 0,
    },
    error: {
      fontSize: 14.5, color: '#8a1c1c', background: '#fdecec',
      border: '1px solid #f3c9c9', borderRadius: 8,
      padding: '10px 12px', margin: '0 0 14px',
    },
    link: { color: accent, fontWeight: 600, textDecoration: 'none' },
    dayRow: {
      display: 'flex', gap: 8, overflowX: 'auto',
      padding: '4px 0 10px', margin: '4px 0 6px',
      WebkitOverflowScrolling: 'touch',
    },
    dayChip: {
      flex: '0 0 auto', width: 64, padding: '9px 0',
      border: '1px solid #dcdfe3', borderRadius: 10, background: '#fff',
      cursor: 'pointer', display: 'flex', flexDirection: 'column',
      alignItems: 'center', gap: 1, font: 'inherit', color: '#33363a',
    },
    dayChipActive: {
      flex: '0 0 auto', width: 64, padding: '9px 0',
      border: `1px solid ${accent}`, borderRadius: 10, background: accent,
      cursor: 'pointer', display: 'flex', flexDirection: 'column',
      alignItems: 'center', gap: 1, font: 'inherit', color: '#fff',
    },
    dayWeekday: { fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '0.04em', opacity: 0.85 },
    dayNumber: { fontSize: 18, fontWeight: 700, lineHeight: 1.15 },
    dayMonth: { fontSize: 11.5, opacity: 0.85 },
    slotArea: { minHeight: 96, margin: '10px 0 18px' },
    slotGrid: {
      display: 'grid',
      gridTemplateColumns: 'repeat(auto-fill, minmax(112px, 1fr))',
      gap: 9,
    },
    slot: {
      padding: '12px 8px', border: '1px solid #dcdfe3', borderRadius: 9,
      background: '#fff', cursor: 'pointer', font: 'inherit',
      fontSize: 15, color: '#33363a',
    },
    slotActive: {
      padding: '12px 8px', border: `1px solid ${accent}`, borderRadius: 9,
      background: accent, cursor: 'pointer', font: 'inherit',
      fontSize: 15, color: '#fff', fontWeight: 600,
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
