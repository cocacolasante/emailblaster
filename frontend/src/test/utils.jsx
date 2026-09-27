import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '../components/Toast.jsx';

/** Renders the current location so tests can assert on redirects. */
export function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="location">{`${loc.pathname}${loc.search}`}</div>;
}

export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

/**
 * Render `routes` (an array of <Route>) inside Router + QueryClient + Toast
 * providers at `path`.  A catch-all route renders <LocationProbe/> so a
 * navigation to an unregistered path is observable.
 */
export function renderRoutes(routes, { path = '/', queryClient = makeQueryClient() } = {}) {
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <ToastProvider defaultDuration={0}>
          <Routes>
            {routes}
            <Route path="*" element={<LocationProbe />} />
          </Routes>
        </ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, queryClient };
}
