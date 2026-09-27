import client from './client.js';

export async function listMembers() {
  const { data } = await client.get('/team/members');
  return data;
}

export async function updateMemberRole(userId, role) {
  const { data } = await client.patch(`/team/members/${userId}`, { role });
  return data;
}

/** Remove a member (or yourself = leave the workspace).  `reassignTo` hands
 *  the removed user's owned records to another member. */
export async function removeMember(userId, reassignTo) {
  const params = reassignTo ? { reassign_to: reassignTo } : undefined;
  await client.delete(`/team/members/${userId}`, { params });
}

export async function listInvites() {
  const { data } = await client.get('/team/invites');
  return data;
}

export async function createInvite({ email, role }) {
  const { data } = await client.post('/team/invites', { email, role });
  return data;
}

export async function revokeInvite(id) {
  await client.delete(`/team/invites/${id}`);
}

export async function updateWorkspace({ name }) {
  const { data } = await client.patch('/team/workspace', { name });
  return data;
}
