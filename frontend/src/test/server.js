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

/** Build one GET /settings/integrations row.  Every provider defaults to
 *  configured so feature entry points stay enabled in unrelated tests. */
export function makeIntegration(provider, overrides = {}) {
  const LABELS = {
    anthropic: 'Anthropic', brevo: 'Brevo', hunter: 'Hunter.io',
    apollo: 'Apollo.io', unipile: 'Unipile', adzuna: 'Adzuna',
  };
  const hasWebhook = provider === 'brevo' || provider === 'unipile';
  return {
    provider,
    label: LABELS[provider] || provider,
    description: `${LABELS[provider] || provider} integration`,
    docs_url: `https://example.com/${provider}`,
    fields: [
      { key: 'api_key', label: 'API key', secret: true, required: true, placeholder: '', help: '' },
    ],
    configured: true,
    preview: '••••ab12',
    values: {},
    last_test_status: 'ok',
    last_tested_at: '2026-09-01T10:00:00Z',
    last_test_error: null,
    updated_at: '2026-09-01T10:00:00Z',
    webhook_url: hasWebhook ? `https://api.example.com/webhooks/${provider}/ws-1` : null,
    webhook_header: hasWebhook ? `X-${provider}-Auth` : null,
    ...overrides,
  };
}

export const INTEGRATION_PROVIDERS = ['anthropic', 'brevo', 'hunter', 'apollo', 'unipile', 'adzuna'];

/** All providers configured (the default for every test). */
export const TEST_INTEGRATIONS = INTEGRATION_PROVIDERS.map((p) => makeIntegration(p));

/** Handler override: the listed providers report `configured: false`. */
export function integrationsWithMissing(...missing) {
  return http.get(api('/settings/integrations'), () => HttpResponse.json(
    INTEGRATION_PROVIDERS.map((p) => makeIntegration(p, missing.includes(p)
      ? { configured: false, preview: null, last_test_status: null, last_tested_at: null }
      : {})),
  ));
}

/** Agent access (Muse over MCP) defaults: a public endpoint, no keys. */
export const TEST_MCP_CONNECTION = {
  mcp_url: 'https://outreach.example.com/mcp',
  public: true,
};

export const defaultHandlers = [
  http.get(api('/api-keys/connection'), () => HttpResponse.json(TEST_MCP_CONNECTION)),
  http.get(api('/api-keys'), () => HttpResponse.json([])),
  http.get(api('/settings/integrations'), () => HttpResponse.json(TEST_INTEGRATIONS)),
  http.get(api('/auth/me'), () => HttpResponse.json(TEST_ME)),
  http.get(api('/team/members'), () => HttpResponse.json(TEST_MEMBERS)),
  http.get(api('/auth/config'), () => HttpResponse.json({ allow_signup: true, platform_email: false })),
];

export const server = setupServer(...defaultHandlers);
