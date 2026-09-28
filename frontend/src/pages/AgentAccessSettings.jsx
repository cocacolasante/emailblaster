import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createApiKey,
  getApiKeyConnection,
  listApiKeys,
  revokeApiKey,
} from '../api/apiKeys.js';
import { errorDetail } from '../api/auth.js';
import { useToast } from '../components/Toast.jsx';
import { Badge, Button, Card, Field, Input, Modal, Table } from '../components/ui.jsx';
import { ErrorState, LoadingCards } from '../components/states.jsx';
import { formatDateTime } from '../utils/format.js';
import { CopyButton } from './IntegrationsSettings.jsx';

export const API_KEYS_KEY = ['api-keys'];
const CONNECTION_KEY = ['api-keys', 'connection'];

/** "Never" / "just now" / "5m ago" / "3h ago" / "2d ago". */
export function formatLastUsed(iso, now = Date.now()) {
  if (!iso) return 'Never';
  const diff = now - new Date(iso).getTime();
  if (Number.isNaN(diff)) return 'Never';
  if (diff < 60000) return 'just now';
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function EndpointCard() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: CONNECTION_KEY,
    queryFn: getApiKeyConnection,
  });

  return (
    <Card className="p-5" data-testid="mcp-endpoint-card">
      <h3 className="text-base font-semibold text-slate-900 m-0">MCP endpoint</h3>
      {isLoading && <p className="text-xs text-slate-500 mt-2 mb-0">Loading…</p>}
      {error && (
        <div className="mt-2">
          <ErrorState message={errorDetail(error, 'Failed to load the MCP endpoint')} onRetry={refetch} />
        </div>
      )}
      {data && (
        <>
          <div className="mt-3 flex items-center gap-2 flex-wrap text-xs">
            <code
              data-testid="mcp-url"
              className="font-mono text-slate-800 bg-slate-50 border border-slate-200 rounded px-2 py-1 break-all"
            >
              {data.mcp_url}
            </code>
            <CopyButton value={data.mcp_url} testId="copy-mcp-url" />
          </div>
          <p className="text-xs text-slate-500 mt-2 mb-0">
            In Muse, add a custom connector: paste this URL and use an API key below as the
            bearer token.
          </p>
          {!data.public && (
            <div
              role="alert"
              data-testid="mcp-not-public"
              className="mt-3 bg-amber-50 border border-amber-200 text-amber-800 text-sm rounded-lg px-4 py-3"
            >
              This is a local address — Muse can't reach localhost. Set{' '}
              <code className="font-mono">PUBLIC_ORIGIN</code> and run the named tunnel so the
              endpoint is publicly reachable.
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function CapabilitiesCard() {
  return (
    <Card className="p-5" data-testid="mcp-capabilities">
      <h3 className="text-base font-semibold text-slate-900 m-0">What Muse can do</h3>
      <ul className="mt-2 mb-0 pl-5 list-disc text-sm text-slate-700 flex flex-col gap-1">
        <li>Search leads (by name, company or email) and deals; read campaigns, replies, tasks, notes and activity logs</li>
        <li>Update the CRM — leads, notes, tasks, deals and owners</li>
        <li>Add a lead to the ignore list (stops all outreach to them)</li>
        <li>Pause and resume campaigns</li>
      </ul>
      <p className="text-sm text-slate-600 mt-3 mb-0">
        It cannot send emails or LinkedIn messages, launch campaigns, delete anything, or
        manage your team, integrations or keys.
      </p>
      <p className="text-xs text-slate-500 mt-2 mb-0">
        A key acts as you — the member who created it — with your permissions in this workspace.
      </p>
    </Card>
  );
}

function CreateKeyCard() {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState(null);
  // The plaintext token lives ONLY in this component state — never in the
  // query cache — and is dropped when the user dismisses it.
  const [created, setCreated] = useState(null);

  const mutation = useMutation({
    mutationFn: createApiKey,
    onSuccess: (key) => {
      setCreated({ name: key?.name, token: key?.token });
      setName('');
      queryClient.invalidateQueries({ queryKey: API_KEYS_KEY });
    },
  });

  function submit(e) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setNameError('Give the key a name');
      return;
    }
    setNameError(null);
    mutation.mutate(trimmed);
  }

  return (
    <Card className="p-5" data-testid="create-api-key-card">
      <h3 className="text-base font-semibold text-slate-900 m-0">Create an API key</h3>
      <form onSubmit={submit} className="mt-3 flex gap-2 items-start" noValidate>
        <Field label="Key name" error={nameError} className="flex-1">
          {(props) => (
            <Input
              {...props}
              data-testid="api-key-name"
              value={name}
              placeholder="Muse on my phone"
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
            />
          )}
        </Field>
        <Button type="submit" size="sm" className="mt-6" loading={mutation.isPending} data-testid="create-api-key">
          Create
        </Button>
      </form>
      {mutation.isError && (
        <p role="alert" data-testid="create-api-key-error" className="text-xs text-danger-600 mt-2 mb-0">
          {errorDetail(mutation.error, 'Could not create the key')}
        </p>
      )}
      {created && (
        <div
          role="status"
          data-testid="new-api-key"
          className="mt-4 bg-emerald-50 border border-emerald-200 text-emerald-900 text-sm rounded-lg px-4 py-3"
        >
          <p className="font-semibold m-0">Copy this key now — it won't be shown again</p>
          <p className="text-xs text-emerald-800 mt-0.5 mb-0">
            {created.name ? <>Key “{created.name}” created. </> : null}
            It's stored hashed, so there's no way to reveal it later — if you lose it, revoke it
            and create a new one.
          </p>
          <div className="mt-2 flex items-center gap-2 flex-wrap text-xs">
            <code
              data-testid="new-api-key-token"
              className="font-mono text-slate-800 bg-white border border-emerald-200 rounded px-2 py-1 break-all"
            >
              {created.token}
            </code>
            <CopyButton value={created.token} testId="copy-new-api-key" />
            <Button
              type="button" variant="ghost" size="sm"
              onClick={() => setCreated(null)}
              data-testid="dismiss-new-api-key"
            >
              Done
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}

function KeysTable() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [confirmKey, setConfirmKey] = useState(null);
  const { data: keys = [], isLoading, error, refetch } = useQuery({
    queryKey: API_KEYS_KEY,
    queryFn: listApiKeys,
  });

  const revokeMut = useMutation({
    mutationFn: (id) => revokeApiKey(id),
    onSuccess: () => {
      toast.success(`Key "${confirmKey?.name}" revoked`);
      setConfirmKey(null);
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: API_KEYS_KEY }),
  });

  function openConfirm(key) {
    revokeMut.reset();
    setConfirmKey(key);
  }

  const columns = [
    { key: 'name', label: 'Name', render: (k) => <span className="font-medium text-slate-900">{k.name}</span> },
    {
      key: 'prefix', label: 'Key',
      render: (k) => <code data-testid="api-key-prefix" className="font-mono text-xs text-slate-700">{k.prefix}…</code>,
    },
    { key: 'created_by', label: 'Created by', render: (k) => k.created_by_name || '—' },
    {
      key: 'last_used_at', label: 'Last used',
      render: (k) => (
        <span title={k.last_used_at ? formatDateTime(k.last_used_at) : undefined}>
          {formatLastUsed(k.last_used_at)}
        </span>
      ),
    },
    {
      key: 'status', label: 'Status',
      render: (k) => (k.revoked_at
        ? <Badge variant="neutral" data-testid="api-key-status" data-status="revoked">Revoked</Badge>
        : <Badge variant="success" data-testid="api-key-status" data-status="active">Active</Badge>),
    },
    {
      key: 'actions', label: '', align: 'right',
      render: (k) => (k.revoked_at ? null : (
        <Button
          type="button" variant="ghost" size="sm"
          className="text-danger-600 hover:bg-danger-50"
          onClick={() => openConfirm(k)}
          data-testid={`revoke-api-key-${k.id}`}
        >
          Revoke
        </Button>
      )),
    },
  ];

  return (
    <div data-testid="api-keys-section">
      <h3 className="text-base font-semibold text-slate-900 mb-2 mt-0">API keys</h3>
      {isLoading && <LoadingCards count={1} />}
      {error && <ErrorState message={errorDetail(error, 'Failed to load API keys')} onRetry={refetch} />}
      {!isLoading && !error && (
        <Table
          testId="api-keys-table"
          columns={columns}
          rows={keys}
          empty="No API keys yet — create one above to connect Muse."
        />
      )}

      <Modal
        open={!!confirmKey}
        onClose={() => setConfirmKey(null)}
        title="Revoke API key?"
        size="sm"
        testId="revoke-api-key-modal"
        footer={(
          <>
            <Button variant="secondary" size="sm" onClick={() => setConfirmKey(null)}>Cancel</Button>
            <Button
              variant="danger" size="sm"
              loading={revokeMut.isPending}
              onClick={() => revokeMut.mutate(confirmKey.id)}
              data-testid="confirm-revoke-api-key"
            >
              Revoke key
            </Button>
          </>
        )}
      >
        <p className="text-sm text-slate-600 m-0">
          “{confirmKey?.name}” stops working immediately. Any agent using it — like Muse — loses
          access until you give it a new key. This can't be undone.
        </p>
        {revokeMut.isError && (
          <p role="alert" data-testid="revoke-api-key-error" className="text-xs text-danger-600 mt-3 mb-0">
            {errorDetail(revokeMut.error, 'Revoke failed')}
          </p>
        )}
      </Modal>
    </div>
  );
}

export default function AgentAccessTab() {
  return (
    <div data-testid="agent-access-tab">
      <h2 className="text-lg font-semibold text-slate-900 mb-1">Agent access</h2>
      <p className="text-sm text-slate-500 mt-0 mb-4">
        Let an AI assistant like Muse work in this workspace from your phone, over MCP.
      </p>
      <div className="flex flex-col gap-4">
        <EndpointCard />
        <CapabilitiesCard />
        <CreateKeyCard />
        <KeysTable />
      </div>
    </div>
  );
}
