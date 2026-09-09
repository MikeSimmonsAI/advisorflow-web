/**
 * launchConfig - the Launch Engine's PRESENTATION constants.
 *
 * ===========================================================================
 * WHAT IS LEFT HERE, AND WHAT MOVED
 * ===========================================================================
 *
 * This file used to hold the whole prototype: a mock brand, a mock customer,
 * mock answers, the step list and a hand-written completion percentage per
 * step. All of that is now REAL and lives on the server -
 * app/services/launch_intake.py owns the step schema, the required fields and
 * the completion arithmetic, and GET /launch/me returns the brand and customer
 * from the Platform and Organization rows behind the session.
 *
 * A percentage computed in the browser is a percentage devtools can set to
 * 100, and a mock customer in a bundled literal is a customer name that can
 * appear on the wrong screen. Neither belongs in the client.
 *
 * What remains is what genuinely IS presentation and has no server opinion:
 * icon path geometry, and the list of what the brand delivers.
 */

export const ICONS = {
  rocket: 'M12 2c3.2 2.2 5 5.6 5 9.3l3 2.2-3.4 1 .3 3.4-3-1.6-1.9 2.6-1.9-2.6-3 1.6.3-3.4-3.4-1 3-2.2C7 7.6 8.8 4.2 12 2Zm0 6.2a1.9 1.9 0 1 0 0 3.8 1.9 1.9 0 0 0 0-3.8Z',
  grid:    'M4 4h6v6H4V4Zm10 0h6v6h-6V4ZM4 14h6v6H4v-6Zm10 0h6v6h-6v-6Z',
  clip:    'M9 3h6a2 2 0 0 1 2 2h1a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h1a2 2 0 0 1 2-2Zm0 2v1h6V5H9Z',
  layers:  'M12 3 3 8l9 5 9-5-9-5Zm-7.6 8.6L3 12.5l9 5 9-5-1.4-.9L12 15.8l-7.6-4.2Z',
  folder:  'M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z',
  users:   'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm7 0a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM2 20a7 7 0 0 1 14 0v1H2v-1Zm15.2-6.6A6 6 0 0 1 22 19.2V21h-4v-1a8.9 8.9 0 0 0-.8-3.6Z',
  plug:    'M9 2v6H7v3a5 5 0 0 0 4 4.9V22h2v-6.1A5 5 0 0 0 17 11V8h-2V2h-2v6h-2V2H9Z',
  life:    'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm0 6a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm7.3 2.7-3.4.9a4 4 0 0 0-.5-1.3l2.5-2.5c.6.8 1.1 1.8 1.4 2.9ZM12 4.7c1.1 0 2.1.2 3 .7l-2.4 2.4a4 4 0 0 0-1.2-.2c-.4 0-.8 0-1.2.2L7.8 5.4c1-.5 2.1-.7 3.2-.7Zm-6 2 2.5 2.6a4 4 0 0 0-.5 1.3l-3.4-.9c.3-1.1.8-2.1 1.4-3Zm-1.4 6 3.4-.9c.1.5.3.9.5 1.3L6 15.6a7.3 7.3 0 0 1-1.4-2.9Zm4.4 6.6 2.4-2.4c.4.1.8.2 1.2.2h.4l2.4 2.5a7.3 7.3 0 0 1-6.4-.3Zm8.7-1.6-2.5-2.5c.2-.4.4-.8.5-1.3l3.4.9c-.3 1.1-.8 2.1-1.4 2.9Z',
  bell:    'M12 2a6 6 0 0 0-6 6v3.6L4 15v1h16v-1l-2-3.4V8a6 6 0 0 0-6-6Zm0 20a2.8 2.8 0 0 0 2.8-2.6H9.2A2.8 2.8 0 0 0 12 22Z',
  search:  'M10.5 3a7.5 7.5 0 1 0 4.6 13.4l4.2 4.3 1.5-1.5-4.3-4.2A7.5 7.5 0 0 0 10.5 3Zm0 2a5.5 5.5 0 1 1 0 11 5.5 5.5 0 0 1 0-11Z',
  caret:   'M9 5l7 7-7 7',
  arrow:   'M5 12h12m-5-6 6 6-6 6',
  check:   'M5 12.5 9.5 17 19 7',
  shield:  'M12 2 4 5.5v6c0 4.6 3.3 8.7 8 10.5 4.7-1.8 8-5.9 8-10.5v-6L12 2Z',
  info:    'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm-1 5h2v2h-2V7Zm0 4h2v6h-2v-6Z',
  upload:  'M12 3 7 8h3v7h4V8h3l-5-5ZM4 18h16v3H4v-3Z',
  doc:     'M6 2h8l4 4v16H6V2Zm7 1.5V7h3.5L13 3.5Z',
  phone:   'M6.6 3h3l1.5 4-2 1.5a12 12 0 0 0 5.4 5.4l1.5-2 4 1.5v3a2 2 0 0 1-2.2 2A17 17 0 0 1 4.6 5.2 2 2 0 0 1 6.6 3Z',
  mail:    'M3 5h18v14H3V5Zm2 2v.4l7 4.4 7-4.4V7H5Z',
}

/* ── WHAT THE BRAND WILL LAUNCH ───────────────────────────────────────────
   The customer is never asked "what automation do you want?". This is the
   answer, stated to them, because the brand is the expert in the room. */
export const DELIVERABLES = [
  { t: 'Modern customer website',
    d: 'Built to your brand, fast, and written for the customer you actually serve.' },
  { t: 'ComparePower rate experience',
    d: 'Live plans and rates presented inside your own site.' },
  { t: 'Customer & lead capture',
    d: 'Every enquiry lands in one place with its full context attached.' },
  { t: 'Automated follow-up',
    d: 'Email and text sequences that run until a person takes over.' },
  { t: 'Internal pipeline & work queue',
    d: 'Your team sees exactly who needs them next, in order.' },
  { t: 'Team access & permissions',
    d: 'Roles set up so people see their work and nothing they should not.' },
  { t: 'Operational reporting',
    d: 'Volume, response time, conversion — the numbers you run the week on.' },
  { t: 'Testing & training',
    d: 'A full pass with your team before anything touches a real customer.' },
  { t: 'Launch',
    d: 'Cutover on your target date, with us on the line for it.' },
]
