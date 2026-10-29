import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import ConnectAgent, { connectRedirect } from './ConnectAgent.jsx';
import { server, api } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

const routes = [<Route key="ca" path="/connect-agent" element={<ConnectAgent />} />];
const REQUEST = {
  client_name: 'Claude', redirect_host: 'claude.ai',
  workspace_name: 'Acme Outreach', user_email: 'ada@example.com',
};

afterEach(() => vi.restoreAllMocks());

describe('ConnectAgent (OAuth consent)', () => {
  it('shows who is connecting and hands the code back on Allow', async () => {
    server.use(http.get(api('/oauth/requests/req1'), () => HttpResponse.json(REQUEST)));
    let body;
    server.use(http.post(api('/oauth/requests/req1/decision'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ redirect_to: 'https://claude.ai/api/mcp/auth_callback?code=c1&state=s' });
    }));
    const go = vi.spyOn(connectRedirect, 'go').mockImplementation(() => {});
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/connect-agent?request=req1' });

    expect(await screen.findByRole('heading', { name: /connect claude/i })).toBeInTheDocument();
    expect(screen.getByTestId('connect-workspace-name')).toHaveTextContent('Acme Outreach');
    expect(screen.getByText('claude.ai')).toBeInTheDocument();
    await user.click(screen.getByTestId('connect-approve'));

    await vi.waitFor(() => expect(go).toHaveBeenCalledWith(
      'https://claude.ai/api/mcp/auth_callback?code=c1&state=s',
    ));
    expect(body).toEqual({ approve: true });
  });

  it('Cancel sends a denial', async () => {
    server.use(http.get(api('/oauth/requests/req1'), () => HttpResponse.json(REQUEST)));
    let body;
    server.use(http.post(api('/oauth/requests/req1/decision'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ redirect_to: 'https://claude.ai/cb?error=access_denied' });
    }));
    const go = vi.spyOn(connectRedirect, 'go').mockImplementation(() => {});
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/connect-agent?request=req1' });

    await user.click(await screen.findByTestId('connect-deny'));
    await vi.waitFor(() => expect(go).toHaveBeenCalledWith('https://claude.ai/cb?error=access_denied'));
    expect(body).toEqual({ approve: false });
  });

  it('explains an expired request', async () => {
    server.use(http.get(api('/oauth/requests/old'), () => HttpResponse.json({ detail: 'expired' }, { status: 404 })));
    renderRoutes(routes, { path: '/connect-agent?request=old' });
    expect(await screen.findByTestId('connect-expired')).toBeInTheDocument();
  });
});
