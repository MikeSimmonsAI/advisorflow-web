/**
 * THE NEVER-A-FAKE-ZERO GUARANTEE.
 *
 * Owner Command shipped showing "Customers: 0, Users: 0, Leads: 0" on a healthy
 * platform holding all three, because `asCount(undefined)` is `0` and the field
 * names it asked for did not exist in the payload. These tests pin the property
 * that makes that impossible: a value that was not read is never a number.
 */

import {
  anyError, centsField, listField, numField, stateLabel, textField,
  withNotConfigured,
} from '../api/state';

const READY = (data: unknown) => ({ data, isLoading: false, isError: false });
const LOADING = { data: undefined, isLoading: true, isError: false };
const FAILED = {
  data: undefined, isLoading: false, isError: true,
  error: { status: 503, detail: 'The server is restarting.', offline: true },
};

describe('a failed request is never a business number', () => {
  it('reports error, not zero, when the request failed', () => {
    const f = numField(FAILED, 'total_orgs');
    expect(f.state).toBe('error');
    expect(f.value).toBeUndefined();
    expect(f.reason).toBe('No connection');
  });

  it('reports unavailable, not zero, when the field is absent', () => {
    // Exactly the real bug: the payload is fine, the key is not in it.
    const f = numField(READY({ total_orgs: 12 }), 'organizations', 'org_count');
    expect(f.state).toBe('unavailable');
    expect(f.value).toBeUndefined();
  });

  it('reads the field the server actually returns', () => {
    const f = numField(READY({ total_orgs: 12, total_users: 40 }), 'active_orgs', 'total_orgs');
    expect(f.state).toBe('ready');
    expect(f.value).toBe(12);
  });

  it('keeps a REAL zero as a zero', () => {
    const f = numField(READY({ total_leads: 0 }), 'total_leads');
    expect(f.state).toBe('zero');
    expect(f.value).toBe(0);
  });

  it('stays loading only while genuinely in flight', () => {
    expect(numField(LOADING, 'anything').state).toBe('loading');
    // Settled with no body is not "still loading" — that is a hung spinner.
    expect(numField({ data: undefined, isLoading: false }, 'x').state).toBe('unavailable');
  });

  it('never coerces a non-numeric value into a number', () => {
    expect(numField(READY({ n: 'not a number' }), 'n').state).toBe('unavailable');
    expect(numField(READY({ n: null }), 'n').state).toBe('unavailable');
  });
});

describe('money', () => {
  it('converts cents to units only when a value exists', () => {
    expect(centsField(READY({ visible_mrr_cents: 250_000 }), 'visible_mrr_cents').value)
      .toBe(2500);
    expect(centsField(READY({}), 'visible_mrr_cents').state).toBe('unavailable');
  });

  it('prefers the server\'s own reason over inventing one', () => {
    const f = withNotConfigured(
      centsField(READY({}), 'mrr_cents'),
      'This customer has no plan yet.',
    );
    expect(f.state).toBe('not_configured');
    expect(f.reason).toBe('This customer has no plan yet.');
  });
});

describe('lists', () => {
  it('separates an empty list from a missing one', () => {
    expect(listField(READY({ organizations: [] }), 'organizations').state).toBe('zero');
    expect(listField(READY({}), 'organizations').state).toBe('unavailable');
    expect(listField(FAILED, 'organizations').state).toBe('error');
  });

  it('accepts a bare array as well as an envelope', () => {
    expect(listField(READY([1, 2, 3])).value).toHaveLength(3);
  });
});

describe('text and labels', () => {
  it('treats an empty string as absent', () => {
    expect(textField(READY({ name: '' }), 'name').state).toBe('unavailable');
    expect(textField(READY({ name: 'Atlantis' }), 'name').value).toBe('Atlantis');
  });

  it('never labels a non-value as a number', () => {
    expect(stateLabel('unavailable')).toBe('Unavailable');
    expect(stateLabel('not_configured')).toBe('Not set up');
    expect(stateLabel('error')).toBe('Could not load');
  });

  it('knows when a section should offer retry', () => {
    expect(anyError(numField(FAILED, 'a'), numField(READY({ a: 1 }), 'a'))).toBe(true);
    // Unavailable is OUR bug or a shape change; retrying cannot fix it, so a
    // section holding only unavailable fields must not offer the button.
    expect(anyError(numField(READY({}), 'a'))).toBe(false);
  });

  it('distinguishes a refusal from a transport failure', () => {
    const refused = numField(
      { data: undefined, isLoading: false, isError: true, error: { status: 403 } }, 'a');
    expect(refused.reason).toBe('Not authorised for this');
  });
});
