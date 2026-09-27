import { useQuery } from '@tanstack/react-query';
import { listMembers } from '../api/team.js';

export const TEAM_MEMBERS_KEY = ['team-members'];

/** Members of the active workspace.  Shared by the Workspace settings tab
 *  and (later) record-owner pickers — one cached request for all of them. */
export function useMembers(options = {}) {
  const query = useQuery({
    queryKey: TEAM_MEMBERS_KEY,
    queryFn: listMembers,
    staleTime: 60 * 1000,
    ...options,
  });
  return { ...query, members: query.data || [] };
}

export default useMembers;
