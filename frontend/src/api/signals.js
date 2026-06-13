import client from './client.js';

// ---------- Watches ----------

export async function listWatches() {
  const { data } = await client.get('/signals/watches');
  return data;
}

export async function createWatch(payload) {
  const { data } = await client.post('/signals/watches', payload);
  return data;
}

export async function updateWatch(id, payload) {
  const { data } = await client.patch(`/signals/watches/${id}`, payload);
  return data;
}

export async function deleteWatch(id) {
  await client.delete(`/signals/watches/${id}`);
}

export async function runWatchNow(id) {
  const { data } = await client.post(`/signals/watches/${id}/run-now`);
  return data;
}

// ---------- Signal feed ----------

export async function listSignals(params = {}) {
  const { data } = await client.get('/signals', { params });
  return data;
}

export async function actionSignal(id) {
  const { data } = await client.post(`/signals/${id}/action`);
  return data;
}

export async function dismissSignal(id) {
  const { data } = await client.post(`/signals/${id}/dismiss`);
  return data;
}

/** One watch per company (funding/hiring) — paste a list, get
 *  {created, skipped_duplicate, watch_ids}. */
export async function createWatchesBulk(payload) {
  const { data } = await client.post('/signals/watches/bulk', payload);
  return data;
}
