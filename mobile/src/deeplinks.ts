/**
 * DEEP LINKS ARE NAVIGATION. THEY ARE NOT AUTHORISATION.
 *
 * A link names a record. That is all it does. The screen it opens fetches that
 * record through the normal authorised endpoint and the server decides — so an
 * `https://app.evosyspro.live/leads/<someone-elses-id>` link, arriving from an
 * email, a text message, another app or a hand-typed URL, ends in exactly the
 * refusal a hand-typed API call would. Possession of an id grants nothing.
 *
 * That is why this module contains no permission logic at all, and why adding
 * some would be a mistake: a client-side check here would be a second, weaker
 * copy of the server's answer, and the first thing anybody would do on finding
 * it is trust it.
 *
 * PUBLIC TOKEN LINKS STAY ON THE WEB, DELIBERATELY. `/book`, `/deal-room` and
 * `/survey` are prospect-facing — the person opening one does not have this
 * app and must not be sent to an app store. They are absent from the table
 * below, so the OS opens them in a browser, which is where they work.
 */

/** Record types a push payload or a link may name, and where each one opens. */
const RECORD_ROUTES: Record<string, (id: string) => string> = {
  lead: (id) => `/lead/${id}`,
  opportunity: (id) => `/opportunity/${id}`,
  appointment: (id) => `/appointment/${id}`,
  proposal: (id) => `/proposal/${id}`,
  organization: (id) => `/org/${id}`,
  customer: (id) => `/customer/${id}`,
};

/** Web paths the backend already puts in emails and SMS (the `FRONTEND_URL`
 *  call sites), mapped onto the app's own routes. */
const WEB_PATH_ROUTES: Array<{ pattern: RegExp; type: string }> = [
  { pattern: /^\/leads\/([^/?#]+)/, type: 'lead' },
  { pattern: /^\/sales\/opportunities\/([^/?#]+)/, type: 'opportunity' },
  { pattern: /^\/opportunities\/([^/?#]+)/, type: 'opportunity' },
  { pattern: /^\/sales\/appointments\/([^/?#]+)/, type: 'appointment' },
  { pattern: /^\/appointments\/([^/?#]+)/, type: 'appointment' },
  { pattern: /^\/proposals\/([^/?#]+)/, type: 'proposal' },
  { pattern: /^\/sales\/proposals\/([^/?#]+)/, type: 'proposal' },
];

/** Prospect-facing paths this app must NOT claim. */
const WEB_ONLY = [/^\/book/, /^\/deal-room/, /^\/survey/, /^\/public\//];

export function isWebOnlyPath(path: string): boolean {
  return WEB_ONLY.some((p) => p.test(path));
}

export function routeForRecord(
  recordType: string | null | undefined,
  recordId: string | null | undefined,
): string | null {
  if (!recordType || !recordId) return null;
  const build = RECORD_ROUTES[recordType];
  if (!build) return null;
  // A record id arrives from outside. It goes into a URL path, so anything that
  // could change the shape of that path is refused rather than escaped —
  // there is no legitimate id with a slash or a dot-segment in it.
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(recordId)) return null;
  return build(recordId);
}

/**
 * Turn an incoming URL into an in-app route, or null to let the OS handle it.
 *
 * Returning null is a real answer, not a failure: an unrecognised path on a
 * brand host is a page that exists on the web and not in the app, and sending
 * the person to a 404 inside the app is worse than opening their browser.
 */
export function routeForUrl(url: string): string | null {
  let path: string;
  try {
    const parsed = new URL(url);
    path = parsed.pathname;
  } catch {
    return null;
  }
  if (isWebOnlyPath(path)) return null;

  for (const { pattern, type } of WEB_PATH_ROUTES) {
    const match = path.match(pattern);
    if (match) return routeForRecord(type, match[1]);
  }
  return null;
}
