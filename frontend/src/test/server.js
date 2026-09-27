/**
 * Shared msw server for the test suite.  Default handlers give every test an
 * authenticated session (GET /auth/me) so the RequireAuth gate + Nav user
 * menu render without per-test setup.  Override per test with
 * `server.use(http.get(api('/auth/me'), () => ...))`.
 */
import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

export const API_BASE = 'http://localhost:8000';
export const api = (path) => `${API_BASE}${path}`;

export const TEST_ME = {
  user: { id: 'user-1', email: 'ada@example.com', name: 'Ada Lovelace', display_name: 'Ada Lovelace' },
  workspace: { id: 'ws-1', name: 'Acme Outreach', slug: 'acme-outreach' },
  role: 'owner',
  notify_email_enabled: true,
  digest_enabled: false,
  memberships: [{ tenant_id: 'ws-1', tenant_name: 'Acme Outreach', role: 'owner' }],
  allow_signup: true,
};

/** The workspace member list behind useMembers() / owner avatars + pickers. */
export const TEST_MEMBERS = [
  {
    user_id: 'user-1', email: 'ada@example.com', name: 'Ada Lovelace',
    display_name: 'Ada Lovelace', role: 'owner', joined_at: '2026-01-01T00:00:00Z',
  },
];

export const defaultHandlers = [
  http.get(api('/auth/me'), () => HttpResponse.json(TEST_ME)),
  http.get(api('/team/members'), () => HttpResponse.json(TEST_MEMBERS)),
  http.get(api('/auth/config'), () => HttpResponse.json({ allow_signup: true, platform_email: false })),
];

export const server = setupServer(...defaultHandlers);
