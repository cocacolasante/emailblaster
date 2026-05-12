import client from './client.js';

export async function getApiStatus() {
  const { data } = await client.get('/settings/api-status');
  return data;
}
