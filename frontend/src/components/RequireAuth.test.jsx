import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import RequireAuth from './RequireAuth.jsx';
import { server, api } from '../test/server.js';
import { renderRoutes, LocationProbe } from '../test/utils.jsx';

function routes() {
  return [
    <Route key="login" path="/login" element={<LocationProbe />} />,
    <Route
      key="app"
      path="/campaigns/*"
      element={<RequireAuth><div data-testid="protected">secret</div></RequireAuth>}
    />,
  ];
}

describe('RequireAuth', () => {
  it('renders children once /auth/me resolves', async () => {
    renderRoutes(routes(), { path: '/campaigns/1' });
    expect(screen.getByTestId('auth-loading')).toBeInTheDocument();
    expect(await screen.findByTestId('protected')).toBeInTheDocument();
  });

  it('redirects to /login?next=… when /auth/me is 401', async () => {
    server.use(http.get(api('/auth/me'), () => HttpResponse.json({ detail: 'not authenticated' }, { status: 401 })));
    renderRoutes(routes(), { path: '/campaigns/1?tab=leads' });
    const loc = await screen.findByTestId('location');
    expect(loc).toHaveTextContent('/login?next=%2Fcampaigns%2F1%3Ftab%3Dleads');
    expect(screen.queryByTestId('protected')).toBeNull();
  });
});
