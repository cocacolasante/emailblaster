import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';

import AgentAccessTab, { formatLastUsed } from './AgentAccessSettings.jsx';
import { api, server } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

const TOKEN = 'eb_live_abcd1234_SECRETSECRETSECRET';

function makeKey(overrides = {}) {
  return {
    id: 'key-1',
    name: 'Muse on my phone',
    prefix: 'eb_live_abcd1234',
    created_by: 'user-1',
    created_by_name: 'Ada Lovelace',
    created_at: '2026-09-01T10:00:00Z',
    last_used_at: null,
    revoked_at: null,
    ...overrides,
  };
}

let keys;
beforeEach(() => {
  keys = [];
  server.use(http.get(api('/api-keys'), () => HttpResponse.json(keys)));
});

function renderTab() {
  return renderRoutes([<Route key="s" path="/settings" element={<AgentAccessTab />} />], { path: '/settings' });
}

describe('formatLastUsed', () => {
  it('handles never / minutes / hours / days', () => {
    const now = new Date('2026-09-27T12:00:00Z').getTime();
    expect(formatLastUsed(null, now)).toBe('Never');
    expect(formatLastUsed('2026-09-27T11:59:30Z', now)).toBe('just now');
    expect(formatLastUsed('2026-09-27T11:55:00Z', now)).toBe('5m ago');
    expect(formatLastUsed('2026-09-27T09:00:00Z', now)).toBe('3h ago');
    expect(formatLastUsed('2026-09-25T12:00:00Z', now)).toBe('2d ago');
  });
});

describe('Agent access tab', () => {
  it('renders the MCP endpoint with a working copy button and capability summary', async () => {
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByTestId('mcp-url')).toHaveTextContent('https://outreach.example.com/mcp');
    expect(screen.getByText(/add a custom connector/i)).toBeInTheDocument();
    expect(screen.queryByTestId('mcp-not-public')).toBeNull();
    expect(screen.getByText(/cannot send anything without your explicit approval/i)).toBeInTheDocument();
    expect(screen.getByText(/a key acts as you/i)).toBeInTheDocument();

    await user.click(screen.getByTestId('copy-mcp-url'));
    await expect(navigator.clipboard.readText()).resolves.toBe('https://outreach.example.com/mcp');
    expect(await screen.findByText('Copied to clipboard')).toBeInTheDocument();
  });

  it('warns when the endpoint is not publicly reachable', async () => {
    server.use(http.get(api('/api-keys/connection'), () => HttpResponse.json({
      mcp_url: 'http://localhost:8000/mcp', public: false,
    })));
    renderTab();
    const warn = await screen.findByTestId('mcp-not-public');
    expect(warn).toHaveTextContent(/can't reach localhost/i);
    expect(warn).toHaveTextContent('PUBLIC_ORIGIN');
  });

  it('shows the empty state when there are no keys', async () => {
    renderTab();
    expect(await screen.findByTestId('api-keys-table-empty')).toBeInTheDocument();
  });

  it('creates a key, shows the token once, and never lists it', async () => {
    const user = userEvent.setup();
    let posted;
    server.use(http.post(api('/api-keys'), async ({ request }) => {
      posted = await request.json();
      const row = makeKey({ name: posted.name });
      keys = [row];
      return HttpResponse.json({ ...row, token: TOKEN }, { status: 201 });
    }));
    renderTab();
    await screen.findByTestId('api-keys-table-empty');

    await user.type(screen.getByTestId('api-key-name'), 'Muse on my phone');
    await user.click(screen.getByTestId('create-api-key'));

    const banner = await screen.findByTestId('new-api-key');
    expect(posted).toEqual({ name: 'Muse on my phone' });
    expect(banner).toHaveTextContent(/copy this key now — it won't be shown again/i);
    expect(within(banner).getByTestId('new-api-key-token')).toHaveTextContent(TOKEN);
    expect(screen.getByTestId('api-key-name')).toHaveValue('');

    // The list refetches with the new row — prefix only, no token.
    const table = await screen.findByTestId('api-keys-table');
    await waitFor(() => expect(within(table).getAllByTestId('api-keys-table-row')).toHaveLength(1));
    expect(within(table).getByTestId('api-key-prefix')).toHaveTextContent('eb_live_abcd1234…');
    expect(within(table).queryByText(new RegExp(TOKEN))).toBeNull();
    expect(within(table).getByText('Never')).toBeInTheDocument();
    expect(within(table).getByTestId('api-key-status')).toHaveTextContent('Active');

    await user.click(within(banner).getByTestId('copy-new-api-key'));
    await expect(navigator.clipboard.readText()).resolves.toBe(TOKEN);

    // Dismissed = gone for good.
    await user.click(screen.getByTestId('dismiss-new-api-key'));
    expect(screen.queryByTestId('new-api-key')).toBeNull();
    expect(screen.queryByText(new RegExp(TOKEN))).toBeNull();
  });

  it('requires a name before creating', async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByTestId('api-keys-table-empty');
    await user.click(screen.getByTestId('create-api-key'));
    expect(await screen.findByText('Give the key a name')).toBeInTheDocument();
  });

  it('surfaces a server detail when create fails', async () => {
    const user = userEvent.setup();
    server.use(http.post(api('/api-keys'), () => HttpResponse.json(
      { detail: 'Key limit reached' }, { status: 400 },
    )));
    renderTab();
    await user.type(await screen.findByTestId('api-key-name'), 'x');
    await user.click(screen.getByTestId('create-api-key'));
    expect(await screen.findByTestId('create-api-key-error')).toHaveTextContent('Key limit reached');
  });

  it('revokes a key after confirming in the modal', async () => {
    const user = userEvent.setup();
    keys = [
      makeKey({ last_used_at: new Date(Date.now() - 2 * 3600000).toISOString() }),
      makeKey({ id: 'key-2', name: 'Old key', revoked_at: '2026-09-02T00:00:00Z' }),
    ];
    let revokedId;
    server.use(http.post(api('/api-keys/:id/revoke'), ({ params }) => {
      revokedId = params.id;
      keys = keys.map((k) => (k.id === params.id ? { ...k, revoked_at: '2026-09-27T00:00:00Z' } : k));
      return HttpResponse.json(keys.find((k) => k.id === params.id));
    }));
    renderTab();

    await screen.findByText('2h ago');
    const statuses = screen.getAllByTestId('api-key-status').map((b) => b.textContent);
    expect(statuses).toEqual(['Active', 'Revoked']);
    // Revoked keys have no revoke button.
    expect(screen.queryByTestId('revoke-api-key-key-2')).toBeNull();

    await user.click(screen.getByTestId('revoke-api-key-key-1'));
    const modal = await screen.findByTestId('revoke-api-key-modal');
    expect(modal).toHaveTextContent(/stops working immediately/i);
    await user.click(within(modal).getByTestId('confirm-revoke-api-key'));

    await waitFor(() => expect(screen.queryByTestId('revoke-api-key-key-1')).toBeNull());
    expect(revokedId).toBe('key-1');
    expect(screen.getAllByTestId('api-key-status').map((b) => b.textContent)).toEqual(['Revoked', 'Revoked']);
    expect(await screen.findByText('Key "Muse on my phone" revoked')).toBeInTheDocument();
  });

  it('shows the server detail when revoke is forbidden', async () => {
    const user = userEvent.setup();
    keys = [makeKey({ created_by: 'user-2', created_by_name: 'Grace Hopper' })];
    server.use(http.post(api('/api-keys/:id/revoke'), () => HttpResponse.json(
      { detail: 'Only the key creator or a workspace admin can revoke it' }, { status: 403 },
    )));
    renderTab();
    expect(await screen.findByText('Grace Hopper')).toBeInTheDocument();
    await user.click(screen.getByTestId('revoke-api-key-key-1'));
    const modal = await screen.findByTestId('revoke-api-key-modal');
    await user.click(within(modal).getByTestId('confirm-revoke-api-key'));
    expect(await within(modal).findByTestId('revoke-api-key-error'))
      .toHaveTextContent('Only the key creator or a workspace admin can revoke it');
  });
});
