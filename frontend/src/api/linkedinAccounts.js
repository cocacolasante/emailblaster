import client from './client.js';

export async function listLinkedInAccounts() {
  const { data } = await client.get('/linkedin-accounts/');
  return data;
}

export async function getLinkedInAccount(id) {
  const { data } = await client.get(`/linkedin-accounts/${id}`);
  return data;
}

export async function createLinkedInAccount(payload) {
  const { data } = await client.post('/linkedin-accounts/', payload);
  return data;
}

export async function updateLinkedInAccount(id, payload) {
  const { data } = await client.patch(`/linkedin-accounts/${id}`, payload);
  return data;
}

export async function deleteLinkedInAccount(id) {
  await client.delete(`/linkedin-accounts/${id}`);
}

export async function testLinkedInAccount(id) {
  const { data } = await client.post(`/linkedin-accounts/${id}/test`);
  return data;
}

export async function resolveLinkedInChallenge(id) {
  const { data } = await client.post(`/linkedin-accounts/${id}/resolve-challenge`, {});
  return data;
}
