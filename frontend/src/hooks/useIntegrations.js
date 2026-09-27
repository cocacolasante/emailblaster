import { useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { listIntegrations } from '../api/integrations.js';

export const INTEGRATIONS_KEY = ['integrations'];

/** Deep link to the Integrations settings tab. */
export const INTEGRATIONS_PATH = '/settings?tab=integrations';

/** Display names used before the list has loaded (and in gating hints). */
export const PROVIDER_LABELS = {
  anthropic: 'Anthropic',
  brevo: 'Brevo',
  hunter: 'Hunter',
  apollo: 'Apollo',
  unipile: 'Unipile',
  adzuna: 'Adzuna',
};

/**
 * The workspace's provider integrations.  `isConfigured(provider)` is
 * deliberately optimistic: it returns false ONLY when the server has said
 * the provider exists and is not configured.  While loading, on error, or
 * for an unknown provider it returns true — gated buttons never flash
 * disabled, and the backend's 409 `integration_not_configured` (toasted by
 * IntegrationMissingListener) remains the hard guard.
 */
export function useIntegrations(options = {}) {
  const query = useQuery({
    queryKey: INTEGRATIONS_KEY,
    queryFn: listIntegrations,
    staleTime: 60 * 1000,
    ...options,
  });
  const integrations = useMemo(
    () => (Array.isArray(query.data) ? query.data : []),
    [query.data],
  );

  const byProvider = useMemo(() => {
    const map = {};
    for (const i of integrations) map[i.provider] = i;
    return map;
  }, [integrations]);

  const isConfigured = useCallback(
    (provider) => {
      const row = byProvider[provider];
      return row ? !!row.configured : true;
    },
    [byProvider],
  );

  const labelFor = useCallback(
    (provider) => byProvider[provider]?.label || PROVIDER_LABELS[provider] || provider,
    [byProvider],
  );

  return { ...query, integrations, isConfigured, labelFor, loaded: query.isSuccess };
}

export default useIntegrations;
