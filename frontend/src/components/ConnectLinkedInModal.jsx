import { useEffect, useState } from 'react';

import {
  createLinkedInAccount,
  resolveLinkedInChallenge,
  testLinkedInAccount,
  updateLinkedInAccount,
} from '../api/linkedinAccounts.js';

const EMPTY = {
  label: '',
  linkedin_email: '',
  password: '',
  proxy_url: '',
};

export default function ConnectLinkedInModal({ account, onClose, onSaved }) {
  const editing = Boolean(account);
  const [form, setForm] = useState(EMPTY);
  const [showPassword, setShowPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState(null);
  const [resolvingChallenge, setResolvingChallenge] = useState(false);

  useEffect(() => {
    if (account) {
      setForm({
        label: account.label || '',
        linkedin_email: account.linkedin_email || '',
        password: '',
        proxy_url: account.proxy_url || '',
      });
      if (account.status === 'challenged' && account.pending_challenge_url) {
        setTestResult({
          ok: false,
          status: 'challenged',
          challenge_url: account.pending_challenge_url,
        });
      }
    } else {
      setForm(EMPTY);
      setTestResult(null);
    }
  }, [account]);

  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      const payload = { ...form };
      if (editing && !payload.password) delete payload.password;
      if (!payload.proxy_url) delete payload.proxy_url;

      const saved = editing
        ? await updateLinkedInAccount(account.id, payload)
        : await createLinkedInAccount(payload);

      // Auto-run a connection test after save.
      try {
        const r = await testLinkedInAccount(saved.id);
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

  async function handleTest() {
    if (!editing) {
      setError('Save the account first, then test.');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const r = await testLinkedInAccount(account.id);
      setTestResult(r);
    } catch (e) {
      setTestResult({ ok: false, error: e?.message || 'Test failed' });
    } finally {
      setTesting(false);
    }
  }

  async function handleResolveChallenge() {
    if (!editing) return;
    setResolvingChallenge(true);
    try {
      await resolveLinkedInChallenge(account.id);
      setTestResult(null);
      // After resolve, the user re-tests — they'll click Test next.
      if (onSaved) onSaved({ ...account, status: 'untested', pending_challenge_url: null });
    } catch (e) {
      setError(e?.message || 'Resolve failed');
    } finally {
      setResolvingChallenge(false);
    }
  }

  const challenged = testResult?.status === 'challenged' || testResult?.meta?.challenged;

  return (
    <div data-testid="modal-overlay" className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center mb-5">
          <h2 className="m-0 text-lg font-semibold text-slate-900">
            {editing ? 'Edit LinkedIn account' : 'Connect LinkedIn account'}
          </h2>
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
            <label htmlFor="li-label" className="block text-sm font-medium text-slate-700 mb-1">Label</label>
            <input
              id="li-label"
              value={form.label}
              onChange={(e) => update('label', e.target.value)}
              placeholder="e.g. Anthony — main"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
          </div>

          <div>
            <label htmlFor="li-email" className="block text-sm font-medium text-slate-700 mb-1">LinkedIn login email</label>
            <input
              id="li-email"
              value={form.linkedin_email}
              onChange={(e) => update('linkedin_email', e.target.value)}
              placeholder="you@example.com"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
            />
          </div>

          <div>
            <label htmlFor="li-password" className="block text-sm font-medium text-slate-700 mb-1">
              Password{' '}
              {editing && <span className="text-xs text-slate-400 font-normal">(leave blank to keep current)</span>}
            </label>
            <div className="flex gap-2">
              <input
                id="li-password"
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
            <p className="mt-1 text-xs text-slate-500">
              If 2FA is enabled on this LinkedIn account, the first test will
              fail with a challenge — disable 2FA or be ready to solve it in
              your browser.
            </p>
          </div>

          <div>
            <label htmlFor="li-proxy" className="block text-sm font-medium text-slate-700 mb-1">
              Proxy URL{' '}
              <span className="text-xs text-slate-400 font-normal">(optional, recommended for active use)</span>
            </label>
            <input
              id="li-proxy"
              value={form.proxy_url}
              onChange={(e) => update('proxy_url', e.target.value)}
              placeholder="http://user:pass@host:port"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white font-mono"
            />
            <p className="mt-1 text-xs text-slate-500">
              Leave blank to use the system default (or no proxy in local dev).
              A residential proxy (Bright Data / Smartproxy) is strongly
              recommended once you're regularly polling.
            </p>
          </div>

          <div className="flex items-center gap-3 p-4 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-900">
            <svg className="w-4 h-4 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
            </svg>
            <span>
              LinkedIn automation violates their Terms of Service. Use a
              throwaway / secondary account in development. Stay well below
              the per-account daily cap — defaults are conservative for a reason.
            </span>
          </div>

          {challenged && testResult?.challenge_url && (
            <div className="p-3 rounded-lg bg-yellow-50 border border-yellow-200 text-sm text-yellow-900 space-y-2">
              <div className="font-medium">LinkedIn wants a security check.</div>
              <div className="text-xs">
                Open the link below in the same browser you normally use for
                LinkedIn, complete the captcha / PIN / phone verification,
                then click "I've resolved it".
              </div>
              <a
                href={testResult.challenge_url}
                target="_blank"
                rel="noreferrer"
                className="block text-xs font-mono break-all underline"
              >
                {testResult.challenge_url}
              </a>
              <button
                type="button"
                onClick={handleResolveChallenge}
                disabled={resolvingChallenge}
                className="px-3 py-1.5 text-xs bg-yellow-100 hover:bg-yellow-200 text-yellow-900 border border-yellow-300 rounded-md disabled:opacity-50"
              >
                {resolvingChallenge ? 'Clearing…' : "I've resolved it"}
              </button>
            </div>
          )}

          {testResult && !challenged && (
            <div
              data-testid="li-test-result"
              className={`p-3 rounded-lg text-sm ${testResult.ok ? 'bg-emerald-50 border border-emerald-200 text-emerald-800' : 'bg-red-50 border border-red-200 text-red-800'}`}
            >
              {testResult.ok
                ? 'Connection OK — session saved.'
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
