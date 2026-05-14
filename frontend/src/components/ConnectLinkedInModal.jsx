import { useEffect, useRef, useState } from 'react';

import {
  connectViaUnipile,
  createLinkedInAccount,
  deleteLinkedInAccount,
  getLinkedInAccount,
  resolveLinkedInChallenge,
  syncUnipileStatus,
  testLinkedInAccount,
  updateLinkedInAccount,
} from '../api/linkedinAccounts.js';

// --- Static defaults --------------------------------------------------

const EMPTY_DIY = {
  label: '',
  linkedin_email: '',
  password: '',
  proxy_url: '',
  li_at_cookie: '',
};

// How often to poll /linkedin-accounts/{id}/sync-unipile while waiting for
// the user to complete Unipile's hosted-login.
const SYNC_POLL_MS = 3000;
// Give up polling after this many ms — user can re-open the modal to resume.
const SYNC_POLL_TIMEOUT_MS = 10 * 60 * 1000;

// ---------------------------------------------------------------------

export default function ConnectLinkedInModal({ account, onClose, onSaved }) {
  const editing = Boolean(account);
  const isUnipile = account?.provider_kind === 'unipile';

  // Mode toggle — "unipile" is the recommended hosted flow; "diy" is the
  // legacy password + li_at paste path.  When editing an existing
  // Unipile-managed row we lock to "unipile".  When editing a DIY row we
  // lock to "diy".  On the "create new" screen the user gets a choice and
  // we default to "unipile".
  const [mode, setMode] = useState(
    editing ? (isUnipile ? 'unipile' : 'diy') : 'unipile',
  );

  // ---- DIY form state --------------------------------------------------
  const [form, setForm] = useState(EMPTY_DIY);
  const [showPassword, setShowPassword] = useState(false);
  const [showCookieSection, setShowCookieSection] = useState(false);

  // ---- Common state ---------------------------------------------------
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState(null);
  const [resolvingChallenge, setResolvingChallenge] = useState(false);

  // ---- Unipile flow state ---------------------------------------------
  const [unipileLabel, setUnipileLabel] = useState('');
  const [unipileLaunching, setUnipileLaunching] = useState(false);
  const [unipileAccountId, setUnipileAccountId] = useState(null);
  const [unipileWaiting, setUnipileWaiting] = useState(false);
  const pollRef = useRef(null);

  // ---- Effects --------------------------------------------------------

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
      setForm(EMPTY_DIY);
      setTestResult(null);
    }
  }, [account]);

  // Stop polling on unmount / mode change.
  useEffect(() => {
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, []);

  function updateForm(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  // ---- DIY path -------------------------------------------------------

  async function handleDiySave() {
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

  async function handleDiyTest() {
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
      if (onSaved) onSaved({ ...account, status: 'untested', pending_challenge_url: null });
    } catch (e) {
      setError(e?.message || 'Resolve failed');
    } finally {
      setResolvingChallenge(false);
    }
  }

  // ---- Unipile path ---------------------------------------------------

  async function handleUnipileLaunch() {
    if (!unipileLabel.trim()) {
      setError('Give the account a label so you can recognise it later.');
      return;
    }
    setError(null);
    setUnipileLaunching(true);
    try {
      const successUrl = `${window.location.origin}/settings?unipile=success`;
      const failureUrl = `${window.location.origin}/settings?unipile=failure`;
      const { account_id, hosted_url } = await connectViaUnipile({
        label: unipileLabel.trim(),
        success_redirect_url: successUrl,
        failure_redirect_url: failureUrl,
      });
      setUnipileAccountId(account_id);
      setUnipileWaiting(true);
      // Open the hosted login in a new tab.  Pop-up blockers may stop
      // this; user can still click the link manually.
      window.open(hosted_url, '_blank', 'noopener,noreferrer');
      // Start polling for status flip.
      startUnipilePoll(account_id);
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || 'Failed to start Unipile flow';
      setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    } finally {
      setUnipileLaunching(false);
    }
  }

  function startUnipilePoll(accountId) {
    const start = Date.now();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      if (Date.now() - start > SYNC_POLL_TIMEOUT_MS) {
        clearInterval(pollRef.current);
        pollRef.current = null;
        setUnipileWaiting(false);
        setError('Timed out waiting for Unipile. Close this and reopen to retry.');
        return;
      }
      try {
        const fresh = await syncUnipileStatus(accountId).catch(() => getLinkedInAccount(accountId));
        if (fresh?.status === 'ok') {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setUnipileWaiting(false);
          setTestResult({ ok: true });
          if (onSaved) onSaved(fresh);
        } else if (fresh?.status === 'challenged') {
          // Unipile is still working on it; user might need to enter a code.
          // Keep polling — the next status update will resolve this.
        } else if (fresh?.status === 'failed' || fresh?.status === 'restricted') {
          clearInterval(pollRef.current);
          pollRef.current = null;
          setUnipileWaiting(false);
          setTestResult({ ok: false, error: fresh.last_error || `status=${fresh.status}` });
        }
      } catch (e) {
        // Transient — keep polling.
      }
    }, SYNC_POLL_MS);
  }

  async function handleCancelPending() {
    // Delete the placeholder row + tell Unipile we're abandoning the link.
    if (!unipileAccountId) return;
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    try {
      await deleteLinkedInAccount(unipileAccountId);
    } catch (e) {
      // Best-effort; don't block the close.
    }
    setUnipileWaiting(false);
    setUnipileAccountId(null);
    onClose?.();
  }

  const challenged = testResult?.status === 'challenged' || testResult?.meta?.challenged;

  // -- Render ------------------------------------------------------------

  return (
    <div
      data-testid="modal-overlay"
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
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

        {/* Mode toggle (only on new / non-locked rows) */}
        {!editing && (
          <div className="mb-5 inline-flex w-full rounded-lg border border-slate-200 bg-slate-50 p-1">
            <button
              type="button"
              onClick={() => setMode('unipile')}
              className={`flex-1 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                mode === 'unipile' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Hosted (Unipile)
            </button>
            <button
              type="button"
              onClick={() => setMode('diy')}
              className={`flex-1 px-3 py-1.5 text-sm font-medium rounded-md transition-colors ${
                mode === 'diy' ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              Local (legacy)
            </button>
          </div>
        )}

        {/* ---- Unipile flow ---- */}
        {mode === 'unipile' && (
          <div className="space-y-4">
            {!unipileWaiting && (
              <>
                <p className="text-sm text-slate-600 leading-relaxed">
                  Click <strong>Connect via Unipile</strong> to open LinkedIn's
                  login form in a new tab. Unipile runs a real desktop Chrome
                  on a residential IP, so LinkedIn sees a normal human session
                  and you won't get challenge-flagged. We never see your
                  password — Unipile handles it.
                </p>
                <div>
                  <label htmlFor="up-label" className="block text-sm font-medium text-slate-700 mb-1">Label</label>
                  <input
                    id="up-label"
                    value={unipileLabel}
                    onChange={(e) => setUnipileLabel(e.target.value)}
                    placeholder="e.g. Anthony — main"
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
                  />
                </div>
                <div className="flex gap-2 justify-end pt-2 border-t border-slate-100">
                  <button
                    type="button"
                    onClick={onClose}
                    className="inline-flex items-center px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 text-sm font-medium border border-slate-300 rounded-lg transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={handleUnipileLaunch}
                    disabled={unipileLaunching}
                    className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {unipileLaunching ? 'Starting…' : 'Connect via Unipile'}
                  </button>
                </div>
              </>
            )}
            {unipileWaiting && (
              <div className="space-y-3">
                <div className="p-4 rounded-lg bg-blue-50 border border-blue-200 text-sm text-blue-900">
                  <div className="font-medium mb-1">Waiting for you to finish in the new tab…</div>
                  <p className="text-xs">
                    A Unipile login window should have opened. Complete the
                    LinkedIn login there. When you're done, this modal will
                    refresh automatically.
                  </p>
                </div>
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={handleCancelPending}
                    className="text-xs text-slate-500 underline hover:text-slate-700"
                  >
                    Cancel and clean up
                  </button>
                </div>
              </div>
            )}
            {testResult?.ok && (
              <div
                data-testid="li-test-result"
                className="p-3 rounded-lg text-sm bg-emerald-50 border border-emerald-200 text-emerald-800"
              >
                Connection OK — Unipile is now driving this LinkedIn account.
              </div>
            )}
            {testResult?.ok === false && !challenged && (
              <div
                data-testid="li-test-result"
                className="p-3 rounded-lg text-sm bg-red-50 border border-red-200 text-red-800"
              >
                Connection failed: {testResult.error || 'unknown error'}
              </div>
            )}
            {error && (
              <div data-testid="modal-error" className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">
                {error}
              </div>
            )}
          </div>
        )}

        {/* ---- DIY (legacy) flow ---- */}
        {mode === 'diy' && (
          <div className="space-y-4">
            <div>
              <label htmlFor="li-label" className="block text-sm font-medium text-slate-700 mb-1">Label</label>
              <input
                id="li-label"
                value={form.label}
                onChange={(e) => updateForm('label', e.target.value)}
                placeholder="e.g. Anthony — main"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white"
              />
            </div>

            <div>
              <label htmlFor="li-email" className="block text-sm font-medium text-slate-700 mb-1">LinkedIn login email</label>
              <input
                id="li-email"
                value={form.linkedin_email}
                onChange={(e) => updateForm('linkedin_email', e.target.value)}
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
                  onChange={(e) => updateForm('password', e.target.value)}
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
                Local automation uses the password to drive a headless browser.
                LinkedIn's bot-detection routinely flags this path —{' '}
                <strong className="text-amber-700">switch to "Hosted (Unipile)" above</strong>{' '}
                unless you specifically need the local fallback.
              </p>
            </div>

            <div>
              <label htmlFor="li-proxy" className="block text-sm font-medium text-slate-700 mb-1">
                Proxy URL{' '}
                <span className="text-xs text-slate-400 font-normal">(optional)</span>
              </label>
              <input
                id="li-proxy"
                value={form.proxy_url}
                onChange={(e) => updateForm('proxy_url', e.target.value)}
                placeholder="http://user:pass@host:port"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white font-mono"
              />
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
                    <code className="bg-slate-100 px-1 rounded text-xs">li_at</code> session cookie
                  </label>
                  <input
                    id="li-at"
                    value={form.li_at_cookie}
                    onChange={(e) => updateForm('li_at_cookie', e.target.value)}
                    placeholder="Paste your li_at cookie value here"
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg text-xs text-slate-900 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 bg-white font-mono"
                  />
                </div>
              )}
            </div>

            {challenged && (
              <div className="p-3 rounded-lg bg-yellow-50 border border-yellow-200 text-sm text-yellow-900 space-y-2">
                <div className="font-medium">LinkedIn requires verification.</div>
                <p className="text-xs text-yellow-800">
                  Log into LinkedIn in your real browser, complete any
                  verification it asks for, then either paste a fresh{' '}
                  <code className="bg-yellow-100 px-1 rounded">li_at</code> cookie
                  above or click below to clear the challenge state.
                </p>
                <button
                  type="button"
                  onClick={handleResolveChallenge}
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

            <div className="flex gap-2 justify-end pt-4 border-t border-slate-100">
              <button
                type="button"
                onClick={handleDiyTest}
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
                onClick={handleDiySave}
                disabled={saving}
                className="inline-flex items-center px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
