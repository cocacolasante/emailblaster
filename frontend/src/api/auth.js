import client from './client.js';

// ── Public (no session required) ──────────────────────────────────────────

export async function getAuthConfig() {
  const { data } = await client.get('/auth/config');
  return data;
}

export async function register(payload) {
  const { data } = await client.post('/auth/register', payload);
  return data;
}

export async function login(payload) {
  const { data } = await client.post('/auth/login', payload);
  return data;
}

export async function logout() {
  const { data } = await client.post('/auth/logout');
  return data;
}

export async function forgotPassword(email) {
  const { data } = await client.post('/auth/forgot', { email });
  return data;
}

export async function resetPassword(token, password) {
  const { data } = await client.post('/auth/reset', { token, password });
  return data;
}

export async function getInvite(token) {
  const { data } = await client.get(`/auth/invite/${encodeURIComponent(token)}`);
  return data;
}

export async function acceptInvite(payload) {
  const { data } = await client.post('/auth/invite/accept', payload);
  return data;
}

// ── Authenticated ─────────────────────────────────────────────────────────

export async function getMe() {
  const { data } = await client.get('/auth/me');
  return data;
}

export async function updateMe(payload) {
  const { data } = await client.patch('/auth/me', payload);
  return data;
}

export async function switchWorkspace(tenantId) {
  const { data } = await client.post('/auth/switch-workspace', { tenant_id: tenantId });
  return data;
}

/** Pull a human-readable message out of an axios error (FastAPI `detail`
 *  may be a string or a validation-error list). */
export function errorDetail(err, fallback = 'Something went wrong') {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => d?.msg || String(d)).join('; ');
  }
  return err?.message || fallback;
}
