import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listAccounts,
  deleteAccount,
  testAccount,
  updateAccount,
} from '../api/connectedAccounts.js';
import {
  listLinkedInAccounts,
  deleteLinkedInAccount,
  testLinkedInAccount,
} from '../api/linkedinAccounts.js';
import { getApiStatus } from '../api/settings.js';
import { getAgentSettings, updateAgentSettings } from '../api/agent.js';
import {
  listFundingSources,
  runFundingSourceNow,
  stopFundingSource,
  updateFundingSource,
} from '../api/signals.js';
import ConnectInboxModal from '../components/ConnectInboxModal.jsx';
import ConnectLinkedInModal from '../components/ConnectLinkedInModal.jsx';
import { useToast } from '../components/Toast.jsx';
import { Tabs } from '../components/ui.jsx';

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

  // Promote a connected inbox to the workspace default sender.  The
  // backend's PATCH handler clears the flag on every other row in the
  // same transaction so there's only ever one default.
  const setDefaultMutation = useMutation({
    mutationFn: (id) => updateAccount(id, { is_default_sender: true }),
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
      <div className="flex justify-between items-center mb-2">
        <h2 className="text-lg font-semibold text-slate-900 m-0">Connected inboxes</h2>
        <button
          onClick={openCreate}
          className="inline-flex items-center px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium rounded-lg transition-colors"
        >
          + Connect inbox
        </button>
      </div>
      {accounts.length > 1 && (
        <p className="text-xs text-slate-500 mb-4" data-testid="default-sender-explainer">
          The inbox marked <span className="font-semibold text-emerald-700">Default sender</span>
          {' '}is the from-address for one-off sends from the Research-a-client tool when no
          per-send override is set.  Make sure the address is a verified sender on your Brevo
          account.
        </p>
      )}

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
                  {acc.is_default_sender && (
                    <span
                      data-testid={`default-sender-badge-${acc.id}`}
                      className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800"
                      title="One-off sends go from this address by default."
                    >
                      ✓ Default sender
                    </span>
                  )}
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
                {!acc.is_default_sender && (
                  <button
                    onClick={() => setDefaultMutation.mutate(acc.id)}
                    disabled={setDefaultMutation.isPending}
                    data-testid={`set-default-sender-${acc.id}`}
                    className="px-3 py-1.5 bg-white hover:bg-emerald-50 text-emerald-700 text-xs font-medium border border-emerald-200 rounded-md transition-colors disabled:opacity-50"
                    title="Use this inbox as the from-address for one-off sends"
                  >
                    Set as default sender
                  </button>
                )}
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
          className="inline-flex items-center px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium rounded-lg transition-colors"
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


const AGENT_TOGGLES = [
  { key: 'auto_log_replies', label: 'Auto-log inbound replies', hint: 'Each reply becomes an inbound email activity on the lead (and deal, if converted).' },
  { key: 'auto_create_convert_reminders', label: 'Convert reminders on positive replies', hint: 'A confident positive reply creates a "Convert lead to opportunity" task. The agent never converts on its own.' },
  { key: 'auto_draft_replies', label: 'Draft suggested replies (Sonnet)', hint: 'Generates a suggested reply you can copy — never sent automatically.' },
  { key: 'stale_opp_nudges_enabled', label: 'Stale deal nudges', hint: 'Open deals idle for a week get a re-engage task + alert.' },
  { key: 'daily_digest_enabled', label: 'Daily digest email', hint: 'One summary email a day: due/overdue tasks, replies, pipeline movement.' },
  { key: 'notify_on_positive_reply', label: 'Email me on positive replies', hint: '' },
  { key: 'notify_on_any_reply', label: 'Email me on every reply', hint: 'Noisy — positive-only is usually enough.' },
];

function AgentTab() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['agent-settings'],
    queryFn: getAgentSettings,
  });

  const updateMutation = useMutation({
    mutationFn: updateAgentSettings,
    onSuccess: (updated) => {
      queryClient.setQueryData(['agent-settings'], updated);
    },
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading agent settings…</p>;
  if (error) return <p className="text-sm text-red-600">Failed to load agent settings</p>;

  const onToggle = (key) => updateMutation.mutate({ [key]: !data[key] });

  return (
    <div data-testid="agent-tab">
      <h2 className="text-lg font-semibold text-slate-900 mb-1">CRM &amp; inbox agent</h2>
      <p className="text-sm text-slate-500 mt-0 mb-5">
        The agent logs replies, reminds you about tasks, and flags quiet
        deals. It never emails prospects, converts leads, or changes deal
        stages — those stay yours.
      </p>

      {!data.agent_enabled && (
        <div data-testid="agent-killswitch-banner" className="mb-4 bg-amber-50 border border-amber-200 text-amber-800 text-sm rounded-lg px-4 py-3">
          The agent is disabled at the deployment level (<code>AGENT_ENABLED=false</code>).
          These toggles will have no effect until it's re-enabled.
        </div>
      )}
      <div data-testid="owner-email-status" className={`mb-5 text-sm rounded-lg px-4 py-3 border ${data.owner_email_configured ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-amber-50 border-amber-200 text-amber-800'}`}>
        {data.owner_email_configured
          ? 'Owner alert email is configured — notifications will be emailed to you.'
          : 'OWNER_NOTIFY_EMAIL is not set — alerts will appear in the app bell but no emails will be sent.'}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm divide-y divide-slate-100">
        {AGENT_TOGGLES.map((t) => (
          <label key={t.key} data-testid={`agent-toggle-${t.key}`} className="flex items-start gap-3 px-5 py-4 cursor-pointer">
            <input
              type="checkbox"
              checked={!!data[t.key]}
              onChange={() => onToggle(t.key)}
              className="mt-0.5 w-4 h-4"
            />
            <span>
              <span className="block text-sm font-medium text-slate-800">{t.label}</span>
              {t.hint && <span className="block text-xs text-slate-400 mt-0.5">{t.hint}</span>}
            </span>
          </label>
        ))}
      </div>

      <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mt-4 flex flex-col gap-4">
        <div>
          <label className="block text-sm font-medium text-slate-800 mb-1" htmlFor="agent-confidence">
            Minimum confidence to act ({Math.round((data.min_confidence_to_act ?? 0.6) * 100)}%)
          </label>
          <input
            id="agent-confidence"
            data-testid="agent-confidence-input"
            type="range" min="0" max="1" step="0.05"
            value={data.min_confidence_to_act ?? 0.6}
            onChange={(e) => updateMutation.mutate({ min_confidence_to_act: parseFloat(e.target.value) })}
            className="w-full max-w-xs"
          />
          <p className="text-xs text-slate-400 m-0">
            Classifications below this confidence are logged but never trigger reminders or alerts.
          </p>
        </div>
        <div>
          <span className="block text-sm font-medium text-slate-800 mb-1">Quiet hours (UTC)</span>
          <div className="flex items-center gap-2">
            <select
              data-testid="quiet-start-select"
              value={data.quiet_hours_start_utc ?? ''}
              onChange={(e) => {
                const v = e.target.value;
                if (v === '') updateMutation.mutate({ clear_quiet_hours: true });
                else updateMutation.mutate({
                  quiet_hours_start_utc: parseInt(v, 10),
                  quiet_hours_end_utc: data.quiet_hours_end_utc ?? (parseInt(v, 10) + 8) % 24,
                });
              }}
              className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white"
            >
              <option value="">Off</option>
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
              ))}
            </select>
            <span className="text-sm text-slate-500">to</span>
            <select
              data-testid="quiet-end-select"
              value={data.quiet_hours_end_utc ?? ''}
              disabled={data.quiet_hours_start_utc == null}
              onChange={(e) => updateMutation.mutate({
                quiet_hours_start_utc: data.quiet_hours_start_utc,
                quiet_hours_end_utc: parseInt(e.target.value, 10),
              })}
              className="border border-slate-300 rounded-lg px-2 py-1.5 text-sm bg-white disabled:opacity-50"
            >
              <option value="">—</option>
              {Array.from({ length: 24 }, (_, h) => (
                <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
              ))}
            </select>
          </div>
          <p className="text-xs text-slate-400 m-0 mt-1">
            Alert emails inside this window are held; the daily digest sweeps them up.
          </p>
        </div>
      </div>
    </div>
  );
}


function fmtRunTime(iso) {
  if (!iso) return 'never';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? 'never' : d.toLocaleString();
}

function FundingSourceCard({ source, hunterConfigured }) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const isIrs = source.source === 'irs_bmf';
  const cfg = source.config || {};

  const [enabled, setEnabled] = useState(source.enabled);
  const [lookbackDays, setLookbackDays] = useState(cfg.lookback_days ?? 7);
  const [maxAward, setMaxAward] = useState(
    cfg.max_award_amount == null ? '' : String(cfg.max_award_amount),
  );
  const [rulingMonths, setRulingMonths] = useState(cfg.ruling_lookback_months ?? 2);
  const [states, setStates] = useState((cfg.states || []).join(', '));
  const [maxPerRun, setMaxPerRun] = useState(cfg.max_per_run ?? 25);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['funding-sources'] });

  const saveMut = useMutation({
    mutationFn: () => {
      const payload = { enabled };
      if (String(maxPerRun).trim() !== '') payload.max_per_run = Number(maxPerRun);
      if (isIrs) {
        payload.ruling_lookback_months = Number(rulingMonths);
        payload.states = states.split(',').map((s) => s.trim()).filter(Boolean);
      } else {
        payload.lookback_days = Number(lookbackDays);
        // Blank = no cap (explicit null); a number caps the award size.
        payload.max_award_amount = maxAward.trim() === '' ? null : Number(maxAward);
      }
      return updateFundingSource(source.source, payload);
    },
    onSuccess: () => { invalidate(); toast.success(`${source.label} saved`); },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Save failed'),
  });

  const runMut = useMutation({
    mutationFn: () => runFundingSourceNow(source.source),
    onSuccess: () => toast.success(`${source.label} check queued — results appear in Signals shortly`),
    onError: (err) => toast.error(err?.response?.data?.detail || 'Run failed'),
  });

  const stopMut = useMutation({
    mutationFn: () => stopFundingSource(source.source),
    onSuccess: (data) => {
      invalidate();
      const s = data?.stopped || {};
      const killed = (s.terminated?.length || 0) + (s.purged_queued || 0) + (s.purged_unacked || 0);
      toast.success(
        killed
          ? `${source.label} run stopped (${s.terminated?.length || 0} task${(s.terminated?.length || 0) === 1 ? '' : 's'} terminated)`
          : `No ${source.label} run was in progress`,
      );
    },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Stop failed'),
  });

  const isRunning = source.last_run_status === 'running';

  return (
    <div
      data-testid={`funding-card-${source.source}`}
      className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-4"
    >
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="text-base font-semibold text-slate-900 m-0">{source.label}</h3>
          <p className="text-xs text-slate-500 m-0 mt-0.5">
            {isIrs
              ? 'Newly-ruled 501(c)(3) organizations (IRS EO BMF, per state).'
              : 'Recent federal grant awards to nonprofits (USAspending).'}
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm text-slate-700 cursor-pointer shrink-0">
          <input
            type="checkbox"
            data-testid={`funding-enabled-${source.source}`}
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Enabled
        </label>
      </div>

      <div className="flex flex-wrap items-end gap-4 mb-3">
        {isIrs ? (
          <>
            <label className="text-xs text-slate-600">
              States (comma-separated)
              <input
                type="text"
                data-testid="funding-states"
                value={states}
                onChange={(e) => setStates(e.target.value)}
                placeholder="PA, NJ, NY"
                className="mt-1 block w-56 border border-slate-300 rounded-lg px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs text-slate-600">
              Ruling lookback (months)
              <input
                type="number" min={1} max={24}
                data-testid="funding-ruling-months"
                value={rulingMonths}
                onChange={(e) => setRulingMonths(e.target.value)}
                className="mt-1 block w-32 border border-slate-300 rounded-lg px-2 py-1.5 text-sm"
              />
            </label>
          </>
        ) : (
          <div className="flex flex-wrap gap-4">
            <label className="text-xs text-slate-600">
              Lookback (days)
              <input
                type="number" min={1} max={365}
                data-testid="funding-lookback-days"
                value={lookbackDays}
                onChange={(e) => setLookbackDays(e.target.value)}
                className="mt-1 block w-32 border border-slate-300 rounded-lg px-2 py-1.5 text-sm"
              />
            </label>
            <label className="text-xs text-slate-600">
              Max award amount ($)
              <input
                type="number" min={0} step={1000}
                data-testid="funding-max-award"
                value={maxAward}
                onChange={(e) => setMaxAward(e.target.value)}
                placeholder="no cap"
                className="mt-1 block w-32 border border-slate-300 rounded-lg px-2 py-1.5 text-sm"
              />
              <span className="block mt-0.5 text-[11px] text-slate-400">
                Skip grants above this (big grants → large orgs). Blank = no cap.
              </span>
            </label>
          </div>
        )}

        {/* Lead-pull cap — applies to both feeds; safeguards a huge run. */}
        <label className="text-xs text-slate-600">
          Max leads per run
          <input
            type="number" min={1} max={1000}
            data-testid={`funding-max-per-run-${source.source}`}
            value={maxPerRun}
            onChange={(e) => setMaxPerRun(e.target.value)}
            className="mt-1 block w-32 border border-slate-300 rounded-lg px-2 py-1.5 text-sm"
          />
          <span className="block mt-0.5 text-[11px] text-slate-400">
            Caps how many orgs are enriched per run.
          </span>
        </label>
      </div>

      <div className="flex items-center justify-between border-t border-slate-100 pt-3">
        <div className="text-xs text-slate-500">
          Last run: <span className="font-medium text-slate-700">{fmtRunTime(source.last_run_at)}</span>
          {source.last_run_status && ` (${source.last_run_status})`}
          {' · '}{source.signal_count} signal{source.signal_count === 1 ? '' : 's'} surfaced
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            data-testid={`funding-save-${source.source}`}
            onClick={() => saveMut.mutate()}
            disabled={saveMut.isPending}
            className="px-3 py-1.5 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded-lg disabled:opacity-50"
          >
            {saveMut.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            type="button"
            data-testid={`funding-run-${source.source}`}
            onClick={() => runMut.mutate()}
            disabled={runMut.isPending || !source.enabled}
            title={source.enabled ? 'Poll this feed now' : 'Enable + save first'}
            className="px-3 py-1.5 text-sm border border-slate-300 text-slate-700 hover:bg-slate-50 rounded-lg disabled:opacity-50"
          >
            Run now
          </button>
          <button
            type="button"
            data-testid={`funding-stop-${source.source}`}
            onClick={() => stopMut.mutate()}
            disabled={stopMut.isPending}
            title="Terminate an in-flight run and stop token spend"
            className={`px-3 py-1.5 text-sm rounded-lg disabled:opacity-50 ${
              isRunning
                ? 'bg-red-600 hover:bg-red-700 text-white'
                : 'border border-red-300 text-red-700 hover:bg-red-50'
            }`}
          >
            {stopMut.isPending ? 'Stopping…' : 'Stop'}
          </button>
        </div>
      </div>
    </div>
  );
}

function DiscoveryTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['funding-sources'],
    queryFn: listFundingSources,
    // Poll while a feed is mid-run so the status + Stop button stay live.
    refetchInterval: (query) =>
      (query.state.data?.sources || []).some((s) => s.last_run_status === 'running')
        ? 5000
        : false,
  });

  if (isLoading) return <p className="text-sm text-slate-500">Loading discovery feeds…</p>;
  if (error) return <p className="text-sm text-red-600">Failed to load discovery feeds</p>;

  return (
    <div data-testid="discovery-tab">
      <h2 className="text-lg font-semibold text-slate-900 mb-1">Discovery feeds</h2>
      <p className="text-sm text-slate-500 mt-0 mb-5">
        Free external feeds that surface nonprofits worth contacting into your{' '}
        <strong>Signals</strong> queue. Detected orgs become reviewable signals —
        with a contact found, a campaign-less CRM lead + reach-out task are staged.
        Nothing is ever emailed or added to a campaign automatically.
      </p>

      {!data.hunter_configured && (
        <div
          data-testid="hunter-warning"
          className="mb-4 bg-amber-50 border border-amber-200 text-amber-800 text-sm rounded-lg px-4 py-3"
        >
          No Hunter API key configured — discovered orgs surface as
          notification-only signals (no contact email, so no lead/task is staged).
          Add <code>HUNTER_API_KEY</code> to resolve decision-maker contacts.
        </div>
      )}

      {data.sources.map((src) => (
        <FundingSourceCard key={src.source} source={src} hunterConfigured={data.hunter_configured} />
      ))}

      <p className="text-xs text-slate-400 mt-2">
        Feeds also poll automatically — USAspending daily, IRS monthly. "Run now"
        triggers an immediate poll. Results land on the Signals page (filter by source).
      </p>
    </div>
  );
}


export default function Settings() {
  const [tab, setTab] = useState('inboxes');

  return (
    <div className="p-8 max-w-[900px] mx-auto">
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Settings</h1>
      <Tabs
        testId="settings-tab"
        className="mb-6"
        active={tab}
        onChange={setTab}
        tabs={[
          { key: 'inboxes', label: 'Connected inboxes' },
          { key: 'linkedin', label: 'LinkedIn accounts' },
          { key: 'agent', label: 'Agent' },
          { key: 'discovery', label: 'Discovery' },
          { key: 'api', label: 'API status' },
        ]}
      />
      {tab === 'inboxes' && <ConnectedInboxesTab />}
      {tab === 'linkedin' && <LinkedInAccountsTab />}
      {tab === 'agent' && <AgentTab />}
      {tab === 'discovery' && <DiscoveryTab />}
      {tab === 'api' && <ApiStatusTab />}
    </div>
  );
}
