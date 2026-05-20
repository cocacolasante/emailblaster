import client from './client.js';

// 60s timeout — deep mode can take ~45s.  Default axios timeout (0 = no
// limit) is fine but being explicit prevents pathological hangs.
const ONE_MINUTE = 60_000;

export async function researchClient(payload) {
  const { data } = await client.post('/research-client', payload, {
    timeout: ONE_MINUTE,
  });
  return data;
}
