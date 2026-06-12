import client from './client.js';

// ---------- Leads (manual creation + CRM status) ----------

export async function createCrmLead(payload) {
  const { data } = await client.post('/crm/leads', payload);
  return data;
}

export async function updateLeadCrmStatus(leadId, crmStatus) {
  const { data } = await client.patch(`/crm/leads/${leadId}`, { crm_status: crmStatus });
  return data;
}

/** Convert a lead → opportunity. Carries the contact snapshot; flips
 *  the lead to crm_status=converted. 409 when already converted. */
export async function convertLead(leadId, payload = {}) {
  const { data } = await client.post(`/crm/leads/${leadId}/convert`, payload);
  return data;
}

// ---------- Opportunities ----------

export async function listOpportunities(params = {}) {
  const { data } = await client.get('/crm/opportunities', { params });
  return data;
}

export async function getOpportunity(id) {
  const { data } = await client.get(`/crm/opportunities/${id}`);
  return data;
}

export async function createOpportunity(payload) {
  const { data } = await client.post('/crm/opportunities', payload);
  return data;
}

export async function updateOpportunity(id, payload) {
  const { data } = await client.patch(`/crm/opportunities/${id}`, payload);
  return data;
}

export async function deleteOpportunity(id) {
  await client.delete(`/crm/opportunities/${id}`);
}

/** Per-stage roll-up {stage, count, total_amount} for the Kanban header. */
export async function getPipelineSummary() {
  const { data } = await client.get('/crm/opportunities/pipeline');
  return data;
}

// ---------- Activities ----------

export async function listActivities(params = {}) {
  const { data } = await client.get('/crm/activities', { params });
  return data;
}

export async function createActivity(payload) {
  const { data } = await client.post('/crm/activities', payload);
  return data;
}

export async function updateActivity(id, payload) {
  const { data } = await client.patch(`/crm/activities/${id}`, payload);
  return data;
}

export async function deleteActivity(id) {
  await client.delete(`/crm/activities/${id}`);
}
