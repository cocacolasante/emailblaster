import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useToast } from './Toast.jsx';

const DEDUPE_MS = 5000;

/**
 * Global listener for the `integration-missing` window event the API client
 * dispatches on a 409 `integration_not_configured`.  Toasts the server's
 * detail with a link to the Integrations settings tab.  Several queries often
 * fail together on one page, so repeats for the same provider are deduped.
 */
export default function IntegrationMissingListener() {
  const toast = useToast();
  const last = useRef({ provider: null, at: 0 });

  useEffect(() => {
    function onMissing(e) {
      const detail = e?.detail || {};
      const provider = detail.provider || 'unknown';
      const now = Date.now();
      if (last.current.provider === provider && now - last.current.at < DEDUPE_MS) return;
      last.current = { provider, at: now };
      const text = detail.detail
        || `${provider} isn't configured for this workspace.`;
      toast.error(
        <span data-testid="integration-missing-toast">
          {text}{' '}
          <Link to="/settings?tab=integrations" className="underline font-semibold text-white">
            Configure integrations
          </Link>
        </span>,
        8000,
      );
    }
    window.addEventListener('integration-missing', onMissing);
    return () => window.removeEventListener('integration-missing', onMissing);
  }, [toast]);

  return null;
}
