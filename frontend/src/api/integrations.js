import client from './client.js';

/** Per-workspace provider credentials (Multi-tenancy P5).  Every provider
 *  key (Anthropic, Brevo, Hunter, Apollo, Unipile, Adzuna) is stored
 *  encrypted per workspace; secrets are write-only from the UI's view. */

export async function listIntegrations() {
  const { data } = await client.get('/settings/integrations');
  return data;
}

/** Save a provider's fields.  Secret fields sent blank (or omitted) keep the
 *  stored value server-side. */
export async function saveIntegration(provider, values) {
  const { data } = await client.put(`/settings/integrations/${provider}`, values);
  return data;
}

export async function deleteIntegration(provider) {
  await client.delete(`/settings/integrations/${provider}`);
}

export async function testIntegration(provider) {
  const { data } = await client.post(`/settings/integrations/${provider}/test`);
  return data;
}

export async function getWebhookSecret(provider) {
  const { data } = await client.get(`/settings/integrations/${provider}/webhook-secret`);
  return data;
}

export async function rotateWebhookSecret(provider) {
  const { data } = await client.post(`/settings/integrations/${provider}/rotate-webhook-secret`);
  return data;
}

export async function registerUnipileWebhooks() {
  const { data } = await client.post('/settings/integrations/unipile/register-webhooks');
  return data;
}
