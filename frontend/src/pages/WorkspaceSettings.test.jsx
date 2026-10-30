import { describe, it, expect, vi } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import Settings from './Settings.jsx';
import { server, api, TEST_ME, TEST_DELIVERABILITY } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

const MEMBERS = [
  { user_id: 'user-1', email: 'ada@example.com', name: 'Ada Lovelace', display_name: 'Ada Lovelace', role: 'owner', joined_at: '2026-09-01T00:00:00Z' },
  { user_id: 'user-2', email: 'bob@example.com', name: 'Bob', display_name: 'Bob', role: 'member', joined_at: '2026-09-02T00:00:00Z' },
];

function teamHandlers({ invites = [] } = {}) {
  server.use(
    http.get(api('/team/members'), () => HttpResponse.json(MEMBERS)),
    http.get(api('/team/invites'), () => HttpResponse.json(invites)),
  );
}

const routes = [<Route key="s" path="/settings" element={<Settings />} />];

describe('Settings — Workspace tab', () => {
  it('is selectable via ?tab=workspace and lists members', async () => {
    teamHandlers();
    renderRoutes(routes, { path: '/settings?tab=workspace' });
    expect(screen.getByRole('tab', { name: 'Workspace' })).toHaveAttribute('aria-selected', 'true');
    const row = await screen.findByTestId('member-row-user-2');
    expect(within(row).getByText('bob@example.com')).toBeInTheDocument();
    // Self row offers "Leave workspace"; other rows offer Remove (owner view).
    expect(screen.getByTestId('leave-workspace')).toBeInTheDocument();
    expect(screen.getByTestId('remove-member-user-2')).toBeInTheDocument();
    expect(await screen.findByTestId('invites-empty')).toBeInTheDocument();
  });

  it('shows a copyable invite link when the invite was not emailed', async () => {
    teamHandlers();
    let body;
    server.use(http.post(api('/team/invites'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({
        id: 'inv-1', email: 'carol@example.com', role: 'admin',
        expires_at: '2026-10-04T00:00:00Z', created_at: '2026-09-27T00:00:00Z',
        invite_url: 'http://localhost:5173/accept-invite?token=abc', emailed: false,
      }, { status: 201 });
    }));
    // userEvent.setup() installs a clipboard stub on navigator.
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/settings?tab=workspace' });
    await user.type(await screen.findByTestId('invite-email'), 'carol@example.com');
    await user.selectOptions(screen.getByTestId('invite-role'), 'admin');
    await user.click(screen.getByTestId('invite-submit'));

    const share = await screen.findByTestId('invite-share');
    expect(share).toHaveTextContent(/email isn't configured — share this link/i);
    expect(screen.getByTestId('invite-link')).toHaveValue('http://localhost:5173/accept-invite?token=abc');
    expect(body).toEqual({ email: 'carol@example.com', role: 'admin' });

    await user.click(screen.getByTestId('invite-link-copy'));
    expect(await screen.findByText('Copied')).toBeInTheDocument();
    await expect(navigator.clipboard.readText()).resolves.toBe('http://localhost:5173/accept-invite?token=abc');
  });

  it('shows server detail inline when a role change is rejected', async () => {
    teamHandlers();
    server.use(http.patch(api('/team/members/user-2'), () => HttpResponse.json({ detail: 'cannot demote the last owner' }, { status: 409 })));
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/settings?tab=workspace' });
    await user.selectOptions(await screen.findByTestId('member-role-user-2'), 'admin');
    expect(await screen.findByTestId('member-error-user-2')).toHaveTextContent('cannot demote the last owner');
  });

  it('hides the invite form from plain members', async () => {
    teamHandlers();
    server.use(http.get(api('/auth/me'), () => HttpResponse.json({ ...TEST_ME, role: 'member' })));
    renderRoutes(routes, { path: '/settings?tab=workspace' });
    await screen.findByTestId('member-row-user-2');
    expect(screen.queryByTestId('invite-card')).toBeNull();
    expect(screen.queryByTestId('member-role-user-2')).toBeNull();
    expect(screen.getByTestId('workspace-name-readonly')).toHaveTextContent('Acme Outreach');
  });
});

describe('Settings — Profile tab', () => {
  it('toggles the daily digest via PATCH /auth/me', async () => {
    let body;
    server.use(http.patch(api('/auth/me'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ ...TEST_ME, digest_enabled: true });
    }));
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/settings?tab=profile' });
    const toggle = await screen.findByTestId('profile-toggle-digest_enabled');
    await vi.waitFor(() => expect(screen.getByTestId('profile-name')).toHaveValue('Ada Lovelace'));
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    await user.click(toggle);
    await vi.waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
    expect(body).toEqual({ digest_enabled: true });
  });
});

describe('Settings — Workspace tab — bounce-rate auto-pause', () => {
  it('lets an owner raise the threshold and reset it', async () => {
    teamHandlers();
    const bodies = [];
    server.use(http.put(api('/settings/deliverability'), async ({ request }) => {
      const body = await request.json();
      bodies.push(body);
      const t = body.bounce_threshold;
      return HttpResponse.json({
        ...TEST_DELIVERABILITY, bounce_threshold: t ?? 0.05, custom: t !== null,
      });
    }));
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/settings?tab=workspace' });

    const input = await screen.findByTestId('bounce-threshold-input');
    expect(input).toHaveValue(5);
    expect(screen.getByTestId('bounce-threshold-save')).toBeDisabled();
    await user.clear(input);
    await user.type(input, '10');
    await user.click(screen.getByTestId('bounce-threshold-save'));
    await vi.waitFor(() => expect(bodies).toEqual([{ bounce_threshold: 0.1 }]));

    await user.click(await screen.findByTestId('bounce-threshold-reset'));
    await vi.waitFor(() => expect(bodies[1]).toEqual({ bounce_threshold: null }));
    await vi.waitFor(() => expect(screen.getByTestId('bounce-threshold-input')).toHaveValue(5));
  });

  it('rejects values outside 1–30%', async () => {
    teamHandlers();
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/settings?tab=workspace' });
    const input = await screen.findByTestId('bounce-threshold-input');
    await user.clear(input);
    await user.type(input, '45');
    expect(screen.getByTestId('bounce-threshold-save')).toBeDisabled();
  });

  it('is read-only for members', async () => {
    teamHandlers();
    server.use(http.get(api('/auth/me'), () => HttpResponse.json({ ...TEST_ME, role: 'member' })));
    renderRoutes(routes, { path: '/settings?tab=workspace' });
    expect(await screen.findByTestId('bounce-threshold-readonly')).toHaveTextContent('5% hard-bounce rate (default)');
    expect(screen.queryByTestId('bounce-threshold-input')).toBeNull();
  });
});
