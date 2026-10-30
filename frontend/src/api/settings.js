import client from './client.js';

export async function getApiStatus() {
  const { data } = await client.get('/settings/api-status');
  return data;
}

/** The deliverability breaker for this workspace: the hard-bounce rate
 *  (a fraction) that auto-pauses a campaign, plus its fixed companions. */
export async function getDeliverabilitySettings() {
  const { data } = await client.get('/settings/deliverability');
  return data;
}

/** Owners/admins.  `bounceThreshold` is a fraction (0.06 = 6%); null resets
 *  to the default. */
export async function updateDeliverabilitySettings(bounceThreshold) {
  const { data } = await client.put('/settings/deliverability', { bounce_threshold: bounceThreshold });
  return data;
}
