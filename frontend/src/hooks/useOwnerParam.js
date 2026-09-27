import { useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

/** The `?owner=` list filter, persisted in the URL so a "My leads" view is
 *  linkable + survives reloads.  Value: '' (everyone) | 'me' | 'unassigned'
 *  | <user id>.  Setting '' removes the param. */
export function useOwnerParam(key = 'owner') {
  const [searchParams, setSearchParams] = useSearchParams();
  const owner = searchParams.get(key) || '';
  const setOwner = useCallback((next) => {
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      if (next) p.set(key, next);
      else p.delete(key);
      return p;
    }, { replace: true });
  }, [key, setSearchParams]);
  return [owner, setOwner];
}

/** `{owner}` params object for list APIs — empty when unfiltered so the
 *  request (and the query key) stays identical to the pre-P3 one. */
export function ownerParams(owner) {
  return owner ? { owner } : {};
}

export default useOwnerParam;
