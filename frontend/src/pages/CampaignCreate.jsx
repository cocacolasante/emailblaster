import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { listAccounts } from '../api/connectedAccounts.js';
import { createCampaign, getPreview, getPreviewProgress } from '../api/campaigns.js';
import ConnectInboxModal from '../components/ConnectInboxModal.jsx';
import LeadUpload from '../components/LeadUpload.jsx';
import ScheduleConfig from '../components/ScheduleConfig.jsx';

const TONES = ['Professional', 'Friendly', 'Direct', 'Conversational', 'Formal'];

const DEFAULT_FORM = {
  name: '',
  goal: '',
  tone: 'Professional',
  sender_name: '',
  sender_email: '',
  research_mode: 'fast',
  sample_count: 5,
  connected_account_id: '',
  schedule_days: [0, 1, 2, 3, 4],
  schedule_time_start: '09:00',
  schedule_time_end: '17:00',
  schedule_timezone: 'UTC',
  max_per_hour: null,
  max_per_day: null,
  min_delay_seconds: 60,
  min_delay_unit: 'seconds',
};

function StatusBadge({ status }) {
  if (!status) return null;
  const colors = {
    untested: { bg: '#fff3cd', fg: '#7a5a00' },
    ok: { bg: '#e6f7ed', fg: '#1b5e20' },
    failed: { bg: '#fdecea', fg: '#b71c1c' },
  };
  const c = colors[status] || colors.untested;
  return (
    <span
      data-testid="inbox-status"
      style={{
        padding: '2px 8px',
        borderRadius: 12,
        fontSize: 12,
        background: c.bg,
        color: c.fg,
        marginLeft: 8,
      }}
    >
      {status === 'ok' ? 'Connected' : status === 'failed' ? 'Failed' : 'Untested'}
    </span>
  );
}

// --------------------------------------------------------------------------
// Step 1: campaign details
// --------------------------------------------------------------------------

function Step1({ form, setForm, onSubmit, submitting, error, accounts, onConnectInbox }) {
  function update(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  const selectedAccount = accounts.find((a) => a.id === form.connected_account_id);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      style={{ display: 'grid', gap: 16 }}
    >
      <section>
        <h2 style={sectionH2}>Campaign details</h2>
        <label style={labelStyle}>
          Name
          <input
            required
            value={form.name}
            onChange={(e) => update('name', e.target.value)}
            style={inputStyle}
          />
        </label>

        <label style={labelStyle}>
          Goal
          <textarea
            required
            rows={3}
            placeholder="What is the goal of this campaign? E.g. Book a demo, announce a product, invite to event"
            value={form.goal}
            onChange={(e) => update('goal', e.target.value)}
            style={{ ...inputStyle, fontFamily: 'inherit' }}
          />
        </label>

        <div style={{ display: 'flex', gap: 12 }}>
          <label style={{ ...labelStyle, flex: 1 }}>
            Tone
            <select
              value={form.tone}
              onChange={(e) => update('tone', e.target.value)}
              style={inputStyle}
            >
              {TONES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label style={{ ...labelStyle, flex: 1 }}>
            Sample count
            <input
              type="number"
              min="1"
              required
              value={form.sample_count}
              onChange={(e) => update('sample_count', Number(e.target.value))}
              style={inputStyle}
            />
          </label>
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <label style={{ ...labelStyle, flex: 1 }}>
            Sender name
            <input
              required
              value={form.sender_name}
              onChange={(e) => update('sender_name', e.target.value)}
              style={inputStyle}
            />
          </label>
          <label style={{ ...labelStyle, flex: 1 }}>
            Sender email
            <input
              type="email"
              required
              value={form.sender_email}
              onChange={(e) => update('sender_email', e.target.value)}
              style={inputStyle}
            />
          </label>
        </div>

        <div style={{ marginTop: 8 }}>
          <span style={labelStyle}>Research mode</span>
          <div role="radiogroup" style={{ display: 'flex', gap: 8 }}>
            {['fast', 'deep'].map((mode) => (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={form.research_mode === mode}
                onClick={() => update('research_mode', mode)}
                style={form.research_mode === mode ? activePillStyle : pillStyle}
              >
                {mode === 'fast' ? 'Fast (web only, ~10s/lead)' : 'Deep (+Apollo, ~45s/lead)'}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section>
        <h2 style={sectionH2}>Reply tracking <span style={{ fontWeight: 400, color: '#888', fontSize: 14 }}>(optional)</span></h2>
        <p style={{ color: '#555', fontSize: 13, marginTop: 0 }}>
          Connect an inbox to automatically detect when leads reply to your emails.
        </p>

        {accounts.length === 0 ? (
          <div data-testid="no-inbox-prompt" style={{ padding: 12, background: '#f5f5f5', borderRadius: 6 }}>
            <p style={{ margin: 0, marginBottom: 8 }}>No inboxes connected yet.</p>
            <button type="button" onClick={onConnectInbox} style={btnStyle}>
              Connect an inbox
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <select
              aria-label="Reply tracking inbox"
              value={form.connected_account_id}
              onChange={(e) => update('connected_account_id', e.target.value)}
              style={{ ...inputStyle, flex: 1 }}
            >
              <option value="">No reply tracking</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label} ({a.email_address})
                </option>
              ))}
            </select>
            {selectedAccount && <StatusBadge status={selectedAccount.last_test_status} />}
          </div>
        )}
      </section>

      <section>
        <h2 style={sectionH2}>Schedule</h2>
        <ScheduleConfig value={form} onChange={(next) => setForm(next)} />
      </section>

      {error && (
        <div data-testid="step1-error" style={errorStyle}>{error}</div>
      )}

      <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
        <button
          type="submit"
          disabled={submitting}
          style={primaryBtn}
          data-testid="step1-submit"
        >
          {submitting ? 'Creating…' : 'Next: upload leads'}
        </button>
      </div>
    </form>
  );
}

// --------------------------------------------------------------------------
// Step 3: progress poll + auto-redirect
// --------------------------------------------------------------------------

function Step3({ campaignId, onComplete }) {
  const { data: progress } = useQuery({
    queryKey: ['preview-progress', campaignId],
    queryFn: () => getPreviewProgress(campaignId),
    refetchInterval: 5000,
  });
  const { data: preview } = useQuery({
    queryKey: ['preview', campaignId],
    queryFn: () => getPreview(campaignId),
    refetchInterval: 5000,
  });

  useEffect(() => {
    if (preview?.all_ready) onComplete();
  }, [preview, onComplete]);

  const total = progress?.total_leads ?? 0;
  const researched = progress?.researched ?? 0;
  const composed = progress?.composed ?? 0;
  const pct = total ? Math.min(100, Math.round((composed / total) * 100)) : 0;

  return (
    <div data-testid="step3" style={{ textAlign: 'center', padding: 32 }}>
      <h2>Researching and composing emails…</h2>
      <p style={{ color: '#555' }}>
        {composed} of {total} composed · {researched} researched
      </p>
      <div style={{ background: '#e5e7eb', borderRadius: 8, overflow: 'hidden', height: 12, margin: '20px 0' }}>
        <div
          data-testid="progress-bar"
          style={{
            width: `${pct}%`,
            height: '100%',
            background: '#2563eb',
            transition: 'width 0.3s',
          }}
        />
      </div>
      <p style={{ fontSize: 13, color: '#666' }}>
        This page will auto-advance once the sample emails are ready for review.
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------
// Container
// --------------------------------------------------------------------------

export default function CampaignCreate() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(DEFAULT_FORM);
  const [campaignId, setCampaignId] = useState(null);
  const [showInboxModal, setShowInboxModal] = useState(false);
  const [error, setError] = useState(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: accounts = [] } = useQuery({
    queryKey: ['connected-accounts'],
    queryFn: listAccounts,
  });

  const createMutation = useMutation({
    mutationFn: createCampaign,
    onSuccess: (data) => {
      setCampaignId(data.id);
      setStep(2);
    },
    onError: (err) => {
      const detail = err?.response?.data?.detail || err.message || 'Failed to create campaign';
      setError(typeof detail === 'string' ? detail : JSON.stringify(detail));
    },
  });

  function buildPayload() {
    return {
      ...form,
      connected_account_id: form.connected_account_id || null,
      max_per_hour: form.max_per_hour || null,
      max_per_day: form.max_per_day || null,
      schedule_time_start:
        form.schedule_time_start.length === 5
          ? `${form.schedule_time_start}:00`
          : form.schedule_time_start,
      schedule_time_end:
        form.schedule_time_end.length === 5
          ? `${form.schedule_time_end}:00`
          : form.schedule_time_end,
    };
  }

  function handleStep1Submit() {
    setError(null);
    createMutation.mutate(buildPayload());
  }

  function onUploadComplete() {
    setStep(3);
  }

  function onStep3Complete() {
    if (campaignId) navigate(`/campaigns/${campaignId}/preview`);
  }

  return (
    <div style={{ padding: 24, maxWidth: 760, margin: '0 auto' }}>
      <h1>New campaign</h1>
      <div role="list" aria-label="Steps" style={{ display: 'flex', gap: 8, marginBottom: 24 }}>
        {['Details', 'Upload leads', 'Research'].map((label, i) => {
          const idx = i + 1;
          const active = idx === step;
          const done = idx < step;
          return (
            <div key={label} data-testid={`step-${idx}-indicator`} style={stepIndicator(active, done)}>
              {idx}. {label}
            </div>
          );
        })}
      </div>

      {step === 1 && (
        <Step1
          form={form}
          setForm={setForm}
          onSubmit={handleStep1Submit}
          submitting={createMutation.isPending}
          error={error}
          accounts={accounts}
          onConnectInbox={() => setShowInboxModal(true)}
        />
      )}
      {step === 2 && campaignId && (
        <LeadUpload campaignId={campaignId} onComplete={onUploadComplete} />
      )}
      {step === 3 && campaignId && (
        <Step3 campaignId={campaignId} onComplete={onStep3Complete} />
      )}

      {showInboxModal && (
        <ConnectInboxModal
          account={null}
          onClose={() => setShowInboxModal(false)}
          onSaved={(saved) => {
            queryClient.invalidateQueries({ queryKey: ['connected-accounts'] });
            // Auto-select the freshly created account.
            setForm((f) => ({ ...f, connected_account_id: saved.id }));
            setShowInboxModal(false);
          }}
        />
      )}
    </div>
  );
}

// ---------- shared styles ----------

const labelStyle = {
  display: 'block',
  marginBottom: 10,
  fontSize: 13,
  fontWeight: 500,
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

const sectionH2 = { marginTop: 0, fontSize: 18 };

const pillStyle = {
  padding: '6px 14px',
  border: '1px solid #ccc',
  borderRadius: 999,
  background: 'white',
  cursor: 'pointer',
  fontSize: 13,
};

const activePillStyle = {
  ...pillStyle,
  background: '#2563eb',
  color: 'white',
  border: '1px solid #2563eb',
};

const btnStyle = {
  padding: '8px 14px',
  border: '1px solid #ccc',
  borderRadius: 4,
  background: 'white',
  cursor: 'pointer',
  fontSize: 14,
};

const primaryBtn = {
  ...btnStyle,
  background: '#2563eb',
  color: 'white',
  border: '1px solid #2563eb',
};

const errorStyle = {
  color: '#b71c1c',
  background: '#fdecea',
  padding: 10,
  borderRadius: 4,
};

function stepIndicator(active, done) {
  return {
    padding: '8px 14px',
    borderRadius: 4,
    fontSize: 13,
    fontWeight: active ? 600 : 400,
    background: active ? '#2563eb' : done ? '#86efac' : '#e5e7eb',
    color: active ? 'white' : done ? '#065f46' : '#666',
  };
}
