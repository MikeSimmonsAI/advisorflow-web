/**
 * PUBLIC ROOFING DEMO - fictional, self-contained sample data.
 *
 * Nothing here comes from a real company or homeowner and nothing is fetched:
 * every name is invented, every phone number is a 555 number, every email is
 * @example.com, every address is made up. The demo never calls the private
 * Roofing API. Dates are relative to "today" in the visitor's browser so the
 * story always reads as current, and every percentage shown is computed from
 * the counts below, so it always matches what it is measured against.
 */

export const DEMO_BASE = '/roofing/demo'
export const PHOTO = (f) => `/roofing-demo/${f}.jpg`

export const COMPANY = {
  name: 'Summit Peak Roofing',
  phone: '(214) 555-0140',
  email: 'office@summitpeak.example.com',
  city: 'Plano, TX',
}

export const TEAM = [
  { id: 'u1', name: 'Dana Whitaker', role: 'Owner' },
  { id: 'u2', name: 'Alex Moreno', role: 'Field inspector' },
  { id: 'u3', name: 'Jordan Pierce', role: 'Salesperson' },
  { id: 'u4', name: 'Casey Lin', role: 'Salesperson' },
]

// ── time helpers: everything is relative to the visitor's today ───────────
export function day(offset, hour = 10, minute = 0) {
  const d = new Date()
  d.setDate(d.getDate() + offset)
  d.setHours(hour, minute, 0, 0)
  return d
}
export const fmtWhen = (d) => d.toLocaleString(undefined, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
export const fmtDate = (d) => d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })
export const fmtTime = (d) => d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
export function agoText(d) {
  const m = Math.round((Date.now() - d.getTime()) / 60000)
  if (m < 60) return `${Math.max(1, m)}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.round(h / 24)}d ago`
}

// ── the company's numbers (last 30 days) ──────────────────────────────────
// One consistent story: each figure is a subset of the one before it.
export const OUTCOMES = {
  imported: 1560,
  contacted: 1248,
  responded: 312,
  engaged: 214,
  offered: 131,         // inspection requested / times offered
  confirmed: 86,        // inspections confirmed
  reports: 64,          // inspection reports completed
  estimates: 52,        // estimates recorded
  won: 21,
  lost: 9,
  upcoming: 9,
  in_follow_up: 486,
  sent_today: 142,
  due_24h: 167,
  replies_today: 11,
  booked_today: 3,
  attention: 4,
  opted_out: 37,
  estimate_value_open: 542000,   // matches the homepage sample figure
  won_value: 287650,
}
export const TOUCHES = [
  { touch: 1, received: 1248, replied: 112, opted_out: 14 },
  { touch: 2, received: 1090, replied: 88, opted_out: 9 },
  { touch: 3, received: 912, replied: 61, opted_out: 6 },
  { touch: 4, received: 760, replied: 34, opted_out: 5 },
  { touch: 5, received: 590, replied: 17, opted_out: 3 },
]
export const pctOf = (a, b) => (b ? Math.round((a / b) * 1000) / 10 : null)

// 30 days of activity that adds up to the totals above.
export const TREND = (() => {
  const sent = TOUCHES.reduce((a, t) => a + t.received, 0)
  const out = []
  let s = 0, r = 0, b = 0
  for (let i = 29; i >= 0; i -= 1) {
    const w = 0.7 + 0.6 * Math.abs(Math.sin(i * 1.7))
    const d = day(-i, 12)
    out.push({ period: d.toISOString().slice(0, 10), w })
  }
  const W = out.reduce((a, p) => a + p.w, 0)
  out.forEach((p, i) => {
    const last = i === out.length - 1
    p.sent = last ? sent - s : Math.round((sent * p.w) / W); s += p.sent
    p.replies = last ? OUTCOMES.responded - r : Math.round((OUTCOMES.responded * p.w) / W); r += p.replies
    p.booked = last ? OUTCOMES.confirmed - b : Math.round((OUTCOMES.confirmed * p.w) / W); b += p.booked
  })
  return out
})()

// ── the homeowners (all fictional) ────────────────────────────────────────
export const STAGES = [
  { key: 'imported', label: 'Imported', color: '#94a3b8' },
  { key: 'contacted', label: 'Contacted', color: '#2f6fed' },
  { key: 'engaged', label: 'Engaged', color: '#4b89f3' },
  { key: 'inspection_offered', label: 'Inspection Offered', color: '#12925e' },
  { key: 'inspection_booked', label: 'Inspection Booked', color: '#f26a1b' },
  { key: 'inspected', label: 'Inspected', color: '#d9580c' },
  { key: 'estimate_sent', label: 'Estimate Sent', color: '#6d4fd8' },
  { key: 'won', label: 'Won', color: '#12925e' },
  { key: 'lost', label: 'Lost', color: '#64748b' },
]
export const STAGE_LABEL = Object.fromEntries(STAGES.map((s) => [s.key, s.label]))

const H = (id, first, last, street, city, zip, extra) => ({
  id, name: `${first} ${last}`, first, phone: `(214) 555-0${100 + Number(id.slice(1))}`,
  email: `${first.toLowerCase()}.${last.toLowerCase().replace(/[^a-z]/g, '')}@example.com`,
  address: `${street}, ${city}, TX ${zip}`, ...extra,
})

export const HOMEOWNERS = [
  H('h1', 'Rosa', 'Delgado', '4417 Bluebonnet Ln', 'Plano', '75023', { stage: 'engaged', source: 'Website form (2024)', owner: 'Jordan Pierce',
    roof_issue: 'Leak over the kitchen after the spring storm', service: 'repair', urgency: 'high', roof_age: 14,
    lane: 'human_needed', touch: 2, bucket: 'needs_human', reason: 'Asked a question about her insurance adjuster',
    summary: 'Replied that her adjuster visits Thursday and asked if someone can be there. Needs a person to answer.',
    next: 'Answer personally - offer to meet the adjuster', last: day(0, 9, 12), unread: 1 }),
  H('h2', 'Marcus', 'Whitfield', '918 Cedar Ridge Dr', 'Frisco', '75034', { stage: 'inspection_offered', source: 'Past estimate (2023)', owner: 'Casey Lin',
    roof_issue: 'Hail last spring, granules in the gutters', service: 'replacement', urgency: 'normal', roof_age: 19,
    lane: 'requested', touch: 3, bucket: 'ready_to_book', reason: 'Said yes to an inspection',
    summary: 'Wants an inspection. Offered Tue 10 AM or Wed 1 PM - waiting for him to pick.',
    next: 'Confirm a time', last: day(0, 8, 40), unread: 1 }),
  H('h3', 'Linda', 'Nguyen', '1311 Willow Creek Rd', 'Allen', '75013', { stage: 'inspection_booked', source: 'Storm inquiry list', owner: 'Alex Moreno',
    roof_issue: 'Missing shingles on the south side', service: 'repair', urgency: 'high', roof_age: 12,
    lane: 'booked', touch: 2, summary: 'Booked a roof inspection after the second follow-up.',
    next: 'Inspection booked', inspection_at: day(2, 10), last: day(-1, 16, 5) }),
  H('h4', 'James', 'Carter', '1200 Oak Hollow Dr', 'Plano', '75024', { stage: 'inspection_booked', source: 'Referral', owner: 'Alex Moreno',
    roof_issue: 'Stain on the bedroom ceiling', service: 'not_sure', urgency: 'high', roof_age: 21,
    lane: 'booked', touch: 1, summary: 'Inspector is on site now - report in progress.',
    next: 'Finish the inspection report', inspection_at: day(0, 9), last: day(0, 9) }),
  H('h5', 'Daniel', 'Foster', '1866 Timberline Dr', 'Plano', '75023', { stage: 'estimate_sent', source: 'Past estimate (2022)', owner: 'Jordan Pierce',
    roof_issue: 'Hail damage after the April storm', service: 'replacement', urgency: 'normal', roof_age: 22,
    summary: 'Report R-1006 complete. Estimate of $11,250 given - follow up Friday.',
    next: 'Follow up on the estimate', last: day(-2, 15) }),
  H('h6', 'Angela', 'Brooks', '702 Prairie View Ct', 'McKinney', '75070', { stage: 'inspected', source: 'Website form (2023)', owner: 'Casey Lin',
    roof_issue: 'Wind took off a few shingles', service: 'repair', urgency: 'normal', roof_age: 9,
    summary: 'Report R-1009 complete: targeted repair recommended.',
    next: 'Prepare the estimate', last: day(-1, 14) }),
  H('h7', 'Robert', 'Hayes', '355 Stonebridge Ln', 'Frisco', '75035', { stage: 'won', source: 'Past estimate (2023)', owner: 'Jordan Pierce',
    roof_issue: 'Old roof, leaking at the chimney', service: 'replacement', urgency: 'high', roof_age: 24,
    summary: 'Accepted a $16,900 replacement. Job won.', next: 'Job won', last: day(-4, 11) }),
  H('h8', 'Priya', 'Shah', '2210 Lakeside Blvd', 'Allen', '75002', { stage: 'lost', source: 'Storm inquiry list', owner: 'Casey Lin',
    roof_issue: 'Wanted a second opinion on wear', service: 'not_sure', urgency: 'low', roof_age: 17,
    summary: 'Declined - roof has a few good years left. Re-check next spring.', next: 'Closed lost', lost_reason: 'Not ready yet - re-inspect in spring', last: day(-6, 10) }),
  H('h9', 'Kevin', "O'Neil", '4830 Rolling Hills Dr', 'Plano', '75024', { stage: 'contacted', source: 'Past estimate (2021)', owner: 'Jordan Pierce',
    roof_issue: 'Asked about a quote two years ago', service: 'replacement', urgency: 'low', roof_age: 18,
    lane: 'working', touch: 2, summary: 'Two follow-ups sent, no reply yet.', next: 'Touch 3 goes out tomorrow 10 AM', next_touch_at: day(1, 10), last: day(-3, 10) }),
  H('h10', 'Teresa', 'Morales', '617 Meadowlark Way', 'McKinney', '75071', { stage: 'engaged', source: 'Website form (2024)', owner: 'Casey Lin',
    roof_issue: 'Curling shingles over the garage', service: 'repair', urgency: 'normal', roof_age: 16,
    lane: 'replied', touch: 3, bucket: 'new_reply', reason: 'Asked what an inspection costs',
    summary: 'Asked whether the inspection is free. AI answered; she is thinking about a day.', next: 'Offer inspection times', last: day(0, 7, 55), unread: 1 }),
  H('h11', 'Brian', 'Kim', '90 Elm Park Ct', 'Frisco', '75033', { stage: 'engaged', source: 'Referral', owner: 'Jordan Pierce',
    roof_issue: 'Hail dents on the gutters', service: 'not_sure', urgency: 'normal', roof_age: 11,
    lane: 'replied', touch: 1, bucket: 'new_reply', reason: 'Replied interested',
    summary: 'Replied "yes, please have someone look" to the first text.', next: 'Offer inspection times', last: day(0, 6, 30), unread: 1 }),
  H('h12', 'Sandra', 'Lee', '1440 Sunset Ridge', 'Allen', '75013', { stage: 'contacted', source: 'Storm inquiry list', owner: 'Casey Lin',
    roof_issue: 'Wants the roof checked before selling', service: 'not_sure', urgency: 'normal', roof_age: 13,
    lane: 'working', touch: 1, summary: 'First text sent this morning.', next: 'Touch 2 goes out in 2 days', next_touch_at: day(2, 11), last: day(0, 10) }),
  H('h13', 'Michael', 'Grant', '3021 Brookhaven Dr', 'Plano', '75075', { stage: 'imported', source: 'Spreadsheet import', owner: 'Jordan Pierce',
    roof_issue: 'Old inspection request (2022)', service: 'repair', urgency: 'low', roof_age: 20,
    lane: 'eligible', touch: 0, summary: 'Ready to contact. Not started.', next: 'Start follow-up', last: day(-1, 9) }),
]
export const byId = Object.fromEntries(HOMEOWNERS.map((h) => [h.id, h]))

// ── conversations (synthetic - nothing was ever sent) ─────────────────────
const ai = (at, body, extra) => ({ dir: 'out', actor: 'ai', at, body, ...extra })
const inb = (at, body, tag) => ({ dir: 'in', at, body, tag })
const team = (at, sender, body) => ({ dir: 'out', actor: 'team', sender, at, body })
const sys = (at, body) => ({ sys: true, at, body })

export const THREADS = {
  h3: [
    ai(day(-9, 10), "Hi Linda, this is Alex with Summit Peak Roofing. You asked about missing shingles after the storm - are you still interested in having someone take a look at your roof?"),
    ai(day(-7, 11), 'Hi Linda, Alex again with Summit Peak Roofing. A free inspection takes about an hour and you will know exactly where your roof stands. Want me to find a time?'),
    inb(day(-7, 18, 22), 'Yes please. A few more blew off last week.', 'interested'),
    ai(day(-7, 18, 24), 'Sorry to hear that - I can have Alex out Thursday at 10:00 AM or Friday at 1:00 PM. Which works better?'),
    inb(day(-7, 19, 2), 'Thursday at 10 works.', 'booking'),
    ai(day(-7, 19, 3), `You're all set, Linda: roof inspection ${fmtWhen(day(2, 10))} with Alex Moreno. Reply here if anything changes.`),
    sys(day(-7, 19, 3), 'Inspection booked and added to the team calendar'),
  ],
  h1: [
    ai(day(-6, 10), 'Hi Rosa, this is Jordan with Summit Peak Roofing. You asked about a leak over your kitchen - still need someone to look at it?'),
    ai(day(-4, 11), 'Hi Rosa, Jordan again. If the leak is still there, we can usually get out within 48 hours. Want a time?'),
    inb(day(0, 9, 12), 'Yes but my insurance adjuster is coming Thursday. Can someone from your team be there too?', 'question'),
    sys(day(0, 9, 12), 'Handed to a person: the homeowner asked something the AI should not answer alone'),
  ],
  h2: [
    ai(day(-12, 10), 'Hi Marcus, this is Casey with Summit Peak Roofing. We gave you an estimate after last spring\'s hail - has anything changed with your roof?'),
    ai(day(-10, 11), 'Hi Marcus, Casey again. Granules in the gutters usually mean the shingles are wearing - happy to take a fresh look at no cost.'),
    ai(day(-7, 10), 'Hi Marcus, still glad to get your roof re-checked. Reply YES and I will send a few open times.'),
    inb(day(0, 8, 38), 'YES. Getting worse after the last storm.', 'interested'),
    ai(day(0, 8, 40), 'Thanks Marcus - I have Tuesday at 10:00 AM or Wednesday at 1:00 PM. Which is better for you?'),
  ],
  h10: [
    ai(day(-8, 10), 'Hi Teresa, this is Casey with Summit Peak Roofing. You mentioned curling shingles over the garage - still want someone to look?'),
    ai(day(-6, 11), 'Hi Teresa, Casey again. Curling shingles can let water in during the next storm - want me to find a time?'),
    ai(day(-3, 10), "Hi Teresa, quick note - the inspection is free and there's no obligation."),
    inb(day(0, 7, 55), 'Is the inspection really free? What happens after?', 'question'),
    ai(day(0, 7, 56), 'Yes, completely free. Our inspector checks the roof, takes photos and gives you a written report. If you need work, you get an estimate - no pressure.'),
  ],
  h11: [
    ai(day(-1, 10), 'Hi Brian, this is Jordan with Summit Peak Roofing. Your neighbor referred you about hail dents on the gutters - want someone to take a look?'),
    inb(day(0, 6, 30), 'Yes, please have someone look.', 'interested'),
  ],
  h9: [
    ai(day(-5, 10), 'Hi Kevin, this is Jordan with Summit Peak Roofing. You asked about a roof quote a while back - are you still thinking about it?'),
    ai(day(-3, 10), 'Hi Kevin, Jordan again. Happy to do a free inspection so you know where things stand.'),
  ],
  h12: [ai(day(0, 10), 'Hi Sandra, this is Casey with Summit Peak Roofing. You wanted your roof checked before selling - want me to find a time this week?')],
  h4: [
    ai(day(-6, 10), 'Hi James, this is Alex with Summit Peak Roofing - you were referred to us about a ceiling stain. Want an inspection?'),
    inb(day(-6, 12, 10), 'Yes. Mornings are best.', 'interested'),
    ai(day(-6, 12, 11), `Great - you're booked for ${fmtWhen(day(0, 9))} with Alex Moreno.`),
    sys(day(-6, 12, 11), 'Inspection booked'),
  ],
  h5: [
    ai(day(-14, 10), 'Hi Daniel, this is Jordan with Summit Peak Roofing. After the April storm, want us to re-check the roof we quoted?'),
    inb(day(-13, 17), 'Yes, I saw shingle pieces in the yard.', 'interested'),
    ai(day(-13, 17, 2), `You're booked for ${fmtWhen(day(-2, 9))} with Alex Moreno.`),
    sys(day(-2, 9, 55), 'Inspection report R-1006 completed'),
    team(day(-1, 14, 30), 'Jordan Pierce', 'Hi Daniel, I went over the estimate with you this morning - $11,250 for the full replacement. Call me with any questions.'),
  ],
  h6: [
    ai(day(-9, 10), 'Hi Angela, this is Casey with Summit Peak Roofing. Did the wind cause more trouble with the shingles?'),
    inb(day(-8, 9), 'A few more came off. Can you come look?', 'interested'),
    ai(day(-8, 9, 2), `Booked: ${fmtWhen(day(-1, 13))} with Alex Moreno.`),
    sys(day(-1, 13, 55), 'Inspection report R-1009 completed'),
  ],
  h7: [
    ai(day(-30, 10), 'Hi Robert, this is Jordan with Summit Peak Roofing - still having trouble at the chimney?'),
    inb(day(-29, 11), 'Yes, it leaks every time it rains.', 'interested'),
    sys(day(-21, 15), 'Inspection report R-1002 completed'),
    team(day(-20, 10), 'Jordan Pierce', 'Robert, here is the estimate we discussed: $16,900 for the full replacement.'),
    inb(day(-4, 11), "Let's do it. When can you start?", 'interested'),
    sys(day(-4, 11, 30), 'Marked won - $16,900'),
  ],
  h8: [
    ai(day(-25, 10), 'Hi Priya, this is Casey with Summit Peak Roofing. Want a second opinion on the roof wear you mentioned?'),
    inb(day(-24, 15), 'Sure, next week is fine.', 'interested'),
    sys(day(-17, 11), 'Inspection report R-1004 completed'),
    inb(day(-6, 10), 'Thanks for the report. We will wait until spring.', 'not_now'),
    sys(day(-6, 10, 20), 'Marked lost - not ready yet'),
  ],
  h13: [],
}

// ── inspections (the star of the demo) ────────────────────────────────────
const P = (f, label, caption) => ({ src: PHOTO(f), label, caption })
export const INSPECTIONS = [
  {
    id: 'i-upcoming', lead: 'h3', status: 'upcoming', type: 'Roof Inspection', inspector: 'Alex Moreno',
    scheduled: day(2, 10), note: 'Gate code 4410. Missing shingles on the south side.',
  },
  {
    id: 'i-progress', lead: 'h4', status: 'in_progress', type: 'Storm Damage Assessment', inspector: 'Alex Moreno',
    scheduled: day(0, 9), started: day(0, 9, 4),
    roof_type: 'Architectural shingle', roof_age: 21, stories: 2, condition: 'poor',
    damage: ['Evidence of water intrusion', 'Flashing deterioration'], severity: 'Moderate', areas: ['Chimney', 'Attic'],
    notes: 'Flashing at the chimney has separated from the brick. Water staining visible on the attic decking below it.',
    photos: [P('flashing', 'Flashing', 'Deteriorated chimney flashing - pulled away from the brick'), P('attic', 'Attic', 'Water staining on the attic decking below the chimney')],
  },
  {
    id: 'i-1006', lead: 'h5', status: 'completed', report: 'R-1006', type: 'Roof Inspection', inspector: 'Alex Moreno', completed_by: 'Alex Moreno',
    scheduled: day(-2, 9), started: day(-2, 9, 2), completed: day(-2, 9, 55),
    roof_type: 'Architectural shingle', roof_age: 22, stories: 2, condition: 'Poor',
    damage: ['Hail-like impact marks', 'Granule loss', 'Gutter damage'], severity: 'Moderate', areas: ['North slope', 'West slope', 'Gutters'],
    notes: 'Hail-like impact marks across the north and west slopes - roughly 9-12 per test square. Granule loss at the impact points and dents in the front gutter. Homeowner reports an open claim with their insurance provider.',
    recommendation: 'Full replacement', scope: 'Full tear-off and replacement, architectural shingle, new drip edge and gutter section.',
    insurance: 'Homeowner reports an open claim', estimate: 11250, sales: 'estimate_sent',
    photos: [
      P('overview', 'Overview', 'Front elevation - architectural shingle, 2 stories'),
      P('hail', 'Damage', 'Hail-like impact marks - north slope (test square circled)'),
      P('granules', 'Shingles', 'Granule loss - exposed shingle surface at the impact points'),
      P('gutter', 'Gutters', 'Dented front gutter with shingle granules collecting in the trough'),
    ],
    history: [
      ['Inspection started', 'Alex Moreno', day(-2, 9, 2)],
      ['Report completed', 'Alex Moreno', day(-2, 9, 55)],
      ['Sales step: Estimate amount and scope recorded', 'Jordan Pierce', day(-2, 15, 10)],
      ['Sales step: Estimate recorded as given to homeowner', 'Jordan Pierce', day(-1, 14, 30)],
    ],
  },
  {
    id: 'i-1009', lead: 'h6', status: 'completed', report: 'R-1009', type: 'Roof Inspection', inspector: 'Alex Moreno', completed_by: 'Alex Moreno',
    scheduled: day(-1, 13), started: day(-1, 13, 3), completed: day(-1, 13, 55),
    roof_type: 'Architectural shingle', roof_age: 9, stories: 2, condition: 'Fair',
    damage: ['Possible wind damage / lifted shingles', 'Missing shingles', 'Flashing deterioration'], severity: 'Moderate', areas: ['South slope', 'Ridge', 'Chimney'],
    notes: 'Six shingles missing on the south slope after a reported wind event, two more creased and lifting. Step flashing at the chimney has pulled away. Decking is sound - a targeted repair will hold.',
    recommendation: 'Repair', scope: 'Replace 2 squares on the south slope, reseal and re-flash the chimney.',
    insurance: 'Homeowner plans to contact their insurance', sales: 'inspected',
    photos: [
      P('overview', 'Overview', 'Front elevation - architectural shingle, 2 stories'),
      P('missing', 'Damage', 'Missing shingles after a reported wind event - south slope'),
      P('flashing', 'Flashing', 'Deteriorated chimney flashing - step flashing lifted'),
    ],
    history: [['Inspection started', 'Alex Moreno', day(-1, 13, 3)], ['Report completed', 'Alex Moreno', day(-1, 13, 55)]],
  },
  {
    id: 'i-1002', lead: 'h7', status: 'completed', report: 'R-1002', type: 'Roof Inspection', inspector: 'Alex Moreno', completed_by: 'Alex Moreno',
    scheduled: day(-21, 14), started: day(-21, 14, 1), completed: day(-21, 15),
    roof_type: 'Architectural shingle', roof_age: 24, stories: 2, condition: 'Poor',
    damage: ['Evidence of water intrusion', 'Flashing deterioration', 'Soft or rotted decking'], severity: 'Severe', areas: ['Chimney', 'Attic', 'Interior ceiling'],
    notes: 'Evidence of water intrusion at the chimney. Flashing has failed; water has stained the attic decking and the bedroom ceiling. Two sheets of decking are soft. Roof is 24 years old - replacement recommended over a patch.',
    recommendation: 'Full replacement', scope: 'Tear-off, replace soft decking, new chimney flashing and cricket, full replacement.',
    insurance: 'Not discussed', estimate: 16900, won: 16900, sales: 'won',
    photos: [
      P('overview', 'Overview', 'Front elevation - architectural shingle, 2 stories'),
      P('flashing', 'Flashing', 'Deteriorated chimney flashing - pulled away from the brick'),
      P('attic', 'Attic', 'Water staining on the attic decking below the chimney'),
      P('interior', 'Interior', 'Interior ceiling water staining - second bedroom'),
    ],
    history: [
      ['Inspection started', 'Alex Moreno', day(-21, 14, 1)], ['Report completed', 'Alex Moreno', day(-21, 15)],
      ['Sales step: Estimate amount and scope recorded', 'Jordan Pierce', day(-21, 17)],
      ['Sales step: Estimate recorded as given to homeowner', 'Jordan Pierce', day(-20, 10)],
      ['Sales step: Homeowner accepted - job won', 'Jordan Pierce', day(-4, 11, 30)],
    ],
  },
  {
    id: 'i-1004', lead: 'h8', status: 'completed', report: 'R-1004', type: 'Roof Inspection', inspector: 'Alex Moreno', completed_by: 'Alex Moreno',
    scheduled: day(-17, 10), started: day(-17, 10, 2), completed: day(-17, 11),
    roof_type: 'Architectural shingle', roof_age: 17, stories: 2, condition: 'Fair',
    damage: ['Granule loss', 'Cracked / curled shingles'], severity: 'Minor', areas: ['South slope', 'West slope'],
    notes: 'No storm damage. Shingles are near end of life - granule loss and some cracking on the south and west faces. No leaks today. Recommend planning a replacement within 2-3 years.',
    recommendation: 'Monitor / maintenance', scope: 'Re-inspect next spring; budget for replacement.',
    insurance: 'No claim reported', sales: 'lost',
    photos: [P('overview', 'Overview', 'Front elevation - architectural shingle, 2 stories'), P('granules', 'Shingles', 'Age wear - widespread granule loss')],
    history: [['Inspection started', 'Alex Moreno', day(-17, 10, 2)], ['Report completed', 'Alex Moreno', day(-17, 11)], ['Sales step: Homeowner declined', 'Casey Lin', day(-6, 10, 20)]],
  },
]
export const inspectionById = Object.fromEntries(INSPECTIONS.map((i) => [i.id, i]))
export const inspectionsFor = (leadId) => INSPECTIONS.filter((i) => i.lead === leadId)

// The pipeline: sample cards per stage, with the stage totals for the whole company.
export const PIPELINE_TOTALS = {
  imported: 312, contacted: 722, engaged: 83, inspection_offered: 45, inspection_booked: 22,
  inspected: 12, estimate_sent: 31, won: 21, lost: 9,
}
// Imported 1,560 in the funnel = everyone who ever reached a stage; the board counts where they are NOW.

export const PLAYBOOK = {
  name: 'Roofing Inspection Follow-Up',
  goal: 'Turn past inquiries and estimates into booked inspections',
  audience: 'Homeowners who can be contacted (consent, valid number, not opted out)',
  touches: [
    { touch: 1, day: 0, channel: 'text', text: 'Hi {first name}, this is {salesperson} with {company}. Are you still interested in having someone look at your roof?' },
    { touch: 2, day: 2, channel: 'text', text: 'A free inspection takes about an hour and you will know exactly where your roof stands. Want me to find a time?' },
    { touch: 3, day: 5, channel: 'email', text: 'Your roof inspection - we can have someone out at no cost and walk you through what we find.' },
    { touch: 4, day: 9, channel: 'text', text: 'Still happy to get your roof looked at. Reply YES and I will send a few open times.' },
    { touch: 5, day: 20, channel: 'text', text: 'This is my last follow-up for now. If anything changes with your roof, just reply here.' },
  ],
  stop_rules: ['Stops the moment a homeowner replies', 'Stops on opt-out (STOP)', 'Stops when an inspection is booked', 'Never texts outside quiet hours'],
}
