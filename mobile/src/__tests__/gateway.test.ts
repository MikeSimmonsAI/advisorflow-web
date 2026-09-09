/**
 * THE REDEPLOY WINDOW.
 *
 * The backend behind this app is deployed many times a day. For roughly twenty
 * to forty seconds around each deploy the edge answers 502/503/504 with its own
 * plain-text body:
 *
 *     upstream connect error or disconnect/reset before headers.
 *     reset reason: connection termination
 *
 * A phone that launches inside that window used to surface that as a hard
 * failure, and a 2xx carrying a non-JSON body used to be handed to screens as
 * though it had parsed — which is how another system's error text ends up
 * rendered inside this app as if the app had said it.
 *
 * These pin both: ride out the window, and never pass off a foreign body as
 * data.
 */

import { ApiError, api, request } from '../api/client';

const ENVOY_BODY =
  'upstream connect error or disconnect/reset before headers. ' +
  'reset reason: connection termination';

function res(status: number, body: string, json = false) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (!json) throw new SyntaxError('Unexpected token u in JSON');
      return JSON.parse(body);
    },
    text: async () => body,
  } as unknown as Response;
}

describe('gateway errors during a redeploy', () => {
  afterEach(() => jest.useRealTimers());

  it('retries a 503 and succeeds when the new instance comes up', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(res(503, ENVOY_BODY))
      .mockResolvedValueOnce(res(200, '{"status":"ok"}', true));
    (global.fetch as jest.Mock) = fetchMock;

    await expect(api.get('/health')).resolves.toEqual({ status: 'ok' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  }, 20000);

  it('gives up after the retries and reports a restart, not a refusal', async () => {
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(res(502, ENVOY_BODY));

    const err = (await api.get('/health').catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(502);
    // offline: the request never reached the app server, so this is a retry
    // situation and must not read as "you may not do that".
    expect(err.offline).toBe(true);
    expect(err.isAuthorization).toBe(false);
    expect(err.detail).toMatch(/restarting/i);
    // The proxy's own words are never adopted as this API's message.
    expect(err.detail).not.toMatch(/upstream connect error/i);
  }, 30000);

  it('does not retry an ordinary application error', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValue(res(403, '{"detail":"Not permitted"}', true));
    (global.fetch as jest.Mock) = fetchMock;

    const err = (await api.get('/leads/1').catch((e) => e)) as ApiError;
    expect(err.status).toBe(403);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('a 2xx that is not JSON', () => {
  it('is refused rather than handed to a screen as data', async () => {
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(res(200, ENVOY_BODY));

    const err = (await request('/auth/my-contexts').catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.offline).toBe(true);
    expect(err.detail).not.toContain('upstream connect error');
  });

  it('still allows an empty body', async () => {
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(res(200, ''));
    await expect(request('/whatever')).resolves.toBeUndefined();
  });
});
