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
  li_at_cookie: '',
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
  const [showCookieSection, setShowCookieSection] = useState(false);

  useEffect(() => {
    if (account) {
      setForm({
        label: account.label || '',
        linkedin_email: account.linkedin_email || '',
        password: '',
        proxy_url: account.proxy_url || '',
        li_at_cookie: '',
      });
      if (account.status === 'challenged') {
        setTestResult({
          ok: false,
          status: 'challenged',
          challenge_url: account.pending_challenge_url,
        });
        setShowCookieSection(true);
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
      if (!payload.li_at_cookie) delete payload.li_at_cookie;

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
              Required — the app logs into LinkedIn on your behalf using a real
              browser. It also uses this password to re-login automatically when
              the session expires, so you don&apos;t need to update cookies manually.{' '}
              <span className="text-amber-700 font-medium">Disable 2FA on this account</span>{' '}
              so automated re-logins work without interruption.
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

          <div className="border border-slate-200 rounded-lg overflow-hidden">
            <button
              type="button"
              onClick={() => setShowCookieSection((v) => !v)}
              className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-50 hover:bg-slate-100 text-sm font-medium text-slate-700 transition-colors"
            >
              <span>Optional: seed with an existing session cookie</span>
              <svg
                className={`w-4 h-4 text-slate-400 transition-transform ${showCookieSection ? 'rotate-180' : ''}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>
            {showCookieSection && (
              <div className="px-4 pb-4 pt-3 space-y-2">
                <label htmlFor="li-at" className="block text-sm font-medium text-slate-700">
                  <code className="bg-slate-100 px-1 rounded text-xs">li_at</code> session cookie{' '}
                  <span className="text-xs text-slate-400 font-normal">(optional)</span>
                </label>
                <input
                  id="li-at"
                  value={form.li_at_cookie}
                  onChange={(e) => update('li_at_cookie', e.target.value)}
                  placeholder="Paste your li_at cookie value here"
                  className="w-full px-3 py-2 border border-slate-300 rounded-lg text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white font-mono"
                />
                <div className="text-xs text-slate-500 space-y-1">
                  <p>
                    If you&apos;re already logged into LinkedIn in your browser, pasting
                    your <code className="bg-slate-100 px-1 rounded">li_at</code> cookie lets
                    the app skip the login form on the first test. The password is still used
                    to re-login automatically when the session later expires.
                  </p>
                  <p className="font-medium text-slate-600 mt-1">How to get it:</p>
                  <ol className="list-decimal list-inside space-y-0.5">
                    <li>Open LinkedIn in your browser and log in normally</li>
                    <li>Open DevTools (F12) → Application → Cookies → https://www.linkedin.com</li>
                    <li>Find the cookie named <code className="bg-slate-100 px-1 rounded">li_at</code></li>
                    <li>Copy its value and paste it above, then Save</li>
                  </ol>
                </div>
              </div>
            )}
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

          {challenged && (
            <div className="p-3 rounded-lg bg-yellow-50 border border-yellow-200 text-sm text-yellow-900 space-y-2">
              <div className="font-medium">LinkedIn requires verification before the app can log in.</div>
              <div className="text-xs space-y-2">
                <p className="font-medium text-yellow-800">Step 1 — log into LinkedIn in your real browser and complete any verification it shows:</p>
                <a
                  href="https://www.linkedin.com"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1.5 px-3 py-2 bg-yellow-100 hover:bg-yellow-200 border border-yellow-300 rounded-md font-medium text-yellow-900"
                >
                  <svg className="w-3.5 h-3.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                  Open LinkedIn
                </a>
                <p className="font-medium text-yellow-800 pt-1">Step 2 — after completing verification, do one of:</p>
                <ol className="list-decimal list-inside space-y-1 text-yellow-800">
                  <li>Grab your fresh <code className="bg-yellow-100 px-1 rounded">li_at</code> cookie from DevTools → expand <strong>&ldquo;Optional: seed with an existing session cookie&rdquo;</strong> above, paste it, and Save.</li>
                  <li>Or click <strong>Clear challenge state</strong> below then <strong>Test connection</strong> — the app will re-login with your password.</li>
                </ol>
              </div>
              <button
                type="button"
                onClick={() => { handleResolveChallenge(); setShowCookieSection(true); }}
                disabled={resolvingChallenge}
                className="px-3 py-1.5 text-xs bg-yellow-100 hover:bg-yellow-200 text-yellow-900 border border-yellow-300 rounded-md disabled:opacity-50"
              >
                {resolvingChallenge ? 'Clearing…' : 'Clear challenge state'}
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
