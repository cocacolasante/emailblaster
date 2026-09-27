import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import AcceptInvite from './AcceptInvite.jsx';
import { server, api, TEST_ME } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

const routes = [<Route key="ai" path="/accept-invite" element={<AcceptInvite />} />];

function previewReturns(preview) {
  server.use(http.get(api('/auth/invite/tok123'), () => HttpResponse.json(preview)));
}

describe('AcceptInvite page', () => {
  it('new user: creates an account with name + password and lands in the app', async () => {
    previewReturns({ workspace_name: 'Acme', email: 'new@acme.com', role: 'member', user_exists: false });
    let body;
    server.use(http.post(api('/auth/invite/accept'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(TEST_ME);
    }));
    const user = userEvent.setup();
    const { queryClient } = renderRoutes(routes, { path: '/accept-invite?token=tok123' });

    expect(await screen.findByRole('heading', { name: /join acme/i })).toBeInTheDocument();
    expect(screen.getByText(/as a member/i)).toBeInTheDocument();
    await user.type(screen.getByTestId('invite-name'), 'New Person');
    await user.type(screen.getByTestId('invite-password'), 'longenough');
    await user.type(screen.getByTestId('invite-confirm'), 'longenough');
    await user.click(screen.getByTestId('invite-submit'));

    expect(await screen.findByTestId('location')).toHaveTextContent(/^\/$/);
    expect(body).toEqual({ token: 'tok123', password: 'longenough', name: 'New Person' });
    expect(queryClient.getQueryData(['auth-me'])).toEqual(TEST_ME);
  });

  it('new user: validates password confirmation client-side', async () => {
    previewReturns({ workspace_name: 'Acme', email: 'new@acme.com', role: 'member', user_exists: false });
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/accept-invite?token=tok123' });
    await screen.findByTestId('invite-name');
    await user.type(screen.getByTestId('invite-password'), 'longenough');
    await user.type(screen.getByTestId('invite-confirm'), 'different1');
    await user.click(screen.getByTestId('invite-submit'));
    expect(await screen.findByTestId('form-error')).toHaveTextContent(/don't match/i);
  });

  it('existing user: asks for their existing password only', async () => {
    previewReturns({ workspace_name: 'Acme', email: 'ada@example.com', role: 'admin', user_exists: true });
    server.use(http.post(api('/auth/invite/accept'), () => HttpResponse.json({ detail: 'invalid password' }, { status: 401 })));
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/accept-invite?token=tok123' });

    expect(await screen.findByTestId('invite-existing')).toHaveTextContent(/existing password/i);
    expect(screen.getByText(/as an admin/i)).toBeInTheDocument();
    expect(screen.queryByTestId('invite-name')).toBeNull();
    expect(screen.queryByTestId('invite-confirm')).toBeNull();

    await user.type(screen.getByTestId('invite-password'), 'wrongpass');
    await user.click(screen.getByTestId('invite-submit'));
    expect(await screen.findByTestId('form-error')).toHaveTextContent(/incorrect password/i);
  });

  it('shows a friendly error for an invalid token', async () => {
    server.use(http.get(api('/auth/invite/bad'), () => HttpResponse.json({ detail: 'not found' }, { status: 404 })));
    renderRoutes(routes, { path: '/accept-invite?token=bad' });
    expect(await screen.findByTestId('invite-invalid')).toHaveTextContent(/invalid, has expired/i);
  });
});
