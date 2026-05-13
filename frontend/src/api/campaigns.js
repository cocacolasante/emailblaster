import client from './client.js';

export async function listCampaigns() {
  const { data } = await client.get('/campaigns/');
  return data;
}

export async function getCampaign(id) {
  const { data } = await client.get(`/campaigns/${id}`);
  return data;
}

export async function createCampaign(payload) {
  const { data } = await client.post('/campaigns/', payload);
  return data;
}

export async function updateCampaign(id, payload) {
  const { data } = await client.patch(`/campaigns/${id}`, payload);
  return data;
}

export async function deleteCampaign(id) {
  await client.delete(`/campaigns/${id}`);
}

export async function pauseCampaign(id) {
  const { data } = await client.post(`/campaigns/${id}/pause`);
  return data;
}

export async function resumeCampaign(id) {
  const { data } = await client.post(`/campaigns/${id}/resume`);
  return data;
}

export async function listCampaignLeads(id, params = {}) {
  const { data } = await client.get(`/campaigns/${id}/leads`, { params });
  return data;
}

// ---------- Preview ----------

export async function getPreview(id) {
  const { data } = await client.get(`/campaigns/${id}/preview`);
  return data;
}

export async function getPreviewProgress(id) {
  const { data } = await client.get(`/campaigns/${id}/preview/progress`);
  return data;
}

export async function updateSample(campaignId, leadId, payload) {
  const { data } = await client.patch(
    `/campaigns/${campaignId}/preview/samples/${leadId}`,
    payload,
  );
  return data;
}

export async function approveAll(campaignId) {
  const { data } = await client.post(`/campaigns/${campaignId}/preview/approve-all`);
  return data;
}

export async function rejectPreview(campaignId) {
  const { data } = await client.post(`/campaigns/${campaignId}/preview/reject`);
  return data;
}

// ---------- Lead upload ----------

export async function uploadLeadsPreview(campaignId, file) {
  const fd = new FormData();
  fd.append('file', file);
  const { data } = await client.post(
    `/campaigns/${campaignId}/upload`,
    fd,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return data;
}

export async function confirmLeadsUpload(campaignId, file, mapping) {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('mapping', JSON.stringify(mapping));
  const { data } = await client.post(
    `/campaigns/${campaignId}/leads/confirm-upload`,
    fd,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  );
  return data;
}

// ---------- Lead detail ----------

export async function getLeadDetail(campaignId, leadId) {
  const { data } = await client.get(`/campaigns/${campaignId}/leads/${leadId}`);
  return data;
}

// ---------- Activity ----------

export async function getCampaignActivity(id) {
  const { data } = await client.get(`/campaigns/${id}/activity`);
  return data;
}

// ---------- Analytics ----------

export async function getAnalytics(id) {
  const { data } = await client.get(`/campaigns/${id}/analytics`);
  return data;
}

// ---------- Errors / retry ----------

export async function listCampaignErrors(id) {
  const { data } = await client.get(`/campaigns/${id}/errors`);
  return data;
}

export async function retryFailedLeads(id) {
  const { data } = await client.post(`/campaigns/${id}/retry-failed`);
  return data;
}
