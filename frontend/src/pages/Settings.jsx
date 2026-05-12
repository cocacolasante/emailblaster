import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listAccounts,
  deleteAccount,
  testAccount,
} from '../api/connectedAccounts.js';
import { getApiStatus } from '../api/settings.js';
import ConnectInboxModal from '../components/ConnectInboxModal.jsx';

const STATUS_LABEL = {
  untested: 'Untested',
  ok: 'Connected',
  failed: 'Failed',
};

const STATUS_COLOR = {
  untested: { bg: '#fff3cd', fg: '#7a5a00' },
  ok: { bg: '#e6f7ed', fg: '#1b5e20' },
  failed: { bg: '#fdecea', fg: '#b71c1c' },
};


function StatusBadge({ status }) {
  const colors = STATUS_COLOR[status] || STATUS_COLOR.untested;
  return (
    <span
      data-testid="status-badge"
      data-status={status}
      style={{
        padding: '2px 8px',
        borderRadius: 12,
        fontSize: 12,
        background: colors.bg,
        color: colors.fg,
      }}
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

  if (isLoading) return <p>Loading inboxes…</p>;
  if (error) return <p style={{ color: '#b71c1c' }}>Failed to load inboxes: {String(error.message)}</p>;

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Connected inboxes</h2>
        <button onClick={openCreate} style={primaryBtn}>+ Connect inbox</button>
      </div>

      {accounts.length === 0 ? (
        <div data-testid="empty-state" style={{ padding: 32, textAlign: 'center', background: '#f9fafb', borderRadius: 8 }}>
          <p>No inboxes connected yet.</p>
          <p style={{ fontSize: 13, color: '#666' }}>
            Connect an inbox to enable reply tracking on your campaigns.
          </p>
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 12 }}>
          {accounts.map((acc) => (
            <div key={acc.id} data-testid="account-card" style={cardStyle}>
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <strong>{acc.label}</strong>
                  <StatusBadge status={acc.last_test_status} />
                </div>
                <div style={{ fontSize: 13, color: '#666' }}>
                  {acc.email_address} · {acc.imap_host}:{acc.imap_port}
                </div>
                {acc.last_test_status === 'failed' && acc.last_test_error && (
                  <div style={{ fontSize: 12, color: '#b71c1c', marginTop: 4 }}>
                    {acc.last_test_error}
                  </div>
                )}
                {acc.last_polled_at && (
                  <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
                    Last polled: {new Date(acc.last_polled_at).toLocaleString()}
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                <button
                  onClick={() => testMutation.mutate(acc.id)}
                  disabled={testMutation.isPending}
                  style={btnStyle}
                >
                  Test
                </button>
                <button onClick={() => openEdit(acc)} style={btnStyle}>Edit</button>
                <button
                  onClick={() => {
                    if (confirm(`Delete inbox "${acc.label}"?`)) {
                      deleteMutation.mutate(acc.id);
                    }
                  }}
                  style={{ ...btnStyle, color: '#b71c1c' }}
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


function ApiStatusTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['api-status'],
    queryFn: getApiStatus,
  });

  if (isLoading) return <p>Loading API status…</p>;
  if (error) return <p style={{ color: '#b71c1c' }}>Failed to load API status</p>;

  const rows = [
    { key: 'anthropic', label: 'Anthropic API', required: true },
    { key: 'brevo', label: 'Brevo', required: true },
    { key: 'apollo', label: 'Apollo.io', required: false },
    { key: 'hunter', label: 'Hunter.io', required: false },
  ];

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>API status</h2>
      <div style={{ display: 'grid', gap: 8 }}>
        {rows.map((r) => {
          const configured = data?.[r.key];
          return (
            <div key={r.key} data-testid={`api-row-${r.key}`} style={cardStyle}>
              <div style={{ flex: 1 }}>
                <strong>{r.label}</strong>
                {!r.required && (
                  <span style={{ marginLeft: 8, fontSize: 12, color: '#888' }}>
                    optional — app works without this
                  </span>
                )}
              </div>
              <StatusBadge status={configured ? 'ok' : 'untested'} />
              <span style={{ marginLeft: 8, fontSize: 13 }}>
                {configured ? 'Configured' : 'Not configured'}
              </span>
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
    <div style={{ padding: 24, maxWidth: 900, margin: '0 auto' }}>
      <h1>Settings</h1>
      <div role="tablist" style={{ display: 'flex', gap: 4, borderBottom: '1px solid #e5e7eb', marginBottom: 24 }}>
        <button
          role="tab"
          aria-selected={tab === 'inboxes'}
          onClick={() => setTab('inboxes')}
          style={tabBtn(tab === 'inboxes')}
        >
          Connected inboxes
        </button>
        <button
          role="tab"
          aria-selected={tab === 'api'}
          onClick={() => setTab('api')}
          style={tabBtn(tab === 'api')}
        >
          API status
        </button>
      </div>
      {tab === 'inboxes' && <ConnectedInboxesTab />}
      {tab === 'api' && <ApiStatusTab />}
    </div>
  );
}

const btnStyle = {
  padding: '6px 12px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 13,
};

const primaryBtn = {
  ...btnStyle,
  background: '#2563eb',
  color: 'white',
  border: '1px solid #2563eb',
};

const cardStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: 12,
  padding: 16,
  border: '1px solid #e5e7eb',
  borderRadius: 8,
  background: 'white',
};

function tabBtn(active) {
  return {
    padding: '10px 18px',
    border: 'none',
    background: 'none',
    cursor: 'pointer',
    fontSize: 14,
    fontWeight: active ? 600 : 400,
    color: active ? '#2563eb' : '#666',
    borderBottom: active ? '2px solid #2563eb' : '2px solid transparent',
    marginBottom: -1,
  };
}
