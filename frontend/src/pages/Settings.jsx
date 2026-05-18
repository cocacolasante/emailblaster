import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listAccounts,
  deleteAccount,
  testAccount,
} from '../api/connectedAccounts.js';
import {
  listLinkedInAccounts,
  deleteLinkedInAccount,
  testLinkedInAccount,
} from '../api/linkedinAccounts.js';
import { getApiStatus } from '../api/settings.js';
import ConnectInboxModal from '../components/ConnectInboxModal.jsx';
import ConnectLinkedInModal from '../components/ConnectLinkedInModal.jsx';

const STATUS_LABEL = {
  untested: 'Untested',
  ok: 'Connected',
  failed: 'Failed',
  challenged: 'Challenge needed',
  restricted: 'Restricted',
};

const STATUS_CLASSES = {
  untested: 'bg-yellow-100 text-yellow-700',
  ok: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-600',
  challenged: 'bg-amber-100 text-amber-700',
  restricted: 'bg-red-200 text-red-800',
};


function StatusBadge({ status }) {
  const cls = STATUS_CLASSES[status] || STATUS_CLASSES.untested;
  return (
    <span
      data-testid="status-badge"
      data-status={status}
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${cls}`}
    >
      {STATUS_LABEL[status] || status}
    </span>
  );
}


function ConnectedInboxesTab() {
  const queryClient = useQueryClient();
  const [modalAccount, setModalAccount] = useState(undefined); // undefined = closed
  const { data: accounts = [], isLoading, error } = useQuery({
    queryKey: ['connected-accounts'],
    queryFn: listAccounts,
  });

  const testMutation = useMutation({
    mutationFn: testAccount,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['connected-accounts'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteAccount,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['connected-accounts'] }),
  });

  function openCreate() {
    setModalAccount(null);
  }
  function openEdit(account) {
    setModalAccount(account);
  }
  function closeModal() {
    setModalAccount(undefined);
  }
  function onModalSaved() {
    queryClient.invalidateQueries({ queryKey: ['connected-accounts'] });
  }

  if (isLoading) return <p className="text-sm text-slate-500">Loading inboxes…</p>;
  if (error) return <p className="text-sm text-red-600">Failed to load inboxes: {String(error.message)}</p>;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-lg font-semibold text-slate-900 m-0">Connected inboxes</h2>
        <button
          onClick={openCreate}
          className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
        >
          + Connect inbox
        </button>
      </div>

      {accounts.length === 0 ? (
        <div data-testid="empty-state" className="text-center py-16 text-slate-500">
          <div className="w-14 h-14 mx-auto mb-4 bg-slate-100 rounded-full flex items-center justify-center">
            <svg className="w-7 h-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
          </div>
          <p className="font-medium text-slate-700 mb-1">No inboxes connected yet.</p>
          <p className="text-sm text-slate-400">
            Connect an inbox to enable reply tracking on your campaigns.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {accounts.map((acc) => (
            <div key={acc.id} data-testid="account-card" className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-center gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-semibold text-slate-900 text-sm">{acc.label}</span>
                  <StatusBadge status={acc.last_test_status} />
                </div>
                <div className="text-xs text-slate-500">
                  {acc.email_address} · {acc.imap_host}:{acc.imap_port}
                </div>
                {acc.last_test_status === 'failed' && acc.last_test_error && (
                  <div className="text-xs text-red-600 mt-1">{acc.last_test_error}</div>
                )}
                {acc.last_polled_at && (
                  <div className="text-xs text-slate-400 mt-1">
                    Last polled: {new Date(acc.last_polled_at).toLocaleString()}
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => testMutation.mutate(acc.id)}
                  disabled={testMutation.isPending}
                  className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors disabled:opacity-50"
                >
                  Test
                </button>
                <button
                  onClick={() => openEdit(acc)}
                  className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors"
                >
                  Edit
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Delete inbox "${acc.label}"?`)) {
                      deleteMutation.mutate(acc.id);
                    }
                  }}
                  className="px-3 py-1.5 bg-white hover:bg-red-50 text-red-600 text-xs font-medium border border-red-200 rounded-md transition-colors"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {modalAccount !== undefined && (
        <ConnectInboxModal
          account={modalAccount}
          onClose={closeModal}
          onSaved={onModalSaved}
        />
      )}
    </div>
  );
}


function LinkedInAccountsTab() {
  const queryClient = useQueryClient();
  const [modalAccount, setModalAccount] = useState(undefined);
  const { data: accounts = [], isLoading, error } = useQuery({
    queryKey: ['linkedin-accounts'],
    queryFn: listLinkedInAccounts,
  });

  const testMutation = useMutation({
    mutationFn: testLinkedInAccount,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['linkedin-accounts'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteLinkedInAccount,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['linkedin-accounts'] }),
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading LinkedIn accounts…</p>;
  if (error) return <p className="text-sm text-red-600">Failed to load LinkedIn accounts: {String(error.message)}</p>;

  return (
    <div>
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-lg font-semibold text-slate-900 m-0">LinkedIn accounts</h2>
        <button
          onClick={() => setModalAccount(null)}
          className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors"
        >
          + Connect LinkedIn account
        </button>
      </div>

      <div className="mb-4 p-3 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-900">
        LinkedIn automation violates their Terms of Service. Use a throwaway
        account in development; keep volume conservative (the system enforces
        a daily cap per account).
      </div>

      {accounts.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <div className="w-14 h-14 mx-auto mb-4 bg-slate-100 rounded-full flex items-center justify-center">
            <svg className="w-7 h-7 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6zM2 9h4v12H2zM4 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4z" />
            </svg>
          </div>
          <p className="font-medium text-slate-700 mb-1">No LinkedIn accounts connected yet.</p>
          <p className="text-sm text-slate-400">
            Connect a LinkedIn account to enable warm-up actions in your sequences.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {accounts.map((acc) => (
            <div key={acc.id} className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 flex items-center gap-4">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-semibold text-slate-900 text-sm">{acc.label}</span>
                  <StatusBadge status={acc.status} />
                </div>
                <div className="text-xs text-slate-500">
                  {acc.linkedin_email}{acc.proxy_url ? ` · proxied` : ''}
                </div>
                {acc.last_error && (
                  <div className="text-xs text-red-600 mt-1">{acc.last_error}</div>
                )}
                {acc.pending_challenge_url && (
                  <div className="text-xs text-amber-700 mt-1">
                    Pending challenge — finish the verification in Unipile's
                    hosted browser, then open this account's <strong>Edit</strong>
                    button and click <strong>Clear challenge state</strong>.
                    Then hit Test to confirm.
                  </div>
                )}
                {acc.last_polled_at && (
                  <div className="text-xs text-slate-400 mt-1">
                    Last polled: {new Date(acc.last_polled_at).toLocaleString()}
                  </div>
                )}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => testMutation.mutate(acc.id)}
                  disabled={testMutation.isPending}
                  className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors disabled:opacity-50"
                >
                  Test
                </button>
                <button
                  onClick={() => setModalAccount(acc)}
                  className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors"
                >
                  Edit
                </button>
                <button
                  onClick={() => {
                    if (confirm(`Delete LinkedIn account "${acc.label}"?`)) {
                      deleteMutation.mutate(acc.id);
                    }
                  }}
                  className="px-3 py-1.5 bg-white hover:bg-red-50 text-red-600 text-xs font-medium border border-red-200 rounded-md transition-colors"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {modalAccount !== undefined && (
        <ConnectLinkedInModal
          account={modalAccount}
          onClose={() => setModalAccount(undefined)}
          onSaved={() => queryClient.invalidateQueries({ queryKey: ['linkedin-accounts'] })}
        />
      )}
    </div>
  );
}


function ApiStatusTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['api-status'],
    queryFn: getApiStatus,
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading API status…</p>;
  if (error) return <p className="text-sm text-red-600">Failed to load API status</p>;

  const rows = [
    { key: 'anthropic', label: 'Anthropic API', required: true },
    { key: 'brevo', label: 'Brevo', required: true },
    { key: 'apollo', label: 'Apollo.io', required: false },
    { key: 'hunter', label: 'Hunter.io', required: false },
  ];

  return (
    <div>
      <h2 className="text-lg font-semibold text-slate-900 mb-4">API status</h2>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {rows.map((r) => {
          const configured = data?.[r.key];
          return (
            <div key={r.key} data-testid={`api-row-${r.key}`} className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 flex items-center gap-4">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${configured ? 'bg-emerald-50' : 'bg-red-50'}`}>
                {configured ? (
                  <svg className="w-5 h-5 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  <svg className="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                )}
              </div>
              <div className="flex-1">
                <div className="font-semibold text-slate-900 text-sm">{r.label}</div>
                {!r.required && (
                  <div className="text-xs text-slate-400">optional — app works without this</div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <StatusBadge status={configured ? 'ok' : 'untested'} />
                <span className="text-xs text-slate-600">
                  {configured ? 'Configured' : 'Not configured'}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


export default function Settings() {
  const [tab, setTab] = useState('inboxes');

  return (
    <div className="p-8 max-w-[900px] mx-auto">
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Settings</h1>
      <div role="tablist" className="flex border-b border-slate-200 mb-6">
        <button
          role="tab"
          aria-selected={tab === 'inboxes'}
          onClick={() => setTab('inboxes')}
          className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors bg-transparent cursor-pointer ${
            tab === 'inboxes'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          Connected inboxes
        </button>
        <button
          role="tab"
          aria-selected={tab === 'linkedin'}
          onClick={() => setTab('linkedin')}
          className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors bg-transparent cursor-pointer ${
            tab === 'linkedin'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          LinkedIn accounts
        </button>
        <button
          role="tab"
          aria-selected={tab === 'api'}
          onClick={() => setTab('api')}
          className={`px-4 py-3 text-sm font-medium border-b-2 -mb-px transition-colors bg-transparent cursor-pointer ${
            tab === 'api'
              ? 'border-blue-600 text-blue-600'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          API status
        </button>
      </div>
      {tab === 'inboxes' && <ConnectedInboxesTab />}
      {tab === 'linkedin' && <LinkedInAccountsTab />}
      {tab === 'api' && <ApiStatusTab />}
    </div>
  );
}
