import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';

import IntegrationsTab, { buildIntegrationPayload } from './IntegrationsSettings.jsx';
import { api, makeIntegration, server, TEST_ME } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

const ANTHROPIC_FIELDS = [
  { key: 'api_key', label: 'API key', secret: true, required: true, placeholder: 'sk-ant-…', help: 'From console.anthropic.com' },
  { key: 'model', label: 'Model', secret: false, required: false, placeholder: '', help: '' },
];

const BREVO_FIELDS = [
  { key: 'api_key', label: 'API key', secret: true, required: true, placeholder: 'xkeysib-…', help: '' },
  { key: 'sender_email', label: 'Sender email', secret: false, required: true, placeholder: 'you@acme.com', help: '' },
];

function listFixture(overrides = {}) {
  return [
    makeIntegration('anthropic', {
      fields: ANTHROPIC_FIELDS,
      values: { model: 'claude-sonnet-4-6' },
      preview: '••••ab12',
      ...overrides.anthropic,
    }),
    makeIntegration('brevo', {
      fields: BREVO_FIELDS,
      configured: false, preview: null, last_test_status: null, last_tested_at: null,
      values: {},
      ...overrides.brevo,
    }),
    makeIntegration('unipile', { ...overrides.unipile }),
  ];
}

let current;
function useList(list) {
  current = list;
  server.use(http.get(api('/settings/integrations'), () => HttpResponse.json(current)));
}

function asRole(role) {
  server.use(http.get(api('/auth/me'), () => HttpResponse.json({ ...TEST_ME, role })));
}

function renderTab() {
  return renderRoutes([<Route key="s" path="/settings" element={<IntegrationsTab />} />], { path: '/settings' });
}

beforeEach(() => {
  useList(listFixture());
});

describe('buildIntegrationPayload', () => {
  it('omits blank secrets and keeps non-secret values', () => {
    expect(buildIntegrationPayload(ANTHROPIC_FIELDS, { api_key: '  ', model: ' claude-x ' }))
      .toEqual({ model: 'claude-x' });
    expect(buildIntegrationPayload(ANTHROPIC_FIELDS, { api_key: 'sk-new', model: '' }))
      .toEqual({ api_key: 'sk-new', model: '' });
  });
});

describe('Integrations tab', () => {
  it('renders one card per provider with status, preview and intro copy', async () => {
    renderTab();
    const anthropic = await screen.findByTestId('integration-card-anthropic');
    expect(screen.getByText(/each workspace uses its own api keys/i)).toBeInTheDocument();
    expect(within(anthropic).getByTestId('integration-status')).toHaveTextContent('Connected ✓');
    expect(within(anthropic).getByTestId('integration-preview')).toHaveTextContent('••••ab12');
    expect(within(anthropic).getByRole('link', { name: /docs/i })).toHaveAttribute('href', 'https://example.com/anthropic');

    const brevo = screen.getByTestId('integration-card-brevo');
    expect(within(brevo).getByTestId('integration-status')).toHaveTextContent('Not connected');
    expect(await within(brevo).findByRole('button', { name: 'Connect' })).toBeInTheDocument();
    // Webhook section only for a configured webhook provider.
    expect(within(brevo).queryByTestId('webhook-section-brevo')).toBeNull();
    const unipile = screen.getByTestId('integration-card-unipile');
    expect(within(unipile).getByTestId('webhook-url')).toHaveTextContent('/webhooks/unipile/ws-1');
    expect(within(unipile).getByTestId('webhook-header')).toHaveTextContent('X-unipile-Auth');
  });

  it('shows the "Test failed" pill with the stored error', async () => {
    useList(listFixture({ anthropic: { last_test_status: 'failed', last_test_error: 'invalid x-api-key' } }));
    renderTab();
    const card = await screen.findByTestId('integration-card-anthropic');
    expect(within(card).getByTestId('integration-status')).toHaveTextContent('Test failed');
    expect(within(card).getByTestId('integration-test-error')).toHaveTextContent('invalid x-api-key');
  });

  it('edit leaves a blank secret out of the PUT body (preserved server-side)', async () => {
    let body = null;
    server.use(http.put(api('/settings/integrations/anthropic'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(makeIntegration('anthropic', { fields: ANTHROPIC_FIELDS, values: body }));
    }));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-anthropic');
    await user.click(await within(card).findByRole('button', { name: 'Edit' }));

    const secret = within(card).getByTestId('integration-field-api_key');
    expect(secret).toHaveValue('');
    expect(secret).toHaveAttribute('type', 'password');
    expect(secret).toHaveAttribute('placeholder', '•••• saved — leave blank to keep');
    const model = within(card).getByTestId('integration-field-model');
    expect(model).toHaveValue('claude-sonnet-4-6');

    await user.clear(model);
    await user.type(model, 'claude-opus');
    await user.click(within(card).getByTestId('integration-save'));

    await waitFor(() => expect(body).toEqual({ model: 'claude-opus' }));
    await waitFor(() => expect(within(card).queryByTestId('integration-form-anthropic')).toBeNull());
  });

  it('connect sends the entered secret; show/hide toggles the input type', async () => {
    let body = null;
    server.use(http.put(api('/settings/integrations/brevo'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(makeIntegration('brevo', { fields: BREVO_FIELDS }));
    }));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-brevo');
    await user.click(await within(card).findByRole('button', { name: 'Connect' }));

    // Required fields are validated client-side first.
    await user.click(within(card).getByTestId('integration-save'));
    expect(within(card).getAllByText('Required')).toHaveLength(2);
    expect(body).toBeNull();

    const secret = within(card).getByTestId('integration-field-api_key');
    await user.type(secret, 'xkeysib-123');
    await user.click(within(card).getByTestId('integration-toggle-api_key'));
    expect(secret).toHaveAttribute('type', 'text');
    await user.type(within(card).getByTestId('integration-field-sender_email'), 'me@acme.com');
    await user.click(within(card).getByTestId('integration-save'));

    await waitFor(() => expect(body).toEqual({ api_key: 'xkeysib-123', sender_email: 'me@acme.com' }));
  });

  it('surfaces a server 422 on save', async () => {
    server.use(http.put(api('/settings/integrations/brevo'), () => (
      HttpResponse.json({ detail: 'missing: sender_email' }, { status: 422 })
    )));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-brevo');
    await user.click(await within(card).findByRole('button', { name: 'Connect' }));
    await user.type(within(card).getByTestId('integration-field-api_key'), 'k');
    await user.type(within(card).getByTestId('integration-field-sender_email'), 'x@y.z');
    await user.click(within(card).getByTestId('integration-save'));
    expect(await within(card).findByTestId('integration-save-error')).toHaveTextContent('missing: sender_email');
  });

  it('test connection shows the failure error text', async () => {
    server.use(http.post(api('/settings/integrations/anthropic/test'), () => {
      const failed = makeIntegration('anthropic', {
        fields: ANTHROPIC_FIELDS,
        last_test_status: 'failed',
        last_test_error: '401 authentication_error: invalid x-api-key',
      });
      current = current.map((r) => (r.provider === 'anthropic' ? failed : r));
      return HttpResponse.json(failed);
    }));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-anthropic');
    await user.click(await within(card).findByRole('button', { name: /test connection/i }));
    expect(await within(card).findByTestId('integration-test-error'))
      .toHaveTextContent('401 authentication_error: invalid x-api-key');
    expect(within(card).getByTestId('integration-status')).toHaveTextContent('Test failed');
  });

  it('remove confirms in a dialog then DELETEs', async () => {
    let deleted = false;
    server.use(http.delete(api('/settings/integrations/anthropic'), () => {
      deleted = true;
      return new HttpResponse(null, { status: 204 });
    }));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-anthropic');
    await user.click(await within(card).findByRole('button', { name: 'Remove' }));
    const dialog = await screen.findByTestId('remove-integration-modal');
    expect(deleted).toBe(false);
    await user.click(within(dialog).getByTestId('confirm-remove-integration'));
    await waitFor(() => expect(deleted).toBe(true));
  });

  it('reveal secret fetches and shows the webhook secret', async () => {
    server.use(http.get(api('/settings/integrations/unipile/webhook-secret'), () => HttpResponse.json({
      webhook_secret: 'whsec_abc123',
      webhook_url: 'https://api.example.com/webhooks/unipile/ws-1',
      webhook_header: 'X-Unipile-Auth',
    })));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-unipile');
    expect(within(card).queryByTestId('webhook-secret')).toBeNull();
    await user.click(await within(card).findByTestId('reveal-webhook-secret'));
    expect(await within(card).findByTestId('webhook-secret')).toHaveTextContent('whsec_abc123');
  });

  it('rotate secret confirms, then shows the new secret', async () => {
    server.use(http.post(api('/settings/integrations/unipile/rotate-webhook-secret'), () => HttpResponse.json({
      webhook_secret: 'whsec_rotated', webhook_url: 'u', webhook_header: 'h',
    })));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-unipile');
    await user.click(await within(card).findByTestId('rotate-webhook-secret'));
    const dialog = await screen.findByTestId('rotate-secret-modal');
    await user.click(within(dialog).getByTestId('confirm-rotate-secret'));
    expect(await within(card).findByTestId('webhook-secret')).toHaveTextContent('whsec_rotated');
  });

  it('register webhooks toasts success and failure', async () => {
    server.use(http.post(api('/settings/integrations/unipile/register-webhooks'), () => HttpResponse.json({
      registered: ['messaging', 'account_status', 'users'], url: 'u',
    })));
    const user = userEvent.setup();
    renderTab();
    const card = await screen.findByTestId('integration-card-unipile');
    await user.click(await within(card).findByTestId('register-unipile-webhooks'));
    expect(await screen.findByText(/registered 3 unipile webhooks/i)).toBeInTheDocument();

    server.use(http.post(api('/settings/integrations/unipile/register-webhooks'), () => (
      HttpResponse.json({ detail: 'Unipile rejected the request' }, { status: 409 })
    )));
    await user.click(within(card).getByTestId('register-unipile-webhooks'));
    expect(await screen.findByText('Unipile rejected the request')).toBeInTheDocument();
  });

  it('members get a read-only view', async () => {
    asRole('member');
    renderTab();
    expect(await screen.findByTestId('integrations-readonly-note'))
      .toHaveTextContent('Only workspace owners and admins can change integrations.');
    const card = await screen.findByTestId('integration-card-anthropic');
    expect(within(card).queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(within(card).queryByRole('button', { name: /test connection/i })).toBeNull();
    expect(within(screen.getByTestId('integration-card-brevo')).queryByRole('button', { name: 'Connect' })).toBeNull();
    const unipile = screen.getByTestId('integration-card-unipile');
    expect(within(unipile).getByTestId('webhook-url')).toBeInTheDocument();
    expect(within(unipile).queryByTestId('reveal-webhook-secret')).toBeNull();
    expect(within(unipile).queryByTestId('register-unipile-webhooks')).toBeNull();
  });
});
