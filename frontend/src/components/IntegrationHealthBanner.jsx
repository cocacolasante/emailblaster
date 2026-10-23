import { Link } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import { INTEGRATIONS_PATH, useIntegrations } from '../hooks/useIntegrations.js';

/**
 * App-wide alert for a connected integration whose key stopped working —
 * set by a manual Test or by the background workers when a provider rejects
 * the workspace's key (e.g. Brevo's "unrecognised IP address" lock).  Not
 * dismissible: it clears itself once the key works again.
 */
export default function IntegrationHealthBanner() {
  const { isManager } = useAuth();
  const { integrations, loaded } = useIntegrations({ refetchInterval: 5 * 60 * 1000 });
  if (!loaded) return null;
  const broken = integrations.filter((i) => i.configured && i.last_test_status === 'failed');
  if (!broken.length) return null;

  return (
    <div className="px-6 pt-4 pr-20" data-testid="integration-health-banner">
      {broken.map((i) => (
        <div
          key={i.provider}
          role="alert"
          data-testid={`integration-health-${i.provider}`}
          className="mb-2 bg-red-50 border border-red-200 text-red-900 text-sm rounded-card px-4 py-3"
        >
          <p className="m-0 font-medium">{i.label} isn't accepting this workspace's API key</p>
          {i.last_test_error ? (
            <p className="m-0 mt-0.5 text-xs text-red-800 break-words">{i.last_test_error}</p>
          ) : null}
          <p className="m-0 mt-1 text-xs text-red-800">
            {i.provider === 'brevo'
              ? 'Emails are on hold and opens/clicks aren\'t being recorded; sending resumes on its own once the key works. '
              : 'Anything that uses it is paused until the key works again. '}
            {isManager ? (
              <Link to={INTEGRATIONS_PATH} className="underline font-semibold">Open Integrations</Link>
            ) : 'Ask a workspace admin to fix it in Settings → Integrations.'}
          </p>
        </div>
      ))}
    </div>
  );
}
