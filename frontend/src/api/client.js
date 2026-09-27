import axios from 'axios';

const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const client = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
  // The API authenticates via the httpOnly `eb_session` cookie.  Frontend
  // (:5173) and API (:8000) are same-site, so the cookie rides along as long
  // as we opt in to credentials on every request.
  withCredentials: true,
});

/** Routes that render without a session (and without the app shell).  The
 *  401 interceptor never bounces the user off one of these. */
export const PUBLIC_ROUTES = [
  '/login',
  '/signup',
  '/forgot-password',
  '/reset-password',
  '/accept-invite',
];

export function isPublicPath(pathname = '') {
  return PUBLIC_ROUTES.some((r) => pathname === r || pathname.startsWith(`${r}/`));
}

/** Build `/login?next=<path+search>` for the given location. */
export function loginUrlFor(pathname = '/', search = '') {
  const next = `${pathname || '/'}${search || ''}`;
  if (!next || next === '/') return '/login';
  return `/login?next=${encodeURIComponent(next)}`;
}

/** Indirection so tests can observe the hard redirect (jsdom doesn't
 *  implement navigation). */
export const authRedirect = {
  go(url) {
    window.location.assign(url);
  },
};

function isAuthUrl(url) {
  if (!url) return false;
  try {
    return new URL(url, baseURL).pathname.startsWith('/auth/');
  } catch {
    return String(url).startsWith('/auth/');
  }
}

/** Response-error interceptor (exported for tests).
 *  - 401 on a feature endpoint → hard redirect to /login?next=…  (the
 *    /auth/* endpoints are left to their callers: a failed login or the
 *    RequireAuth probe of /auth/me handles its own 401).
 *  - 409 `integration_not_configured` → window `integration-missing` event,
 *    picked up by <IntegrationMissingListener /> to toast a fix-it link.
 *  The error is always re-thrown so callers keep their existing handling. */
export function handleResponseError(error) {
  const status = error?.response?.status;
  const data = error?.response?.data;

  if (status === 401 && !isAuthUrl(error?.config?.url)) {
    const { pathname, search } = window.location;
    if (!isPublicPath(pathname)) {
      authRedirect.go(loginUrlFor(pathname, search));
    }
  } else if (status === 409 && data?.error === 'integration_not_configured') {
    try {
      window.dispatchEvent(new CustomEvent('integration-missing', { detail: data }));
    } catch {
      /* non-fatal */
    }
  }
  return Promise.reject(error);
}

client.interceptors.response.use((response) => response, handleResponseError);

export default client;
