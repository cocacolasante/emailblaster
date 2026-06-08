import { useState, useEffect } from 'react';
import { createAccount, updateAccount, testAccount } from '../api/connectedAccounts.js';

const PRESETS = {
  Gmail: { imap_host: 'imap.gmail.com', imap_port: 993, imap_use_ssl: true },
  Outlook: { imap_host: 'outlook.office365.com', imap_port: 993, imap_use_ssl: true },
  Yahoo: { imap_host: 'imap.mail.yahoo.com', imap_port: 993, imap_use_ssl: true },
};

const EMPTY = {
  label: '',
  email_address: '',
  imap_host: '',
  imap_port: 993,
  imap_use_ssl: true,
  username: '',
  password: '',
  signature: '',
};

export default function ConnectInboxModal({ account, onClose, onSaved }) {
  const editing = Boolean(account);
  const [form, setForm] = useState(EMPTY);
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (account) {
      setForm({
        label: account.label || '',
        email_address: account.email_address || '',
        imap_host: account.imap_host || '',
        imap_port: account.imap_port || 993,
        imap_use_ssl: account.imap_use_ssl ?? true,
        username: account.username || '',
        password: '',
        signature: account.signature || '',
      });
    } else {
      setForm(EMPTY);
    }
  }, [account]);

  function update(field, value) {
    setForm((f) => {
      const next = { ...f, [field]: value };
      // Auto-fill username from email if the user hasn't customised it yet.
      if (field === 'email_address' && (!f.username || f.username === f.email_address)) {
        next.username = value;
      }
      return next;
    });
  }

  function applyPreset(name) {
    const p = PRESETS[name];
    if (!p) return;
    setForm((f) => ({ ...f, ...p }));
  }

  async function handleTest() {
    if (!editing) {
      setError('Save the inbox first, then test the connection.');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testAccount(account.id);
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, error: e?.message || 'Test failed' });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      const payload = { ...form };
      if (editing && !payload.password) delete payload.password;
      let saved;
      if (editing) {
        saved = await updateAccount(account.id, payload);
      } else {
        saved = await createAccount(payload);
      }
      // Auto-run a connection test after save.
      try {
        const r = await testAccount(saved.id);
        setTestResult(r);
      } catch (e) {
        setTestResult({ ok: false, error: e?.message || 'Saved, but test failed' });
      }
      if (onSaved) onSaved(saved);
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || 'Save failed';
      setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div data-testid="modal-overlay" className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-5">
          <h2 className="m-0 text-lg font-semibold text-slate-900">{editing ? 'Edit inbox' : 'Connect inbox'}</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 text-xl font-medium leading-none bg-transparent border-none cursor-pointer p-1"
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label htmlFor="inbox-label" className="block text-sm font-medium text-slate-700 mb-1">Label</label>
            <input
              id="inbox-label"
              value={form.label}
              onChange={(e) => update('label', e.target.value)}
              placeholder="e.g. Work Gmail"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
          </div>

          <div>
            <label htmlFor="inbox-email" className="block text-sm font-medium text-slate-700 mb-1">Email address</label>
            <input
              id="inbox-email"
              value={form.email_address}
              onChange={(e) => update('email_address', e.target.value)}
              placeholder="you@example.com"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
          </div>

          <div>
            <span className="block text-sm font-medium text-slate-700 mb-2">Preset</span>
            <div className="flex gap-2 flex-wrap">
              {Object.keys(PRESETS).map((p) => (
                <button
                  type="button"
                  key={p}
                  onClick={() => applyPreset(p)}
                  className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors"
                  aria-label={`Use ${p} preset`}
                >
                  {p}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label htmlFor="inbox-imap-host" className="block text-sm font-medium text-slate-700 mb-1">IMAP host</label>
            <input
              id="inbox-imap-host"
              value={form.imap_host}
              onChange={(e) => update('imap_host', e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
          </div>

          <div className="flex gap-3">
            <div className="flex-1">
              <label htmlFor="inbox-port" className="block text-sm font-medium text-slate-700 mb-1">Port</label>
              <input
                id="inbox-port"
                type="number"
                value={form.imap_port}
                onChange={(e) => update('imap_port', Number(e.target.value))}
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              />
            </div>
            <div className="flex-1 flex items-end pb-2">
              <label className="flex items-center gap-2 text-sm font-medium text-slate-700 cursor-pointer">
                <input
                  type="checkbox"
                  checked={form.imap_use_ssl}
                  onChange={(e) => update('imap_use_ssl', e.target.checked)}
                  className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                />
                Use SSL
              </label>
            </div>
          </div>

          <div>
            <label htmlFor="inbox-username" className="block text-sm font-medium text-slate-700 mb-1">Username</label>
            <input
              id="inbox-username"
              value={form.username}
              onChange={(e) => update('username', e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
            <p className="mt-1 text-xs text-slate-500">
              For Gmail aliases, set this to your primary mailbox login (not the alias). Aliases share their parent mailbox and cannot log in over IMAP on their own.
            </p>
          </div>

          <div>
            <label htmlFor="inbox-password" className="block text-sm font-medium text-slate-700 mb-1">
              Password{' '}
              {editing && <span className="text-xs text-slate-400 font-normal">(leave blank to keep current)</span>}
            </label>
            <div className="flex gap-2">
              <input
                id="inbox-password"
                type={showPassword ? 'text' : 'password'}
                value={form.password}
                onChange={(e) => update('password', e.target.value)}
                className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="px-3 py-1.5 bg-white hover:bg-slate-50 text-slate-700 text-xs font-medium border border-slate-300 rounded-md transition-colors"
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? 'Hide' : 'Show'}
              </button>
            </div>
          </div>

          <div className="flex items-center gap-3 p-4 bg-blue-50 border border-blue-200 rounded-lg text-sm text-blue-800">
            <svg className="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
            </svg>
            <span>
              For Gmail, use an{' '}
              <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer" className="underline">
                App Password
              </a>{' '}
              (not your account password). Requires 2-Step Verification.
              For Outlook, use your regular password or an app password if MFA is enabled.
            </span>
          </div>

          <div>
            <label htmlFor="inbox-signature" className="block text-sm font-medium text-slate-700 mb-1">
              Email signature
              <span className="text-xs text-slate-400 font-normal"> (optional)</span>
            </label>
            <textarea
              id="inbox-signature"
              rows={5}
              value={form.signature}
              onChange={(e) => update('signature', e.target.value)}
              data-testid="inbox-signature"
              placeholder={'Best,\nAnthony Colasante\ncsuitecode.com · book a call → calendly.com/anthony'}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 font-mono leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
            <p className="text-xs text-slate-500 mt-1">
              Appended to one-off sends from the Research-a-client tool when this inbox is the
              from-address.  Replaces the AI's sign-off line if one was generated, otherwise
              appended after a blank line.  Leave blank for no signature.
            </p>
          </div>

          {testResult && (
            <div
              data-testid="test-result"
              className={`p-3 rounded-lg text-sm ${testResult.ok ? 'bg-emerald-50 border border-emerald-200 text-emerald-800' : 'bg-red-50 border border-red-200 text-red-800'}`}
            >
              {testResult.ok
                ? `Connection OK${testResult.message_count != null ? ` — ${testResult.message_count} messages in INBOX` : ''}`
                : `Connection failed: ${testResult.error || 'unknown error'}`}
            </div>
          )}

          {error && (
            <div data-testid="modal-error" className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
              {error}
            </div>
          )}
        </div>

        <div className="flex gap-2 justify-end mt-6 pt-4 border-t border-slate-100">
          <button
            type="button"
            onClick={handleTest}
            disabled={testing || !editing}
            className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
