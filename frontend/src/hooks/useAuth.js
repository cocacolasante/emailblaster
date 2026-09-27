import { useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import {
  getMe,
  logout as apiLogout,
  switchWorkspace as apiSwitchWorkspace,
} from '../api/auth.js';

/** The single source of truth for "who am I / which workspace".  Every
 *  consumer reads this key; login / accept-invite / switch write it. */
export const AUTH_ME_KEY = ['auth-me'];

export const MANAGER_ROLES = ['owner', 'admin'];

export function isUnauthorized(err) {
  return err?.response?.status === 401;
}

/** Shared query options so RequireAuth + Nav + Settings dedupe onto one
 *  request.  A 401 is a definitive "logged out" — never retried. */
export const authMeQueryOptions = {
  queryKey: AUTH_ME_KEY,
  queryFn: getMe,
  staleTime: 5 * 60 * 1000,
  retry: (count, err) => !isUnauthorized(err) && count < 1,
};

/** Replace the whole cache with a freshly-authenticated session: drops every
 *  cached query from the previous user/workspace, then seeds `auth-me`. */
export function adoptSession(queryClient, me) {
  queryClient.clear();
  if (me) queryClient.setQueryData(AUTH_ME_KEY, me);
}

export function useAuth() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const query = useQuery(authMeQueryOptions);
  const me = query.data || null;
  const role = me?.role || null;

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      /* the cookie may already be gone — log out locally regardless */
    }
    navigate('/login', { replace: true });
    queryClient.clear();
  }, [navigate, queryClient]);

  const switchWorkspace = useCallback(async (tenantId) => {
    const next = await apiSwitchWorkspace(tenantId);
    // Every cached list/detail belongs to the old workspace — wipe it all,
    // then re-read `me` from the server for the new active workspace.
    adoptSession(queryClient, next);
    await queryClient.invalidateQueries({ queryKey: AUTH_ME_KEY });
    navigate('/', { replace: true });
    return next;
  }, [navigate, queryClient]);

  return {
    me,
    user: me?.user || null,
    workspace: me?.workspace || null,
    role,
    isManager: MANAGER_ROLES.includes(role),
    memberships: me?.memberships || [],
    isLoading: query.isLoading,
    error: query.error,
    refetch: query.refetch,
    logout,
    switchWorkspace,
  };
}
