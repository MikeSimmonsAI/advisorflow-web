/**
 * launchConfig — the Launch Engine's data, in one place.
 *
 * ===========================================================================
 * THE ARCHITECTURE THIS FILE EXISTS TO PROTECT
 * ===========================================================================
 *
 *     ADVISORFLOW            the platform capability
 *          |
 *     LAUNCH ENGINE          onboarding, as a platform feature
 *          |
 *     WHITE-LABEL BRAND      EvoSys Pro, BookaBoost, the next one
 *          |
 *     CUSTOMER ORGANIZATION  Atlantis Light & Power
 *
 * Nothing below is imported by name anywhere. Every component takes `brand`
 * and `customer` as props and reads them by shape, so Stage 2 replaces the two
 * literals at the bottom of this file with a fetch and changes nothing else.
 *
 * WHAT THE CUSTOMER SEES IS THE BRAND. AdvisorFlow powers this and is named
 * once, quietly, in the rail footer — the way a platform is credited, not the
 * way a product is advertised. Atlantis is onboarding with EvoSys Pro.
 *
 * ===========================================================================
 * STAGE 1 — MOCK DATA ONLY
 * ===========================================================================
 *
 * Every value here is invented for the prototype. The credential fields on the
 * Website & Hosting step carry EMPTY strings and always will in this file:
 * a real password must never sit in a bundled literal, and Stage 2's storage
 * does not exist yet.
 */

/* ── the two icons' worth of geometry the rail and cards need ────────────── */
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

/* ── WHITE-LABEL BRAND ─────────────────────────────────────────────────────
   Stage 2 replaces this object with GET /launch/brand. The shape is what the
   components depend on; the values are not. */
export const MOCK_BRAND = {
  id: 'brand_evosyspro',
  name: 'EvoSys Pro',
  short: 'EP',
  tagline: 'Enterprise Systems, Engineered',
  logoUrl: null,             // real asset drops in here; the mark falls back to `short`
  supportEmail: 'launch@evosyspro.live',
  supportPhone: '(469) 553-7417',
  ecosystem: 'EvoSys Pro Ecosystem',
  poweredBy: 'AdvisorFlow',  // named once, quietly, in the rail footer
}

/* ── CUSTOMER ORGANIZATION ────────────────────────────────────────────────
   Stage 2 replaces this with GET /launch/customer/:id. */
export const MOCK_CUSTOMER = {
  id: 'org_atlantis',
  name: 'Atlantis Light & Power',
  short: 'AL',
  logoUrl: null,             // NO fabricated logo — see LaunchHero
  industry: 'Retail Electricity Provider',
  targetGoLive: '2026-11-03',
  user: { name: 'Dana Whitfield', title: 'Director of Operations',
          email: 'dwhitfield@atlantislp.com', initials: 'DW' },
}

/* ── THE IMPLEMENTATION LIFECYCLE ─────────────────────────────────────────
   The top tracker: where the WHOLE implementation stands, of which the
   customer's intake is only the first phase. It is deliberately a different
   axis from the intake steps in the right panel — one is the project, the
   other is the form. Collapsing them was the thing that made earlier
   onboarding screens read as a wizard rather than a programme. */
export const LIFECYCLE = [
  { key: 'intake',       label: 'Complete Intake',       state: 'now'  },
  { key: 'access',       label: 'Provide Access & Files', state: 'next' },
  { key: 'build',        label: 'EvoSys Pro Builds',      state: 'next' },
  { key: 'integrations', label: 'Integrations',           state: 'next' },
  { key: 'review',       label: 'Review & Test',          state: 'next' },
  { key: 'training',     label: 'Training',               state: 'next' },
  { key: 'golive',       label: 'Go Live',                state: 'next' },
]

/* ── THE INTAKE STEPS ─────────────────────────────────────────────────────
   `pct` is Stage 1 mock completion. Stage 2 computes it from answered
   required fields; nothing that reads it needs to know which.

   THE SEEDED NUMBERS ARE CHOSEN, NOT ARBITRARY. They average to the 20% the
   approved design shows on Step 1, and they put the prototype in a state where
   every condition the UI has to render is actually on screen somewhere: one
   section complete, one in progress, the rest not started, and one carrying
   named outstanding items on the review screen. A demo seeded to all-complete
   would never show the review screen doing its job. */
export const STEPS = [
  { key: 'company',  n: 1, label: 'Company Information',
    title: 'Company Information',
    blurb: 'The legal and operating details we build your system around — how you are named on contracts, who we contact, and where you serve customers.',
    pct: 60 },
  { key: 'branding', n: 2, label: 'Branding & Assets',
    title: 'Branding & Assets',
    blurb: 'Everything your new website and customer communications will be dressed in. Send what you have; we will tell you what is missing.',
    pct: 100 },
  { key: 'website',  n: 3, label: 'Website & Hosting Access',
    title: 'Website & Hosting Access',
    blurb: 'Where your current site and domain live, so we can build alongside it and cut over cleanly without any downtime for your customers.',
    pct: 0 },
  { key: 'compare',  n: 4, label: 'ComparePower Integration',
    title: 'ComparePower Integration',
    blurb: 'Your ComparePower relationship and the people who own it. EvoSys Pro handles the technical integration end to end.',
    pct: 0 },
  { key: 'systems',  n: 5, label: 'Team & Current Systems',
    title: 'Team & Current Systems',
    blurb: 'The tools you run on today and the people who use them, so nothing you depend on gets stranded when the new system goes live.',
    pct: 0 },
  { key: 'process',  n: 6, label: 'Customer Process',
    title: 'Customer Process',
    blurb: 'What happens after a customer gives you their information. This is the single most important section — it is what the automation is built to reproduce.',
    pct: 0 },
  { key: 'files',    n: 7, label: 'Files & Documents',
    title: 'Files & Documents',
    blurb: 'The documents and samples we need in hand before the build starts.',
    pct: 0 },
  { key: 'review',   n: 8, label: 'Review & Submit',
    title: 'Review & Submit',
    blurb: 'A last look at everything you have given us, then sign off and hand it to the implementation team.',
    pct: 0 },
]

export const STEP_KEYS = STEPS.map(s => s.key)

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

/* ── MOCK INTAKE ANSWERS ──────────────────────────────────────────────────
   Believable Atlantis values so the prototype reads as a real screen. NOTHING
   sensitive is seeded: every credential field is deliberately empty. */
export const MOCK_ANSWERS = {
  legalName: 'Atlantis Light & Power, LLC',
  dba: 'Atlantis Light & Power',
  ein: '86-••••••4',
  contactFirst: 'Dana',
  contactLast: 'Whitfield',
  contactTitle: 'Director of Operations',
  contactEmail: 'dwhitfield@atlantislp.com',
  contactMobile: '(214) 555-0142',
  contactBusiness: '(972) 555-0110',
  address: '4400 Gulf Tower Drive, Suite 720',
  city: 'Houston',
  state: 'TX',
  zip: '77027',
  website: 'https://atlantislp.com',
  founded: '2014',
  employees: '25-50',
  territory: 'Texas deregulated market',
  statesServed: 'Texas',
  markets: 'Oncor, CenterPoint, AEP Texas Central, TNMP',
  goLive: '2026-11-03',
  additionalInfo: '',
  notes: '',

  // Website & hosting — credentials intentionally blank in Stage 1.
  hostProvider: 'SiteGround',
  hostUser: '',
  hostPass: '',
  hostLoginUrl: 'https://login.siteground.com',
  cms: 'WordPress 6.x',
  registrar: 'GoDaddy',
  registrarUser: '',
  registrarPass: '',
  dnsUrl: 'https://sso.godaddy.com',
  techContact: 'Ray Okonkwo — ray@atlantislp.com',

  // ComparePower
  cpContact: 'Marissa Lopez',
  cpEmail: 'partners@comparepower.com',
  cpPhone: '(512) 555-0177',
  cpTech: '',
  cpDocsUrl: '',
  cpSandbox: 'not_requested',
  cpProd: 'not_requested',
  cpPartnerId: '',
  cpNotes: '',

  // Systems
  sysAdmin: 'Ray Okonkwo — IT Manager',
  leadRecipient: 'Customer Care shared inbox',
  emailProvider: 'Google Workspace',
  smsProvider: 'None today',
  calendarProvider: 'Google Calendar',
  crm: 'Spreadsheets + shared inbox',
  forms: 'WordPress contact form',
  otherSoftware: 'QuickBooks Online, Slack',
  userList: '',

  // Process
  processDetail: '',
  firstReceiver: 'Customer Care shared inbox',
  responseTime: 'same_day',
  rateReviewer: 'Customer Care specialist',
  enrollmentHelper: 'Customer Care specialist',
  completionMarker: '',
  noResponse: '',

  // Signature
  sigName: '',
  sigTitle: '',
  sigCompany: 'Atlantis Light & Power, LLC',
  sigDate: '',
  sigAffirm: false,
}
