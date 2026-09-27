import client from './client.js';

/** Workspace API keys for agent access (Muse over MCP).  Keys are hashed
 *  server-side: the plaintext `token` is returned ONLY by createApiKey. */

export async function getApiKeyConnection() {
  const { data } = await client.get('/api-keys/connection');
  return data;
}

export async function listApiKeys() {
  const { data } = await client.get('/api-keys');
  return data;
}

export async function createApiKey(name) {
  const { data } = await client.post('/api-keys', { name });
  return data;
}

export async function revokeApiKey(id) {
  const { data } = await client.post(`/api-keys/${id}/revoke`);
  return data;
}
