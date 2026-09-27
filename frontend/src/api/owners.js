import client from './client.js';

export const OWNER_RECORD_TYPES = ['lead', 'opportunity', 'activity', 'campaign', 'account', 'contact'];

/** Bulk-(re)assign records to a workspace member.  `ownerId` null
 *  unassigns.  Returns {updated: n}.  422 {detail} when the owner isn't a
 *  member of this workspace. */
export async function assignOwners(recordType, ids, ownerId) {
  const { data } = await client.post('/owners/assign', {
    record_type: recordType,
    ids,
    owner_id: ownerId ?? null,
  });
  return data;
}
