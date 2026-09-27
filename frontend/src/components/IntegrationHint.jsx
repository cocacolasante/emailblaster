import { Link, useInRouterContext } from 'react-router-dom';
import { INTEGRATIONS_PATH, useIntegrations } from '../hooks/useIntegrations.js';

/** Link to Settings → Integrations; falls back to a plain anchor when the
 *  component renders outside a Router (a few page tests mount bare). */
export function IntegrationsLink({ children, className = '' }) {
  const inRouter = useInRouterContext();
  const cls = `underline font-medium ${className}`;
  if (!inRouter) return <a href={INTEGRATIONS_PATH} className={cls}>{children}</a>;
  return <Link to={INTEGRATIONS_PATH} className={cls}>{children}</Link>;
}

function missingProviders(providers, isConfigured) {
  const list = Array.isArray(providers) ? providers : [providers];
  return list.filter((p) => !isConfigured(p));
}

function joinLabels(labels) {
  if (labels.length <= 1) return labels.join('');
  return `${labels.slice(0, -1).join(', ')} and ${labels[labels.length - 1]}`;
}

/**
 * Inline "Connect <Provider> in Settings → Integrations" note shown next to a
 * gated button.  Renders nothing when every listed provider is configured.
 * `providers` is a provider key or an array of keys; optional `children`
 * are appended after the link (e.g. " to enable X.").
 */
export function IntegrationHint({ providers, className = '', testId, children }) {
  const { isConfigured, labelFor } = useIntegrations();
  const missing = missingProviders(providers, isConfigured);
  if (!missing.length) return null;
  const tid = testId || `integration-hint-${missing.join('-')}`;
  return (
    <p data-testid={tid} className={`text-xs text-amber-700 m-0 ${className}`}>
      Connect {joinLabels(missing.map(labelFor))} in{' '}
      <IntegrationsLink>Settings → Integrations</IntegrationsLink>
      {children}
    </p>
  );
}

/**
 * Page-level warning banner for a missing provider.  `children` (optional)
 * explains what won't work; the fix-it link is appended.
 */
export function IntegrationBanner({ providers, children, className = '', testId }) {
  const { isConfigured, labelFor } = useIntegrations();
  const missing = missingProviders(providers, isConfigured);
  if (!missing.length) return null;
  const tid = testId || `integration-banner-${missing.join('-')}`;
  return (
    <div
      role="status"
      data-testid={tid}
      className={`bg-amber-50 border border-amber-200 text-amber-800 text-sm rounded-lg px-4 py-3 ${className}`}
    >
      {children ? <>{children}{' '}</> : null}
      Connect {joinLabels(missing.map(labelFor))} in{' '}
      <IntegrationsLink>Settings → Integrations</IntegrationsLink>.
    </div>
  );
}

export default IntegrationHint;
