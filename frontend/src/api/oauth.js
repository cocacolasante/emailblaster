import client from './client.js';

/** OAuth consent for MCP clients (Claude, ChatGPT).  The backend parks the
 *  client's authorize request and sends the browser to /connect-agent. */

export async function getConnectRequest(requestId) {
  const { data } = await client.get(`/oauth/requests/${encodeURIComponent(requestId)}`);
  return data;
}

/** Returns `{ redirect_to }` — where to send the browser (back to the app). */
export async function decideConnectRequest(requestId, approve) {
  const { data } = await client.post(
    `/oauth/requests/${encodeURIComponent(requestId)}/decision`, { approve },
  );
  return data;
}
