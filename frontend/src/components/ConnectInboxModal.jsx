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
    <div data-testid="modal-overlay" style={overlayStyle} onClick={onClose}>
      <div style={modalStyle} onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>{editing ? 'Edit inbox' : 'Connect inbox'}</h2>

        <label style={labelStyle}>
          Label
          <input
            value={form.label}
            onChange={(e) => update('label', e.target.value)}
            placeholder="e.g. Work Gmail"
            style={inputStyle}
          />
        </label>

        <label style={labelStyle}>
          Email address
          <input
            value={form.email_address}
            onChange={(e) => update('email_address', e.target.value)}
            placeholder="you@example.com"
            style={inputStyle}
          />
        </label>

        <div style={{ marginBottom: 8 }}>
          <span style={{ fontSize: 13, color: '#555', marginRight: 8 }}>Preset:</span>
          {Object.keys(PRESETS).map((p) => (
            <button
              type="button"
              key={p}
              onClick={() => applyPreset(p)}
              style={pillStyle}
              aria-label={`Use ${p} preset`}
            >
              {p}
            </button>
          ))}
        </div>

        <label style={labelStyle}>
          IMAP host
          <input
            value={form.imap_host}
            onChange={(e) => update('imap_host', e.target.value)}
            style={inputStyle}
          />
        </label>

        <div style={{ display: 'flex', gap: 12 }}>
          <label style={{ ...labelStyle, flex: 1 }}>
            Port
            <input
              type="number"
              value={form.imap_port}
              onChange={(e) => update('imap_port', Number(e.target.value))}
              style={inputStyle}
            />
          </label>
          <label style={{ ...labelStyle, flex: 1, justifyContent: 'flex-end' }}>
            <span style={{ display: 'block' }}>
              <input
                type="checkbox"
                checked={form.imap_use_ssl}
                onChange={(e) => update('imap_use_ssl', e.target.checked)}
              />{' '}
              Use SSL
            </span>
          </label>
        </div>

        <label style={labelStyle}>
          Username
          <input
            value={form.username}
            onChange={(e) => update('username', e.target.value)}
            style={inputStyle}
          />
        </label>

        <label style={labelStyle}>
          Password {editing && <span style={{ fontSize: 12, color: '#888' }}>(leave blank to keep current)</span>}
          <div style={{ display: 'flex', gap: 4 }}>
            <input
              type={showPassword ? 'text' : 'password'}
              value={form.password}
              onChange={(e) => update('password', e.target.value)}
              style={{ ...inputStyle, flex: 1 }}
            />
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              style={pillStyle}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
            >
              {showPassword ? 'Hide' : 'Show'}
            </button>
          </div>
        </label>

        <p style={helperStyle}>
          For Gmail, use an{' '}
          <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">
            App Password
          </a>{' '}
          (not your account password). Requires 2-Step Verification.
          For Outlook, use your regular password or an app password if MFA is enabled.
        </p>

        {testResult && (
          <div
            data-testid="test-result"
            style={{
              padding: 10,
              marginBottom: 12,
              borderRadius: 4,
              background: testResult.ok ? '#e6f7ed' : '#fdecea',
              color: testResult.ok ? '#1b5e20' : '#b71c1c',
              fontSize: 13,
            }}
          >
            {testResult.ok
              ? `Connection OK${testResult.message_count != null ? ` — ${testResult.message_count} messages in INBOX` : ''}`
              : `Connection failed: ${testResult.error || 'unknown error'}`}
          </div>
        )}

        {error && (
          <div data-testid="modal-error" style={{ color: '#b71c1c', marginBottom: 12 }}>
            {error}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button type="button" onClick={handleTest} disabled={testing || !editing} style={btnStyle}>
            {testing ? 'Testing…' : 'Test connection'}
          </button>
          <button type="button" onClick={onClose} style={btnStyle}>
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            style={{ ...btnStyle, background: '#2563eb', color: 'white' }}
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}

const overlayStyle = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0,0,0,0.4)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 1000,
};

const modalStyle = {
  background: 'white',
  padding: 24,
  borderRadius: 8,
  width: 480,
  maxWidth: '90vw',
  maxHeight: '90vh',
  overflowY: 'auto',
  boxShadow: '0 10px 30px rgba(0,0,0,0.2)',
};

const labelStyle = {
  display: 'block',
  marginBottom: 10,
  fontSize: 13,
  color: '#333',
};

const inputStyle = {
  display: 'block',
  width: '100%',
  padding: '8px 10px',
  border: '1px solid #ccc',
  borderRadius: 4,
  fontSize: 14,
  marginTop: 4,
  boxSizing: 'border-box',
};

const btnStyle = {
  padding: '8px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 14,
};

const pillStyle = {
  ...btnStyle,
  padding: '4px 10px',
  fontSize: 12,
  marginRight: 4,
};

const helperStyle = {
  fontSize: 12,
  color: '#555',
  background: '#f5f5f5',
  padding: 10,
  borderRadius: 4,
  marginBottom: 12,
};
