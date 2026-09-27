import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Route } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import Login, { safeNext } from './Login.jsx';
import { server, api, TEST_ME } from '../test/server.js';
import { renderRoutes } from '../test/utils.jsx';

const routes = [<Route key="login" path="/login" element={<Login />} />];

async function fillAndSubmit(user) {
  await user.type(screen.getByTestId('login-email'), 'ada@example.com');
  await user.type(screen.getByTestId('login-password'), 'hunter22');
  await user.click(screen.getByTestId('login-submit'));
}

describe('Login page', () => {
  it('logs in, seeds auth-me and navigates to the next param', async () => {
    let body;
    server.use(http.post(api('/auth/login'), async ({ request }) => {
      body = await request.json();
      return HttpResponse.json(TEST_ME);
    }));
    const user = userEvent.setup();
    const { queryClient } = renderRoutes(routes, { path: '/login?next=%2Fleads%3Fpage%3D2' });

    await fillAndSubmit(user);

    expect(await screen.findByTestId('location')).toHaveTextContent('/leads?page=2');
    expect(body).toEqual({ email: 'ada@example.com', password: 'hunter22' });
    expect(queryClient.getQueryData(['auth-me'])).toEqual(TEST_ME);
  });

  it('navigates to / when there is no next param', async () => {
    server.use(http.post(api('/auth/login'), () => HttpResponse.json(TEST_ME)));
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/login' });
    await fillAndSubmit(user);
    expect(await screen.findByTestId('location')).toHaveTextContent(/^\/$/);
  });

  it('shows an error on invalid credentials and stays put', async () => {
    server.use(http.post(api('/auth/login'), () => HttpResponse.json({ detail: 'invalid email or password' }, { status: 401 })));
    const user = userEvent.setup();
    renderRoutes(routes, { path: '/login' });
    await fillAndSubmit(user);
    expect(await screen.findByTestId('form-error')).toHaveTextContent(/invalid email or password/i);
    expect(screen.queryByTestId('location')).toBeNull();
  });

  it('shows the signup link only when signup is allowed', async () => {
    renderRoutes(routes, { path: '/login' });
    expect(await screen.findByRole('link', { name: /create an account/i })).toHaveAttribute('href', '/signup');
    expect(screen.getByRole('link', { name: /forgot password/i })).toHaveAttribute('href', '/forgot-password');
  });

  it('hides the signup link when signup is disabled', async () => {
    server.use(http.get(api('/auth/config'), () => HttpResponse.json({ allow_signup: false, platform_email: false })));
    const { queryClient } = renderRoutes(routes, { path: '/login' });
    await waitFor(() => expect(queryClient.getQueryData(['auth-config'])).toEqual({ allow_signup: false, platform_email: false }));
    expect(screen.queryByRole('link', { name: /create an account/i })).toBeNull();
  });

  it('rejects open-redirect next values', () => {
    expect(safeNext('//evil.com')).toBe('/');
    expect(safeNext('https://evil.com')).toBe('/');
    expect(safeNext('/campaigns')).toBe('/campaigns');
  });
});
