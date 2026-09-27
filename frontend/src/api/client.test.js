import { describe, it, expect, vi, afterEach } from 'vitest';
import { http, HttpResponse } from 'msw';
import client, { authRedirect, handleResponseError, isPublicPath, loginUrlFor } from './client.js';
import { server, api } from '../test/server.js';

afterEach(() => {
  vi.restoreAllMocks();
  window.history.pushState({}, '', '/');
});

describe('api client', () => {
  it('creates an axios instance with JSON headers', () => {
    expect(client.defaults.headers['Content-Type']).toBe('application/json');
    expect(typeof client.defaults.baseURL).toBe('string');
    expect(client.defaults.baseURL.length).toBeGreaterThan(0);
  });

  it('sends cookies with every request', () => {
    expect(client.defaults.withCredentials).toBe(true);
  });

  it('builds login URLs carrying the current path', () => {
    expect(loginUrlFor('/campaigns/1', '?tab=x')).toBe('/login?next=%2Fcampaigns%2F1%3Ftab%3Dx');
    expect(loginUrlFor('/', '')).toBe('/login');
    expect(isPublicPath('/accept-invite')).toBe(true);
    expect(isPublicPath('/campaigns')).toBe(false);
  });
});

describe('401 interceptor', () => {
  it('redirects to /login?next=… when a feature endpoint returns 401', async () => {
    const go = vi.spyOn(authRedirect, 'go').mockImplementation(() => {});
    window.history.pushState({}, '', '/campaigns/abc?tab=leads');
    server.use(http.get(api('/campaigns/'), () => new HttpResponse(null, { status: 401 })));

    await expect(client.get('/campaigns/')).rejects.toMatchObject({ response: { status: 401 } });
    expect(go).toHaveBeenCalledWith('/login?next=%2Fcampaigns%2Fabc%3Ftab%3Dleads');
  });

  it('does not redirect for /auth/ endpoints', async () => {
    const go = vi.spyOn(authRedirect, 'go').mockImplementation(() => {});
    window.history.pushState({}, '', '/campaigns');
    server.use(http.post(api('/auth/login'), () => HttpResponse.json({ detail: 'invalid email or password' }, { status: 401 })));

    await expect(client.post('/auth/login', {})).rejects.toMatchObject({ response: { status: 401 } });
    expect(go).not.toHaveBeenCalled();
  });

  it('does not redirect while already on a public route', async () => {
    const go = vi.spyOn(authRedirect, 'go').mockImplementation(() => {});
    window.history.pushState({}, '', '/reset-password?token=t');
    await expect(handleResponseError({
      config: { url: '/leads/' }, response: { status: 401, data: {} },
    })).rejects.toBeTruthy();
    expect(go).not.toHaveBeenCalled();
  });

  it('dispatches integration-missing on a 409 integration_not_configured', async () => {
    const listener = vi.fn();
    window.addEventListener('integration-missing', listener);
    const body = { error: 'integration_not_configured', provider: 'brevo', detail: 'Brevo is not configured' };
    server.use(http.post(api('/campaigns/x/approve-all'), () => HttpResponse.json(body, { status: 409 })));

    await expect(client.post('/campaigns/x/approve-all')).rejects.toBeTruthy();
    expect(listener).toHaveBeenCalledTimes(1);
    expect(listener.mock.calls[0][0].detail).toMatchObject({ provider: 'brevo' });
    window.removeEventListener('integration-missing', listener);
  });
});
