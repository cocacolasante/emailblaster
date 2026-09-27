import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth.js';
import { INTEGRATIONS_PATH, useIntegrations } from '../hooks/useIntegrations.js';

/** The providers a workspace needs before it can run its first campaign. */
const CORE_PROVIDERS = ['anthropic', 'brevo'];

const dismissKey = (workspaceId) => `eb:setup-nudge-dismissed:${workspaceId || 'default'}`;

function readDismissed(key) {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

/**
 * Onboarding callout on the Campaigns home page: shown to owners/admins
 * while Anthropic and/or Brevo are still unconfigured.  Dismissal is a
 * per-viewer convenience (localStorage, per workspace) — it reappears in a
 * fresh browser, which is fine for a nudge.
 */
export default function WorkspaceSetupNudge() {
  const { isManager, workspace } = useAuth();
  const { isConfigured, labelFor, loaded } = useIntegrations();
  const key = dismissKey(workspace?.id);
  const [dismissed, setDismissed] = useState(() => readDismissed(key));

  const missing = CORE_PROVIDERS.filter((p) => !isConfigured(p));
  if (!isManager || !loaded || dismissed || readDismissed(key) || missing.length === 0) return null;

  function dismiss() {
    try { window.localStorage.setItem(key, '1'); } catch { /* storage blocked — hide for this session only */ }
    setDismissed(true);
  }

  return (
    <div
      role="status"
      data-testid="workspace-setup-nudge"
      className="mb-5 flex items-start gap-3 bg-brand-50 border border-brand-200 text-brand-900 text-sm rounded-card px-4 py-3"
    >
      <div className="flex-1">
        <p className="m-0 font-medium">
          Finish setting up your workspace: connect {missing.map(labelFor).join(' and ')}
        </p>
        <p className="m-0 mt-0.5 text-xs text-brand-800">
          Each workspace uses its own API keys — campaigns can't research, write or send until they're connected.{' '}
          <Link to={INTEGRATIONS_PATH} className="underline font-semibold" data-testid="setup-nudge-link">
            Open Integrations
          </Link>
        </p>
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        data-testid="setup-nudge-dismiss"
        className="text-brand-500 hover:text-brand-700 text-lg leading-none p-1 rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        ×
      </button>
    </div>
  );
}
