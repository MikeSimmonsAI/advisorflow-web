/**
 * THE API CLIENT — headers, refusals, and the offline case.
 *
 * The workspace-header tests are the important ones. `X-Workspace-Id` scopes a
 * request to one customer organization; sending it from an executive or
 * platform screen asks the server to narrow a brand-wide read to a tenant, and
 * leaving a stale one behind after a switch is a cross-tenant request with a
 * plausible-looking explanation.
 */

import {
  ApiError, api, getRequestContext, request, setRequestContext,
  setUnauthorizedHandler,
} from '../api/client';

function ok(body: unknown = {}, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    content: '1',
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

describe('request context', () => {
  beforeEach(() => {
    setRequestContext({});
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(ok());
  });

  function headersOfLastCall(): Record<string, string> {
    const call = (global.fetch as jest.Mock).mock.calls.at(-1);
    return call[1].headers as Record<string, string>;
  }

  it('always identifies the device', async () => {
    await api.get('/anything');
    const h = headersOfLastCall();
    expect(h['X-Client-Platform']).toBeDefined();
    expect(h['X-Device-Id']).toBeDefined();
    expect(h['X-App-Version']).toBeDefined();
  });

  it('sends the workspace header only inside a workspace', async () => {
    setRequestContext({ workspaceOrgId: 'org-1' });
    await api.get('/leads/');
    expect(headersOfLastCall()['X-Workspace-Id']).toBe('org-1');
  });

  it('drops the workspace header when the context is replaced', async () => {
    setRequestContext({ workspaceOrgId: 'org-1' });
    setRequestContext({ workspaceOrgId: null });
    await api.get('/executive/portfolio');
    expect(headersOfLastCall()['X-Workspace-Id']).toBeUndefined();
  });

  it('replaces the whole context rather than merging into it', async () => {
    // A partial setter is how a stale org id survives a switch into an
    // experience that must not send one.
    setRequestContext({ workspaceOrgId: 'org-1', brandOverride: 'brand-1' });
    setRequestContext({ brandOverride: 'brand-2' });
    expect(getRequestContext().workspaceOrgId).toBeUndefined();
    await api.get('/x');
    expect(headersOfLastCall()['X-Workspace-Id']).toBeUndefined();
  });
});

describe('failures', () => {
  afterEach(() => setUnauthorizedHandler(null));

  it('calls the single 401 handler exactly once and does not retry', async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(
      ok({ detail: 'Session expired. Please log in again.' }, 401));

    await expect(api.get('/auth/my-contexts')).rejects.toBeInstanceOf(ApiError);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(global.fetch as jest.Mock).toHaveBeenCalledTimes(1);
  });

  it('lets sign-in handle its own 401, because there it means "wrong password"', async () => {
    const onUnauthorized = jest.fn();
    setUnauthorizedHandler(onUnauthorized);
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(
      ok({ detail: 'Incorrect email or password' }, 401));

    await expect(
      request('/auth/login', { method: 'POST', allowUnauthorized: true }),
    ).rejects.toMatchObject({ detail: 'Incorrect email or password' });
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('marks a request that never left the phone as offline', async () => {
    (global.fetch as jest.Mock) = jest.fn().mockRejectedValue(
      new TypeError('Network request failed'));
    const err = await api.get('/sales/my-day').catch((e) => e as ApiError);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).offline).toBe(true);
    expect((err as ApiError).status).toBe(0);
  });

  it('keeps a refusal distinguishable from a transport failure', async () => {
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(
      ok({ detail: 'Executive Suite access required.' }, 403));
    const err = await api.get('/executive/portfolio').catch((e) => e as ApiError);
    expect((err as ApiError).offline).toBe(false);
    expect((err as ApiError).isAuthorization).toBe(true);
    expect((err as ApiError).detail).toBe('Executive Suite access required.');
  });

  it('surfaces the server own message rather than a generic one', async () => {
    (global.fetch as jest.Mock) = jest.fn().mockResolvedValue(
      ok({ detail: 'Too many failed login attempts. Please wait 15 minutes.' }, 429));
    const err = await api.get('/x').catch((e) => e as ApiError);
    expect((err as ApiError).detail).toMatch(/15 minutes/);
  });
});
