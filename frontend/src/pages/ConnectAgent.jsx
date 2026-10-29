import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { decideConnectRequest, getConnectRequest } from '../api/oauth.js';
import { errorDetail, switchWorkspace as apiSwitchWorkspace } from '../api/auth.js';
import { adoptSession, useAuth } from '../hooks/useAuth.js';
import AuthLayout, { FormError } from '../components/AuthLayout.jsx';
import { Button } from '../components/ui.jsx';

/** Indirection so tests can observe the hand-off back to the agent app. */
export const connectRedirect = {
  go(url) {
    window.location.assign(url);
  },
};

/**
 * OAuth consent screen for agent apps (Claude, ChatGPT) connecting to the
 * MCP server.  Reached from the backend's /oauth/authorize redirect, behind
 * the normal login.  Approving issues a key that acts as this member in the
 * workspace shown — the same as a key made in Settings → Agent access.
 */
export default function ConnectAgent() {
  const [params] = useSearchParams();
  const requestId = params.get('request') || '';
  const queryClient = useQueryClient();
  const { workspace, memberships } = useAuth();
  const [switchError, setSwitchError] = useState(null);

  const requestQuery = useQuery({
    queryKey: ['oauth-connect-request', requestId, workspace?.id],
    queryFn: () => getConnectRequest(requestId),
    enabled: !!requestId,
    retry: false,
  });

  const decision = useMutation({
    mutationFn: (approve) => decideConnectRequest(requestId, approve),
    onSuccess: (data) => connectRedirect.go(data.redirect_to),
  });

  async function onSwitch(e) {
    setSwitchError(null);
    try {
      adoptSession(queryClient, await apiSwitchWorkspace(e.target.value));
    } catch (err) {
      setSwitchError(errorDetail(err, "Couldn't switch workspace."));
    }
  }

  if (!requestId || requestQuery.error?.response?.status === 404) {
    return (
      <AuthLayout testId="connect-agent-page" title="Connection request expired">
        <p className="text-sm text-slate-600 m-0" data-testid="connect-expired">
          This link has expired or was already used. Go back to the app you were
          connecting and start again.
        </p>
      </AuthLayout>
    );
  }
  if (requestQuery.isLoading) {
    return <AuthLayout testId="connect-agent-page" title="Loading…" />;
  }
  if (requestQuery.isError) {
    return (
      <AuthLayout testId="connect-agent-page" title="Couldn't load the request">
        <FormError>{errorDetail(requestQuery.error)}</FormError>
        <Button variant="secondary" onClick={() => requestQuery.refetch()}>Retry</Button>
      </AuthLayout>
    );
  }

  const req = requestQuery.data;
  return (
    <AuthLayout
      testId="connect-agent-page"
      title={`Connect ${req.client_name}`}
      subtitle={<>{req.client_name} wants to work with your Email Blaster campaigns.</>}
    >
      <div className="space-y-4 text-sm text-slate-600">
        <div>
          Signed in as <span className="font-medium text-slate-800">{req.user_email}</span>
        </div>
        {memberships.length > 1 ? (
          <label className="block">
            <span className="block text-xs font-medium text-slate-500 mb-1">Workspace</span>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
              value={workspace?.id || ''}
              onChange={onSwitch}
              data-testid="connect-workspace"
            >
              {memberships.map((m) => (
                <option key={m.tenant_id} value={m.tenant_id}>{m.tenant_name}</option>
              ))}
            </select>
          </label>
        ) : (
          <div>
            Workspace <span className="font-medium text-slate-800" data-testid="connect-workspace-name">{req.workspace_name}</span>
          </div>
        )}
        <ul className="m-0 pl-5 list-disc space-y-1">
          <li>It acts as you: it can read campaigns, leads, replies and the CRM, update CRM records, and pause or resume campaigns.</li>
          <li>It can&apos;t launch campaigns, delete anything, or manage the team, integrations or keys.</li>
          <li>One-off emails and LinkedIn messages are sent only after you approve them in the chat.</li>
        </ul>
        <p className="m-0 text-xs text-slate-500">
          You can disconnect it anytime in Settings → Agent access. You&apos;ll return to{' '}
          <span className="font-medium">{req.redirect_host}</span>.
        </p>
        <FormError>{switchError || (decision.isError ? errorDetail(decision.error) : null)}</FormError>
        <div className="flex gap-2">
          <Button className="flex-1" loading={decision.isPending && decision.variables === true}
            disabled={decision.isPending} onClick={() => decision.mutate(true)} data-testid="connect-approve">
            Allow
          </Button>
          <Button variant="secondary" className="flex-1" disabled={decision.isPending}
            onClick={() => decision.mutate(false)} data-testid="connect-deny">
            Cancel
          </Button>
        </div>
      </div>
    </AuthLayout>
  );
}
