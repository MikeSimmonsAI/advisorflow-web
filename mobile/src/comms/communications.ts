/**
 * ONE PLACE THE APP REACHES A PROSPECT — and one place a known gap lives.
 *
 * THE GAP, STATED PLAINLY (Phase 0, GAP-4)
 * ----------------------------------------
 * There is no brand-sales prospect messaging on the server. No `sales*` router
 * imports `sms_service` or `email_service`; `sms_router`, `email_router` and
 * `compose_router` are all `require_tenant_user`, and a brand salesperson has
 * `organization_id = NULL` by positive architectural assertion — they sell the
 * product, they are not a tenant of it. So a rep today reaches a prospect
 * through transactional sends only (`/sales/proposals/{id}/send`,
 * `/sales/appointments/{id}/resend-invitation`).
 *
 * WHAT THIS MODULE DOES ABOUT IT
 * ------------------------------
 * V1 hands off to the device: `tel:`, `sms:`, `mailto:`. That works, today, on
 * a real phone, and it is honest about what it is.
 *
 * The important part is that it happens HERE and nowhere else. Not one screen
 * imports `Linking` to dial a number. When the provider-neutral communications
 * layer gains a brand-sales path, `contactProspect` starts calling it and the
 * twelve call sites do not change — which is the entire reason to write an
 * interface around three URL schemes.
 *
 * LOGGING, AS FAR AS IT HONESTLY GOES
 * -----------------------------------
 * A device handoff is invisible to the platform: the OS makes the call, and
 * nothing tells the server it happened. Rather than pretend, every handoff that
 * has an opportunity behind it writes a note through the endpoint that already
 * exists — `POST /sales/opportunities/{id}/notes` — so the activity trail shows
 * "Called the prospect from mobile" at the right time against the right deal.
 *
 * That is a REAL record in the platform's own store, not a shadow one. It is
 * also not a conversation thread, and this module does not pretend it is: there
 * is no message body, no delivery status and no reply. `isFullyLogged` returns
 * false, and the UI says so, because a rep who believes a text was logged when
 * it was not will stop writing notes.
 */

import { Linking, Platform } from 'react-native';

import { sales } from '../api/endpoints';

export type ContactChannel = 'call' | 'text' | 'email';

export type ContactTarget = {
  channel: ContactChannel;
  /** E.164 or a human-typed number for call/text; an address for email. */
  value: string;
  /** For the activity note, when the contact belongs to a deal. */
  opportunityId?: string | null;
  /** Shown in the note so the trail says who, not just what. */
  personLabel?: string | null;
  subject?: string;
  body?: string;
};

export type ContactResult = {
  opened: boolean;
  logged: boolean;
  reason?: string;
};

/**
 * Whether a completed contact is visible in the platform afterwards.
 *
 * FALSE TODAY, ON PURPOSE. Flipping this to true is the single switch that
 * says the brand-sales communications backend has landed; every "not logged"
 * caption in the UI reads from it rather than hard-coding the claim.
 */
export const isFullyLogged = false;

/** Strip everything a dialler cannot use. A number typed with spaces, dashes
 *  and a country code in brackets is the normal case, not the exception. */
function dialable(value: string): string {
  const trimmed = value.trim();
  const plus = trimmed.startsWith('+') ? '+' : '';
  return plus + trimmed.replace(/[^\d]/g, '');
}

function buildUrl(target: ContactTarget): string | null {
  const { channel, value } = target;
  if (!value?.trim()) return null;

  if (channel === 'call') return `tel:${dialable(value)}`;

  if (channel === 'text') {
    const number = dialable(value);
    const body = target.body ? encodeURIComponent(target.body) : '';
    // iOS and Android disagree about the separator before the body. Getting
    // this wrong opens the composer with the body silently dropped.
    if (!body) return `sms:${number}`;
    return Platform.OS === 'ios'
      ? `sms:${number}&body=${body}`
      : `sms:${number}?body=${body}`;
  }

  const params: string[] = [];
  if (target.subject) params.push(`subject=${encodeURIComponent(target.subject)}`);
  if (target.body) params.push(`body=${encodeURIComponent(target.body)}`);
  return `mailto:${encodeURIComponent(value.trim())}${params.length ? `?${params.join('&')}` : ''}`;
}

const VERB: Record<ContactChannel, string> = {
  call: 'Called',
  text: 'Texted',
  email: 'Emailed',
};

/**
 * Reach the prospect, and leave a trace where one can be left.
 *
 * The note is best-effort and is written AFTER the handoff opens. A failed note
 * must never stop a rep from making a call — the call is the work, the note is
 * the record of it.
 */
export async function contactProspect(target: ContactTarget): Promise<ContactResult> {
  const url = buildUrl(target);
  if (!url) return { opened: false, logged: false, reason: 'No number or address on file.' };

  let opened = false;
  try {
    const supported = await Linking.canOpenURL(url);
    if (!supported) {
      return {
        opened: false,
        logged: false,
        reason: target.channel === 'call'
          ? 'This device cannot place calls.'
          : 'This device has no app for that.',
      };
    }
    await Linking.openURL(url);
    opened = true;
  } catch {
    return { opened: false, logged: false, reason: 'Could not open that on this device.' };
  }

  if (!target.opportunityId) {
    return {
      opened,
      logged: false,
      reason: 'Not linked to a deal, so there is nowhere to record it yet.',
    };
  }

  try {
    const who = target.personLabel ? ` ${target.personLabel}` : ' the prospect';
    await sales.addNote(target.opportunityId, {
      body: `${VERB[target.channel]}${who} from mobile.`,
      note: `${VERB[target.channel]}${who} from mobile.`,
    });
    return { opened, logged: true };
  } catch {
    return {
      opened,
      logged: false,
      reason: 'The call went through; the note did not save. Add it when you have signal.',
    };
  }
}

/** What the UI tells the person about where this ends up. One sentence, and it
 *  is the truth rather than the aspiration. */
export function contactDisclosure(hasOpportunity: boolean): string {
  if (isFullyLogged) return 'This is sent and logged in EvoSys Pro.';
  return hasOpportunity
    ? 'Your phone makes the call or text. EvoSys Pro records that it happened, not what was said.'
    : 'Your phone makes the call or text. Nothing is recorded until this is linked to a deal.';
}
