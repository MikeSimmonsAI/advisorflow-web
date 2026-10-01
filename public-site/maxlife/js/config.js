/* Max Life public site - deployment configuration (DATA, not logic).
 *
 * apiBase:       origin of the EvoSys Pro API that hosts /site-intake/... ("" = same origin,
 *                e.g. when the site is served behind a reverse proxy that forwards /site-intake).
 * platformSlug:  the brand slug an operator configured in God Mode -> Platform -> Public intake.
 *                It NAMES a brand; the destination workspace is resolved server-side. No org id here.
 * consentVersion: bump whenever the SMS disclosure wording in index.html changes.
 */
window.MAXLIFE_SITE = {
  apiBase: "",
  platformSlug: "maxlife",
  consentVersion: "maxlife-site-sms-2026-10-01"
};
